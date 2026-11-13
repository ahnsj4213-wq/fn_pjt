"""Test postings from companies' public tech blogs (ADR-0029): python -m app.postings.tech_blog --raw-dir <dir with kurly.jsonl ...>

The raw blog posts come from tech_blog_rag_starter (PR #4) and stay outside the repository (they are the companies' text). This
loader checks every post really comes from its company's blog, stores each post as company_knowledge chunks (origin TECH_BLOG, with
its URL and date), creates the companies with one disabled recruiter (a posting needs an author), and the virtual postings of
data/tech_blog_postings.json, each linked to the posts it was written from. Then it embeds the new chunks (a timeout is retried;
what is still not embedded is reported as embedding_pending, and running the loader again finishes it).

Nothing here is a real job opening: is_virtual is set, the company intro says so, the screens say so, and there is no deadline.
Idempotent: a post or posting that exists is not added again; a posting nobody applied to is rewritten from the file.
"""
import argparse
import asyncio
import json
import secrets
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select

from app.ai.client import AIUnavailable, get_ai_client
from app.core.config import get_settings
from app.core.db import session_scope, sessionmaker
from app.core.timeutil import utcnow
from app.models import Application, Company, CompanyKnowledge, JobPosting, User
from app.postings.schemas import PostingIn
from app.postings.service import create_posting
from app.questions.knowledge import embed_pending, split_document

DATA_FILE = Path(__file__).parent / "data" / "tech_blog_postings.json"
NS = uuid.UUID("2c8f4e9a-1b7d-4c3e-8f60-7a5d9e2b4c18")
POSTING_FIELDS = ("title", "job_family", "experience_level", "duties", "requirements", "preferred")
EMPLOYMENT = "테스트용 가상 공고(채용 아님)"
EMBED_ATTEMPTS, EMBED_RETRY_SEC = 5, 20  # the CPU embedding server times out now and then under a 1,500-chunk load


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NS, ":".join(parts))


def doc_id(url: str) -> uuid.UUID:
    return _id("doc", url)


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or DATA_FILE).read_text(encoding="utf-8"))


def read_posts(raw_dir: Path, company: dict) -> list[dict]:
    """The company's collected posts. A post of another blog, a synthetic one or a duplicate URL stops the load."""
    posts, seen = [], set()
    for n, line in enumerate((raw_dir / f"{company['key']}.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        d = json.loads(line)
        where = f"{company['key']}.jsonl:{n}"
        if d.get("company_id") != company["key"]:
            raise ValueError(f"{where}: company_id {d.get('company_id')}")
        if urlparse(d["source_url"]).hostname not in company["hosts"] or not d["source_url"].startswith("https://"):
            raise ValueError(f"{where}: {d['source_url']} is not on {company['hosts']}")
        if d.get("is_synthetic") is not False or d.get("document_type") != "tech_blog":
            raise ValueError(f"{where}: not a collected tech blog post")
        if d["source_url"] in seen:
            raise ValueError(f"{where}: duplicate {d['source_url']}")
        seen.add(d["source_url"])
        posts.append(d)
    return posts


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value if "T" in value else f"{value}T00:00:00+09:00")  # a date only: midnight KST, as the blog shows it


async def ensure_company(db, c: dict) -> tuple[uuid.UUID, uuid.UUID]:
    company_id, user_id = _id("company", c["key"]), _id("user", c["key"])
    company = await db.get(Company, company_id)
    if company is None:
        db.add(Company(id=company_id, name=c["name"], intro=c["intro"], verification="APPROVED"))
        await db.flush()
        db.add(User(id=user_id, role="RECRUITER", company_id=company_id, email=f"techblog-{c['key']}@example.invalid",
                    password_hash=f"!disabled:{secrets.token_hex(16)}", name="테스트 공고 관리", status="DISABLED"))
        await db.flush()
    else:
        company.name, company.intro = c["name"], c["intro"]
    return company_id, user_id


async def load_posts(db, company_id: uuid.UUID, posts: list[dict]) -> dict:
    added_docs = added_chunks = 0
    for d in posts:
        did = doc_id(d["source_url"])
        if await db.scalar(select(CompanyKnowledge.id).where(CompanyKnowledge.doc_id == did).limit(1)) is not None:
            continue
        for n, chunk in enumerate(split_document(d["content"]), start=1):
            db.add(CompanyKnowledge(company_id=company_id, posting_id=None, doc_id=did, chunk_no=n, doc_type="TECH", title=d["title"],
                                    text=chunk, origin="TECH_BLOG", source_url=d["source_url"], published_at=_published(d.get("published_at"))))
            added_chunks += 1
        added_docs += 1
    await db.flush()
    return {"docs_added": added_docs, "chunks_added": added_chunks}


async def ensure_posting(db, company_id: uuid.UUID, user_id: uuid.UUID, row: dict, known_urls: set[str]) -> tuple[uuid.UUID, str]:
    missing = [u for u in row["sources"] if u not in known_urls]
    if missing:
        raise ValueError(f"{row['key']}: sources not among {row['company']}'s collected posts: {missing}")
    sources = [doc_id(u) for u in row["sources"]]
    body = PostingIn(**{f: row[f] for f in POSTING_FIELDS}, employment_type=EMPLOYMENT,
                     coding_enabled=row.get("coding_enabled", False), coding_time_limit_min=row.get("coding_time_limit_min"))
    posting = await db.scalar(select(JobPosting).where(JobPosting.company_id == company_id, JobPosting.is_virtual,
                                                       JobPosting.title == row["title"], JobPosting.deleted_at.is_(None)))
    if posting is None:
        out = await create_posting(db, company_id, user_id, body)
        posting = await db.get(JobPosting, out.id)
        now = utcnow()
        # Published here, not by service.publish: that one demands a closing date, and a test posting must not invent one.
        posting.is_virtual, posting.source_doc_ids = True, sources
        posting.status, posting.published_at, posting.updated_at = "OPEN", now, now
        await db.flush()
        return posting.id, "created"
    if await db.scalar(select(func.count()).select_from(Application).where(Application.posting_id == posting.id)):
        return posting.id, "kept (has applications)"
    changed = any(getattr(posting, f) != row[f] for f in POSTING_FIELDS) or list(posting.source_doc_ids) != sources
    if changed:
        for f in POSTING_FIELDS:
            setattr(posting, f, row[f])
        posting.source_doc_ids, posting.row_version, posting.updated_at = sources, posting.row_version + 1, utcnow()
    return posting.id, "updated" if changed else "unchanged"


async def load(raw_dir: Path, config: dict | None = None) -> dict:
    config = config or load_config()
    report: dict = {"companies": {}, "postings": {}}
    async with session_scope() as db:
        for c in config["companies"]:
            posts = read_posts(raw_dir, c)
            company_id, user_id = await ensure_company(db, c)
            report["companies"][c["key"]] = {"company_id": str(company_id), "posts": len(posts), **await load_posts(db, company_id, posts)}
            known = {p["source_url"] for p in posts}
            for row in (r for r in config["postings"] if r["company"] == c["key"]):
                pid, state = await ensure_posting(db, company_id, user_id, row, known)
                report["postings"][row["key"]] = {"id": str(pid), "state": state}
    # Each batch commits, so a timeout of the embedding server loses nothing: try again from where it stopped.
    report["embedded_now"], report["embedding_pending"] = 0, None
    for attempt in range(EMBED_ATTEMPTS):
        try:
            async with sessionmaker()() as db:
                report["embedded_now"] += (await embed_pending(db, get_ai_client(), get_settings().embed_model))["embedded"]
            report["embedding_pending"] = 0
            break
        except AIUnavailable:
            if attempt + 1 < EMBED_ATTEMPTS:
                await asyncio.sleep(EMBED_RETRY_SEC)
    if report["embedding_pending"] is None:
        async with session_scope() as db:
            report["embedding_pending"] = await db.scalar(select(func.count()).select_from(CompanyKnowledge).where(
                CompanyKnowledge.origin == "TECH_BLOG", CompanyKnowledge.embedding.is_(None)))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m app.postings.tech_blog")
    ap.add_argument("--raw-dir", required=True, help="folder with kurly.jsonl, toss.jsonl, kakao.jsonl (tech_blog_rag_starter/data/raw)")
    print(json.dumps(asyncio.run(load(Path(ap.parse_args().raw_dir))), ensure_ascii=False, indent=1))
    sys.exit(0)
