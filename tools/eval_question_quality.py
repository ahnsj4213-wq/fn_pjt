"""Question quality of every virtual company with the real models, without writing anything: T2 through the composer's own code.

    docker compose exec -T api python - < ../tools/eval_question_quality.py

For each company: three JOB questions and one motivation question. Reports how many came back, how many name something concrete
from the document they cite (two or more of its content words), and how many different documents the JOB questions came from.
The numbers count shape only; whether a question is good is for a person to read, so the questions are printed too.
"""
import asyncio
import json
import statistics
import time
import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.ai.client import get_ai_client
from app.ai.textrules import content_tokens
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Company, CompanyKnowledge, JobPosting
from app.questions import compose as C
from app.questions import seed_knowledge


def concrete(question: str, source: str) -> bool:
    return len(content_tokens(question) & content_tokens(source)) >= 2


async def one(db, row: dict, ai, model: str) -> dict:
    company_id = seed_knowledge._id("company", row["key"])
    company = await db.get(Company, company_id)
    posting = await db.scalar(select(JobPosting).where(JobPosting.company_id == company_id, JobPosting.title == row["posting"]["title"]))
    c = C._Ctx(SimpleNamespace(id=uuid.uuid4()), posting, company, None, ai, model, db)
    out: dict = {"key": row["key"]}
    t = time.time()
    jobs = await C._generate(c, "JOB", 3)
    motive = await C._generate(c, "COMPANY", 1)
    out["seconds"] = round(time.time() - t, 1)
    docs = []
    n_concrete = 0
    for q in jobs:
        ref = q["source"]
        if ref["type"] == "KNOWLEDGE":
            chunk = await db.get(CompanyKnowledge, uuid.UUID(ref["id"]))
            docs.append(chunk.title)
            n_concrete += concrete(q["text"], f"{chunk.title} {chunk.text}")
        else:
            docs.append(ref["type"])
            n_concrete += concrete(q["text"], "\n".join(x or "" for x in (posting.title, posting.duties, posting.requirements, posting.preferred)))
    out |= {"job": len(jobs), "concrete": n_concrete, "distinct_docs": len(set(docs)), "motive": len(motive),
            "questions": [q["text"] for q in jobs], "docs": docs, "motivation": [q["text"] for q in motive]}
    return out


async def main() -> None:
    ai, model = get_ai_client(), get_settings().embed_model
    results = []
    for row in seed_knowledge.load_file():
        async with session_scope() as db:
            results.append(await one(db, row, ai, model))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    n = len(results)
    print(json.dumps({"companies": n, "job_questions_of_36": sum(r["job"] for r in results), "concrete": sum(r["concrete"] for r in results),
                      "distinct_docs_mean": round(statistics.mean(r["distinct_docs"] for r in results), 2),
                      "motivation_of_12": sum(r["motive"] for r in results), "seconds_median": statistics.median(r["seconds"] for r in results)}, ensure_ascii=False))


asyncio.run(main())
