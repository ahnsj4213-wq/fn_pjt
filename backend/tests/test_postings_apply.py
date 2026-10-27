"""JOB-01~07, ADM-05, RES-01~07, SEC-02 and AC-02/03/04/05/06/21 (non-AI parts)."""
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import session_scope
from tests.fixtures import RESUME, apply, consent, create_posting, image_only_pdf, posting_body, text_pdf
from tests.helpers import candidate, recruiter

pytestmark = pytest.mark.integration


async def test_job03_draft_publish_and_tabs(client):
    await recruiter(client)
    r = await client.post("/api/recruiter/postings", json={"title": "임시 공고", "coding_enabled": False})
    assert r.status_code == 201
    draft = r.json()
    assert draft["status"] == "DRAFT" and draft["evaluation_settings"]["weights"] == {
        "expertise": 35, "logic": 25, "specificity": 25, "delivery": 15}  # 6.4 defaults
    r = await client.post(f"/api/recruiter/postings/{draft['id']}/publish")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INCOMPLETE_POSTING"
    r = await client.put(f"/api/recruiter/postings/{draft['id']}", json={**posting_body(), "row_version": draft["row_version"]})
    assert r.status_code == 200
    stale = await client.put(f"/api/recruiter/postings/{draft['id']}", json={**posting_body(), "row_version": draft["row_version"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert (await client.post(f"/api/recruiter/postings/{draft['id']}/publish")).status_code == 200
    await create_posting(client, publish=False, title="다른 임시 공고")
    tabs = {s: [p["id"] for p in (await client.get(f"/api/recruiter/postings?status={s}")).json()]
            for s in ("OPEN", "DRAFT", "CLOSED")}
    assert draft["id"] in tabs["OPEN"] and len(tabs["DRAFT"]) == 1 and not tabs["CLOSED"]


async def test_coding_toggle_resets_weights_to_coding_defaults(client):
    await recruiter(client)
    p = await create_posting(client, publish=False)
    problems = (await client.get("/api/recruiter/coding-problems")).json()
    assert problems and all("HIDDEN" not in str(pr) for pr in problems)
    hidden_inputs = ["1 1\n7\n1 1\n"]
    assert not any(e["input"] in hidden_inputs for e in problems[0]["public_examples"])
    r = await client.put(f"/api/recruiter/postings/{p['id']}", json={
        **posting_body(coding_enabled=True, coding_problem_id=problems[0]["id"], coding_time_limit_min=60),
        "row_version": p["row_version"]})
    body = r.json()
    assert body["weights_reset"] is True
    assert body["evaluation_settings"]["weights"] == {"expertise": 30, "logic": 20, "specificity": 20,
                                                      "delivery": 10, "coding": 20}


async def test_ac21_weights_sum_and_coding_item_rules(client):
    await recruiter(client)
    p = await create_posting(client)
    url = f"/api/recruiter/postings/{p['id']}/evaluation-settings"
    for weights, code in (({"expertise": 35, "logic": 25, "specificity": 25, "delivery": 14}, "WEIGHT_SUM_NOT_100"),
                          ({"expertise": 35, "logic": 25, "specificity": 25, "delivery": 16}, "WEIGHT_SUM_NOT_100"),
                          ({"expertise": 30, "logic": 20, "specificity": 20, "delivery": 10, "coding": 20},
                           "WEIGHTS_CODING_MISMATCH")):
        r = await client.put(url, json={"weights": weights})
        assert r.status_code == 422 and r.json()["error"]["code"] == code
    r = await client.put(url, json={"weights": {"expertise": 40, "logic": 20, "specificity": 20, "delivery": 20},
                                    "custom_question": "우리 서비스의 결제 장애를 줄이려면 무엇부터 하겠습니까?"})
    assert r.status_code == 200 and r.json()["version"] == 2
    # DB CHECK is the second line of defence.
    async with session_scope() as db:
        with pytest.raises(DBAPIError):
            await db.execute(text(
                "INSERT INTO posting_eval_settings(posting_id, company_id, version, w_expertise, w_logic, w_specificity, "
                "w_delivery, rubric_version, created_by) SELECT id, company_id, 9, 40, 20, 20, 19, 'rubric-v1', created_by "
                "FROM job_postings WHERE id = :p"), {"p": p["id"]})
    # JOB-06: once the first interview starts the settings lock (simulated here; set by the start transaction).
    async with session_scope() as db:
        await db.execute(text("UPDATE job_postings SET settings_locked_at = now() WHERE id = :p"), {"p": p["id"]})
    r = await client.put(url, json={"weights": {"expertise": 35, "logic": 25, "specificity": 25, "delivery": 15}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SETTINGS_LOCKED"
    assert (await client.post(url + "/reset")).status_code == 409
    current = (await client.get(f"/api/recruiter/postings/{p['id']}")).json()
    r = await client.put(f"/api/recruiter/postings/{p['id']}", json={
        **posting_body(coding_enabled=True, coding_time_limit_min=30), "row_version": current["row_version"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SETTINGS_LOCKED"


async def test_ac02_recruiter_sees_posting_but_cannot_apply(client, make_client):
    await recruiter(client)
    p = await create_posting(client)
    assert (await client.get(f"/api/postings/{p['id']}")).status_code == 200
    r = await client.post(f"/api/candidate/postings/{p['id']}/applications", json={"consent_id": p["id"], "resume": RESUME})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ROLE_FORBIDDEN"
    assert (await client.get("/api/consent/current")).status_code == 403


async def test_ac03_other_company_posting_is_indistinguishable_from_missing(client, make_client):
    await recruiter(client)
    p = await create_posting(client)
    other = make_client()
    await recruiter(other)
    missing = await other.get("/api/recruiter/postings/00000000-0000-0000-0000-000000000000")
    for method, url in (("GET", f"/api/recruiter/postings/{p['id']}"),
                        ("PUT", f"/api/recruiter/postings/{p['id']}"),
                        ("POST", f"/api/recruiter/postings/{p['id']}/close"),
                        ("DELETE", f"/api/recruiter/postings/{p['id']}"),
                        ("GET", f"/api/recruiter/postings/{p['id']}/evaluation-settings")):
        r = await other.request(method, url, json=posting_body(row_version=1) if method == "PUT" else None)
        assert r.status_code == 404, (method, url)
        assert r.json()["error"]["code"] == missing.json()["error"]["code"] == "NOT_FOUND"
        assert r.json()["error"]["message"] == missing.json()["error"]["message"]
    assert all(x["id"] != p["id"] for x in (await other.get("/api/recruiter/postings")).json())
    assert (await client.get(f"/api/recruiter/postings/{p['id']}")).json()["status"] == "OPEN"  # unchanged


async def test_ac04_duplicate_application_rejected_even_concurrently(client, make_client):
    await recruiter(client)
    p = await create_posting(client)
    cand = make_client()
    await candidate(cand)
    c = await consent(cand, p["id"])
    body = {"consent_id": c["consent_id"], "resume": RESUME}
    results = await asyncio.gather(*[cand.post(f"/api/candidate/postings/{p['id']}/applications", json=body)
                                     for _ in range(8)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and codes.count(409) == 7
    assert all(r.json()["error"]["code"] == "ALREADY_APPLIED" for r in results if r.status_code == 409)
    posting = (await cand.get(f"/api/postings/{p['id']}")).json()
    assert posting["has_applied"] is True
    listing = (await client.get("/api/recruiter/postings?status=OPEN")).json()
    assert listing[0]["applicant_count"] == 1


async def test_ac05_required_consent_blocks_application_and_is_recorded(client, make_client):
    await recruiter(client)
    p = await create_posting(client)
    cand = make_client()
    await candidate(cand)
    declined = await consent(cand, p["id"], agree_all=False)
    assert declined["all_required_agreed"] is False
    r = await cand.post(f"/api/candidate/postings/{p['id']}/applications",
                        json={"consent_id": declined["consent_id"], "resume": RESUME})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONSENT_REQUIRED"
    ok = await consent(cand, p["id"])
    assert ok["version"] == "consent-v0-draft" and ok["agreed_at"]
    async with session_scope() as db:
        rows = (await db.execute(text("SELECT document_version, answers, agreed_at FROM consent_records "
                                      "ORDER BY agreed_at"))).all()
        assert len(rows) == 2 and rows[0][1]["purpose"] is False and rows[1][0] == "consent-v0-draft"
        with pytest.raises(DBAPIError):  # consent records are append-only
            await db.execute(text("UPDATE consent_records SET all_required_agreed = true"))


async def test_res06_minimum_resume_and_snapshot_is_frozen(client, make_client):
    await recruiter(client)
    p = await create_posting(client)
    cand = make_client()
    await candidate(cand)
    r = await apply(cand, p["id"], resume={"name": "이름만"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "RESUME_MIN_FIELDS"
    r = await apply(cand, p["id"])
    assert r.status_code == 201
    async with session_scope() as db:
        snap = (await db.execute(text("SELECT resume_snapshot_text FROM applications"))).scalar_one()
        assert "복합 인덱스" in snap and "김지원" not in snap  # name excluded from grounding text
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE applications SET resume_snapshot = '{}'::jsonb"))
    # RES-07: the registered resume was not overwritten implicitly
    assert (await cand.get("/api/candidate/resume")).json()["resume"] is None


async def test_job04_delete_only_without_applicants(client, make_client):
    await recruiter(client)
    empty = await create_posting(client)
    assert (await client.delete(f"/api/recruiter/postings/{empty['id']}")).status_code == 204
    used = await create_posting(client)
    cand = make_client()
    await candidate(cand)
    assert (await apply(cand, used["id"])).status_code == 201
    r = await client.delete(f"/api/recruiter/postings/{used['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "HAS_APPLICATIONS"
    r = await client.post(f"/api/recruiter/postings/{used['id']}/close")
    assert r.status_code == 200 and r.json()["status"] == "CLOSED"
    # 7.4: closed postings take no new applications; the READY attempt expires (no answers -> no evaluation)
    apps = (await cand.get("/api/candidate/applications")).json()
    assert apps[0]["attempt_status"] == "EXPIRED" and apps[0]["can_start"] is False


async def test_ac06_upload_rules(make_client):
    cand = make_client()
    await candidate(cand)
    url = "/api/candidate/resume-files"
    r = await cand.post(url, files={"file": ("cv.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "NOT_PDF"
    r = await cand.post(url, files={"file": ("fake.pdf", b"\x89PNG\r\n\x1a\n", "application/pdf")})
    assert r.status_code == 415
    big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024)
    r = await cand.post(url, files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
    r = await cand.post(url, files={"file": ("broken.pdf", b"%PDF-1.4\n%garbage without objects", "application/pdf")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "PDF_UNREADABLE"
    r = await cand.post(url, files={"file": ("scan.pdf", image_only_pdf(), "application/pdf")})
    assert r.status_code == 201 and r.json()["text_extractable"] is False and r.json()["manual_input_required"] is True
    good = text_pdf(["김지원 이력서", "경력: 커머스랩 백엔드 개발자 2020-2024", "주문 처리 API 응답 지연 개선",
                     "기술: Python, PostgreSQL"])
    r = await cand.post(url, files={"file": ("../../etc/cv.pdf", good, "application/pdf")})
    body = r.json()
    assert r.status_code == 201 and body["text_extractable"] is True and body["extraction_status"] == "PENDING"
    async with session_scope() as db:
        key, txt = (await db.execute(text("SELECT storage_key, extracted_text FROM resume_files "
                                          "WHERE text_extractable"))).one()
        jobs = (await db.execute(text("SELECT kind FROM jobs"))).scalars().all()
    assert ".." not in key and "/" not in key and "커머스랩" in txt and jobs == ["extract_resume"]
