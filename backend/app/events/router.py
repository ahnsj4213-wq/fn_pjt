"""`GET /api/events/stream`: Server-Sent Events for the signed-in user (ADR-0013).

Candidates get their own applications' changes, recruiters their company's. One event type, `application`, whose
data says which application changed; the screen refetches it. A stream lasts at most STREAM_MAX_SEC and the browser
reconnects on its own (EventSource), which re-checks the session each time. The session lookup uses its own short
DB session so a long stream never holds a database connection.
"""
import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.auth.sessions import load_session
from app.core.config import get_settings
from app.core.db import sessionmaker
from app.core.errors import AppError
from app.core.redis import get_redis
from app.events.relay import company_channel, user_channel

router = APIRouter(prefix="/api/events", tags=["common"])
STREAM_MAX_SEC = 600
KEEPALIVE_SEC = 15


async def event_stream(pubsub, is_disconnected: Callable[[], Awaitable[bool]], *, max_sec: float = STREAM_MAX_SEC,
                       keepalive_sec: float = KEEPALIVE_SEC) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    deadline = time.monotonic() + max_sec
    try:
        while time.monotonic() < deadline and not await is_disconnected():
            msg = await pubsub.get_message(ignore_subscribe_messages=True,
                                           timeout=min(keepalive_sec, max(0.0, deadline - time.monotonic())))
            yield f"event: application\ndata: {msg['data']}\n\n" if msg else ": keepalive\n\n"
    finally:
        await pubsub.aclose()


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    async with sessionmaker()() as db:
        p = await load_session(db, request.cookies.get(get_settings().cookie_name))
    if p is None:
        raise AppError(401, "UNAUTHENTICATED", "로그인이 필요합니다.")
    r = get_redis()
    if r is None:
        raise AppError(503, "REALTIME_UNAVAILABLE", "실시간 알림을 사용할 수 없습니다.")
    channel = company_channel(p.company_id) if p.role == "RECRUITER" else user_channel(p.user_id)
    pubsub = r.pubsub()
    try:
        await asyncio.wait_for(pubsub.subscribe(channel), timeout=3)
    except Exception:  # noqa: BLE001 - Redis down: the screen keeps working without live updates
        await pubsub.aclose()
        raise AppError(503, "REALTIME_UNAVAILABLE", "실시간 알림을 사용할 수 없습니다.") from None
    return StreamingResponse(event_stream(pubsub, request.is_disconnected), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
