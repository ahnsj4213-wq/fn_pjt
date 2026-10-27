"""Operator CLI (OQ-19: no operator web UI in the MVP).

    python -m app.cli create-company --name 회사명 [--intro 소개]
    python -m app.cli create-recruiter --company-id <uuid> --email a@b.c --name 이름   (password prompted)
    python -m app.cli seed-demo
    python -m app.cli requeue-evaluation --application-id <uuid>

Every command writes an audit row with actor_role=OPERATOR_CLI. Commands never print answer text or resumes.
"""
import argparse
import asyncio
import getpass
import sys
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.passwords import hash_password
from app.core.db import session_scope
from app.models import Company, User


async def create_company(db: AsyncSession, *, name: str, intro: str | None = None) -> UUID:
    company = Company(name=name, intro=intro)
    db.add(company)
    await db.flush()
    await audit(db, action="COMPANY_CREATE", target_type="company", target_id=company.id, actor_id=None,
                actor_role="OPERATOR_CLI", company_id=company.id)
    return company.id


async def create_recruiter(db: AsyncSession, *, company_id: str | UUID, email: str, name: str, password: str) -> UUID:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    user = User(role="RECRUITER", company_id=UUID(str(company_id)), email=email.strip().lower(), name=name,
                password_hash=await hash_password(password))
    db.add(user)
    await db.flush()
    await audit(db, action="RECRUITER_ISSUE", target_type="user", target_id=user.id, actor_id=None,
                actor_role="OPERATOR_CLI", company_id=user.company_id)
    return user.id


async def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-company")
    c.add_argument("--name", required=True)
    c.add_argument("--intro")
    r = sub.add_parser("create-recruiter")
    r.add_argument("--company-id", required=True)
    r.add_argument("--email", required=True)
    r.add_argument("--name", required=True)
    args = p.parse_args(argv)

    async with session_scope() as db:
        if args.cmd == "create-company":
            print(await create_company(db, name=args.name, intro=args.intro))
        elif args.cmd == "create-recruiter":
            pw = getpass.getpass("initial password: ")
            print(await create_recruiter(db, company_id=args.company_id, email=args.email, name=args.name, password=pw))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
