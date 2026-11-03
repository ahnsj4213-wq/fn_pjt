"""Shared test actions. Operator-only setup (companies, recruiter accounts) goes through app.cli functions,
the same path the operator CLI uses (USR-03)."""
import itertools

import httpx

from app.cli import create_company, create_recruiter
from app.core.db import session_scope

PASSWORD = "Str0ng-pass!"
_seq = itertools.count(1)


async def new_company(name: str = "회사") -> str:
    async with session_scope() as db:
        return str(await create_company(db, name=f"{name}{next(_seq)}", intro="사내 플랫폼과 결제 시스템을 만드는 회사입니다."))


async def new_recruiter(company_id: str, email: str | None = None) -> str:
    email = email or f"rec{next(_seq)}@corp.example"
    async with session_scope() as db:
        await create_recruiter(db, company_id=company_id, email=email, name="담당자", password=PASSWORD)
    return email


async def signup(client: httpx.AsyncClient, email: str | None = None, **over) -> str:
    email = email or f"cand{next(_seq)}@mail.example"
    body = {"name": "김지원", "email": email, "password": PASSWORD, "password_confirm": PASSWORD,
            "phone": "010-1234-5678", "birth_date": "1998-03-14", "gender": "F", "terms_agreed": True, **over}
    r = await client.post("/api/auth/signup", json=body)
    assert r.status_code == 201, r.text
    return email


async def login(client: httpx.AsyncClient, email: str, role: str = "CANDIDATE", password: str = PASSWORD) -> str:
    r = await client.post("/api/auth/login", json={"role": role, "email": email, "password": password})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    return csrf


async def candidate(client: httpx.AsyncClient) -> str:
    email = await signup(client)
    await login(client, email)
    return email


async def recruiter(client: httpx.AsyncClient, company_id: str | None = None) -> tuple[str, str]:
    company_id = company_id or await new_company()
    email = await new_recruiter(company_id)
    await login(client, email, role="RECRUITER")
    return company_id, email


async def operator(client: httpx.AsyncClient) -> str:
    from app.auth.passwords import hash_password
    from app.models import User

    email = f"op{next(_seq)}@ops.example"
    async with session_scope() as db:
        db.add(User(role="OPERATOR", email=email, name="운영자", password_hash=await hash_password(PASSWORD)))
    await login(client, email, role="RECRUITER")  # operators use the 기업회원 tab
    return email
