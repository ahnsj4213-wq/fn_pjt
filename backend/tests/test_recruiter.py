"""ADM-01~07, AC-03 (application routes), AC-19, AC-20, ADM-06, 7.3."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import session_scope
from tests.evalstate import CANARY, to_eval_done, to_on_hold
from tests.fixtures import apply, create_posting
from tests.helpers import candidate, recruiter

pytestmark = pytest.mark.integration
FORBIDDEN_KEYS = {"total_score", "total", "weights", "weight", "quote", "quotes", "evidence", "memo", "note", "notes",
                  "hidden", "comment", "run_id", "model", "summary", "insufficient_count", "insufficient_by_criterion"}


async def _setup(client, make_client):
    company, _ = await recruiter(client)
    p = await create_posting(client)
    cand = make_client()
    await candidate(cand)
    r = await apply(cand, p["id"])
    return p, cand, r.json()["application_id"]


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | set().union(*(_keys(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj)) if obj else set()
    return set()


async def test_ac20_decision_requires_done_eval_and_keeps_history(client, make_client):
    p, cand, aid = await _setup(client, make_client)
    r = await client.post(f"/api/recruiter/applications/{aid}/decision", json={"decision": "PASS", "expected_version": 0})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EVAL_NOT_DONE"
    await to_eval_done(aid)
    assert (await client.get("/api/recruiter/dashboard")).json()["awaiting_review"] == 1
    r = await client.post(f"/api/recruiter/applications/{aid}/decision", json={"decision": "PASS", "expected_version": 0})
    assert r.status_code == 200 and r.json()["version"] == 1
    stale = await client.post(f"/api/recruiter/applications/{aid}/decision", json={"decision": "FAIL", "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "DECISION_CONFLICT"
    r = await client.post(f"/api/recruiter/applications/{aid}/decision", json={"decision": "FAIL", "expected_version": 1})
    assert r.status_code == 200
    hist = (await client.get(f"/api/recruiter/applications/{aid}/decision-history")).json()
    assert [(h["from"], h["to"]) for h in hist] == [("UNDECIDED", "PASS"), ("PASS", "FAIL")]
    assert all(h["decided_by"]["name"] and h["decided_at"] for h in hist)
    # candidate sees the decision immediately (6.6)
    assert (await cand.get(f"/api/candidate/applications/{aid}")).json()["decision"] == "FAIL"
    async with session_scope() as db:
        actions = (await db.execute(text("SELECT action FROM audit_logs WHERE action='DECISION_SET'"))).scalars().all()
        assert len(actions) == 2
        with pytest.raises(DBAPIError):  # history is append-only
            await db.execute(text("DELETE FROM decision_history"))
    async with session_scope() as db:
        with pytest.raises(DBAPIError):  # cannot revert to UNDECIDED (OQ-28)
            await db.execute(text("UPDATE applications SET decision_status='UNDECIDED', decided_at=NULL WHERE id=:a"),
                             {"a": aid})


async def test_ac19_candidate_result_hidden_until_decision_and_allowlisted(client, make_client):
    p, cand, aid = await _setup(client, make_client)
    await to_eval_done(aid)
    before = (await cand.get(f"/api/candidate/applications/{aid}/result")).json()
    assert before["feedback"] is None
    await client.post(f"/api/recruiter/applications/{aid}/notes", json={"body": f"메모 {CANARY}"})
    await client.post(f"/api/recruiter/applications/{aid}/decision", json={"decision": "PASS", "expected_version": 0})
    responses = [await cand.get(u) for u in (f"/api/candidate/applications/{aid}/result",
                                             f"/api/candidate/applications/{aid}", "/api/candidate/applications",
                                             f"/api/postings/{p['id']}", "/api/postings")]
    for r in responses:
        assert r.status_code == 200
        assert CANARY not in r.text
        assert not (_keys(r.json()) & FORBIDDEN_KEYS), (r.url, _keys(r.json()) & FORBIDDEN_KEYS)
    fb = responses[0].json()["feedback"]
    assert set(fb["criteria"]) == {"expertise", "logic", "specificity", "delivery"}
    assert fb["notice"] == "이 피드백은 AI가 만든 참고 자료입니다"
    assert fb["strengths"] and fb["improvements"]


async def test_recruiter_report_and_list(client, make_client):
    p, cand, aid = await _setup(client, make_client)
    lst = (await client.get("/api/recruiter/applications")).json()
    assert lst["items"][0]["total_score"] is None and lst["items"][0]["eval_status"] is None  # ADM-02
    r = await client.get(f"/api/recruiter/applications/{aid}/report")
    assert r.status_code == 409 and r.json()["error"]["code"] == "REPORT_NOT_READY"
    await to_eval_done(aid)
    lst = (await client.get(f"/api/recruiter/applications?posting_id={p['id']}&eval_status=DONE")).json()
    assert lst["total"] == 1 and lst["items"][0]["total_score"] == "67.25"
    rep = (await client.get(f"/api/recruiter/applications/{aid}/report")).json()
    assert rep["total_score"] == "67.25" and rep["notice"] == "AI 결과는 참고 자료이며 최종 결정은 담당자가 합니다"
    assert rep["versions"]["rubric"] == "rubric-v1"
    detail = (await client.get(f"/api/recruiter/applications/{aid}")).json()
    assert detail["candidate"]["email"] and detail["can_decide"] is True
    async with session_scope() as db:
        assert await db.scalar(text("SELECT count(*) FROM audit_logs WHERE action='REPORT_VIEW'")) == 1


async def test_adm06_re_evaluate_only_on_hold(client, make_client):
    p, cand, aid = await _setup(client, make_client)
    r = await client.post(f"/api/recruiter/applications/{aid}/re-evaluate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ON_HOLD"
    await to_on_hold(aid)
    r = await client.post(f"/api/recruiter/applications/{aid}/re-evaluate")
    assert r.status_code == 202
    async with session_scope() as db:
        st = await db.scalar(text("SELECT eval_status FROM applications WHERE id=:a"), {"a": aid})
        jobs = (await db.execute(text("SELECT kind, payload FROM jobs"))).all()
    assert st == "PENDING" and jobs[-1][0] == "evaluate_application" and jobs[-1][1]["trigger"] == "RECRUITER"
    # candidate never sees "보류"; no re-test requested
    assert (await cand.get(f"/api/candidate/applications/{aid}")).json()["evaluation_label"] == "평가 진행 중"


async def test_adm07_notes_company_visible_author_editable(client, make_client):
    company, _ = await recruiter(client)
    p = await create_posting(client)
    cand = make_client()
    await candidate(cand)
    aid = (await apply(cand, p["id"])).json()["application_id"]
    colleague = make_client()
    await recruiter(colleague, company_id=company)
    n = (await client.post(f"/api/recruiter/applications/{aid}/notes", json={"body": "첫 메모"})).json()
    assert [x["body"] for x in (await colleague.get(f"/api/recruiter/applications/{aid}/notes")).json()] == ["첫 메모"]
    r = await colleague.patch(f"/api/recruiter/notes/{n['id']}", json={"body": "남의 메모 수정"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_AUTHOR"
    assert (await client.delete(f"/api/recruiter/notes/{n['id']}")).status_code == 204
    assert (await colleague.get(f"/api/recruiter/applications/{aid}/notes")).json() == []


async def test_ac03_other_company_cannot_touch_applications(client, make_client):
    p, cand, aid = await _setup(client, make_client)
    await to_eval_done(aid)
    n = (await client.post(f"/api/recruiter/applications/{aid}/notes", json={"body": "x"})).json()
    other = make_client()
    await recruiter(other)
    routes = [("GET", f"/api/recruiter/applications/{aid}", None),
              ("GET", f"/api/recruiter/applications/{aid}/resume", None),
              ("GET", f"/api/recruiter/applications/{aid}/resume-file", None),
              ("GET", f"/api/recruiter/applications/{aid}/report", None),
              ("GET", f"/api/recruiter/applications/{aid}/coding-result", None),
              ("POST", f"/api/recruiter/applications/{aid}/decision", {"decision": "PASS", "expected_version": 0}),
              ("GET", f"/api/recruiter/applications/{aid}/decision-history", None),
              ("POST", f"/api/recruiter/applications/{aid}/re-evaluate", None),
              ("GET", f"/api/recruiter/applications/{aid}/notes", None),
              ("POST", f"/api/recruiter/applications/{aid}/notes", {"body": "침입"}),
              ("PATCH", f"/api/recruiter/notes/{n['id']}", {"body": "침입"}),
              ("DELETE", f"/api/recruiter/notes/{n['id']}", None)]
    for method, url, body in routes:
        r = await other.request(method, url, json=body)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", (method, url, r.status_code)
    assert (await other.get("/api/recruiter/applications")).json()["total"] == 0
    assert (await other.get("/api/recruiter/dashboard")).json() == {"open_postings": 0, "awaiting_review": 0}
    # candidate role cannot call recruiter routes at all; another candidate cannot read this application
    assert (await cand.get(f"/api/recruiter/applications/{aid}")).status_code == 403
    stranger = make_client()
    await candidate(stranger)
    assert (await stranger.get(f"/api/candidate/applications/{aid}")).status_code == 404
    assert (await stranger.get(f"/api/candidate/applications/{aid}/result")).status_code == 404
    async with session_scope() as db:  # nothing changed by the rejected writes
        assert await db.scalar(text("SELECT decision_status FROM applications WHERE id=:a"), {"a": aid}) == "UNDECIDED"
        assert await db.scalar(text("SELECT count(*) FROM recruiter_notes")) == 1


async def test_stats_scoped_to_company(client, make_client):
    p, cand, aid = await _setup(client, make_client)
    await to_eval_done(aid)
    await client.post(f"/api/recruiter/applications/{aid}/decision", json={"decision": "PASS", "expected_version": 0})
    s = (await client.get("/api/recruiter/stats")).json()
    assert s["monthly_applicants"][0]["count"] == 1
    assert s["pass_rate_by_job_family"] == [{"job_family": "BACKEND", "decided": 1, "passed": 1}]
    other = make_client()
    await recruiter(other)
    assert (await other.get("/api/recruiter/stats")).json()["monthly_applicants"] == []
