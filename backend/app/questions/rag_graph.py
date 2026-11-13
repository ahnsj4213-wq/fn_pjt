"""Corrective RAG for generated questions (ADR-0020, revised 2026-11-11): one round per request of the quality loop
(questions/quality.py) for a COMPANY or JOB slot, upfront (the composition nodes of interview/graph.py) or live.

    request 1:  plan_queries -> embed (once) -> retrieve -> generate (T2) -> review
    request 2:  retrieve (documents not shown yet; else the ones shown) -> review what round 1 held back under the last-round
                rules -> generate (T2, `last_chance`, with the failure reasons of request 1) only for what is still missing -> review

  plan_queries  one search per duty line of the posting, so each part of the job finds its own document (code, no model)
  embed         the queries are embedded once per composition of an area; every search and the RAG examples reuse the vectors
  retrieve      vector search inside the company and posting, rank fusion with exact words, at most one chunk per document first.
                A company with no document of its own uses the embedded documents of the other companies as examples of the job
                (marked shared: asked as a situation, never stated as this company's work).
  generate      T2 with the documents and the RAG examples (found once, reused in round 2). No regeneration inside the task: the
                second request of the quality loop is the only regeneration, and it is this module's second round.
  review        keeps a question only if it names something concrete from its document and asks about a document not used yet.
                On the last round the concrete and distinct rules give way: a grounded question beats a code-made one.

Worst case per area: 2 requests x (1 + 1 format repair) = 4 model calls (before 2026-11-11: 5, round 1 with an in-task
regeneration plus round 2). Round 2 calls the model only for what round 1's held-back questions cannot fill.

The state lives on the composer context (`c.rag[kind]`) for the two requests of one node run: it holds document text and the AI
client, which must not be checkpointed, and the node that calls it is the unit of recovery (a crash repeats the node). It holds no
database transaction: every query is a short read (questions/dbio.ShortReads). Nothing here decides anything about a candidate.
"""
import logging
import re
import time
from typing import TypedDict

from sqlalchemy import text

from app.ai.textrules import content_tokens
from app.core.config import get_settings
from app.core.logging import log_event
from app.questions import knowledge

log = logging.getLogger("app.questions")
MAX_ROUNDS = 2
MAX_QUERIES = 5
LINE_QUERY_FLOOR = 0.45  # one line of a posting scores lower than the whole posting (measured in ADR-0020)
_LINES = re.compile(r"\n+|(?<=[.。])\s+")


class RagState(TypedDict, total=False):
    kind: str  # COMPANY | JOB
    n: int
    round: int
    queries: list[str]
    vectors: list | None  # the queries' embeddings (None: embeddings down)
    chunks: list  # this round's documents
    seen: dict  # chunk id -> knowledge.Chunk, every document shown so far
    used_docs: list  # doc ids the accepted questions came from
    candidates: list[dict]
    held: list[dict]  # grounded questions review held back (not concrete / same document): taken on the last round
    accepted: list[dict]
    no_company_info: bool
    shared: bool  # this company has no documents: the other companies' documents are used as job examples
    rejects: dict  # failure code -> count (T2 checks, copies, review), for the log


def lines_of(text: str | None) -> list[str]:
    return [x.strip(" -•·.") for x in _LINES.split(text or "") if len(x.strip(" -•·.")) >= 4]


def posting_queries(kind: str, title: str, intro: str, duties: str | None, requirements: str | None, preferred: str | None) -> list[str]:
    if kind == "COMPANY":
        return ["\n".join(x for x in (title, intro, duties or "") if x), "\n".join(x for x in (title, preferred or "") if x)]
    return [f"{title} {line}" for line in lines_of(duties)][: MAX_QUERIES - 1] + ["\n".join(x for x in (title, requirements or "", preferred or "") if x)]


def concrete(question: str, chunk: knowledge.Chunk) -> bool:
    """Two content words of the document in the question: it talks about this company's work, not about work in general."""
    return len(content_tokens(question) & content_tokens(f"{chunk.title} {chunk.text}")) >= 2


def used_basis(c) -> list[str]:
    """The source sentences earlier questions of this set were made from (posting lines, documents, resume phrases)."""
    return [b for p in c.picked if (b := ((p.grounding or {}).get("basis") or {}).get("text"))]


def _count(rejects: dict, code: str, n: int = 1) -> None:
    if n:
        rejects[code] = rejects.get(code, 0) + n


async def _docs_asked(c) -> list:
    """Documents earlier questions of this set came from (live composition makes them one at a time): searched past, not again."""
    ids = [s["id"] for p in c.picked for s in (p.grounding or {}).get("sources") or [] if s.get("type") == "KNOWLEDGE" and s.get("id")]
    if not ids or c.db is None:
        return []
    rows = await c.db.execute(text("SELECT DISTINCT doc_id FROM company_knowledge WHERE id = ANY(CAST(:i AS uuid[]))"), {"i": ids})
    return [r[0] for r in rows]


async def _plan_queries(state: RagState, c) -> dict:
    p = c.posting
    queries = posting_queries(state["kind"], p.title or "", c.company.intro or "", p.duties, p.requirements, p.preferred)
    # live composition: what the candidate just said finds the documents about it (the posting's lines still come first)
    queries += [a[:300] for a in getattr(c, "answers", [])[:2] if len(a) >= 20]
    queries = [q for q in queries if q.strip()]
    vectors = await knowledge.embed_queries(c.ai, queries, state["kind"]) if queries else []
    return {"queries": queries, "vectors": vectors, "used_docs": await _docs_asked(c), "round": 0, "seen": {}, "accepted": [],
            "held": [], "shared": False, "no_company_info": False, "rejects": {}, "examples": None}


async def _retrieve(state: RagState, c) -> dict:
    settings = get_settings()
    floor = settings.rag_knowledge_min_similarity if state["kind"] == "COMPANY" else LINE_QUERY_FLOOR
    k = max(settings.rag_knowledge_count, state["n"] + 1) if settings.rag_knowledge_count else 0
    search = dict(company_id=c.company.id, posting_id=c.posting.id, queries=state["queries"], k=k, kind=state["kind"],
                  exclude_docs=state["used_docs"], vectors=state.get("vectors"))
    shared = state["shared"]
    if state.get("vectors") is None:  # embeddings down: BM25 alone searches (ADR-0030); nothing found -> the posting, as before
        search["use_vector"] = False
    chunks = [] if shared else await knowledge.search_many(c.db, c.ai, c.embed_model, floor=floor, **search)
    first = not state["seen"]
    if not chunks and first and state["used_docs"] and not shared:  # every own document was asked about already: one again
        chunks = await knowledge.search_many(c.db, c.ai, c.embed_model, floor=floor, **{**search, "exclude_docs": []})
    no_info = state["no_company_info"] or (state["kind"] == "COMPANY" and first and not chunks)
    if not chunks and (shared or first):  # nothing of this company's own: how the same job is done elsewhere
        chunks = await knowledge.search_many(c.db, c.ai, c.embed_model, floor=LINE_QUERY_FLOOR, shared=True, **search)
        shared = shared or bool(chunks)
    fresh = [x for x in chunks if str(x.id) not in state["seen"]]
    if not chunks and state["seen"]:  # every document has given its question: ask from them again rather than from nothing
        chunks = list(state["seen"].values())[:k]
    return {"chunks": chunks, "fresh": len(fresh), "seen": {**state["seen"], **{str(x.id): x for x in chunks}}, "round": state["round"] + 1,
            "shared": shared, "no_company_info": no_info}


async def _generate(state: RagState, c) -> dict:
    from app.questions.compose import _drop_copies, _with_examples  # compose imports this module

    p = c.posting
    missing = state["n"] - len(state["accepted"])
    no_info = state["no_company_info"]
    if no_info and state["round"] == 1:  # nothing verified to ask about the company: a question about the posting's job instead
        log_event(log, "company_info_missing", application_id=str(c.app.id), company_id=str(c.company.id))
    ask = "JOB" if no_info else state["kind"]
    inp = {"kind": ask, "job_family": p.job_family or "", "n": missing, "existing": c.texts() + [q["text"] for q in state["accepted"]],
           "posting_id": str(p.id), "company_id": str(c.company.id),
           "posting": {"title": p.title, "duties": p.duties, "requirements": p.requirements, "preferred": p.preferred},
           "company_intro": "" if no_info else c.company.intro or "", "knowledge": [x.as_input() for x in state["chunks"]],
           # sentences earlier questions of this set came from (a posting line too): one sentence gives one question
           "used_basis": used_basis(c) + [q["basis"]["text"] for q in state["accepted"] if q.get("basis")],
           # ADR-0025: who is asked and how hard comes first; the documents only make the question concrete
           "level": getattr(c, "level", "JUNIOR"), "difficulties": list(getattr(c, "want", []))[len(state["accepted"]):][:missing]}
    if state["round"] >= MAX_ROUNDS:
        inp["last_chance"] = True  # the second request: the relaxed rules, and the failure reasons of the first one
        inp["feedback"] = list(getattr(c, "feedback", []) or [])
    examples = state.get("examples")
    if examples is None or examples[0] != ask:  # found once a run (same query, same kind): round 2 reuses them
        vec = (state.get("vectors") or [None])[0] if state["queries"] else None
        found = await _with_examples(c, ask, state["queries"][0] if state["queries"] else "", inp, vector=vec)
        examples = (ask, found)
    else:
        inp["examples"] = [e.text for e in examples[1]]
    r = await c.ai.llm("question_generate", inp, lane="INTERACTIVE")
    rejects = dict(state["rejects"])
    for code, n in ((r.flags or {}).get("rejects") or {}).items():
        _count(rejects, code, int(n))
    if r.status != "OK":
        log_event(log, "question_generate_failed", application_id=str(c.app.id), status=r.status)
        return {"candidates": [], "examples": examples, "rejects": rejects, "status": r.status}
    if hasattr(c, "failed"):
        c.failed.extend(r.output.get("failed") or [])  # what the task's own checks dropped, for the regeneration's feedback
    items = r.output.get("questions", [])
    kept = await _drop_copies(c, items, examples[1])
    _count(rejects, "RAG_COPY", len(items) - len(kept))
    return {"candidates": kept, "examples": examples, "rejects": rejects, "status": r.status}


async def _review(state: RagState, config=None) -> dict:
    """Keep what is concrete and from a document not used yet; on the last round take the rest too (held ones first)."""
    final = state["round"] >= MAX_ROUNDS
    accepted, used = list(state["accepted"]), list(state["used_docs"])
    held = [] if final else list(state.get("held") or [])
    rejects = dict(state.get("rejects") or {})
    others_left = len({x.doc_id for x in state["chunks"]} - set(used)) > 1
    pool = (list(state.get("held") or []) if final else []) + list(state["candidates"])
    for q in pool:
        if len(accepted) >= state["n"]:
            break
        chunk = state["seen"].get(q["source"]["id"]) if q["source"]["type"] == "KNOWLEDGE" else None
        if chunk is not None and not final:
            if not concrete(q["text"], chunk):
                _count(rejects, "NOT_CONCRETE")
                held.append(q)
                continue
            if chunk.doc_id in used and others_left:
                _count(rejects, "SAME_DOCUMENT")
                held.append(q)
                continue
        accepted.append(q)
        if chunk is not None:
            used.append(chunk.doc_id)
    return {"accepted": accepted, "used_docs": used, "held": held, "rejects": rejects}


async def _timed(name: str, c, kind: str, coro):
    """Stage timing (ADR-0025): `rag_stage` with the step's duration (retrieve = vector/keyword search + rerank, generate = RAG
    examples + T2 with its checks and retry, review = code only)."""
    t0 = time.monotonic()
    try:
        return await coro
    finally:
        log_event(log, "rag_stage", application_id=str(c.app.id), kind=kind, stage=name, duration_ms=int((time.monotonic() - t0) * 1000))


async def run(c, kind: str, n: int) -> RagState:
    """One round: questions of `kind` grounded in the company's documents (or the posting), at most one T2 call. The first call
    of a composition plans and embeds the queries; the second (the quality loop's regeneration) searches past the documents
    already shown and takes what round 1 held back first. Returns the state: `accepted` (this round's, maybe fewer than n),
    `seen` (every document shown), `no_company_info`, `rejects` (this round's; the composer adds them to its counts) and
    `status` (TIMEOUT: no second request)."""
    rag = getattr(c, "rag", None)
    if rag is None:
        rag = c.rag = {}
    state = rag.get(kind)
    if state is None or state["round"] >= MAX_ROUNDS:
        state = {"kind": kind}
        state.update(await _plan_queries(state, c))
        rag[kind] = state
    state.update({"n": n, "accepted": [], "rejects": {}, "candidates": [], "status": "OK"})
    state.update(await _timed("retrieve", c, kind, _retrieve(state, c)))
    if state["round"] >= MAX_ROUNDS and state.get("held"):
        state.update(await _review(state))  # what round 1 held back, under the last-round rules: no model call for it
    if len(state["accepted"]) < n:
        state.update(await _timed("generate", c, kind, _generate(state, c)))
        state.update(await _review(state))
    if state.get("status") == "TIMEOUT" and state["round"] < MAX_ROUNDS and state.get("held"):
        state.update({"round": MAX_ROUNDS, "candidates": []})  # the model is too slow for round 2: take what is held
        state.update(await _review(state))
    log_event(log, "question_rag", application_id=str(c.app.id), kind=kind, attempt=state["round"], count=len(state["accepted"]),
              hits=len(state["seen"]))
    return state
