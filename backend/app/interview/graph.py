"""Graph 1, the AI interview (LangGraph, ADR-0016, revised 2026-11-11): ONE graph per application, `thread_id` = the application
id, checkpoints in PostgreSQL. It composes the questions, runs the interview and opens and closes the coding test.

    START -> start -+-(COMPOSE)--> plan -> project -> motivation -> job_skill -> write -> END        (the questions are ready)
                    |
                    +-(INTERVIEW)-> compose_next -> present -> ask -> wait_answer -(interrupt)-> transcribe -> decide_followup
                    |                   ^             |  |                |                                        |
                    |                   |             |  +-> END          +-> present (the connection dropped:     |
                    |                   |             |      (attempt       show it again)                         |
                    |                   |             |       closed)                                              |
                    |                   +-------------|--------------------------------------------------------- --+
                    |                                 +-> finish -> END     (coding test: CODING; else SUBMITTED)
                    |                 compose_next --+-> present (next question or follow-up) | finish (all done) | END (text mode)
                    |
                    +-(SUBMIT)----> coding_submit -> END      (the final coding submission: snapshot + grading + evaluation)
                    +-(nothing to do)-> END

Entries (the caller says which; `start` checks the database and may end the run at once):
  COMPOSE    the `compose_questions` job (`compose()`): the composition steps of questions/steps.py. A set already written for
             this settings version ends the run with its id.
  INTERVIEW  the voice session or the text endpoints (`begin()`, `resume()`, `ensure_waiting()`).
  SUBMIT     the coding router's final submission (`submit_coding()`), in the router's transaction.
Nodes of the interview:
  compose_next     live composition (ADR-0022): the next main question is composed from the answers so far when the interview
                   has reached it (questions/live.ensure_next; nothing to do for an upfront set). After an answer it also picks
                   the route: the follow-up, the next main question, or the end. The prefetch (composing the next main question
                   while the current one is answered, for speed) stays a background task started by `present`.
  present          `turns.present_next`: the next question (a follow-up if one was asked) as a new turn.
  ask              the transport shows it: voice = question event + TTS + think time; text = nothing.
  wait_answer      `interrupt()`: the graph stops here while the candidate answers. The runtime (voice session or the text
                   endpoint) resumes it with how the answer window ended (`BUTTON`/`TIMEOUT`/...), or with `abandoned`.
  transcribe       the transport hands over what was heard (STT text, audio) and the answer is confirmed once.
  decide_followup  T4 (rules first, then the model within its time limit, otherwise skip), decided once per answer.
  finish           `turns.finish_interview`: the coding test begins (CODING) or the submission + evaluation job, atomically.
  coding_submit    the coding test ends: the final snapshot, the grading job and the evaluation that waits for it
                   (coding.router.submit_snapshot). Running and grading code stay in app.coding (runner, sandbox, grading);
                   the automatic submission at the deadline (`coding_autosubmit`) and a request without the graph use the same
                   function directly, so a lost checkpoint changes nothing (SUBMIT needs none: `start` reads the database).
Every fork is decided by code; no model picks the next step. The transport (audio, timers, STT) and the coding submission are not
part of the graph's state: they are passed in `config["configurable"]` as objects (a `TurnIO`, a `CodingSubmit`; objects are never
copied into the checkpoint metadata, plain strings would be). The state holds ids, flags and, while composing, the questions
picked so far (no answer, no code, no resume text).
"""
import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Protocol, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import log_event
from app.interview import turns
from app.interview.checkpoint import get_checkpointer
from app.models import Application, InterviewAnswer, QuestionSet
from app.questions import live, steps

log = logging.getLogger("app.interview")

WAIT_NODE = "wait_answer"
COMPOSE, INTERVIEW, SUBMIT = "COMPOSE", "INTERVIEW", "SUBMIT"


class State(TypedDict, total=False):
    application_id: str
    entry: str  # COMPOSE | INTERVIEW | SUBMIT, from the caller
    stage: str | None  # what `start` decided: COMPOSE | INTERVIEW | SUBMIT | END
    # composition
    settings_id: str | None
    live: bool  # only the first question now; compose_next makes the others during the interview (ADR-0022)
    picked: list[dict]  # questions/compose.Picked as plain data, until `write`
    qset_id: str | None
    # interview
    mode: str  # VOICE | TEXT
    turn: dict | None  # the turn being asked (TurnView as plain data)
    signal: str | None  # ENDED (the answer window ended) | ABANDONED (the connection dropped)
    end_reason: str | None
    answer_id: str | None
    answered: bool  # set by decide_followup: compose_next picks the route after an answer
    route: str | None  # FOLLOWUP | NEXT | DONE | PRESENT, set by compose_next
    result: str | None  # CODING | SUBMITTED | ALREADY_SUBMITTED | NOT_CODING | TEXT_MODE_REQUIRED | ENDED once the run is over


@dataclass
class Capture:
    """What the transport heard for one answer. Lives in memory only (never in the checkpoint)."""
    transcript: str = ""
    stt_failed: bool = False
    audio_key: str | None = None
    audio_ms: int | None = None
    stt_model: str | None = None


class TurnIO(Protocol):
    async def ask(self, view: turns.TurnView) -> None: ...
    async def capture(self, turn_id: UUID, reason: str) -> Capture: ...
    async def finalized(self, turn_id: UUID, answer: Any, reason: str, capture: Capture) -> None: ...


class TextIO:
    """Text mode transport: nothing is shown or heard by the server; the answer text comes with the request."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    async def ask(self, view: turns.TurnView) -> None:
        return None

    async def capture(self, turn_id: UUID, reason: str) -> Capture:
        return Capture(transcript=self.text)

    async def finalized(self, turn_id: UUID, answer: Any, reason: str, capture: Capture) -> None:
        return None


class CodingSubmit:
    """The final coding submission handed to `coding_submit`: the router's session (its transaction holds the application lock,
    so the snapshot is written atomically with the draft) and the code. An object, so nothing of it reaches the checkpoint."""

    def __init__(self, db, code: str, kind: str) -> None:
        self.db, self.code, self.kind = db, code, kind


_locks: dict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def app_lock(app_id: UUID):
    """One graph step at a time per application inside this process (a double-clicked [제출] must not run it twice)."""
    async with _locks[app_id]:
        yield


def view_to_dict(v: turns.TurnView) -> dict:
    return {k: (str(x) if isinstance(x, UUID) else x) for k, x in asdict(v).items()}


def view_from_dict(d: dict) -> turns.TurnView:
    return turns.TurnView(**{**d, "turn_id": UUID(d["turn_id"]), "question_id": UUID(d["question_id"])})


def _io(config) -> TurnIO:
    return config["configurable"]["io"]


# ── entry ──────────────────────────────────────────────────────────────────────
async def start(state: State) -> dict:
    """Which stage this run is: decided by code from the entry and the database (a short read)."""
    entry, app_id = state.get("entry") or INTERVIEW, UUID(state["application_id"])
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        if app is None:
            return {"stage": END, "result": "ENDED"}
        if entry == COMPOSE:
            done = (await db.execute(select(QuestionSet.id).where(
                QuestionSet.application_id == app_id, QuestionSet.settings_id == UUID(state["settings_id"]),
                QuestionSet.status.in_(["COMPOSING", "READY"])))).scalar_one_or_none()
            return {"stage": END, "qset_id": str(done)} if done is not None else {"stage": COMPOSE}
        if entry == SUBMIT:
            return {"stage": SUBMIT} if app.attempt_status == "CODING" else {"stage": END, "result": "NOT_CODING"}
    return {"stage": INTERVIEW}


def after_start(state: State) -> str:
    return {COMPOSE: steps.CHAIN[0], INTERVIEW: "compose_next", SUBMIT: "coding_submit"}.get(state.get("stage"), END)


# ── interview nodes ────────────────────────────────────────────────────────────
async def compose_next(state: State) -> dict:
    app_id = UUID(state["application_id"])
    await live.ensure_next(app_id)  # live composition: the next main question is made now, from the answers so far (ADR-0022)
    if not state.get("answered"):
        return {"route": "PRESENT"}  # the run (re)starts: show the next question
    async with session_scope() as db:
        nxt = await turns.next_question(db, app_id)
    route = "DONE" if nxt is None else "FOLLOWUP" if nxt.role == "FOLLOWUP" else "NEXT"
    return {"route": route, "answered": False}


async def present(state: State) -> dict:
    app_id = UUID(state["application_id"])
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        if app is None or app.attempt_status != "INTERVIEWING":
            return {"turn": None, "route": None, "result": "ENDED"}  # expired or cancelled: the run is over
        try:
            view = await turns.present_next(db, app_id, input_mode=state["mode"])
        except turns.TextModeRequired:
            return {"turn": None, "route": None, "result": "TEXT_MODE_REQUIRED"}
    if view is None:
        return {"turn": None, "route": "DONE", "result": None}
    if not view.is_followup and get_settings().question_mode == "live":
        live.prefetch(app_id)  # the next main question is composed while this one is answered
    return {"turn": view_to_dict(view), "route": None, "signal": None, "answer_id": None, "result": None}


async def ask(state: State, config) -> dict:
    await _io(config).ask(view_from_dict(state["turn"]))
    return {}


async def wait_answer(state: State) -> dict:
    # Nothing with side effects may come before interrupt(): the node runs again from the top when it is resumed.
    value = interrupt({"turn_id": state["turn"]["turn_id"]})
    if value.get("kind") == "ended":
        return {"signal": "ENDED", "end_reason": value.get("reason") or "BUTTON"}
    return {"signal": "ABANDONED", "end_reason": None}


async def transcribe(state: State, config) -> dict:
    io, turn_id = _io(config), UUID(state["turn"]["turn_id"])
    reason = state["end_reason"] or "BUTTON"
    cap = await io.capture(turn_id, reason)
    async with session_scope() as db:
        answer = await turns.finalize(db, turn_id, end_reason=reason, transcript=cap.transcript, stt_failed=cap.stt_failed,
                                      audio_key=cap.audio_key, audio_ms=cap.audio_ms, stt_model=cap.stt_model)
        # Confirmed already (this step ran again after a crash between finalize and the checkpoint): the stored answer still
        # gets its follow-up decision, which is itself decided once (turns.decide_followup).
        answer_id = answer.id if answer is not None else await db.scalar(
            select(InterviewAnswer.id).where(InterviewAnswer.turn_id == turn_id))
    await io.finalized(turn_id, answer, reason, cap)
    return {"answer_id": str(answer_id) if answer_id is not None else None}


async def decide_followup(state: State) -> dict:
    app_id = UUID(state["application_id"])
    if state.get("answer_id") and not state["turn"]["is_followup"]:
        await turns.decide_followup(app_id, UUID(state["answer_id"]))
    return {"answered": True, "turn": None, "answer_id": None}


async def finish(state: State) -> dict:
    async with session_scope() as db:
        status = await turns.finish_interview(db, UUID(state["application_id"]))
    return {"result": "CODING" if status == "CODING" else "SUBMITTED", "turn": None}


async def coding_submit(state: State, config) -> dict:
    from app.coding.router import submit_snapshot  # the coding router calls this graph

    sub: CodingSubmit = config["configurable"]["coding"]
    app = await sub.db.get(Application, UUID(state["application_id"]))
    if app is None or app.attempt_status != "CODING":
        return {"result": "NOT_CODING"}
    done = await submit_snapshot(sub.db, app, sub.code, sub.kind)
    return {"result": "SUBMITTED" if done else "ALREADY_SUBMITTED"}


# ── edges (all decided by code) ────────────────────────────────────────────────
def after_compose_next(state: State) -> str:
    route = state.get("route")
    if route == "PRESENT":
        return "present"
    if state.get("mode") == "TEXT":
        return END  # text mode: the client asks for the next question itself (POST …/interview/next)
    return "finish" if route == "DONE" else "present"


def after_present(state: State) -> str:
    if state.get("result"):
        return END
    return "ask" if state.get("turn") else "finish"


def after_wait(state: State) -> str:
    return "transcribe" if state.get("signal") == "ENDED" else "present"


def build() -> StateGraph:
    g = StateGraph(State)
    g.add_node("start", start)
    for name, fn in steps.nodes():
        g.add_node(name, fn)
    for name, fn in (("compose_next", compose_next), ("present", present), ("ask", ask), (WAIT_NODE, wait_answer),
                     ("transcribe", transcribe), ("decide_followup", decide_followup), ("finish", finish),
                     ("coding_submit", coding_submit)):
        g.add_node(name, fn)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", after_start, {steps.CHAIN[0]: steps.CHAIN[0], "compose_next": "compose_next",
                                                   "coding_submit": "coding_submit", END: END})
    for a, b in zip(steps.CHAIN, steps.CHAIN[1:] + [END]):
        g.add_edge(a, b)
    g.add_conditional_edges("compose_next", after_compose_next, {"present": "present", "finish": "finish", END: END})
    g.add_conditional_edges("present", after_present, {"ask": "ask", "finish": "finish", END: END})
    g.add_edge("ask", WAIT_NODE)
    g.add_conditional_edges(WAIT_NODE, after_wait, {"transcribe": "transcribe", "present": "present"})
    g.add_edge("transcribe", "decide_followup")
    g.add_edge("decide_followup", "compose_next")
    g.add_edge("finish", END)
    g.add_edge("coding_submit", END)
    return g


_compiled = None


async def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build().compile(checkpointer=await get_checkpointer())
    return _compiled


def reset_graph() -> None:
    global _compiled
    _compiled = None


def thread_id(app_id: UUID) -> str:
    return str(app_id)


# ── running it ─────────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    waiting: bool  # True: stopped at wait_answer for `view`; False: the run is over with `result`
    view: turns.TurnView | None = None
    result: str | None = None  # CODING | SUBMITTED | TEXT_MODE_REQUIRED | ENDED | (text mode: None = handed back)


_FRESH = {"stage": None, "settings_id": None, "live": False, "picked": [], "qset_id": None, "turn": None, "signal": None,
          "end_reason": None, "answer_id": None, "answered": False, "route": None, "result": None}


def _cfg(app_id: UUID, io: TurnIO | None = None, **extra) -> dict:
    return {"configurable": {"thread_id": thread_id(app_id), "io": io, **extra}, "recursion_limit": 100}


async def _forget(app_id: UUID) -> None:
    await (await get_checkpointer()).adelete_thread(thread_id(app_id))


async def _outcome(graph, app_id: UUID, cfg: dict) -> Outcome:
    snap = await graph.aget_state(cfg)
    vals = snap.values or {}
    if WAIT_NODE in (snap.next or ()):
        return Outcome(True, view_from_dict(vals["turn"]))
    out = Outcome(False, None, vals.get("result"))
    if out.result in ("CODING", "SUBMITTED", "ENDED"):
        await _forget(app_id)  # the run is over: nothing left to resume
    return out


async def compose(app_id: UUID, settings_id: UUID) -> UUID:
    """The COMPOSE entry (the `compose_questions` job): compose and write the question set; returns its id. A run left by a failed
    attempt for the same settings version continues at the node that stopped."""
    graph, cfg = await get_graph(), _cfg(app_id)
    cfg["recursion_limit"] = 20
    snap = await graph.aget_state(cfg)
    vals = snap.values or {}
    t0 = time.monotonic()
    if snap.next and vals.get("entry") == COMPOSE and vals.get("settings_id") == str(settings_id):
        log.info("compose resumes at %s", snap.next[0])  # an earlier attempt stopped inside the run (crash, model down)
        final = await graph.ainvoke(None, cfg)
    else:
        final = await graph.ainvoke({**_FRESH, "application_id": str(app_id), "entry": COMPOSE, "settings_id": str(settings_id)}, cfg)
    log_event(log, "compose_stage", application_id=str(app_id), stage="total", duration_ms=int((time.monotonic() - t0) * 1000))
    await _forget(app_id)
    return UUID(final["qset_id"])


async def begin(app_id: UUID, mode: str, io: TurnIO) -> Outcome:
    """Start (or, after a dropped connection, restart) the interview: a fresh input always begins at START, so an interrupted
    thread's pending turn is simply shown again as a new presentation (the turn engine abandons the old one)."""
    graph, cfg = await get_graph(), _cfg(app_id, io)
    await graph.ainvoke({**_FRESH, "application_id": str(app_id), "entry": INTERVIEW, "mode": mode}, cfg)
    return await _outcome(graph, app_id, cfg)


async def resume(app_id: UUID, reason: str, io: TurnIO) -> Outcome:
    """The answer window of the waiting turn ended (`reason`): transcribe, decide the follow-up, go on."""
    graph, cfg = await get_graph(), _cfg(app_id, io)
    await graph.ainvoke(Command(resume={"kind": "ended", "reason": reason}), cfg)
    return await _outcome(graph, app_id, cfg)


async def ensure_waiting(app_id: UUID, view: turns.TurnView, mode: str, io: TurnIO) -> None:
    """Make the thread wait at `wait_answer` for this turn. Normally it already does; an interview that began before the
    graph existed (or a lost checkpoint) is put there from the database state of its live turn."""
    graph, cfg = await get_graph(), _cfg(app_id, io)
    snap = await graph.aget_state(cfg)
    if WAIT_NODE in (snap.next or ()) and (snap.values or {}).get("turn", {}).get("turn_id") == str(view.turn_id):
        return
    await graph.aupdate_state(cfg, {**_FRESH, "application_id": str(app_id), "entry": INTERVIEW, "stage": INTERVIEW, "mode": mode,
                                    "turn": view_to_dict(view)}, as_node="ask")
    await graph.ainvoke(None, cfg)  # runs wait_answer up to its interrupt
    log_event(log, "interview_graph_resynced", application_id=str(app_id))


async def submit_coding(db, app_id: UUID, code: str, kind: str) -> str:
    """The SUBMIT entry: the coding test's final submission in the caller's transaction (`db` holds the application lock).
    Returns SUBMITTED | ALREADY_SUBMITTED | NOT_CODING. The checkpoints of the run are deleted with it."""
    graph, cfg = await get_graph(), _cfg(app_id, coding=CodingSubmit(db, code, kind))
    final = await graph.ainvoke({**_FRESH, "application_id": str(app_id), "entry": SUBMIT}, cfg)
    await _forget(app_id)
    return final.get("result") or "NOT_CODING"
