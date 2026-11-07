"""Load the virtual companies (data/synthetic_companies.json, ADR-0020): python -m app.questions.seed_knowledge

The longer story in data/company_profiles/<key>.json (the same text as docs/synthetic-companies/*.pdf) is loaded as more documents.
Run by hand, never by a migration or the worker: a production database must not get these companies by accident. Everything in
the file is invented; each name ends in (가상) and each chunk has origin SYNTHETIC. Running it again adds what is missing and rewrites an invented posting nobody has applied to; nothing else that exists changes.
A company gets one disabled recruiter that cannot log in (a posting needs an author) and one open posting.
`--demo-login <key>` turns that one recruiter on with a new random password, printed once, to try a company's screens.
"""
import argparse
import asyncio
import json
import secrets
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, text

from app.ai.client import get_ai_client
from app.auth.passwords import hash_password
from app.core.config import get_settings
from app.core.db import session_scope, sessionmaker
from app.core.timeutil import utcnow
from app.models import Application, Company, CompanyKnowledge, JobPosting, User
from app.postings.schemas import PostingIn
from app.postings.service import create_posting, publish
from app.questions.knowledge import embed_pending, split_document

DATA_FILE = Path(__file__).parent / "data" / "synthetic_companies.json"
PROFILE_DIR = DATA_FILE.parent / "company_profiles"
NS = uuid.UUID("6f1d3a52-7c0e-4b7e-9a50-3b6a2d9e1c10")


def load_file(path: Path | None = None) -> list[dict]:
    return json.loads((path or DATA_FILE).read_text(encoding="utf-8"))


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NS, ":".join(parts))


def _pairs(rows: list, sep: str = ": ") -> str:
    return "\n".join(sep.join(r) for r in rows)


def profile_docs(row: dict) -> list[dict]:
    """The company's profile as documents. A project or technology detail keeps the title of the short document it extends."""
    c = json.loads((PROFILE_DIR / f"{row['key']}.json").read_text(encoding="utf-8"))
    projects = [d["title"] for d in row["docs"] if d["type"] == "PROJECT"]
    tech = [d["title"] for d in row["docs"] if d["type"] == "TECH"]
    docs = [
        {"id": "about", "type": "COMPANY", "title": "회사 소개", "text": "\n".join([c["tagline"], c["mission"], *c["about"], _pairs(c["history"])])},
        {"id": "business", "type": "COMPANY", "title": "서비스와 고객", "text": "\n".join([_pairs(c["services"]), c["customers"], c["model"], *c["focus"]])},
        {"id": "tech", "type": "TECH", "title": tech[0], "text": "\n".join([c["arch"], "\n".join(f"{a}: {t}. {w}" for a, t, w in c["stack"])])},
        {"id": "ops", "type": "TECH", "title": tech[1], "text": "\n".join([c["ops_note"], _pairs(c["ops"])])},
    ]
    for n, (title, d) in enumerate(zip(projects, c["projects"], strict=True)):
        metrics = "\n".join(f"{m}: 현재 {now}, 목표 {goal}" for m, now, goal in d["metrics"])
        docs.append({"id": f"project{n}", "type": "PROJECT", "title": title, "text": "\n".join([d["bg"], *d["steps"], metrics, *d["next"]])})
    return docs


async def _refresh_posting(db, posting: JobPosting, data: dict) -> None:
    if await db.scalar(select(Application.id).where(Application.posting_id == posting.id).limit(1)) is not None:
        return
    fields = ("duties", "requirements", "preferred", "experience_level", "employment_type")
    if any(getattr(posting, f) != data.get(f) for f in fields):
        for f in fields:
            setattr(posting, f, data.get(f))
        posting.row_version += 1
        posting.updated_at = utcnow()


async def seed(db, row: dict) -> uuid.UUID:
    """One company with its recruiter, posting and document chunks. Returns the posting id."""
    key = row["key"]
    company_id, user_id = _id("company", key), _id("user", key)
    if await db.get(Company, company_id) is None:
        db.add(Company(id=company_id, name=row["name"], intro=row["intro"], verification="APPROVED"))
        await db.flush()
        db.add(User(id=user_id, role="RECRUITER", company_id=company_id, email=f"synthetic-{key}@example.invalid",
                    password_hash=f"!disabled:{secrets.token_hex(16)}", name="가상 담당자", status="DISABLED"))
        await db.flush()
    posting_id = await db.scalar(select(JobPosting.id).where(JobPosting.company_id == company_id, JobPosting.title == row["posting"]["title"]))
    if posting_id is None:
        out = await create_posting(db, company_id, user_id, PostingIn(
            **row["posting"], headcount=1, location="서울(가상)", deadline_date=(utcnow() + timedelta(days=365)).date()))
        posting_id = out.id
        await publish(db, await db.get(JobPosting, posting_id))
    else:
        await _refresh_posting(db, await db.get(JobPosting, posting_id), row["posting"])
    for d in row["docs"]:
        doc_id = _id("doc", key, d["title"])
        if await db.scalar(select(CompanyKnowledge.id).where(CompanyKnowledge.doc_id == doc_id)) is None:
            db.add(CompanyKnowledge(company_id=company_id, posting_id=posting_id if d.get("posting_scope") else None, doc_id=doc_id, chunk_no=1,
                                    doc_type=d["type"], title=d["title"], text=d["text"], origin="SYNTHETIC"))
    for d in profile_docs(row):
        doc_id = _id("profile", key, d["id"])
        if await db.scalar(select(CompanyKnowledge.id).where(CompanyKnowledge.doc_id == doc_id)) is None:
            for n, chunk in enumerate(split_document(d["text"]), start=1):
                db.add(CompanyKnowledge(company_id=company_id, doc_id=doc_id, chunk_no=n, doc_type=d["type"], title=d["title"], text=chunk, origin="SYNTHETIC"))
    await db.flush()
    return posting_id


async def demo_login(db, key: str) -> tuple[str, str]:
    """Only the invented recruiter of a virtual company can be turned on here. Returns its email and a new password."""
    user = await db.get(User, _id("user", key))
    if user is None:
        raise SystemExit(f"{key}: not loaded yet")
    password = secrets.token_urlsafe(12)
    user.password_hash, user.status = await hash_password(password), "ACTIVE"
    await db.flush()
    return user.email, password


async def main() -> None:
    rows = load_file()
    async with session_scope() as db:
        for row in rows:
            await seed(db, row)
    async with sessionmaker()() as db:
        counts = await embed_pending(db, get_ai_client(), get_settings().embed_model)
    async with session_scope() as db:
        total = await db.scalar(text("SELECT count(*) FROM company_knowledge WHERE origin = 'SYNTHETIC' AND embedding IS NOT NULL"))
    print(json.dumps({"companies": len(rows), "embedded_now": counts["embedded"], "chunks_indexed": total}))


async def _demo(key: str) -> None:
    async with session_scope() as db:
        email, password = await demo_login(db, key)
    print(json.dumps({"email": email, "password": password}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo-login", metavar="KEY")
    args = ap.parse_args()
    asyncio.run(_demo(args.demo_login) if args.demo_login else main())
