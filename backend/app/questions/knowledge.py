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
from app.core.config import get_settings
from app.core.constants import EMBED_DIM
from app.core.logging import log_event
from app.questions import bm25
from app.questions.rag import EMBED_BATCH, SEARCH_CHARS, vec_literal

log = logging.getLogger("app.questions")
CHUNK_CHARS = 500


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


def _vector_sql(shared: bool, company_wide_only: bool = False) -> str:
    """The pgvector query of one scope (the same scopes as bm25.search)."""
    if shared:
        where = "company_id <> :c AND origin <> 'TECH_BLOG' AND NOT (doc_id = ANY(:x))"
    elif company_wide_only:
        where = "company_id = :c AND posting_id IS NULL AND NOT (doc_id = ANY(:x))"
    else:
        where = "company_id = :c AND (posting_id IS NULL OR posting_id = :p) AND NOT (doc_id = ANY(:x))"
    return ("SELECT id, title, text, 1 - (embedding <=> CAST(:v AS vector)), doc_id, doc_type FROM company_knowledge WHERE " + where +
            " AND embedding IS NOT NULL AND embedding_model = :m ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n")


async def _vector_list(db, vec: list[float], model: str, *, company_id, posting_id, n: int, floor: float, exclude_docs, shared: bool,
                       company_wide_only: bool = False) -> list[Chunk]:
    rows = (await db.execute(text(_vector_sql(shared, company_wide_only)),
                             {"c": company_id, "p": posting_id, "x": list(exclude_docs), "m": model, "v": vec_literal(vec), "n": n})).all()
    return [Chunk(i, t, x, float(s), d, dt, shared) for i, t, x, s, d, dt in rows if float(s) >= floor]


async def _bm25_list(db, query: str, *, company_id, posting_id, n: int, exclude_docs, shared: bool,
                     company_wide_only: bool = False) -> list[Chunk]:
    hits = await bm25.search(db, company_id=company_id, posting_id=posting_id, query=query, n=n, exclude_docs=exclude_docs,
                             shared=shared, company_wide_only=company_wide_only)
    return [Chunk(d.id, d.title, d.text, 0.0, d.doc_id, d.doc_type, shared) for d, _ in hits]


def fuse(lists: list[tuple[float, list[Chunk]]]) -> list[Chunk]:
    """Weighted reciprocal rank fusion: sum(weight / (rrf_k + rank)) over the lists, rank from 1; a chunk a list does not hold
    gets nothing from it, and a chunk with no positive score is dropped (a method weighted 0 cannot add one). Best first."""
    rrf_k = get_settings().rag_rrf_k
    found: dict[uuid.UUID, Chunk] = {}
    score: dict[uuid.UUID, float] = {}
    for weight, chunks in lists:
        if weight <= 0:
            continue
        for rank, ch in enumerate(chunks, start=1):
            if ch.id not in found or ch.similarity > found[ch.id].similarity:  # keep the vector similarity when one list has it
                found[ch.id] = ch
            score[ch.id] = score.get(ch.id, 0.0) + weight / (rrf_k + rank)
    return sorted((ch for cid, ch in found.items() if score.get(cid, 0) > 0), key=lambda ch: -score[ch.id])


async def hybrid(db, ai: AIClient | None, model: str, *, company_id: uuid.UUID, posting_id: uuid.UUID | None, queries: list[str], k: int,
                 floor: float, kind: str = "", exclude_docs=(), shared: bool = False, vectors: list[list[float]] | None = None,
                 use_vector: bool = True, company_wide_only: bool = False) -> tuple[list[Chunk], dict]:
    """BM25 and pgvector, each on its own, per query; fused (ADR-0030). One method failing leaves the other; both failing -> [].
    Returns the fused list (not cut to k) and which methods answered: {"vector": bool, "bm25": bool}."""
    s = get_settings()
    n = k * 3
    ran = {"vector": False, "bm25": False}
    lists: list[tuple[float, list[Chunk]]] = []
    if use_vector and s.rag_weight_vector > 0:
        if vectors is None and ai is not None:
            vectors = await embed_queries(ai, queries, kind)
        if vectors and len(vectors) == len(queries) and all(len(v) == EMBED_DIM for v in vectors):
            try:
                found = await _isolated(db, lambda: _all(vectors, lambda vec: _vector_list(
                    db, vec, model, company_id=company_id, posting_id=posting_id, n=n, floor=floor, exclude_docs=exclude_docs,
                    shared=shared, company_wide_only=company_wide_only)))
                lists += [(s.rag_weight_vector, x) for x in found]
                ran["vector"] = True
            except Exception as e:  # noqa: BLE001 - the pgvector query failed (not the model): BM25 still answers
                log_event(log, "rag_degraded", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), reason=f"vector_{type(e).__name__}")
        else:
            log_event(log, "rag_degraded", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), reason="vector_unavailable")
    if s.rag_weight_bm25 > 0:
        try:
            found = await _isolated(db, lambda: _all(queries, lambda q: _bm25_list(
                db, q, company_id=company_id, posting_id=posting_id, n=n, exclude_docs=exclude_docs, shared=shared,
                company_wide_only=company_wide_only)))
            lists += [(s.rag_weight_bm25, x) for x in found]
            ran["bm25"] = True
        except Exception as e:  # noqa: BLE001 - the vector lists still answer; the error type is logged, never the query
            log_event(log, "rag_degraded", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), reason=f"bm25_{type(e).__name__}")
    return fuse(lists), ran


async def _all(items, one) -> list[list[Chunk]]:
    return [await one(x) for x in items]


async def _isolated(db, run):
    """run() so that a failing statement does not take the other method down with it (PR #6 review). On a session the method runs in
    a SAVEPOINT: a failed query rolls back to it and the caller's transaction stays usable. ShortReads runs every statement in its own
    short transaction already. A database that is down altogether fails both methods: the caller takes its usual fallback."""
    if isinstance(db, AsyncSession):
        async with db.begin_nested():
            return await run()
    return await run()


def _log_result(out: list[Chunk], *, kind: str, company_id, posting_id, ran: dict) -> None:
    source = "+".join(m for m in ("vector", "bm25") if ran.get(m)) or "none"
    if out:  # ids and scores only (SEC-08): the query holds resume and answer text
        log_event(log, "knowledge_search", kind=kind, company_id=str(company_id), posting_id=str(posting_id), hits=len(out),
                  source=source, top_similarity=round(max(c.similarity for c in out), 3), example_ids=[str(c.id) for c in out])
    else:
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), source=source, reason="no_company_knowledge")


async def search(db: AsyncSession, ai: AIClient, model: str, *, company_id: uuid.UUID, posting_id: uuid.UUID, query: str,
                 k: int, kind: str = "") -> list[Chunk]:
    """The k chunks of this company that this posting may read, best for `query` by the hybrid search. Both methods down -> none."""
    if company_id is None or posting_id is None:
        raise ValueError("knowledge search needs a company and a posting")
    if k <= 0 or not query.strip():
        return []
    fused, ran = await hybrid(db, ai, model, company_id=company_id, posting_id=posting_id, queries=[query[:SEARCH_CHARS]], k=k,
                              floor=get_settings().rag_knowledge_min_similarity, kind=kind)
    out = fused[:k]
    _log_result(out, kind=kind, company_id=company_id, posting_id=posting_id, ran=ran)
    return out


async def search_many(db: AsyncSession, ai: AIClient, model: str, *, company_id: uuid.UUID, posting_id: uuid.UUID, queries: list[str],
                      k: int, floor: float, kind: str = "", exclude_docs: list = (), shared: bool = False,
                      vectors: list[list[float]] | None = None, use_vector: bool = True) -> list[Chunk]:
    """One vector search and one BM25 search per query, in the same company and posting scope as `search`, all fused by weighted
    RRF (ADR-0030; `floor` applies to the vector lists). One chunk per document title first, so a question set does not rest on
    one project. One method down -> the other answers; both -> none.

    shared=True searches the documents of every OTHER company instead (single administrator, no tenants: CLAUDE.md §0). They show how
    the job is done elsewhere and are marked `shared`, so T2 asks about them as a situation, never as this company's work. TECH_BLOG
    documents are never lent, and a company with its own blog never borrows (ADR-0029).

    `vectors`: the queries' embeddings when the caller has them already (the corrective RAG loop searches the same queries up to
    four times: own documents, again with used ones, other companies', the second round); one per query, same order.
    `use_vector=False`: the caller already knows the embedding model is down; BM25 alone answers."""
    if company_id is None or posting_id is None:
        raise ValueError("knowledge search needs a company and a posting")
    queries = [q[:SEARCH_CHARS] for q in queries if q.strip()]
    if k <= 0 or not queries:
        return []
    if shared and await has_tech_blog(db, company_id):
        # A company with its own public tech blog never borrows: another real company's posts must not reach its candidates.
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", company_id=str(company_id), reason="tech_blog_company_no_sharing")
        return []
    if vectors is not None and len(vectors) != len(queries):
        vectors = None
    ranked, ran = await hybrid(db, ai, model, company_id=company_id, posting_id=posting_id, queries=queries, k=k, floor=floor, kind=kind,
                               exclude_docs=exclude_docs, shared=shared, vectors=vectors, use_vector=use_vector)
    out = diverse(ranked)[:k]
    _log_result(out, kind=kind, company_id=company_id, posting_id=posting_id, ran=ran)
    return out


def diverse(ranked: list[Chunk]) -> list[Chunk]:
    """One chunk per document title first, then the rest, each part in fused order.

    Only a chunk the vector search found above its floor (similarity > 0) is moved up for being the first of its title. A chunk only
    BM25 found keeps its fused place: measured (ADR-0030, PR #6 review) the move put another post that merely shares a word with the
    query ahead of the second chunk of the right post, and the evidence precision fell from 0.76 to 0.62. When the vector search found
    nothing (or is down) every chunk is BM25's and the rule applies to them as before."""
    vector_found = any(ch.similarity > 0 for ch in ranked)
    first, rest, titles = [], [], set()
    for ch in ranked:
        new_title = ch.title not in titles
        titles.add(ch.title)
        (first if new_title and (ch.similarity > 0 or not vector_found) else rest).append(ch)
    return first + rest


async def has_tech_blog(db, company_id: uuid.UUID) -> bool:
    return await db.scalar(text("SELECT EXISTS (SELECT 1 FROM company_knowledge WHERE company_id = :c AND origin = 'TECH_BLOG')"),
                           {"c": company_id}) is True


async def embed_queries(ai: AIClient, queries: list[str], kind: str = "") -> list[list[float]] | None:
    """The search embeddings of `queries` (cut as search_many cuts them); None when the embedding model is down."""
    queries = [q[:SEARCH_CHARS] for q in queries if q.strip()]
    if not queries:
        return []
    try:
        return await ai.embed(queries)
    except AIUnavailable:
        log_event(log, "rag_skipped", kind=f"KNOWLEDGE:{kind}", reason="embed_unavailable")
        return None


async def search_company(db: AsyncSession, ai: AIClient, model: str, *, company_id: uuid.UUID, query: str, k: int, floor: float) -> list[Chunk]:
    """For a posting that does not exist yet: only this company's documents open to all its postings, by the hybrid search.
    Nothing found while the embedding model is down raises AIUnavailable (the draft endpoint answers 503, as before)."""
    if company_id is None:
        raise ValueError("knowledge search needs a company")
    fused, ran = await hybrid(db, ai, model, company_id=company_id, posting_id=None, queries=[query[:SEARCH_CHARS]], k=k, floor=floor,
                              kind="POSTING_DRAFT", company_wide_only=True)
    out = fused[:k]
    if not out and not ran["vector"] and get_settings().rag_weight_vector > 0:
        raise AIUnavailable("embedding model unavailable")
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
