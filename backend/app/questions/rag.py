"""RAG for question generation (ai-architecture 6.1, TRD 7-4 ③).

Before T2/T3 run, the composer embeds a search text (the posting for COMPANY/JOB, the resume summary for RESUME) and
takes the `rag_example_count` nearest reference questions of the same kind from question_examples (pgvector cosine).
They go into the prompt as tone/level examples only. A generated question whose embedding is within
`rag_copy_cosine` of an example counts as copied and is dropped (the slot then falls back like any failed one).

Embeddings down, or the corpus not indexed yet -> no examples: generation still runs (logged, never blocks a set).
The corpus is loaded from data/question_examples.jsonl and embedded by `sync_examples` (job index_question_examples).
"""
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, AIUnavailable
from app.core.constants import EMBED_DIM
from app.core.logging import log_event

log = logging.getLogger("app.questions")
DATA_FILE = Path(__file__).parent / "data" / "question_examples.jsonl"
EMBED_BATCH = 32
SEARCH_CHARS = 1000


@dataclass
class Example:
    id: uuid.UUID
    text: str


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def load_file(path: Path | None = None) -> list[dict]:
    return [json.loads(line) for line in (path or DATA_FILE).read_text(encoding="utf-8").splitlines() if line.strip()]


async def sync_examples(db: AsyncSession, ai: AIClient, model: str, *, path: Path | None = None,
                        max_batches: int | None = None) -> dict:
    """`db` is a plain session (not session_scope): it commits after the load and after each batch.
    Insert the file's rows that are missing, then embed rows without an embedding of `model`, one committed batch
    at a time (a restart resumes where it stopped). Returns counts. AIUnavailable propagates (the job defers)."""
    from app.questions.compose import norm_hash

    rows = load_file(path)
    have = (await db.execute(text("SELECT count(*) FROM question_examples"))).scalar_one()
    inserted = 0
    if have < len(rows):
        for r in rows:
            res = await db.execute(text(
                "INSERT INTO question_examples (kind, field, text, text_norm_hash, origin) VALUES (:k, :f, :t, :h, :o) "
                "ON CONFLICT (kind, text_norm_hash) DO NOTHING"),
                {"k": r["kind"], "f": r.get("field"), "t": r["text"], "h": norm_hash(r["text"]), "o": r.get("origin")})
            inserted += res.rowcount or 0
        await db.commit()
    embedded, batches = 0, 0
    while max_batches is None or batches < max_batches:
        todo = (await db.execute(text(
            "SELECT id, text FROM question_examples WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM :m "
            "ORDER BY id LIMIT :n"), {"m": model, "n": EMBED_BATCH})).all()
        if not todo:
            break
        vectors = await ai.embed([t for _, t in todo])
        for (eid, _), v in zip(todo, vectors):
            ok = len(v) == EMBED_DIM
            await db.execute(text(
                "UPDATE question_examples SET embedding = CAST(:v AS vector), embedding_model = :m WHERE id = :i"),
                {"v": vec_literal(v) if ok else None, "m": model, "i": eid})
        await db.commit()
        embedded += len(todo)
        batches += 1
    pending = (await db.execute(text(
        "SELECT count(*) FROM question_examples WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM :m"),
        {"m": model})).scalar_one()
    return {"inserted": inserted, "embedded": embedded, "pending": pending}


async def find_examples(db: AsyncSession, ai: AIClient, model: str, kind: str, query: str, k: int,
                        exclude: list[str] = ()) -> list[Example]:
    """The k reference questions of `kind` nearest to `query`, skipping texts already in the set."""
    if k <= 0 or not query.strip():
        return []
    try:
        (vec,) = await ai.embed([query[:SEARCH_CHARS]])
    except AIUnavailable:
        log_event(log, "rag_skipped", kind=kind, reason="embed_unavailable")
        return []
    if len(vec) != EMBED_DIM:
        return []
    rows = (await db.execute(text(
        "SELECT id, text FROM question_examples WHERE kind = :k AND embedding IS NOT NULL AND embedding_model = :m "
        "ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
        {"k": kind, "m": model, "v": vec_literal(vec), "n": k + len(exclude)})).all()
    skip = set(exclude)
    out = [Example(i, t) for i, t in rows if t not in skip][:k]
    if not out:
        log_event(log, "rag_skipped", kind=kind, reason="no_indexed_examples")
    return out


async def copied(db: AsyncSession, ai: AIClient, model: str, texts: list[str], examples: list[Example],
                 cosine: float) -> set[int]:
    """Indexes of `texts` whose embedding is within `cosine` of one of the examples (a copy, not a new question)."""
    if not texts or not examples:
        return set()
    try:
        vectors = await ai.embed(texts)
    except AIUnavailable:
        return set()  # the lexical copy check inside T2/T3 still applies
    hit: set[int] = set()
    for n, v in enumerate(vectors):
        if len(v) == EMBED_DIM and (await db.execute(text(
                "SELECT 1 FROM question_examples WHERE id = ANY(:ids) AND embedding_model = :m "
                "AND embedding <=> CAST(:v AS vector) <= :d LIMIT 1"),
                {"ids": [e.id for e in examples], "m": model, "v": vec_literal(v), "d": 1.0 - cosine})).first():
            hit.add(n)
    return hit
