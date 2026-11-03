"""운영자 · 기업 인증 (SC-O01): list, evidence, approve, reject, and who may do it."""
import io

import pytest

from tests.helpers import candidate, operator, recruiter

BODY = {"company_name": "개인메일기업", "biz_no": "123-45-67890", "name": "박지훈", "code": "123456",
        "password": "password12", "password_confirm": "password12"}


@pytest.fixture
def fixed_code(monkeypatch):
    monkeypatch.setattr("app.auth.company_signup.secrets.randbelow", lambda n: 123456)


async def _pending_company(make_client, email="me@gmail.com"):
    c = make_client()
    await c.post("/api/auth/company-signup/code", json={"email": email})
    r = await c.post("/api/auth/company-signup", json={**BODY, "email": email})
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    await c.post("/api/recruiter/company/evidence", files={"file": ("증빙.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")})
    return c, r.json()["user"]["company_id"]


async def test_only_operators_can_use_it(client, make_client):
    r = await client.get("/api/operator/companies")
    assert r.status_code == 401
    await candidate(client)
    assert (await client.get("/api/operator/companies")).status_code == 403
    rc = make_client()
    await recruiter(rc)
    assert (await rc.get("/api/operator/companies")).status_code == 403
    assert (await rc.post("/api/operator/companies/00000000-0000-0000-0000-000000000000/approve")).status_code == 403


async def test_list_puts_pending_first_and_counts(client, make_client, fixed_code):
    await _pending_company(make_client)
    await operator(client)
    data = (await client.get("/api/operator/companies")).json()
    assert data["counts"]["pending"] >= 1
    first = data["items"][0]
    assert first["status"] == "PENDING" and first["method"] == "DOCUMENT" and first["evidence_name"] == "증빙.pdf"
    assert first["email"] == "me@gmail.com" and first["biz_no"] == "123-45-67890"


async def test_evidence_is_served_to_operator_only(client, make_client, fixed_code):
    rc, company_id = await _pending_company(make_client)
    assert (await rc.get(f"/api/operator/companies/{company_id}/evidence")).status_code == 403
    await operator(client)
    r = await client.get(f"/api/operator/companies/{company_id}/evidence")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"


async def test_approve_lets_the_company_publish(client, make_client, fixed_code):
    rc, company_id = await _pending_company(make_client)
    await operator(client)
    assert (await client.post(f"/api/operator/companies/{company_id}/approve")).status_code == 200
    assert (await rc.get("/api/recruiter/company")).json()["verification"] == "APPROVED"
    assert (await client.post(f"/api/operator/companies/{company_id}/approve")).status_code == 409  # already done


async def test_reject_needs_a_reason_and_the_company_can_apply_again(client, make_client, fixed_code):
    rc, company_id = await _pending_company(make_client)
    await operator(client)
    assert (await client.post(f"/api/operator/companies/{company_id}/reject", json={"reason": " "})).status_code == 422
    assert (await client.post(f"/api/operator/companies/{company_id}/reject", json={"reason": "서류가 흐립니다"})).status_code == 200
    co = (await rc.get("/api/recruiter/company")).json()
    assert co["verification"] == "REJECTED" and co["reject_reason"] == "서류가 흐립니다"
    r = await rc.post("/api/recruiter/company/evidence", files={"file": ("새증빙.pdf", io.BytesIO(b"%PDF-1.4 y"), "application/pdf")})
    assert r.status_code == 200 and r.json()["verification"] == "PENDING" and r.json()["reject_reason"] is None


async def _open_posting(client):
    from datetime import date, timedelta
    r = await client.post("/api/recruiter/postings", json={"title": "백엔드", "job_family": "DEV", "headcount": 1,
                          "deadline_date": str(date.today() + timedelta(days=20)), "coding_enabled": False})
    pid = r.json()["id"]
    r = await client.post(f"/api/recruiter/postings/{pid}/publish")
    assert r.status_code == 200, r.text
    return pid


async def test_hide_removes_the_posting_from_the_main_page_and_blocks_new_applications(client, make_client):
    rc = make_client()
    await recruiter(rc)
    pid = await _open_posting(rc)
    anon = make_client()
    assert pid in [p["id"] for p in (await anon.get("/api/postings")).json()]
    await operator(client)
    assert pid in [p["id"] for p in (await client.get("/api/operator/postings")).json()]
    assert (await client.post(f"/api/operator/postings/{pid}/hide")).status_code == 200
    assert pid not in [p["id"] for p in (await anon.get("/api/postings")).json()]
    assert (await anon.get(f"/api/postings/{pid}")).status_code == 404
    assert [p for p in (await rc.get("/api/recruiter/postings")).json() if p["id"] == pid][0]["hidden"] is True
    assert (await client.post(f"/api/operator/postings/{pid}/show")).status_code == 200
    assert pid in [p["id"] for p in (await anon.get("/api/postings")).json()]


async def test_suspension_hides_every_posting_blocks_publishing_and_keeps_manual_hides(client, make_client):
    rc = make_client()
    company_id, _ = await recruiter(rc)
    a, b = await _open_posting(rc), await _open_posting(rc)
    anon = make_client()
    await operator(client)
    await client.post(f"/api/operator/postings/{b}/hide")  # hidden by hand before the suspension
    assert (await client.post(f"/api/operator/companies/{company_id}/suspend")).status_code == 200
    assert not {a, b} & {p["id"] for p in (await anon.get("/api/postings")).json()}
    assert (await rc.get("/api/recruiter/company")).json()["suspended"] is True
    r = await rc.post("/api/recruiter/postings", json={"title": "새 공고", "job_family": "DEV"})
    r = await rc.post(f"/api/recruiter/postings/{r.json()['id']}/publish")
    assert r.status_code == 403 and r.json()["error"]["code"] == "COMPANY_SUSPENDED"
    assert (await client.post(f"/api/operator/postings/{a}/show")).status_code == 409  # lift the suspension first
    assert (await client.post(f"/api/operator/companies/{company_id}/unsuspend")).status_code == 200
    shown = {p["id"] for p in (await anon.get("/api/postings")).json()}
    assert a in shown and b not in shown  # only what the suspension hid comes back


async def test_posting_moderation_is_operator_only(client, make_client):
    rc = make_client()
    await recruiter(rc)
    pid = await _open_posting(rc)
    assert (await rc.get("/api/operator/postings")).status_code == 403
    assert (await rc.post(f"/api/operator/postings/{pid}/hide")).status_code == 403


async def test_company_issued_by_the_operator_is_labelled_so(client, make_client):
    rc = make_client()
    await recruiter(rc)  # created through the CLI path: no signup method, no evidence
    await operator(client)
    items = (await client.get("/api/operator/companies")).json()["items"]
    assert [i["method"] for i in items if i["status"] == "APPROVED"] == ["ISSUED"]
