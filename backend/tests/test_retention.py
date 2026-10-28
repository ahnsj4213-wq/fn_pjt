"""SEC-07 retention purge and USR-07 withdrawal (TRD 03 §8, docs/data-retention.md)."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope, sessionmaker
from app.core.timeutil import utcnow
from app.retention.service import run_file_gc, run_retention
from tests.fixtures import RESUME, apply, create_posting
from tests.helpers import PASSWORD
from tests.test_handlers import (  # noqa: F401  fake_ai is a fixture
    answer_all,
    drain,
    fake_ai,
    prepare_and_compose,
    q,
    setup_app,
    submit_and_register,
)

pytestmark = pytest.mark.usefixtures("fake_ai")

CONTENT_TABLES = ("interview_answers", "interview_turns", "interview_questions", "answer_evaluations",
                  "answer_pre_evaluations", "recruiter_reports", "candidate_feedback", "recruiter_notes",
                  "followup_decisions", "proctor_events")


async def evaluated_application(client, make_client) -> tuple[str, object, str]:
    """A submitted, evaluated application with one stored answer audio file. Returns (app_id, cand, audio_path)."""
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs)
    await submit_and_register(app_id)
    await drain()
    key = f"answers/{app_id}.flac"
    path = get_settings().data_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC")
    async with session_scope() as db:
        await db.execute(text("SET LOCAL app.purge = 'on'"))  # answers are immutable; this is test setup only
        await db.execute(text("UPDATE interview_answers SET input_mode = 'VOICE', audio_key = :k WHERE id = (SELECT id FROM interview_answers "
                              "WHERE application_id = :a ORDER BY id LIMIT 1)"), {"k": key, "a": app_id})
    r = await client.post(f"/api/recruiter/applications/{app_id}/notes", json={"body": "면접 태도 좋음"})
    assert r.status_code == 201, r.text
    return app_id, cand, str(path)


async def counts(app_id: str) -> dict[str, int]:
    return {t: (await q(f"SELECT count(*) FROM {t} WHERE application_id = :a", a=app_id))[0][0]
            if t not in ("answer_evaluations", "answer_pre_evaluations") else
            (await q(f"SELECT count(*) FROM {t} WHERE answer_id IN (SELECT id FROM interview_answers "
                     "WHERE application_id = :a)", a=app_id))[0][0] for t in CONTENT_TABLES}


async def purge(as_of):
    async with sessionmaker()() as db:
        return await run_retention(db, as_of=as_of)


async def test_retention_purges_audio_then_content_and_keeps_records(client, make_client):
    app_id, _, audio = await evaluated_application(client, make_client)
    r = await client.post(f"/api/recruiter/applications/{app_id}/decision", json={"decision": "PASS", "expected_version": 0})
    assert r.status_code == 200, r.text
    before = await counts(app_id)
    assert before["interview_answers"] == 10 and before["recruiter_reports"] == 1 and before["recruiter_notes"] == 1
    tts_before = (await q("SELECT count(*) FROM question_tts_assets"))[0][0]
    assert tts_before > 0
    now = utcnow()

    # Before 30 days nothing but the finished attempt's TTS cache goes.
    c = await purge(now + timedelta(days=29))
    assert (c.audio_apps, c.content_apps, c.tts_assets) == (0, 0, tts_before)
    assert (await q("SELECT count(*) FROM interview_answers WHERE audio_key IS NOT NULL AND application_id=:a",
                    a=app_id)) == [(1,)]

    # `purge --dry-run` counts without touching anything.
    async with sessionmaker()() as db:
        dry = await run_retention(db, as_of=now + timedelta(days=31), dry_run=True)
    assert (dry.audio_apps, dry.content_apps) == (1, 0)
    assert (await q("SELECT audio_purged_at FROM applications WHERE id=:a", a=app_id)) == [(None,)]

    # Stage 1 (30 days after the decision): audio only.
    c = await purge(now + timedelta(days=31))
    assert (c.audio_apps, c.content_apps) == (1, 0)
    assert await q("SELECT count(*) FROM interview_answers WHERE audio_key IS NOT NULL AND application_id=:a",
                   a=app_id) == [(0,)]
    assert await counts(app_id) == before
    assert (await q("SELECT audio_purged_at IS NOT NULL, content_purged_at IS NULL FROM applications WHERE id=:a",
                    a=app_id)) == [(True, True)]
    assert (await purge(now + timedelta(days=31))).audio_apps == 0  # idempotent

    # Stage 2 (retention_content_days): every content row, the resume snapshot; records stay.
    c = await purge(now + timedelta(days=get_settings().retention_content_days + 1))
    assert c.content_apps == 1
    assert all(v == 0 for v in (await counts(app_id)).values()), await counts(app_id)
    assert await q("SELECT resume_snapshot::text, resume_snapshot_text, decision_status, attempt_status, "
                   "content_purged_at IS NOT NULL FROM applications WHERE id=:a", a=app_id) == \
        [("{}", "", "PASS", "SUBMITTED", True)]
    for table in ("decision_history", "consent_records", "evaluation_runs"):
        col = "id" if table != "consent_records" else "candidate_id"
        n = (await q(f"SELECT count(*) FROM {table} WHERE {col} IN (SELECT {'id' if table != 'consent_records' else 'candidate_id'} "
                     f"FROM {'applications' if table == 'consent_records' else table} WHERE "
                     f"{'id' if table == 'consent_records' else 'application_id'} = :a)", a=app_id))[0][0]
        assert n >= 1, table

    # Files go through the outbox; file_gc deletes them (and a missing file is not an error).
    from pathlib import Path

    assert Path(audio).exists()
    async with sessionmaker()() as db:
        assert await run_file_gc(db) >= 1
    assert not Path(audio).exists()
    assert await q("SELECT count(*) FROM file_deletions WHERE done_at IS NULL") == [(0,)]


async def test_retention_skips_undecided_open_postings_and_active_attempts(client, make_client):
    _, app_id, cand = await setup_app(client, make_client)
    await prepare_and_compose(cand, app_id)  # PREPARING, posting open, no decision: the clock has not started
    c = await purge(utcnow() + timedelta(days=10_000))
    assert (c.audio_apps, c.content_apps) == (0, 0)
    assert (await q("SELECT count(*) FROM interview_questions WHERE application_id=:a", a=app_id))[0][0] == 10


async def test_withdrawal_reauth_active_attempt_and_anonymisation(client, make_client):
    app_id, cand, _ = await evaluated_application(client, make_client)
    me = (await cand.get("/api/auth/me")).json()["user"]
    email = (await q("SELECT email FROM users WHERE id=:u", u=me["id"]))[0][0]
    r = await cand.put("/api/candidate/resume", json={"content": RESUME})
    assert r.status_code == 200, r.text
    # A second application in PREPARING blocks withdrawal (OQ-12 provisional).
    p2 = await create_posting(client, title="두 번째 공고")
    r = await apply(cand, p2["id"])
    app2 = r.json()["application_id"]
    assert (await cand.post(f"/api/candidate/applications/{app2}/interview/prepare")).status_code == 200

    r = await cand.request("DELETE", "/api/candidate/me", json={"password": "wrong-password"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "REAUTH_FAILED"
    r = await cand.request("DELETE", "/api/candidate/me", json={"password": PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTIVE_ATTEMPT"
    assert (await cand.post(f"/api/candidate/applications/{app2}/interview/leave-prepare")).status_code == 200

    r = await cand.request("DELETE", "/api/candidate/me", json={"password": PASSWORD})
    assert r.status_code == 202, r.text
    assert (await cand.get("/api/auth/me")).status_code == 401  # every session revoked
    assert await q("SELECT email, name, phone, password_hash, status, withdrawn_at IS NOT NULL FROM users WHERE id=:u",
                   u=me["id"]) == [(None, None, None, None, "WITHDRAWN", True)]
    assert await q("SELECT count(*) FROM resumes WHERE candidate_id=:u", u=me["id"]) == [(0,)]
    assert await q("SELECT count(*) FROM resume_files WHERE owner_id=:u", u=me["id"]) == [(0,)]
    rows = await q("SELECT id::text, attempt_status, withdrawn_at IS NOT NULL, content_purged_at IS NOT NULL "
                   "FROM applications WHERE candidate_id=:u ORDER BY applied_at", u=me["id"])
    assert rows == [(app_id, "SUBMITTED", True, True), (app2, "EXPIRED", True, True)]
    assert all(v == 0 for v in (await counts(app_id)).values())
    assert await q("SELECT count(*) FROM consent_records WHERE candidate_id=:u", u=me["id"]) == [(2,)]  # kept, no PII
    assert await q("SELECT count(*) FROM jobs WHERE application_id = ANY(ARRAY[:a, :b]::uuid[]) "
                   "AND state IN ('QUEUED','RUNNING')", a=app_id, b=app2) == [(0,)]
    assert (await q("SELECT action FROM audit_logs WHERE action='ACCOUNT_WITHDRAW' AND target_id=:u", u=me["id"])) == \
        [("ACCOUNT_WITHDRAW",)]
    # The recruiter still sees the application row, without personal data.
    listed = (await client.get("/api/recruiter/applications")).json()["items"]
    assert {i["candidate_name"] for i in listed if i["id"] in (app_id, app2)} == {"탈퇴한 지원자"}
    # The old credentials no longer work and the e-mail address is free again.
    r = await cand.post("/api/auth/login", json={"role": "CANDIDATE", "email": email, "password": PASSWORD})
    assert r.status_code == 401
    r = await make_client().post("/api/auth/signup", json={"name": "새사람", "email": email, "password": PASSWORD,
                                                           "password_confirm": PASSWORD, "phone": "010-1111-2222"})
    assert r.status_code == 201, r.text
