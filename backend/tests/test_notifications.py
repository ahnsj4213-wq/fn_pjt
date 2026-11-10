"""알림 창 (SC-R12): new interview, applicant answer to a mailed date, posting deadline with 7일 연장 / 지금 마감."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.interview import turns
from app.notify import mail  # noqa: F401
from tests.fixtures import create_posting
from tests.helpers import recruiter
from tests.test_handlers import drain, setup_app
from tests.test_mail import smtp  # noqa: F401

pytestmark = pytest.mark.integration


async def _notes(client):
    return (await client.get("/api/recruiter/notifications")).json()


async def test_interview_events_and_schedule_answer_reach_the_inbox_once(client, make_client, smtp):  # noqa: F811
    _, app_id, cand = await setup_app(client, make_client)
    assert (await _notes(client))["items"] == []
    start = ("UPDATE applications a SET attempt_status='INTERVIEWING', eval_settings_id=p.current_settings_id, "
             "interview_started_at=now() FROM job_postings p WHERE a.id=:a AND p.id=a.posting_id")
    async with session_scope() as db:  # the applicant leaves half way (ADR-0024: ended); a second signal changes nothing
        await db.execute(text("UPDATE applications SET attempt_status='PREPARING' WHERE id=:a"), {"a": app_id})
        await db.execute(text(start), {"a": app_id})
        await turns.interrupt(db, app_id)
        await turns.interrupt(db, app_id)
    n = await _notes(client)
    assert [(i["kind"], "멈췄습니다" in i["text"]) for i in n["items"]] == [("APPLY", True)] and n["unread"] == 1
    assert n["items"][0]["application_id"] == app_id

    await client.post(f"/api/recruiter/applications/{app_id}/mail", json={
        "kind": "INTERVIEW", "subject": "면접", "body": "안내", "starts_at": (datetime.now(UTC) + timedelta(days=9)).isoformat()})
    await drain()
    token = smtp.messages[-1].get_content().split("token=")[1].split()[0]
    await cand.post("/api/schedule-response", json={"token": token, "answer": "ACCEPT"})
    await cand.post("/api/schedule-response", json={"token": token, "answer": "ACCEPT"})
    items = (await _notes(client))["items"]
    assert [i["kind"] for i in items] == ["SCHEDULE", "APPLY"] and "수락했습니다" in items[0]["text"]

    assert (await client.post("/api/recruiter/notifications/read")).status_code == 204
    assert (await _notes(client))["unread"] == 0


async def test_deadline_alert_extend_close_and_company_scope(client, make_client):
    await recruiter(client)
    p = await create_posting(client)
    pid = p["id"]
    async with session_scope() as db:
        # today 23:59:30 Korea time: always ahead (except the very last seconds) and always inside the next 24 hours
        await db.execute(text("UPDATE job_postings SET deadline_at = (date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') "
                              "+ interval '23 hours 59 minutes 30 seconds') AT TIME ZONE 'Asia/Seoul' WHERE id = :p"), {"p": pid})
    n = (await _notes(client))["items"]
    assert len(n) == 1 and n[0]["kind"] == "DEADLINE" and n[0]["posting_open"] is True
    assert len((await _notes(client))["items"]) == 1  # written once
    other = make_client()
    await recruiter(other)
    assert (await _notes(other))["items"] == []
    assert (await other.post(f"/api/recruiter/notifications/{n[0]['id']}/extend")).status_code == 404

    r = await client.post(f"/api/recruiter/notifications/{n[0]['id']}/extend")
    assert r.status_code == 200 and "늘렸습니다" in r.json()["done_text"]
    async with session_scope() as db:
        left = await db.scalar(text("SELECT deadline_at - now() FROM job_postings WHERE id = :p"), {"p": pid})
    assert left > timedelta(days=7)
    assert (await _notes(client))["items"][0]["done_text"].startswith("마감일을")

    async with session_scope() as db:  # a new deadline on another day, again within 24 hours: a new alert
        await db.execute(text("UPDATE job_postings SET deadline_at = (date_trunc('day', now() AT TIME ZONE 'Asia/Seoul') "
                              "+ interval '1 day 30 seconds') AT TIME ZONE 'Asia/Seoul' WHERE id = :p"), {"p": pid})
    items = (await _notes(client))["items"]
    assert len(items) == 2
    r = await client.post(f"/api/recruiter/notifications/{items[0]['id']}/close")
    assert r.status_code == 200 and r.json()["done_text"] == "공고를 마감했습니다"
    assert (await client.post(f"/api/recruiter/notifications/{items[0]['id']}/close")).status_code == 409
    assert [i for i in (await _notes(client))["items"] if i["id"] == items[0]["id"]][0]["posting_open"] is False
