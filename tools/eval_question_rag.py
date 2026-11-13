"""Interview-question quality with the real models, by company-document search (ADR-0030, PR #6 review). Read-only: the
composer writes nothing (the question bank is retired, ADR-0031).

    docker compose -p aihire -f deploy/docker-compose.yml run --rm --no-deps -v "<repo>/backend:/srv/backend"         -v "<repo>/tools:/srv/tools" api python /srv/tools/eval_question_rag.py [repetitions]

Per virtual posting and configuration: 3 JOB slots + 1 motivation slot through the composer's own quality loop and fallback
(compose._fill_generated_kind, ShortReads as in production). Configurations: legacy (vector lists re-ranked by shared words),
hybrid_old (weighted RRF, every first-of-title chunk moved up), hybrid_fixed (the current code). One JSON line per run ("ROW ...").
Counts to compare: questions from the documents (source AI) vs the posting template fallback, the quality checks' reject codes
(QUOTE_MISMATCH, NOT_CONCRETE, SAME_DOCUMENT, ...), model requests and time. The model is not deterministic: compare repetitions."""
import asyncio, json, sys, time, uuid
from types import SimpleNamespace
from sqlalchemy import select, text
from app.ai.client import get_ai_client
from app.ai.textrules import content_tokens
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Company, CompanyKnowledge, JobPosting
from app.questions import compose as C, knowledge
from app.questions.dbio import ShortReads

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 1
real_hybrid, real_diverse = knowledge.hybrid, knowledge.diverse

async def legacy_hybrid(db, ai, model, *, company_id, posting_id, queries, k, floor, kind="", exclude_docs=(), shared=False, vectors=None,
                        use_vector=True, company_wide_only=False):
    none = {"vector": False, "bm25": False}
    if not use_vector:
        return [], none
    if vectors is None:
        vectors = await knowledge.embed_queries(ai, queries, kind)
    if not vectors or len(vectors) != len(queries):
        return [], none
    found, score = {}, {}
    for vec in vectors:
        kept = await knowledge._vector_list(db, vec, model, company_id=company_id, posting_id=posting_id, n=k * 3, floor=floor,
                                            exclude_docs=exclude_docs, shared=shared, company_wide_only=company_wide_only)
        for rank, ch in enumerate(kept):
            found.setdefault(ch.id, ch); score[ch.id] = score.get(ch.id, 0.0) + 1.0 / (60 + rank)
    wanted = content_tokens("\n".join(queries))
    for rank, ch in enumerate(sorted(found.values(), key=lambda ch: -len(wanted & content_tokens(f"{ch.title} {ch.text}")))):
        score[ch.id] += 1.0 / (60 + rank)
    return sorted(found.values(), key=lambda ch: -score[ch.id]), {"vector": True, "bm25": False}

def old_diverse(ranked):
    first, rest, titles = [], [], set()
    for ch in ranked:
        (rest if ch.title in titles else first).append(ch); titles.add(ch.title)
    return first + rest

CONFIGS = {"legacy": (legacy_hybrid, old_diverse), "hybrid_old": (real_hybrid, old_diverse), "hybrid_fixed": (real_hybrid, real_diverse)}


async def one(posting_id, cfg):
    knowledge.hybrid, knowledge.diverse = CONFIGS[cfg]
    ai, model = get_ai_client(), get_settings().embed_model
    out = {"cfg": cfg}
    async with session_scope() as db:
        posting = await db.get(JobPosting, posting_id); company = await db.get(Company, posting.company_id)
        db.expunge_all()
    # as the composer runs (steps.py, live.py): one short read transaction per statement, none held across a model call
    c = C._Ctx(SimpleNamespace(id=uuid.uuid4(), resume_snapshot=None), posting, company, None, ai, model, ShortReads())
    t = time.time()
    await C._fill_generated_kind(c.db, c, "JOB", [(4, "JOB_SKILL"), (5, "JOB_SKILL"), (6, "JOB_SKILL")], model)
    await C._fill_generated_kind(c.db, c, "COMPANY", [(3, "MOTIVATION")], model)
    out["seconds"] = round(time.time() - t, 1)
    qs = []
    async with session_scope() as db:
        for p in c.picked:
            src = (p.grounding or {}).get("sources") or [{}]
            s0 = src[0] if src else {}
            title, concrete, shared = None, None, None
            if s0.get("type") == "KNOWLEDGE":
                ch = await db.get(CompanyKnowledge, uuid.UUID(s0["id"]))
                title, shared = ch.title, ch.company_id != company.id
                concrete = len(content_tokens(p.text) & content_tokens(f"{ch.title} {ch.text}")) >= 2
            qs.append({"slot": p.slot, "source": p.source, "ref": s0.get("type"), "shared": shared, "doc": title, "concrete": concrete,
                       "fallback": (p.grounding or {}).get("fallback"), "text": p.text})
    out.update({"posting": posting.title, "company": company.name, "rejects": c.rejects, "requests": c.requests, "questions": qs})
    return out

async def main():
    async with session_scope() as db:
        ids = [i for (i,) in (await db.execute(text("SELECT id FROM job_postings WHERE is_virtual ORDER BY company_id, title"))).all()]
    for rep in range(REPS):
        for pid in ids:
            for cfg in CONFIGS:
                try:
                    r = await one(pid, cfg)
                except Exception as e:  # report and go on
                    r = {"cfg": cfg, "posting_id": str(pid), "error": f"{type(e).__name__}: {e}"[:300]}
                r["rep"] = rep
                print("ROW " + json.dumps(r, ensure_ascii=False), flush=True)

asyncio.run(main())
