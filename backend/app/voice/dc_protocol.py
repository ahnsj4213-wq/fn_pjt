"""Data-channel protocol v1 (TRD 06 §8, ADR-0009 §3): the single source for server<->client events.

Envelope: {"v": 1, "type", "seq", "turn_id", "server_now", ...fields}. Every event carries `turn_id` (null when not
about a turn); times are ISO 8601 UTC with milliseconds. Candidate-facing events never carry follow-up reasons,
anchors, question sources or debug fields (D-09) — `extra="forbid"` makes adding one a test failure.
`json_schemas()` feeds the frontend type generation.
"""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import utcnow

VERSION = 1


class _Ev(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hello(_Ev):
    session_id: str
    purpose: Literal["precheck", "interview"]
    resume: dict | None = None


class Question(_Ev):
    main_no: int = Field(ge=1, le=10)
    is_followup: bool
    text: str
    audio: Literal["track", "none"]
    limit_sec: Literal[60, 45]
    presentation_no: int = Field(ge=1, le=3)


class TtsEnd(_Ev):
    played: bool


class AnswerOpened(_Ev):
    opened_at: str
    deadline_at: str
    warn_at: str
    limit_sec: int


class AnswerWarn(_Ev):
    remaining_sec: int


class CaptionSegment(_Ev):
    segment_no: int
    text: str


class AnswerFinalized(_Ev):
    reason: Literal["button", "timeout"]
    text: str
    no_answer: bool


class Empty(_Ev):
    pass


class InterviewCompleted(_Ev):
    next: Literal["CODING", "SUBMITTED"]


class StatusChanged(_Ev):
    attempt_status: str
    mode: Literal["VOICE", "TEXT"]


class PrecheckListen(_Ev):
    opened_at: str
    deadline_at: str


class PrecheckResult(_Ev):
    ok: bool
    text: str
    reason: Literal["OK", "NO_SPEECH", "NOT_RECOGNIZED", "STT_UNAVAILABLE"]


class Error(_Ev):
    code: str


class Pong(_Ev):
    client_seq: int
    client_now: str | float | None = None


SERVER_EVENTS: dict[str, type[_Ev]] = {
    "hello": Hello, "question": Question, "tts_end": TtsEnd, "answer_opened": AnswerOpened, "answer_warn": AnswerWarn,
    "caption_segment": CaptionSegment, "answer_finalized": AnswerFinalized, "processing": Empty,
    "interview_completed": InterviewCompleted, "status_changed": StatusChanged, "session_superseded": Empty,
    "precheck_listen": PrecheckListen, "precheck_result": PrecheckResult, "error": Error, "pong": Pong,
}
CLIENT_EVENTS = ("ping", "client_ready")


def iso(t: datetime) -> str:
    """ISO 8601 UTC with milliseconds and a Z suffix (naive datetimes are taken as UTC)."""
    t = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def envelope(type_: str, seq: int, turn_id: object | None, **fields) -> dict:
    """Validated event dict. Unknown types or fields raise (never send what the protocol does not define)."""
    model = SERVER_EVENTS[type_]
    body = model(**fields).model_dump()
    return {"v": VERSION, "type": type_, "seq": seq, "turn_id": str(turn_id) if turn_id else None,
            "server_now": iso(utcnow()), **body}


def json_schemas() -> dict:
    return {name: model.model_json_schema() for name, model in SERVER_EVENTS.items()}
