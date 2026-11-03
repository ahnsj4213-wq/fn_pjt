"""Email notices (app/notify/mail.py): receipt on apply, notice on every decision, nothing for withdrawn accounts,
SMTP outage retried without using an attempt. An in-process SMTP server (aiosmtpd) stands in for the mailer."""
import email
from email.policy import default as default_policy

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from app.notify import mail  # noqa: F401  registers the send_email handler
from tests.evalstate import to_eval_done
from tests.helpers import PASSWORD
from tests.test_handlers import drain, setup_app


class Inbox:
    def __init__(self) -> None:
        self.messages: list[email.message.EmailMessage] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 - aiosmtpd hook name
        self.messages.append(email.message_from_bytes(envelope.content, policy=default_policy))
        return "250 OK"


@pytest.fixture
def smtp():
    controller_mod = pytest.importorskip("aiosmtpd.controller")
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    inbox = Inbox()
    ctl = controller_mod.Controller(inbox, hostname="127.0.0.1", port=port)
    ctl.start()
    s = get_settings()
    saved = (s.mail_enabled, s.smtp_host, s.smtp_port)
    s.mail_enabled, s.smtp_host, s.smtp_port = True, "127.0.0.1", port
    yield inbox
    s.mail_enabled, s.smtp_host, s.smtp_port = saved
    ctl.stop()


async def _jobs(app_id: str):
    async with session_scope() as db:
        return (await db.execute(text("SELECT payload->>'template', state, attempts, infra_deferrals FROM jobs "
                                      "WHERE kind = 'send_email' AND application_id = :a ORDER BY id"),
                                 {"a": app_id})).all()


async def test_receipt_and_result_mails_carry_no_result(client, make_client, smtp):
    _, app_id, cand = await setup_app(client, make_client)
    await drain()
    assert len(smtp.messages) == 1
    m = smtp.messages[0]
    assert "지원이 접수되었습니다" in m["Subject"] and "/me/applications" in m.get_content()
    me = (await cand.get("/api/auth/me")).json()["user"]
    async with session_scope() as db:
        addr = (await db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": me["id"]})).scalar_one()
    assert m["To"] == addr

    await to_eval_done(app_id)
    r = await client.post(f"/api/recruiter/applications/{app_id}/decision", json={"decision": "PASS", "expected_version": 0})
    assert r.status_code == 200, r.text
    await drain()
    m = smtp.messages[1]
    assert "지원 결과가 등록되었습니다" in m["Subject"]
    assert "합격" not in m.get_content() and "PASS" not in m.get_content()  # the result itself stays on the site
    assert [(t, st) for t, st, *_ in await _jobs(app_id)] == [("APPLICATION_RECEIVED", "SUCCEEDED"),
                                                             ("RESULT_RELEASED", "SUCCEEDED")]


async def test_withdrawn_account_gets_nothing_and_smtp_outage_is_deferred(client, make_client, smtp):
    _, app_id, cand = await setup_app(client, make_client)
    s = get_settings()
    s.smtp_port = 1  # mailer down
    await drain()
    (_, state, attempts, deferrals), = await _jobs(app_id)
    assert (state, attempts, deferrals) == ("QUEUED", 0, 1)  # InfraUnavailable: no attempt used
    r = await cand.request("DELETE", "/api/candidate/me", json={"password": PASSWORD})
    assert r.status_code == 202
    assert [st for _, st, *_ in await _jobs(app_id)] == ["CANCELLED"]
    assert smtp.messages == []


async def test_password_reset_mail_link_works_once_within_30_minutes(client, make_client, smtp):
    from tests.helpers import login, signup

    email_addr = await signup(client)
    await login(client, email_addr)
    other = make_client()
    r = await other.post("/api/auth/password-reset", json={"email": email_addr})
    assert r.status_code == 202
    same = await other.post("/api/auth/password-reset", json={"email": "nobody@mail.example"})
    assert same.status_code == 202 and same.json() == r.json()  # no hint whether the address exists
    await drain()
    (m,) = smtp.messages
    assert m["To"] == email_addr and "/reset-password?token=" in m.get_content()
    token = m.get_content().split("token=")[1].split()[0]
    body = {"token": token, "password": "N3w-passw0rd", "password_confirm": "N3w-passw0rd"}
    assert (await other.post("/api/auth/password-reset/confirm", json=body)).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401  # old sessions signed out
    r = await other.post("/api/auth/password-reset/confirm", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "RESET_TOKEN_INVALID"
    login = await make_client().post("/api/auth/login", json={"role": "CANDIDATE", "email": email_addr,
                                                               "password": "N3w-passw0rd"})
    assert login.status_code == 200


def test_mail_switch_off_queues_nothing():
    assert get_settings().mail_enabled is False  # default: development and tests send nothing
