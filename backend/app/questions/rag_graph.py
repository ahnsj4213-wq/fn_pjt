"""Corrective RAG for generated questions (LangGraph, ADR-0020): one small graph per T2 request, run inside an area node of the
composition graph (questions/graph.py).

    START -> plan_queries -> retrieve -> generate -> review -> END
                                ^                      |
                                +----- still short ----+   (at most MAX_ROUNDS, each round searches the documents not used yet)

  plan_queries  one search per duty line of the posting, so each part of the job finds its own document (code, no model)
  retrieve      vector search inside the company and posting, rank fusion with exact words, at most one chunk per document first.
                A company with no document of its own uses the embedded documents of the other companies as examples of the job
                (marked shared: asked as a situation, never stated as this company's work).
  generate      T2 with the documents and the RAG examples
  review        keeps a question only if it names something concrete from its document and asks about a document not used yet.
                On the last round the concrete and distinct rules give way: a grounded question beats a bank question.

No checkpointer of its own: the composition node that runs it is the unit of recovery, so a crash repeats the node. The state holds
document text and is never stored. Nothing here decides anything about a candidate.
"""
import logging
import re
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

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
    chunks: list  # this round's documents
    seen: dict  # chunk id -> knowledge.Chunk, every document shown so far
    used_docs: list  # doc ids the accepted questions came from
    candidates: list[dict]
    accepted: list[dict]
    no_company_info: bool
    shared: bool  # this company has no documents: the other companies' documents are used as job examples


def lines_of(text: str | None) -> list[str]:
    return [x.strip(" -•·.") for x in _LINES.split(text or "") if len(x.strip(" -•·.")) >= 4]


def posting_queries(kind: str, title: str, intro: str, duties: str | None, requirements: str | None, preferred: str | None) -> list[str]:
    if kind == "COMPANY":
        return ["\n".join(x for x in (title, intro, duties or "") if x), "\n".join(x for x in (title, preferred or "") if x)]
    return [f"{title} {line}" for line in lines_of(duties)][: MAX_QUERIES - 1] + ["\n".join(x for x in (title, requirements or "", preferred or "") if x)]


def concrete(question: str, chunk: knowledge.Chunk) -> bool:
    """Two content words of the document in the question: it talks about this company's work, not about work in general."""
    return len(content_tokens(question) & content_tokens(f"{chunk.title} {chunk.text}")) >= 2


async def _plan_queries(state: RagState, config: RunnableConfig) -> dict:
    c = config["configurable"]["ctx"]
    p = c.posting
    queries = posting_queries(state["kind"], p.title or "", c.company.intro or "", p.duties, p.requirements, p.preferred)
    # live composition: what the candidate just said finds the documents about it (the posting's lines still come first)
    queries += [a[:300] for a in getattr(c, "answers", [])[:2] if len(a) >= 20]
    return {"queries": queries,
            "round": 0, "seen": {}, "used_docs": [], "accepted": [], "shared": False, "no_company_info": False}


async def _retrieve(state: RagState, config: RunnableConfig) -> dict:
    c = config["configurable"]["ctx"]
    settings = get_settings()
    floor = settings.rag_knowledge_min_similarity if state["kind"] == "COMPANY" else LINE_QUERY_FLOOR
    k = max(settings.rag_knowledge_count, state["n"] + 1) if settings.rag_knowledge_count else 0
    search = dict(company_id=c.company.id, posting_id=c.posting.id, queries=state["queries"], k=k, kind=state["kind"], exclude_docs=state["used_docs"])
    shared = state["shared"]
    chunks = [] if shared else await knowledge.search_many(c.db, c.ai, c.embed_model, floor=floor, **search)
    first = not state["seen"]
    no_info = state["no_company_info"] or (state["kind"] == "COMPANY" and first and not chunks)
    if not chunks and (shared or first):  # nothing of this company's own: how the same job is done elsewhere
        chunks = await knowledge.search_many(c.db, c.ai, c.embed_model, floor=LINE_QUERY_FLOOR, shared=True, **search)
        shared = shared or bool(chunks)
    if not chunks and state["seen"]:  # every document has given its question: ask from them again rather than from nothing
        chunks = list(state["seen"].values())[:k]
    return {"chunks": chunks, "seen": {**state["seen"], **{str(x.id): x for x in chunks}}, "round": state["round"] + 1, "shared": shared,
            "no_company_info": no_info}


async def _generate(state: RagState, config: RunnableConfig) -> dict:
    from app.questions.compose import _drop_copies, _with_examples  # compose imports this module

    c = config["configurable"]["ctx"]
    p = c.posting
    missing = state["n"] - len(state["accepted"])
    no_info = state["no_company_info"]
    if no_info:  # nothing verified to ask about the company: a question about the posting's job instead
        log_event(log, "company_info_missing", application_id=str(c.app.id), company_id=str(c.company.id))
    ask = "JOB" if no_info else state["kind"]
    inp = {"kind": ask, "job_family": p.job_family or "", "n": missing, "existing": c.texts() + [q["text"] for q in state["accepted"]],
           "posting_id": str(p.id), "company_id": str(c.company.id),
           "posting": {"title": p.title, "duties": p.duties, "requirements": p.requirements, "preferred": p.preferred},
           "company_intro": "" if no_info else c.company.intro or "", "knowledge": [x.as_input() for x in state["chunks"]]}
    examples = await _with_examples(c, ask, state["queries"][0], inp)
    r = await c.ai.llm("question_generate", inp, lane="INTERACTIVE")
    if r.status != "OK":
        log_event(log, "question_generate_failed", application_id=str(c.app.id), status=r.status)
        return {"candidates": []}
    return {"candidates": await _drop_copies(c, r.output.get("questions", []), examples)}


async def _review(state: RagState, config: RunnableConfig) -> dict:
    final = state["round"] >= MAX_ROUNDS
    accepted, used = list(state["accepted"]), list(state["used_docs"])
    others_left = len({x.doc_id for x in state["chunks"]} - set(used)) > 1
    for q in state["candidates"]:
        if len(accepted) >= state["n"]:
            break
        chunk = state["seen"].get(q["source"]["id"]) if q["source"]["type"] == "KNOWLEDGE" else None
        if chunk is not None and not final:
            if not concrete(q["text"], chunk) or (chunk.doc_id in used and others_left):
                continue
        accepted.append(q)
        if chunk is not None:
            used.append(chunk.doc_id)
    return {"accepted": accepted, "used_docs": used}


def _next(state: RagState) -> str:
    return END if len(state["accepted"]) >= state["n"] or state["round"] >= MAX_ROUNDS else "retrieve"


def build() -> StateGraph:
    g = StateGraph(RagState)
    g.add_node("plan_queries", _plan_queries)
    g.add_node("retrieve", _retrieve)
    g.add_node("generate", _generate)
    g.add_node("review", _review)
    g.add_edge(START, "plan_queries")
    g.add_edge("plan_queries", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "review")
    g.add_conditional_edges("review", _next, {"retrieve": "retrieve", END: END})
    return g


# checkpointer=False: called from a node of the composition graph, it would otherwise inherit that graph's checkpointer and try to
# store the documents and the database session.
GRAPH = build().compile(checkpointer=False)


async def run(c, kind: str, n: int) -> RagState:
    out = await GRAPH.ainvoke({"kind": kind, "n": n}, {"configurable": {"ctx": c}, "recursion_limit": 15})
    log_event(log, "question_rag", application_id=str(c.app.id), kind=kind, rounds=out["round"], accepted=len(out["accepted"]), wanted=n,
              documents=len(out["seen"]))
    return out
