"""The question composition graph (LangGraph, ADR-0016, ai-architecture 6.1): one run per `compose_questions` job,
`thread_id` = compose:<application>:<settings version>, checkpoints in PostgreSQL.

    START -> plan -> personality -> collaboration -> project -> motivation -> job_skill -> verify -> write -> END

  plan           the plan's slots get their questions' source: asked as written (CUSTOM), the coding problem (PROBLEM), or
                 left to the area nodes below.
  personality    bank only (least used, ties random)                    } each area node picks from the bank first,
  collaboration  bank only                                              } makes the rest with T2/T3 (RAG examples,
  project        T3 against the resume, else the job bank               } copies dropped), and falls back to the question
  motivation     company bank, T2 for the rest                          } bank and the shared seed questions. Which source
  job_skill      job bank, T2 for the rest                              } wins is decided by code; a failed or invalid
                                                                          model answer only means the next source.
  verify         every slot has exactly one question and no two are alike (ComposeError otherwise)
  write          one transaction: the live set of an older settings version is superseded, the READY set with its immutable
                 MAIN rows is written, and the TTS pre-synthesis job is queued.
Each node commits its own work, so what the checkpoint says has been done is what the database says. A retry of the job
(worker crash, model down) continues at the node that stopped instead of starting the model calls again.
The state holds slot numbers, question texts and the grounding quotes of the resume; it is deleted when the run ends and
`purge_finished` removes the leftovers of attempts that are over.
"""
import logging
from dataclasses import asdict
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.ai.client import get_ai_client
from app.core.config import get_settings
from app.core.constants import Area
from app.core.db import session_scope
from app.interview.checkpoint import get_checkpointer
from app.jobs.queue import enqueue
from app.models import Application, Company, JobPosting, PostingEvalSettings, QuestionSet
from app.questions import compose as C

log = logging.getLogger("app.questions")


class State(TypedDict, total=False):
    application_id: str
    settings_id: str
    plan: list[dict]  # [{seq, area, text, mode}]
    problem_id: str | None
    empty: dict[str, list[list]]  # area -> [[seq, slot], ...] still to fill
    picked: list[dict]  # C.Picked as plain data
    qset_id: str | None


# ── plain data <-> composer objects ────────────────────────────────────────────
def _picked_to_dict(p: C.Picked) -> dict:
    d = asdict(p)
    d["bank_id"] = str(p.bank_id) if p.bank_id else None
    return d


def _picked_from_dict(d: dict) -> C.Picked:
    return C.Picked(**{**d, "bank_id": UUID(d["bank_id"]) if d.get("bank_id") else None})


def _slot_to_dict(s: C.Slot) -> dict:
    return {"seq": s.seq, "area": s.area.value, "text": s.text, "mode": s.mode}


def _slot_from_dict(d: dict) -> C.Slot:
    return C.Slot(d["seq"], Area(d["area"]), d["text"], d["mode"])


async def _context(db, state: State) -> tuple[Application, PostingEvalSettings, C._Ctx]:
    app = await db.get(Application, UUID(state["application_id"]))
    settings = await db.get(PostingEvalSettings, UUID(state["settings_id"]))
    posting = await db.get(JobPosting, app.posting_id)
    company = await db.get(Company, app.company_id)
    c = C._Ctx(app, posting, company, settings, get_ai_client(), get_settings().embed_model, db)
    c.picked = [_picked_from_dict(p) for p in state.get("picked", [])]
    return app, settings, c


# ── nodes ──────────────────────────────────────────────────────────────────────
async def plan(state: State) -> dict:
    async with session_scope() as db:
        app, settings, c = await _context(db, state)
        slots, problem = await C.plan_slots(db, c.posting, settings)
        empty: dict[str, list[list]] = {a.value: [] for a in Area}
        for sl in slots:
            if sl.text is not None:
                c.picked.append(C.Picked(sl.seq, sl.area.value, sl.text, "CUSTOM"))
            elif sl.mode == "CODE":
                c.picked.append(C.Picked(sl.seq, sl.area.value, f"{problem.title}: 화면의 문제를 코드로 풀면서, 어떻게 "
                                         "풀었는지 말로 설명해 주십시오.", "PROBLEM", grounding={"problem_id": str(problem.id)}))
            else:
                empty[sl.area.value].append([sl.seq, sl.area.value])
        return {"plan": [_slot_to_dict(s) for s in slots], "problem_id": str(problem.id) if problem else None,
                "empty": empty, "picked": [_picked_to_dict(p) for p in c.picked]}


def _area_node(area: Area, fill):
    async def node(state: State) -> dict:
        slots = [tuple(x) for x in state["empty"][area.value]]
        if not slots:
            return {}
        async with session_scope() as db:
            _app, _settings, c = await _context(db, state)
            await fill(db, c, slots)
            return {"picked": [_picked_to_dict(p) for p in c.picked]}
    node.__name__ = area.value.lower()
    return node


async def _fill_personality(db, c, slots):
    await C._fill_bank_only(db, c, "PERSONALITY", slots)


async def _fill_collaboration(db, c, slots):
    await C._fill_bank_only(db, c, "COLLABORATION", slots)


async def _fill_project(db, c, slots):
    await C._fill_resume(db, c, slots)


async def _fill_motivation(db, c, slots):
    await C._fill_generated_kind(db, c, "COMPANY", slots, c.embed_model)


async def _fill_job(db, c, slots):
    await C._fill_generated_kind(db, c, "JOB", slots, c.embed_model)


async def verify(state: State) -> dict:
    picked = [_picked_from_dict(p) for p in state["picked"]]
    want = sorted(s["seq"] for s in state["plan"])
    if sorted(p.seq for p in picked) != want:
        raise C.ComposeError(f"slots {want} but questions for {sorted(p.seq for p in picked)}")
    texts = [p.text for p in sorted(picked, key=lambda x: x.seq)]
    for i, t in enumerate(texts):
        if any(C.similar_question(t, u) for u in texts[:i]):
            raise C.ComposeError(f"question {i + 1} repeats an earlier one")
    return {}


async def write(state: State) -> dict:
    app_id, settings_id = UUID(state["application_id"]), UUID(state["settings_id"])
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        settings = await db.get(PostingEvalSettings, settings_id)
        live = (await db.execute(select(QuestionSet).where(QuestionSet.application_id == app_id,
                                                           QuestionSet.status.in_(["COMPOSING", "READY"])))).scalar_one_or_none()
        if live is not None and live.settings_id == settings_id:  # an earlier attempt already wrote it
            return {"qset_id": str(live.id)}
        if live is not None:
            live.status = "SUPERSEDED"  # weights/custom question changed before the interview (start() re-enqueues)
            await db.flush()
        qset = await C.write_set(db, app, settings, [_slot_from_dict(s) for s in state["plan"]],
                                 sorted((_picked_from_dict(p) for p in state["picked"]), key=lambda p: p.seq))
        await enqueue(db, "tts_pregen", {"question_set_id": str(qset.id)}, application_id=app_id, dedupe_key=f"tts:{qset.id}")
        return {"qset_id": str(qset.id)}


AREA_NODES = (("personality", Area.PERSONALITY, _fill_personality), ("collaboration", Area.COLLABORATION, _fill_collaboration),
              ("project", Area.PROJECT, _fill_project), ("motivation", Area.MOTIVATION, _fill_motivation),
              ("job_skill", Area.JOB_SKILL, _fill_job))


def build() -> StateGraph:
    g = StateGraph(State)
    g.add_node("plan", plan)
    for name, area, fill in AREA_NODES:
        g.add_node(name, _area_node(area, fill))
    g.add_node("verify", verify)
    g.add_node("write", write)
    g.add_edge(START, "plan")
    chain = ["plan"] + [n for n, _a, _f in AREA_NODES] + ["verify", "write"]
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    g.add_edge("write", END)
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
    cfg = {"configurable": {"thread_id": thread_id(app_id, settings_id)}, "recursion_limit": 30}
    snap = await graph.aget_state(cfg)
    if snap.next:  # an earlier attempt stopped inside the run (crash, model down): carry on from that node
        log.info("compose resumes at %s", snap.next[0])
        await graph.ainvoke(None, cfg)
    else:
        await graph.ainvoke({"application_id": str(app_id), "settings_id": str(settings_id), "picked": [], "qset_id": None}, cfg)
    qset_id = UUID((await graph.aget_state(cfg)).values["qset_id"])
    await (await get_checkpointer()).adelete_thread(thread_id(app_id, settings_id))
    return qset_id

