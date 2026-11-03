"""Operator CLI (OQ-19: no operator web UI in the MVP).

    python -m app.cli create-company --name 회사명 [--intro 소개]
    python -m app.cli create-recruiter --company-id <uuid> --email a@b.c --name 이름   (password prompted)
    python -m app.cli seed-demo
    python -m app.cli requeue-evaluation --application-id <uuid>
    python -m app.cli index-question-examples   (RAG corpus: load + embed now, instead of waiting for the worker)

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


async def _purge(dry_run: bool) -> int:
    from app.core.db import sessionmaker
    from app.retention.service import run_file_gc, run_retention

    async with sessionmaker()() as db:
        counts = await run_retention(db, dry_run=dry_run)
        print({"dry_run": dry_run, **counts.as_detail()})
        if not dry_run:
            await audit(db, action="DATA_PURGE", target_type="retention", target_id="cli", actor_id=None,
                        actor_role="OPERATOR_CLI", **counts.as_detail())
            await db.commit()
            print({"files_removed": await run_file_gc(db)})
    return 0


async def _index_examples() -> int:
    from app.ai.client import get_ai_client
    from app.core.config import get_settings
    from app.core.db import sessionmaker
    from app.questions import rag

    async with sessionmaker()() as db:
        print(await rag.sync_examples(db, get_ai_client(), get_settings().embed_model))
    return 0


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
    o = sub.add_parser("create-operator", help="issue an operator account (SC-O01); asks for the password")
    o.add_argument("--email", required=True)
    o.add_argument("--name", required=True)
    a = sub.add_parser("approve-company", help="approve a PENDING company signup (until the operator screen SC-O01)")
    a.add_argument("--company-id", required=True)
    g = sub.add_parser("purge", help="retention purge now (SEC-07); --dry-run only counts")
    g.add_argument("--dry-run", action="store_true")
    sub.add_parser("index-question-examples", help="load and embed the RAG example questions now")
    args = p.parse_args(argv)

    if args.cmd == "purge":
        return await _purge(args.dry_run)
    if args.cmd == "index-question-examples":
        return await _index_examples()

    async with session_scope() as db:
        if args.cmd == "create-company":
            print(await create_company(db, name=args.name, intro=args.intro))
        elif args.cmd == "approve-company":
            from app.auth.company_signup import approve_company

            await approve_company(db, UUID(args.company_id))
            await audit(db, action="COMPANY_APPROVE", target_type="company", target_id=args.company_id, actor_id=None,
                        actor_role="OPERATOR_CLI", company_id=UUID(args.company_id))
        elif args.cmd == "create-operator":
            pw = getpass.getpass("initial password: ")
            if len(pw) < 8:
                raise ValueError("password must be at least 8 characters")
            user = User(role="OPERATOR", email=args.email.strip().lower(), name=args.name, password_hash=await hash_password(pw))
            db.add(user)
            await db.flush()
            await audit(db, action="OPERATOR_ISSUE", target_type="user", target_id=user.id, actor_id=None, actor_role="OPERATOR_CLI")
            print(user.id)
        elif args.cmd == "create-recruiter":
            pw = getpass.getpass("initial password: ")
            print(await create_recruiter(db, company_id=args.company_id, email=args.email, name=args.name, password=pw))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
