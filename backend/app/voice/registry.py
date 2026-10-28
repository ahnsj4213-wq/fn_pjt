"""Live voice sessions of this `app` process (one uvicorn worker, ADR-0001): at most one per application.

Used by the interview router: `request_finish` ([답변 완료], C3) and `last_seen_connected` (text-mode rule, T12).
"""
from datetime import datetime
from uuid import UUID

from app.core.timeutil import utcnow
from app.voice.session import VoiceSession

MAX_PRECHECK_SESSIONS = 4  # C4: server-wide cap on precheck connections

_sessions: dict[UUID, VoiceSession] = {}
_last_seen: dict[UUID, datetime] = {}


class PrecheckBusy(Exception):
    pass


def _closed(s: VoiceSession) -> None:
    if _sessions.get(s.app_id) is s:
        del _sessions[s.app_id]


def get(application_id: UUID) -> VoiceSession | None:
    return _sessions.get(application_id)


def mark_seen(application_id: UUID) -> None:
    _last_seen[application_id] = utcnow()


def last_seen_connected(application_id: UUID) -> datetime | None:
    s = _sessions.get(application_id)
    if s is not None and not s.closed and s.pc.connectionState == "connected":
        mark_seen(application_id)
    return _last_seen.get(application_id)


def precheck_count() -> int:
    return sum(1 for s in _sessions.values() if s.purpose == "precheck" and not s.closed)


async def open_session(application_id: UUID, purpose: str, offer_sdp: str) -> tuple[str, str]:
    """New session for the application; an older one gets `session_superseded` and closes. Returns (answer, id)."""
    old = _sessions.get(application_id)
    if purpose == "precheck" and (old is None or old.purpose != "precheck") and precheck_count() >= MAX_PRECHECK_SESSIONS:
        raise PrecheckBusy()
    if old is not None:
        await old.supersede()
    s = VoiceSession(application_id, purpose, _closed)
    _sessions[application_id] = s
    try:
        answer = await s.accept(offer_sdp)
    except Exception:
        await s.close("BAD_OFFER")
        raise
    return answer, s.id


async def request_finish(application_id: UUID, turn_id: UUID) -> bool:
    s = _sessions.get(application_id)
    return s is not None and not s.closed and s.request_finish(turn_id)


async def close_all() -> None:
    for s in list(_sessions.values()):
        await s.close("SHUTDOWN")
