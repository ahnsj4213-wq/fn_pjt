"""The interview progress graph (LangGraph, ADR-0016, ai-architecture 6.1): one graph per application, `thread_id` = the
application id, checkpoints in PostgreSQL.

    START -> present -> ask -> wait_answer -(interrupt)-> transcribe -> decide_followup -+-> present        (next question
                 |                 |                                                    |                  or follow-up)
                 +-> finish        +-> present   (the connection dropped: show it again) +-> finish -> END   (all questions done)
                 +-> END           (attempt closed / text mode required)               +-> END             (text mode hands
                                                                                                           control back)
Nodes:
  present          `turns.present_next`: the next question (a follow-up if one was asked) as a new turn.
  ask              the transport shows it: voice = question event + TTS + think time; text = nothing.
  wait_answer      `interrupt()`: the graph stops here while the candidate answers. The runtime (voice session or the text
                   endpoint) resumes it with how the answer window ended (`BUTTON`/`TIMEOUT`/...), or with `abandoned`.
  transcribe       the transport hands over what was heard (STT text, audio) and the answer is confirmed once.
  decide_followup  T4 (rules first, then the model within its time limit, otherwise skip), then (live composition) the next main
                   question is composed from the answers so far, then the code picks the edge.
  finish           `turns.finish_interview`: the coding test or submission + evaluation job, atomically.
Every fork is decided by code; no model picks the next step. The transport (audio, timers, STT) is not part of the graph's
state: it is passed as `config["configurable"]["io"]` (a `TurnIO`). The state holds ids and small flags only, never an answer.
"""
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Protocol, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.core.db import session_scope
from app.core.logging import log_event
from app.interview import turns
from app.interview.checkpoint import get_checkpointer
from app.models import Application
from app.questions import live

log = logging.getLogger("app.interview")

WAIT_NODE = "wait_answer"


class State(TypedDict, total=False):
    application_id: str
    mode: str  # VOICE | TEXT
    turn: dict | None  # the turn being asked (TurnView as plain data)
    signal: str | None  # ENDED (the answer window ended) | ABANDONED (the connection dropped)
    end_reason: str | None
    answer_id: str | None
    route: str | None  # FOLLOWUP | NEXT | DONE, set by decide_followup
    result: str | None  # CODING | SUBMITTED | TEXT_MODE_REQUIRED | ENDED once the run is over


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


# ── nodes ──────────────────────────────────────────────────────────────────────
async def present(state: State) -> dict:
    app_id = UUID(state["application_id"])
    await live.ensure_next(app_id)  # live composition: the next main question is made now, from the answers so far (ADR-0022)
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
    await io.finalized(turn_id, answer, reason, cap)
    return {"answer_id": str(answer.id) if answer is not None else None}


async def decide_followup(state: State) -> dict:
    app_id = UUID(state["application_id"])
    if state.get("answer_id") and not state["turn"]["is_followup"]:
        await turns.decide_followup(app_id, UUID(state["answer_id"]))
    await live.ensure_next(app_id)  # no follow-up: the next main question is composed before the route is decided
    async with session_scope() as db:
        nxt = await turns.next_question(db, app_id)
    route = "DONE" if nxt is None else "FOLLOWUP" if nxt.role == "FOLLOWUP" else "NEXT"
    return {"route": route, "turn": None, "answer_id": None}


async def finish(state: State) -> dict:
    async with session_scope() as db:
        status = await turns.finish_interview(db, UUID(state["application_id"]))
    return {"result": "CODING" if status == "CODING" else "SUBMITTED", "turn": None}


# ── edges (all decided by code) ────────────────────────────────────────────────
def after_present(state: State) -> str:
    if state.get("result"):
        return END
    return "ask" if state.get("turn") else "finish"


def after_wait(state: State) -> str:
    return "transcribe" if state.get("signal") == "ENDED" else "present"


def after_decide(state: State) -> str:
    if state.get("mode") == "TEXT":
        return END  # text mode: the client asks for the next question itself (POST …/interview/next)
    return "finish" if state.get("route") == "DONE" else "present"


def build() -> StateGraph:
    g = StateGraph(State)
    for name, fn in (("present", present), ("ask", ask), (WAIT_NODE, wait_answer), ("transcribe", transcribe),
                     ("decide_followup", decide_followup), ("finish", finish)):
        g.add_node(name, fn)
    g.add_edge(START, "present")
    g.add_conditional_edges("present", after_present, {"ask": "ask", "finish": "finish", END: END})
    g.add_edge("ask", WAIT_NODE)
    g.add_conditional_edges(WAIT_NODE, after_wait, {"transcribe": "transcribe", "present": "present"})
    g.add_edge("transcribe", "decide_followup")
    g.add_conditional_edges("decide_followup", after_decide, {"finish": "finish", "present": "present", END: END})
    g.add_edge("finish", END)
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


# ── running it ─────────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    waiting: bool  # True: stopped at wait_answer for `view`; False: the run is over with `result`
    view: turns.TurnView | None = None
    result: str | None = None  # CODING | SUBMITTED | TEXT_MODE_REQUIRED | ENDED | (text mode: None = handed back)


def _cfg(app_id: UUID, io: TurnIO) -> dict:
    return {"configurable": {"thread_id": str(app_id), "io": io}, "recursion_limit": 100}


async def _outcome(graph, app_id: UUID, cfg: dict) -> Outcome:
    snap = await graph.aget_state(cfg)
    vals = snap.values or {}
    if WAIT_NODE in (snap.next or ()):
        return Outcome(True, view_from_dict(vals["turn"]))
    out = Outcome(False, None, vals.get("result"))
    if out.result in ("CODING", "SUBMITTED", "ENDED"):
        await (await get_checkpointer()).adelete_thread(str(app_id))  # the run is over: nothing left to resume
    return out


async def begin(app_id: UUID, mode: str, io: TurnIO) -> Outcome:
    """Start (or, after a dropped connection, restart) the run at `present`: a fresh input always begins at START, so an
    interrupted thread's pending turn is simply shown again as a new presentation (the turn engine abandons the old one)."""
    graph, cfg = await get_graph(), _cfg(app_id, io)
    await graph.ainvoke({"application_id": str(app_id), "mode": mode, "turn": None, "signal": None, "end_reason": None,
                         "answer_id": None, "route": None, "result": None}, cfg)
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
    await graph.aupdate_state(cfg, {"application_id": str(app_id), "mode": mode, "turn": view_to_dict(view), "signal": None,
                                    "end_reason": None, "answer_id": None, "route": None, "result": None}, as_node="ask")
    await graph.ainvoke(None, cfg)  # runs wait_answer up to its interrupt
    log_event(log, "interview_graph_resynced", application_id=str(app_id))
