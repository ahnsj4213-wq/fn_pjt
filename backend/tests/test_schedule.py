"""기업 일정 관리 (SC-R11) and 메일 보내기 (SC-R14): calendar entries, mailed interview dates and the applicant's answer."""
from datetime import UTC, datetime, timedelta

import pytest

from app.notify import mail  # noqa: F401  registers the send_email handler
from tests.helpers import recruiter
from tests.test_handlers import drain, setup_app
from tests.test_mail import smtp  # noqa: F401  in-process SMTP fixture

pytestmark = pytest.mark.integration


def _in(days: int, hour: int = 5) -> str:
    return (datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)).isoformat()


def _month(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m")


async def test_calendar_entries_are_company_scoped_and_deadlines_are_read_only(client, make_client):
    await recruiter(client)
    r = await client.post("/api/recruiter/schedule", json={"starts_at": _in(3), "kind": "INTERNAL", "content": "채용 회의"})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert (await client.post("/api/recruiter/schedule", json={"starts_at": _in(3), "kind": "INTERNAL", "content": " "})).status_code == 422
    items = (await client.get(f"/api/recruiter/schedule?month={_month(3)}")).json()
    assert [(e["kind"], e["content"], e["auto"]) for e in items if e["id"] == sid] == [("INTERNAL", "채용 회의", False)]
    r = await client.put(f"/api/recruiter/schedule/{sid}", json={"starts_at": _in(4), "kind": "RESULT", "content": "결과 발표"})
    assert r.status_code == 200 and r.json()["kind"] == "RESULT"
    assert (await client.put("/api/recruiter/schedule/deadline:x", json={"starts_at": _in(4), "content": "x"})).status_code == 409

    other = make_client()
    await recruiter(other)
    assert all(e["id"] != sid for e in (await other.get(f"/api/recruiter/schedule?month={_month(3)}")).json())
    assert (await other.put(f"/api/recruiter/schedule/{sid}", json={"starts_at": _in(4), "content": "x"})).status_code == 404
    assert (await other.delete(f"/api/recruiter/schedule/{sid}")).status_code == 404

    assert (await client.delete(f"/api/recruiter/schedule/{sid}")).status_code == 204
    assert all(e["id"] != sid for e in (await client.get(f"/api/recruiter/schedule?month={_month(4)}")).json())


async def test_mailed_interview_date_is_answered_from_the_link(client, make_client, smtp):  # noqa: F811
    _, app_id, cand = await setup_app(client, make_client)
    await drain()
    sent_before = len(smtp.messages)
    bad = await client.post(f"/api/recruiter/applications/{app_id}/mail", json={"kind": "INTERVIEW", "subject": "면접 안내", "body": "본문"})
    assert bad.status_code == 422  # no date
    assert (await client.post(f"/api/recruiter/applications/{app_id}/mail",
                              json={"kind": "FREE", "subject": "", "body": "본문"})).status_code == 422
    r = await client.post(f"/api/recruiter/applications/{app_id}/mail", json={
        "kind": "INTERVIEW", "subject": "2차 면접 일정 안내", "body": "안녕하세요.", "starts_at": _in(9), "place": "본사 5층"})
    assert r.status_code == 201 and r.json()["schedule_id"]
    await drain()
    m = smtp.messages[sent_before]
    body = m.get_content()
    assert m["Subject"].startswith("[") and "2차 면접 일정 안내" in m["Subject"]
    assert "본사 5층" in body and "/schedule-response?token=" in body
    token = body.split("token=")[1].split()[0]

    mine = (await cand.get("/api/candidate/schedules")).json()
    assert [(s["title"], s["status"], s["place"]) for s in mine] == [("2차 면접", "PROPOSED", "본사 5층")]
    info = (await cand.get(f"/api/schedule-response?token={token}")).json()
    assert info["place"] == "본사 5층" and info["status"] == "PROPOSED"
    assert (await cand.get("/api/schedule-response?token=nope")).status_code == 404
    r = await cand.post("/api/schedule-response", json={"token": token, "answer": "ACCEPT"})
    assert r.status_code == 200 and r.json()["status"] == "ACCEPTED"
    assert (await cand.get("/api/candidate/schedules")).json()[0]["status"] == "ACCEPTED"
    cal = (await client.get(f"/api/recruiter/schedule?month={_month(9)}")).json()
    mailed = [e for e in cal if e["application_id"] == app_id]
    assert mailed and mailed[0]["auto"] is True and mailed[0]["status"] == "ACCEPTED" and mailed[0]["candidate_name"]

    # An accepted date moved by the company: the applicant gets a change mail with a fresh link.
    sent_before = len(smtp.messages)
    sid = mailed[0]["id"]
    assert (await client.put(f"/api/recruiter/schedule/{sid}", json={"starts_at": _in(10), "kind": "INTERVIEW", "content": "2차 면접",
                                                                     "place": "본사 5층"})).status_code == 200
    await drain()
    assert len(smtp.messages) == sent_before + 1 and "변경" in smtp.messages[-1]["Subject"]
    assert (await cand.post("/api/schedule-response", json={"token": token, "answer": "ACCEPT"})).status_code == 404  # old link is dead

    # Another interview mail replaces a pending date (here: the date is still accepted, so a second one is added only
    # after it is answered); a second mail while PROPOSED replaces it.
    await client.post(f"/api/recruiter/applications/{app_id}/mail", json={
        "kind": "INTERVIEW", "subject": "새 일정", "body": "다시 안내드립니다.", "starts_at": _in(12)})
    await client.post(f"/api/recruiter/applications/{app_id}/mail", json={
        "kind": "INTERVIEW", "subject": "또 새 일정", "body": "다시 안내드립니다.", "starts_at": _in(13)})
    pending = [s for s in (await cand.get("/api/candidate/schedules")).json() if s["status"] == "PROPOSED"]
    assert len(pending) == 1


async def test_mail_and_calendar_need_a_recruiter_of_that_company(client, make_client):
    _, app_id, cand = await setup_app(client, make_client)
    other = make_client()
    await recruiter(other)
    r = await other.post(f"/api/recruiter/applications/{app_id}/mail", json={"kind": "FREE", "subject": "안녕", "body": "x"})
    assert r.status_code == 404
    assert (await cand.post(f"/api/recruiter/applications/{app_id}/mail", json={"kind": "FREE", "subject": "a", "body": "x"})).status_code in (401, 403)
    assert (await cand.get("/api/recruiter/schedule")).status_code == 403
