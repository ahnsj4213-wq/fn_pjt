"""기업회원 가입 (SC-R01): mail code, company-domain auto approval, personal mail waits for an operator."""
import io

import pytest

from tests.helpers import PASSWORD, new_recruiter  # noqa: F401

BODY = {"company_name": "넥스트웨이브", "biz_no": "123-45-67890", "name": "박지훈", "code": "123456",
        "password": "password12", "password_confirm": "password12"}


@pytest.fixture
def fixed_code(monkeypatch):
    monkeypatch.setattr("app.auth.company_signup.secrets.randbelow", lambda n: 123456)


async def _send(c, email):
    r = await c.post("/api/auth/company-signup/code", json={"email": email})
    assert r.status_code == 202
    return r.json()


async def test_company_mail_is_approved_at_once_and_can_publish(make_client, fixed_code):
    c = make_client()
    assert (await _send(c, "hr@nextwave.example"))["free_mail"] is False
    r = await c.post("/api/auth/company-signup", json={**BODY, "email": "hr@nextwave.example"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "RECRUITER"
    assert r.json()["user"]["company_name"] == "넥스트웨이브"
    co = (await c.get("/api/recruiter/company")).json()
    assert co["verification"] == "APPROVED" and co["biz_no"] == "123-45-67890"
    r = await c.post("/api/auth/login", json={"role": "RECRUITER", "email": "hr@nextwave.example", "password": "password12"})
    assert r.status_code == 200


async def test_personal_mail_is_pending_and_cannot_publish(make_client, fixed_code):
    c = make_client()
    assert (await _send(c, "me@gmail.com"))["free_mail"] is True
    r = await c.post("/api/auth/company-signup", json={**BODY, "email": "me@gmail.com"})
    assert r.status_code == 201
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert (await c.get("/api/recruiter/company")).json()["verification"] == "PENDING"
    pdf = io.BytesIO(b"%PDF-1.4 evidence")
    r = await c.post("/api/recruiter/company/evidence", files={"file": ("재직증명서.pdf", pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()["evidence_name"] == "재직증명서.pdf"
    r = await c.post("/api/recruiter/company/evidence", files={"file": ("a.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert r.status_code == 415
    r = await c.post("/api/recruiter/postings", json={"title": "백엔드", "job_family": "DEV"})
    assert r.status_code == 201
    r = await c.post(f"/api/recruiter/postings/{r.json()['id']}/publish")
    assert r.status_code == 403 and r.json()["error"]["code"] == "COMPANY_NOT_VERIFIED"


async def test_wrong_code_is_counted_and_locks_after_five(make_client, fixed_code):
    c = make_client()
    await _send(c, "hr@nextwave.example")
    for _ in range(5):
        r = await c.post("/api/auth/company-signup", json={**BODY, "code": "000000", "email": "hr@nextwave.example"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "SIGNUP_CODE_INVALID"
    r = await c.post("/api/auth/company-signup", json={**BODY, "email": "hr@nextwave.example"})  # the right code, too late
    assert r.status_code == 400


async def test_validation_and_no_code_no_signup(make_client, fixed_code):
    c = make_client()
    r = await c.post("/api/auth/company-signup", json={**BODY, "email": "x@corp.example"})
    assert r.status_code == 400  # no code was sent
    await _send(c, "x@corp.example")
    assert (await c.post("/api/auth/company-signup", json={**BODY, "biz_no": "123", "email": "x@corp.example"})).status_code == 422
    assert (await c.post("/api/auth/company-signup", json={**BODY, "password_confirm": "other1234", "email": "x@corp.example"})).status_code == 422


async def test_code_is_used_once_and_email_taken(make_client, fixed_code):
    c = make_client()
    await _send(c, "hr@nextwave.example")
    assert (await c.post("/api/auth/company-signup", json={**BODY, "email": "hr@nextwave.example"})).status_code == 201
    assert (await c.post("/api/auth/company-signup", json={**BODY, "email": "hr@nextwave.example"})).status_code == 400
    await _send(c, "hr@nextwave.example")
    assert (await c.post("/api/auth/company-signup", json={**BODY, "email": "hr@nextwave.example"})).status_code == 409
