"""USR-01~06, SEC-05, AC-01, AC-02 (account part). Real PostgreSQL via the dev compose."""
from datetime import timedelta

import pytest
from sqlalchemy import select, text, update

from app.core.db import session_scope
from app.models import Session, User
from tests.helpers import PASSWORD, login, new_company, new_recruiter, signup

pytestmark = pytest.mark.integration


async def test_ac01_signup_requires_fields_and_has_no_birth_or_gender(client):
    email = await signup(client)
    async with session_scope() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        assert user.role == "CANDIDATE" and user.password_hash.startswith("$argon2id$")  # SEC-05
        cols = (await db.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public'"))).scalars().all()
    forbidden = {"birth", "birthday", "birthdate", "dob", "gender", "sex", "photo", "picture", "age"}
    assert not [c for c in cols if forbidden & set(c.split("_"))]

    base = {"name": "이름", "email": "x1@mail.example", "password": PASSWORD, "password_confirm": PASSWORD,
            "phone": "010-1111-2222"}
    for missing in base:
        body = {k: v for k, v in base.items() if k != missing}
        assert (await client.post("/api/auth/signup", json=body)).status_code == 422, missing
    for phone in ("01012345678", "010-1234-567", "011-1234-5678"):
        assert (await client.post("/api/auth/signup", json={**base, "phone": phone})).status_code == 422
    for extra in ({"birth_date": "1990-01-01"}, {"gender": "F"}, {"role": "RECRUITER"}):
        r = await client.post("/api/auth/signup", json={**base, **extra})
        assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION"
    r = await client.post("/api/auth/signup", json={**base, "password_confirm": "different-1"})
    assert r.status_code == 422


async def test_signup_duplicate_email_generic_message(client):
    email = await signup(client)
    r = await client.post("/api/auth/signup", json={"name": "a", "email": email, "password": PASSWORD,
                                                    "password_confirm": PASSWORD, "phone": "010-2222-3333"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_UNAVAILABLE"


async def test_usr04_login_failures_are_indistinguishable(client, make_client):
    cand = await signup(client)
    company = await new_company()
    rec = await new_recruiter(company)
    cases = [
        {"role": "CANDIDATE", "email": "nobody@mail.example", "password": PASSWORD},  # unknown account
        {"role": "CANDIDATE", "email": cand, "password": "wrong-password"},           # wrong password
        {"role": "RECRUITER", "email": cand, "password": PASSWORD},                   # role mismatch
        {"role": "CANDIDATE", "email": rec, "password": PASSWORD},                    # recruiter as candidate
    ]
    bodies = set()
    for i, body in enumerate(cases):
        c = make_client()
        r = await c.post("/api/auth/login", json=body)
        assert r.status_code == 401
        err = r.json()["error"]
        bodies.add((err["code"], err["message"]))
    assert bodies == {("AUTH_FAILED", "이메일 또는 비밀번호가 올바르지 않습니다.")}


async def test_ac02_recruiter_logs_in_only_with_issued_account(client, make_client):
    company = await new_company()
    rec = await new_recruiter(company)
    await login(client, rec, role="RECRUITER")
    me = (await client.get("/api/auth/me")).json()
    assert me["user"]["role"] == "RECRUITER" and me["user"]["company_id"] == company
    # There is no way to create a recruiter through signup (extra fields rejected, role fixed).
    c2 = make_client()
    r = await c2.post("/api/auth/signup", json={"name": "a", "email": "new@corp.example", "password": PASSWORD,
                                                "password_confirm": PASSWORD, "phone": "010-2222-3333",
                                                "role": "RECRUITER"})
    assert r.status_code == 422


async def test_login_sets_httponly_cookie_and_session_hash_only(client):
    email = await signup(client)
    r = await client.post("/api/auth/login", json={"role": "CANDIDATE", "email": email, "password": PASSWORD})
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    token = r.cookies.get("aihire_sid")
    async with session_scope() as db:
        stored = (await db.execute(select(Session.token_hash))).scalars().all()
    assert len(stored) == 1 and token.encode() not in stored and len(stored[0]) == 32


async def test_csrf_and_origin_required_for_state_changes(client):
    email = await signup(client)
    await login(client, email)
    csrf = client.headers.pop("X-CSRF-Token")
    r = await client.post("/api/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_REJECTED"
    r = await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ORIGIN_REJECTED"
    assert (await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401  # session revoked server-side


async def test_usr06_idle_two_hours_expires_session(client):
    email = await signup(client)
    await login(client, email)
    assert (await client.get("/api/auth/me")).status_code == 200
    async with session_scope() as db:
        await db.execute(update(Session).values(expires_at=Session.last_seen_at - timedelta(seconds=1)))
    r = await client.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_login_throttle_applies_to_unknown_emails_too(make_client):
    for _ in range(5):
        r = await make_client().post("/api/auth/login",
                                     json={"role": "CANDIDATE", "email": "ghost@mail.example", "password": "x" * 8})
        assert r.status_code == 401
    r = await make_client().post("/api/auth/login",
                                 json={"role": "CANDIDATE", "email": "ghost@mail.example", "password": "x" * 8})
    assert r.status_code == 429


async def test_security_headers_present(client):
    r = await client.get("/api/healthz")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "microphone=(self)" in r.headers["permissions-policy"]
    assert r.headers["x-request-id"]
