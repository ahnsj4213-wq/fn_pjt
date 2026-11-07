"""A company's project and technology documents as the source of interview questions (ADR-0020).

Every search takes the company and the posting and cannot run without them: a chunk of another company, or of another
posting of the same company, is never returned. A chunk below `rag_knowledge_min_similarity` does not count, so a company
without relevant documents gets no chunks and the composer asks from the posting instead of making facts up.
"""
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, AIUnavailable
from app.ai.textrules import content_tokens
from app.core.config import get_settings
from app.core.constants import EMBED_DIM
from app.core.logging import log_event
from app.questions.rag import EMBED_BATCH, SEARCH_CHARS, vec_literal

log = logging.getLogger("app.questions")
CHUNK_CHARS = 500
RRF_K = 60


@dataclass
class Chunk:
    id: uuid.UUID
    title: str
    text: str
    similarity: float
    doc_id: uuid.UUID | None = None
    doc_type: str = ""
    shared: bool = False  # another company's document, used as a work example of the job, never as a fact about this company

    def as_input(self) -> dict:
        return {"id": str(self.id), "title": self.title, "text": self.text} | ({"shared": True} if self.shared else {})


def split_document(body: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Paragraphs joined up to `limit` characters; a longer paragraph is cut at its sentence ends, or hard as a last resort."""
    chunks: list[str] = []
    cur = ""

    def push(piece: str) -> None:
        nonlocal cur
        if cur and len(cur) + 1 + len(piece) > limit:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{piece}" if cur else piece

    for para in (p.strip() for p in body.replace("\r\n", "\n").split("\n")):
        if not para:
            continue
        if len(para) <= limit:
            push(para)
            continue
        for sentence in para.replace(". ", ".\n").split("\n"):
            for start in range(0, len(sentence), limit):
                push(sentence[start: start + limit])
    if cur:
        chunks.append(cur)
    return chunks


async def search(db: AsyncSession, ai: AIClient, model: str, *, company_id: uuid.UUID, posting_id: uuid.UUID, query: str,
                 k: int, kind: str = "") -> list[Chunk]:
    """The k chunks of this company nearest to `query` that this posting may read. Embeddings down -> none (never blocks)."""
    if company_id is None or posting_id is None:
        raise ValueError("knowledge search needs a company and a posting")
    if k <= 0 or not query.strip():
        return []
    try:
        (vec,) = await ai.embed([query[:SEARCH_CHARS]])
    except AIUnavailable:
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", reason="embed_unavailable")
        return []
    if len(vec) != EMBED_DIM:
        return []
    rows = (await db.execute(text(
        "SELECT id, title, text, 1 - (embedding <=> CAST(:v AS vector)) FROM company_knowledge "
        "WHERE company_id = :c AND (posting_id IS NULL OR posting_id = :p) "
        "AND embedding IS NOT NULL AND embedding_model = :m "
        "ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
        {"c": company_id, "p": posting_id, "m": model, "v": vec_literal(vec), "n": k})).all()
    floor = get_settings().rag_knowledge_min_similarity
    out = [Chunk(i, t, x, float(s)) for i, t, x, s in rows if float(s) >= floor]
    if out:  # ids and scores only (SEC-08): the query holds resume and answer text
        log_event(log, "knowledge_search", kind=kind, company_id=str(company_id), posting_id=str(posting_id), hits=len(out),
                  top_similarity=round(out[0].similarity, 3), example_ids=[str(c.id) for c in out])
    else:
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), reason="no_company_knowledge")
    return out


async def search_many(db: AsyncSession, ai: AIClient, model: str, *, company_id: uuid.UUID, posting_id: uuid.UUID, queries: list[str],
                      k: int, floor: float, kind: str = "", exclude_docs: list = (), shared: bool = False) -> list[Chunk]:
    """One search per query in the same company and posting scope as `search`, merged by reciprocal rank fusion together with a ranking
    by words the queries share with the chunk (a technology name embeds poorly). One chunk per document title first, so a question
    set does not rest on one project. Embeddings down -> none.

    shared=True searches the documents of every OTHER company instead (single administrator, no tenants: CLAUDE.md §0). They show how
    the job is done elsewhere and are marked `shared`, so T2 asks about them as a situation, never as this company's work."""
    if company_id is None or posting_id is None:
        raise ValueError("knowledge search needs a company and a posting")
    queries = [q[:SEARCH_CHARS] for q in queries if q.strip()]
    if k <= 0 or not queries:
        return []
    try:
        vectors = await ai.embed(queries)
    except AIUnavailable:
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", reason="embed_unavailable")
        return []
    found: dict[uuid.UUID, Chunk] = {}
    score: dict[uuid.UUID, float] = {}
    for vec in vectors:
        if len(vec) != EMBED_DIM:
            return []
        rows = (await db.execute(text(
            "SELECT id, title, text, 1 - (embedding <=> CAST(:v AS vector)), doc_id, doc_type FROM company_knowledge "
            + ("WHERE company_id <> :c AND NOT (doc_id = ANY(:x)) " if shared else
               "WHERE company_id = :c AND (posting_id IS NULL OR posting_id = :p) AND NOT (doc_id = ANY(:x)) ") +
            "AND embedding IS NOT NULL AND embedding_model = :m ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
            {"c": company_id, "p": posting_id, "x": list(exclude_docs), "m": model, "v": vec_literal(vec), "n": k * 3})).all()
        kept = [Chunk(i, t, x, float(s), d, dt, shared) for i, t, x, s, d, dt in rows if float(s) >= floor]
        for rank, ch in enumerate(kept):
            found.setdefault(ch.id, ch)
            score[ch.id] = score.get(ch.id, 0.0) + 1.0 / (RRF_K + rank)
    wanted = content_tokens("\n".join(queries))
    by_words = sorted(found.values(), key=lambda ch: -len(wanted & content_tokens(f"{ch.title} {ch.text}")))
    for rank, ch in enumerate(by_words):
        score[ch.id] += 1.0 / (RRF_K + rank)
    ranked = sorted(found.values(), key=lambda ch: -score[ch.id])
    first, rest, titles = [], [], set()
    for ch in ranked:
        (rest if ch.title in titles else first).append(ch)
        titles.add(ch.title)
    out = (first + rest)[:k]
    if out:  # ids and scores only (SEC-08)
        log_event(log, "knowledge_search", kind=kind, shared=shared, company_id=str(company_id), posting_id=str(posting_id), hits=len(out), queries=len(queries),
                  top_similarity=round(max(c.similarity for c in out), 3), example_ids=[str(c.id) for c in out])
    else:
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), reason="no_company_knowledge")
    return out


async def search_company(db: AsyncSession, ai: AIClient, model: str, *, company_id: uuid.UUID, query: str, k: int, floor: float) -> list[Chunk]:
    """For a posting that does not exist yet: only this company's documents open to all its postings. Embeddings down propagates."""
    if company_id is None:
        raise ValueError("knowledge search needs a company")
    (vec,) = await ai.embed([query[:SEARCH_CHARS]])
    if len(vec) != EMBED_DIM:
        raise AIUnavailable(f"embedding size {len(vec)}")
    rows = (await db.execute(text(
        "SELECT id, title, text, 1 - (embedding <=> CAST(:v AS vector)) FROM company_knowledge "
        "WHERE company_id = :c AND posting_id IS NULL AND embedding IS NOT NULL AND embedding_model = :m "
        "ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
        {"c": company_id, "m": model, "v": vec_literal(vec), "n": k})).all()
    out = [Chunk(i, t, x, float(s)) for i, t, x, s in rows if float(s) >= floor]
    log_event(log, "knowledge_search", kind="POSTING_DRAFT", company_id=str(company_id), hits=len(out), example_ids=[str(c.id) for c in out])
    return out


async def embed_pending(db: AsyncSession, ai: AIClient, model: str, *, max_batches: int | None = None) -> dict:
    """`db` is a plain session: each batch commits, so a restart resumes. AIUnavailable propagates (the job defers)."""
    embedded, batches = 0, 0
    while max_batches is None or batches < max_batches:
        todo = (await db.execute(text(
            "SELECT id, title, text FROM company_knowledge WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM :m "
            "ORDER BY id LIMIT :n"), {"m": model, "n": EMBED_BATCH})).all()
        if not todo:
            break
        await db.commit()  # embedding a batch outlasts the server's idle-in-transaction limit
        vectors = await ai.embed([f"{title}\n{body}" for _, title, body in todo])
        for (cid, _, _), v in zip(todo, vectors):
            if len(v) != EMBED_DIM:
                raise AIUnavailable(f"embedding size {len(v)}")
            await db.execute(text("UPDATE company_knowledge SET embedding = CAST(:v AS vector), embedding_model = :m WHERE id = :i"),
                             {"v": vec_literal(v), "m": model, "i": cid})
        await db.commit()
        embedded += len(todo)
        batches += 1
    return {"embedded": embedded}
