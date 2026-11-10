"""The question generation graph (LangGraph, ADR-0016; revised 2026-11-10): one run per `compose_questions` job,
`thread_id` = compose:<application>:<settings version>, checkpoints in PostgreSQL. The one entry point is `run(app_id, settings_id)`.

    START -> plan -> project -> motivation -> job_skill -> write -> END
                     (T3)       (T2 + corrective RAG, rag_graph.run)

  plan        the slots of the settings' plan (plan_slots: 인성2·동기1·직무3·경험3·협업1 by default), and every question that needs
              no model: custom questions asked as written (CUSTOM), 인성·협업 made by code from the resume or the posting (TEMPLATE).
              upfront (default, ADR-0025): every slot is composed in this run and voiced by tts_pregen before the interview.
              live mode (ADR-0022): only the first slot is composed here; the plan is stored with the set and app.questions.live
              composes each later question when the candidate reaches it, from the answers so far.
  project     T3 against the resume; what it cannot ground becomes an experience question by code (experience_fallback)
  motivation  T2 from the company's documents; what it cannot make, a posting question by code (posting_fallback)
  job_skill   T2 from the posting and the documents (difficulty per slot from the level), then posting_fallback
  write       T12 for questions taken from the question bank (none in the current sources: skipped, no embedding call), the
              verification (every slot exactly once, no two alike: ComposeError), then one short transaction: the live set of an
              older settings version is superseded, the READY set with its immutable MAIN rows is written, tts_pregen is queued.

Transactions: a node reads its context in a short transaction, calls the models with none open (questions/dbio.ShortReads for the
searches), and only `write` (and a bank insert after its embeddings) writes. A model call never holds a database transaction, so
the server's idle-in-transaction limit cannot cut a slow composition (tested: tests/test_question_graph.py).

Recovery: the nodes that call a model are the units of recovery. A retry of the job (worker crash, model down) continues at the
node that stopped; a node finds the slots still empty from the plan and what is picked, so a node run twice fills nothing twice.
`write` is idempotent: a set already written for this settings version is returned as it is.

State: ids, the live flag and the questions picked so far (slot, text, source, grounding). The plan is not kept (plan_slots is
code over the settings version, recomputed by each node). The picked questions cannot live in the database before `write` without
a new question-set state (the compose job treats a COMPOSING/READY set of the same version as done): that is an API/DB change left
out on purpose. The state is deleted when the run ends and `purge_finished` removes the leftovers of attempts that are over.
"""
import logging
import time
from dataclasses import asdict
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.ai.client import get_ai_client
from app.core.config import get_settings
from app.core.constants import Area
from app.core.db import session_scope
from app.core.logging import log_event
from app.interview.checkpoint import get_checkpointer
from app.jobs.queue import enqueue
from app.models import Application, Company, JobPosting, PostingEvalSettings, QuestionSet
from app.questions import compose as C
from app.questions.dbio import ShortReads

log = logging.getLogger("app.questions")


class State(TypedDict, total=False):
    application_id: str
    settings_id: str
    live: bool  # only the first question now; app.questions.live makes the others during the interview (ADR-0022)
    picked: list[dict]  # C.Picked as plain data
    qset_id: str | None


# ── plain data <-> composer objects ────────────────────────────────────────────
def _picked_to_dict(p: C.Picked) -> dict:
    d = asdict(p)
    d["bank_id"] = str(p.bank_id) if p.bank_id else None
    return d


def _picked_from_dict(d: dict) -> C.Picked:
    return C.Picked(**{**d, "bank_id": UUID(d["bank_id"]) if d.get("bank_id") else None})


async def _load(state: State, with_problem: bool = False):
    """The context, read in one short transaction and closed before any model call. `c.db` reads one statement at a time."""
    async with session_scope() as db:
        app = await db.get(Application, UUID(state["application_id"]))
        settings = await db.get(PostingEvalSettings, UUID(state["settings_id"]))
        posting = await db.get(JobPosting, app.posting_id)
        company = await db.get(Company, app.company_id)
        slots, _problem = await C.plan_slots(db, posting, settings)
    c = C._Ctx(app, posting, company, settings, get_ai_client(), get_settings().embed_model, ShortReads())
    c.picked = [_picked_from_dict(p) for p in state.get("picked", [])]
    return (c, slots, _problem) if with_problem else (c, slots)


def _wanted(slots: list[C.Slot], live: bool) -> list[C.Slot]:
    first = min((s.seq for s in slots), default=0)
    return [s for s in slots if not live or s.seq == first]


def _empty(c: C._Ctx, slots: list[C.Slot], live: bool, area: Area) -> list[tuple[int, str]]:
    """The slots of `area` this run composes that have no question yet (a node run again after a crash fills none twice)."""
    done = {p.seq for p in c.picked}
    return [(s.seq, s.area.value) for s in _wanted(slots, live)
            if s.area == area and s.text is None and s.mode != "CODE" and s.seq not in done]


def _log_rejects(c: C._Ctx, stage: str) -> None:
    for code, n in sorted(c.rejects.items()):  # counts by failure code only: no question, resume or document text
        log_event(log, "question_reject", application_id=str(c.app.id), stage=stage, reason=code, count=n)


# ── nodes ──────────────────────────────────────────────────────────────────────
async def plan(state: State) -> dict:
    live = get_settings().question_mode == "live"
    c, slots, problem = await _load({**state, "picked": []}, with_problem=True)
    for sl in _wanted(slots, live):
        if sl.text is not None:
            c.picked.append(C.custom_pick(c.settings, sl))
        elif sl.mode == "CODE":  # no new plan has CODE slots (plan_slots); kept for the shape of older plans
            if problem is None:
                raise C.ComposeError(f"slot {sl.seq}: a live-coding slot without a problem")
            c.picked.append(C.Picked(sl.seq, sl.area.value, f"{problem.title}: {C.CODE_QUESTION}", "PROBLEM",
                                     grounding={"problem_id": str(problem.id)}))
    # 인성·협업: code only (resume or posting), in this order so the resume parts they name are left out of the later questions
    for area in (Area.PERSONALITY, Area.COLLABORATION):
        empty = _empty(c, slots, live, area)
        if empty:
            await C._fill_common(c.db, c, area.value, empty)
    return {"live": live, "picked": [_picked_to_dict(p) for p in c.picked]}


def _model_node(area: Area, fill):
    async def node(state: State) -> dict:
        c, slots = await _load(state)
        empty = _empty(c, slots, bool(state.get("live")), area)
        if not empty:
            return {}
        await fill(c.db, c, empty)
        _log_rejects(c, area.value.lower())
        return {"picked": [_picked_to_dict(p) for p in c.picked]}
    node.__name__ = area.value.lower()
    return node


async def _fill_project(db, c, slots):
    await C._fill_resume(db, c, slots)


async def _fill_motivation(db, c, slots):
    await C._fill_generated_kind(db, c, "COMPANY", slots, c.embed_model)


async def _fill_job(db, c, slots):
    await C._fill_generated_kind(db, c, "JOB", slots, c.embed_model)


def verify(picked: list[C.Picked], slots: list[C.Slot], live: bool) -> None:
    want = sorted(s.seq for s in _wanted(slots, live))
    if sorted(p.seq for p in picked) != want:
        raise C.ComposeError(f"slots {want} but questions for {sorted(p.seq for p in picked)}")
    texts = [p.text for p in sorted(picked, key=lambda x: x.seq)]
    for i, t in enumerate(texts):
        if any(C.similar_question(t, u) for u in texts[:i]):
            raise C.ComposeError(f"question {i + 1} repeats an earlier one")


async def write(state: State) -> dict:
    app_id, settings_id = UUID(state["application_id"]), UUID(state["settings_id"])
    async with session_scope() as db:  # a short read: an earlier attempt may have written the set already
        done = (await db.execute(select(QuestionSet.id).where(QuestionSet.application_id == app_id, QuestionSet.settings_id == settings_id,
                                                              QuestionSet.status.in_(["COMPOSING", "READY"])))).scalar_one_or_none()
    if done is not None:
        return {"qset_id": str(done)}
    c, slots = await _load(state)
    live = bool(state.get("live"))
    if any(p.source in ("BANK", "BANK_FALLBACK") for p in c.picked):  # T12 only when a bank question is in the set
        resume, docs, _about = await C.adapt_context(c)
        await C._adapt_bank_picks(c, resume, docs)
    picked = sorted(c.picked, key=lambda p: p.seq)
    verify(picked, slots, live)
    async with session_scope() as db:  # a second writer of the same set fails on the live-set unique index; its retry finds it
        current =(await db.execute(select(QuestionSet).where(QuestionSet.application_id == app_id,
                                                              QuestionSet.status.in_(["COMPOSING", "READY"])))).scalar_one_or_none()
        if current is not None and current.settings_id == settings_id:  # written meanwhile
            return {"qset_id": str(current.id)}
        if current is not None:
            current.status = "SUPERSEDED"  # weights/custom question changed before the interview (start() re-enqueues)
            await db.flush()
        app = await db.get(Application, app_id)
        settings = await db.get(PostingEvalSettings, settings_id)
        qset = await C.write_set(db, app, settings, slots, picked, live=live)
        await enqueue(db, "tts_pregen", {"question_set_id": str(qset.id)}, application_id=app_id, dedupe_key=f"tts:{qset.id}")
        return {"qset_id": str(qset.id)}


MODEL_NODES = (("project", Area.PROJECT, _fill_project), ("motivation", Area.MOTIVATION, _fill_motivation),
               ("job_skill", Area.JOB_SKILL, _fill_job))


def _timed(name: str, fn):
    """Stage timing (ADR-0025): one `compose_stage` event per node with its duration; nothing of the questions."""
    async def node(state: State) -> dict:
        t0 = time.monotonic()
        try:
            return await fn(state)
        finally:
            log_event(log, "compose_stage", application_id=state.get("application_id"), stage=name,
                      duration_ms=int((time.monotonic() - t0) * 1000))
    node.__name__ = name
    return node


def build() -> StateGraph:
    g = StateGraph(State)
    g.add_node("plan", _timed("plan", plan))
    for name, area, fill in MODEL_NODES:
        g.add_node(name, _timed(name, _model_node(area, fill)))
    g.add_node("write", _timed("write", write))
    chain = [START, "plan"] + [n for n, _a, _f in MODEL_NODES] + ["write", END]
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    return g


_compiled = None


async def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build().compile(checkpointer=await get_checkpointer())
    return _compiled


def thread_id(app_id: UUID, settings_id: UUID) -> str:
    return f"compose:{app_id}:{settings_id}"


async def run(app_id: UUID, settings_id: UUID) -> UUID:
    """Compose and write the question set; returns its id. A thread left by a failed attempt continues where it stopped."""
    graph = await get_graph()
    cfg = {"configurable": {"thread_id": thread_id(app_id, settings_id)}, "recursion_limit": 20}
    snap = await graph.aget_state(cfg)
    t0 = time.monotonic()
    if snap.next:  # an earlier attempt stopped inside the run (crash, model down): carry on from that node
        log.info("compose resumes at %s", snap.next[0])
        await graph.ainvoke(None, cfg)
    else:
        await graph.ainvoke({"application_id": str(app_id), "settings_id": str(settings_id), "picked": [], "qset_id": None}, cfg)
    qset_id = UUID((await graph.aget_state(cfg)).values["qset_id"])
    log_event(log, "compose_stage", application_id=str(app_id), stage="total", duration_ms=int((time.monotonic() - t0) * 1000))
    await (await get_checkpointer()).adelete_thread(thread_id(app_id, settings_id))
    return qset_id
