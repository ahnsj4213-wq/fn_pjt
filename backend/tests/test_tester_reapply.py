"""Test candidate accounts (AIHIRE_TESTER_EMAILS): applying to the same posting again replaces the earlier application.

Everyone else still gets ALREADY_APPLIED (JOB-02, AC-04); prod refuses the setting.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.fixtures import apply
from tests.test_handlers import fake_ai, q  # noqa: F401  fake_ai is a fixture
from tests.test_retention import CONTENT_TABLES, counts, evaluated_application

pytestmark = pytest.mark.usefixtures("fake_ai")


async def _owner(app_id: str) -> tuple[str, str]:
    return (await q("SELECT a.posting_id::text, u.email FROM applications a JOIN users u ON u.id = a.candidate_id "
                    "WHERE a.id = :a", a=app_id))[0]


async def test_a_tester_applies_again_and_the_earlier_application_is_gone(client, make_client, monkeypatch):
    app_id, cand, _ = await evaluated_application(client, make_client)  # submitted, evaluated, report and note
    posting_id, email = await _owner(app_id)
    assert (await apply(cand, posting_id)).json()["error"]["code"] == "ALREADY_APPLIED"  # not a tester yet

    monkeypatch.setattr(get_settings(), "tester_emails", f"other@x.test, {email.upper()}")
    listed = {p["id"]: p["has_applied"] for p in (await cand.get("/api/postings")).json()}
    assert listed[posting_id] is False  # the main page keeps offering 지원하기
    r = await apply(cand, posting_id)
    assert r.status_code == 201, r.text
    new_id = r.json()["application_id"]
    assert new_id != app_id
    assert (await q("SELECT count(*) FROM applications WHERE id = :a", a=app_id)) == [(0,)]
    assert all(v == 0 for v in (await counts(app_id)).values()), CONTENT_TABLES
    for t in ("evaluation_runs", "application_state_events", "interviews", "question_sets", "jobs"):
        assert (await q(f"SELECT count(*) FROM {t} WHERE application_id = :a", a=app_id)) == [(0,)], t
    assert (await q("SELECT count(*) FROM audit_logs"))[0][0] > 0  # the audit trail stays
    mine = [a["id"] for a in (await cand.get("/api/candidate/applications")).json()]
    assert mine == [new_id]


async def test_other_candidates_still_apply_once(client, make_client, monkeypatch):
    app_id, cand, _ = await evaluated_application(client, make_client)
    posting_id, _ = await _owner(app_id)
    monkeypatch.setattr(get_settings(), "tester_emails", "someone-else@x.test")
    assert (await apply(cand, posting_id)).json()["error"]["code"] == "ALREADY_APPLIED"
    assert (await q("SELECT count(*) FROM applications WHERE id = :a", a=app_id)) == [(1,)]


async def test_a_decided_application_is_kept_because_decisions_are_append_only(client, make_client, monkeypatch):
    app_id, cand, _ = await evaluated_application(client, make_client)
    r = await client.post(f"/api/recruiter/applications/{app_id}/decision", json={"decision": "PASS", "expected_version": 0})
    assert r.status_code == 200, r.text
    posting_id, email = await _owner(app_id)
    monkeypatch.setattr(get_settings(), "tester_emails", email)
    r = await apply(cand, posting_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_DECIDED"
    assert (await q("SELECT count(*) FROM applications WHERE id = :a", a=app_id)) == [(1,)]
    assert (await counts(app_id))["interview_answers"] == 5  # nothing was deleted


def test_prod_refuses_tester_accounts():
    with pytest.raises(ValidationError, match="tester_emails"):
        Settings(env="prod", tester_emails="t@x.test")
