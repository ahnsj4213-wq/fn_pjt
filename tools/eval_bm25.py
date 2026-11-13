"""Search accuracy and latency of the company-document search, before and after BM25 (ADR-0030). Read-only: no row is written.

Run against the stack's real database and embedding model, with the code of this working tree:

    docker compose -p aihire -f deploy/docker-compose.yml run --rm --no-deps \
        -v "<repo>/backend:/srv/backend" -v "<repo>/tools:/srv/tools" api python /srv/tools/eval_bm25.py [--rounds 3]

Configurations: `legacy` (the code before ADR-0030: vector lists re-ranked by shared words) and the weighted RRF of
knowledge.search_many at vector:BM25 = 100:0, 70:30, 60:40, 50:50, 40:60, 0:100, each with the BM25 coverage floors of COVERAGES. Every query is one search_many call
as the composer makes it (one query, k=5, the COMPANY floor), scoped to the company and one of its postings.

Metrics per document (a chunk counts for the post it belongs to), top 5:
  Hit@5     a relevant post is among the results            MRR@5   1 / rank of the first relevant post
  Recall@5  relevant posts found / relevant posts           근거 정확도  results that belong to a relevant post / results
  p50/p95   wall time of one search, embedding call included (ms)
  혼입      results from another company (must be 0)
The weights and the BM25 coverage floor are chosen on split=tune (best MRR@5, then 근거 정확도, then Hit@5, then p95) and reported
on split=val. `--configs` reruns a few configurations with more timed rounds.
"""
import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

from sqlalchemy import text

from app.ai.client import get_ai_client
from app.ai.textrules import content_tokens
from app.core.config import get_settings
from app.core.constants import EMBED_DIM
from app.core.db import session_scope
from app.questions import bm25, knowledge
from app.questions.knowledge import Chunk
from app.questions.rag import vec_literal

K = 5
WEIGHTS = [(1.0, 0.0), (0.7, 0.3), (0.6, 0.4), (0.5, 0.5), (0.4, 0.6), (0.0, 1.0)]
COVERAGES = [0.0, 0.3, 0.5, 0.7]  # rag_bm25_min_coverage: no BM25 floor, then stricter ones (100:0 has no BM25 list)
GRID = ["legacy"] + [(wv, wb, c) for wv, wb in WEIGHTS for c in (COVERAGES if wb > 0 else [0.0])]
QUERIES = Path(__file__).with_name("bm25_eval_queries.json")


def label(cfg) -> str:
    return cfg if isinstance(cfg, str) else f"{round(cfg[0] * 100)}:{round(cfg[1] * 100)}/c{cfg[2]}"


def parse(name: str):
    if name == "legacy":
        return name
    w, c = name.split("/c")
    wv, wb = (int(x) / 100 for x in w.split(":"))
    return (wv, wb, float(c))


async def legacy_search(db, ai, model, *, company_id, posting_id, queries, k, floor) -> list[Chunk]:
    """search_many as it was before ADR-0030 (vector lists, then a re-rank of what they found by shared words)."""
    vectors = await knowledge.embed_queries(ai, queries, "EVAL")
    if not vectors:
        return []
    found: dict[uuid.UUID, Chunk] = {}
    score: dict[uuid.UUID, float] = {}
    for vec in vectors:
        if len(vec) != EMBED_DIM:
            return []
        rows = (await db.execute(text(
            "SELECT id, title, text, 1 - (embedding <=> CAST(:v AS vector)), doc_id, doc_type FROM company_knowledge "
            "WHERE company_id = :c AND (posting_id IS NULL OR posting_id = :p) "
            "AND embedding IS NOT NULL AND embedding_model = :m ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
            {"c": company_id, "p": posting_id, "m": model, "v": vec_literal(vec), "n": k * 3})).all()
        kept = [Chunk(i, t, x, float(s), d, dt) for i, t, x, s, d, dt in rows if float(s) >= floor]
        for rank, ch in enumerate(kept):
            found.setdefault(ch.id, ch)
            score[ch.id] = score.get(ch.id, 0.0) + 1.0 / (60 + rank)
    wanted = content_tokens("\n".join(queries))
    by_words = sorted(found.values(), key=lambda ch: -len(wanted & content_tokens(f"{ch.title} {ch.text}")))
    for rank, ch in enumerate(by_words):
        score[ch.id] += 1.0 / (60 + rank)
    ranked = sorted(found.values(), key=lambda ch: -score[ch.id])
    first, rest, titles = [], [], set()
    for ch in ranked:
        (rest if ch.title in titles else first).append(ch)
        titles.add(ch.title)
    return (first + rest)[:k]


async def run_one(cfg, q: dict, scope: dict, ai, model: str, floor: float) -> tuple[list[Chunk], float]:
    s = get_settings()
    async with session_scope() as db:
        t0 = time.perf_counter()
        if cfg == "legacy":
            out = await legacy_search(db, ai, model, company_id=scope["company_id"], posting_id=scope["posting_id"], queries=[q["query"]],
                                      k=K, floor=floor)
        else:
            s.rag_weight_vector, s.rag_weight_bm25, s.rag_bm25_min_coverage = cfg
            out = await knowledge.search_many(db, ai, model, company_id=scope["company_id"], posting_id=scope["posting_id"],
                                              queries=[q["query"]], k=K, floor=floor, kind="EVAL")
        return out, (time.perf_counter() - t0) * 1000


def measure(results: list[dict]) -> dict:
    n = len(results)
    hit = sum(r["hit"] for r in results) / n
    mrr = sum(r["rr"] for r in results) / n
    rec = sum(r["recall"] for r in results) / n
    prec = sum(r["precision"] for r in results) / n
    lat = sorted(x for r in results for x in r["ms"])
    p95 = lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))]
    return {"n": n, "results": round(sum(r["results"] for r in results) / n, 2), "hit@5": round(hit, 3), "mrr@5": round(mrr, 3), "recall@5": round(rec, 3), "근거정확도": round(prec, 3),
            "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(p95, 1), "혼입": sum(r["foreign"] for r in results)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3, help="timed repetitions per query and configuration (accuracy is deterministic)")
    ap.add_argument("--configs", default="", help="comma-separated labels (e.g. legacy,60:40/c0.5); default: the whole grid")
    args = ap.parse_args()
    configs = [parse(x) for x in args.configs.split(",")] if args.configs else GRID
    data = json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
    settings = get_settings()
    ai, model, floor = get_ai_client(), settings.embed_model, settings.rag_knowledge_min_similarity
    saved = (settings.rag_weight_vector, settings.rag_weight_bm25, settings.rag_bm25_min_coverage)

    scopes, titles = {}, {}
    async with session_scope() as db:
        for name in sorted({q["company"] for q in data}):
            cid, pid = (await db.execute(text(
                "SELECT c.id, p.id FROM companies c JOIN job_postings p ON p.company_id = c.id WHERE c.name = :n AND p.is_virtual "
                "ORDER BY p.title LIMIT 1"), {"n": name})).one()
            scopes[name] = {"company_id": cid, "posting_id": pid}
            titles[name] = {t for (t,) in (await db.execute(text("SELECT DISTINCT title FROM company_knowledge WHERE company_id = :c"),
                                                            {"c": cid})).all()}
    missing = [(q["id"], t) for q in data for t in q["relevant"] if t not in titles[q["company"]]]
    if missing:
        raise SystemExit(f"relevant titles not in the database: {missing}")

    # warm-up: the BM25 index of each company is built once per process (timed separately) and the embedding server is awake
    build_ms = {}
    for name, scope in scopes.items():
        bm25.clear_cache()
        t0 = time.perf_counter()
        async with session_scope() as db:
            await bm25.search(db, company_id=scope["company_id"], posting_id=scope["posting_id"], query="warm", n=1)
        build_ms[name] = round((time.perf_counter() - t0) * 1000, 1)
    async with session_scope() as db:
        for name, scope in scopes.items():
            await bm25.search(db, company_id=scope["company_id"], posting_id=scope["posting_id"], query="warm", n=1)
    await knowledge.embed_queries(ai, ["warm up"], "EVAL")

    rows: dict[str, list[dict]] = {label(c): [] for c in configs}
    for q in data:
        scope = scopes[q["company"]]
        for cfg in configs:
            times, out = [], []
            for _ in range(args.rounds):
                out, ms = await run_one(cfg, q, scope, ai, model, floor)
                times.append(ms)
            async with session_scope() as db:
                owners = dict((await db.execute(text("SELECT id, company_id FROM company_knowledge WHERE id = ANY(:i)"),
                                                {"i": [c.id for c in out]})).all()) if out else {}
            got = [c.title for c in out]
            rel = set(q["relevant"])
            first = next((i for i, t in enumerate(got, 1) if t in rel), None)
            rows[label(cfg)].append({"id": q["id"], "split": q["split"], "kind": q["kind"], "company": q["company"],
                                     "hit": first is not None, "rr": 1 / first if first else 0.0,
                                     "recall": len(rel & set(got)) / len(rel), "precision": (sum(t in rel for t in got) / len(got)) if got else 0.0,
                                     "foreign": sum(owners.get(c.id) != scope["company_id"] for c in out), "ms": times,
                                     "first_rank": first, "results": len(out)})
    settings.rag_weight_vector, settings.rag_weight_bm25, settings.rag_bm25_min_coverage = saved

    report = {"bm25_index_build_ms": build_ms, "queries": len(data), "rounds": args.rounds, "tables": {}}
    for part, pick in (("tune", lambda r: r["split"] == "tune"), ("val", lambda r: r["split"] == "val"), ("all", lambda r: True),
                       ("term", lambda r: r["kind"] == "term"), ("keyword", lambda r: r["kind"] == "keyword"),
                       ("paraphrase", lambda r: r["kind"] == "paraphrase")):
        report["tables"][part] = {name: measure([r for r in rs if pick(r)]) for name, rs in rows.items()}
    tune = report["tables"]["tune"]
    # accuracy first (MRR, then evidence precision, then Hit), latency only to break a tie
    best = max((c for c in tune if c != "legacy"),
               key=lambda c: (tune[c]["mrr@5"], tune[c]["근거정확도"], tune[c]["hit@5"], -tune[c]["p95_ms"]))
    report["chosen_on_tune"] = best
    report["per_query_first_rank"] = {name: {r["id"]: r["first_rank"] for r in rs} for name, rs in rows.items()}
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
