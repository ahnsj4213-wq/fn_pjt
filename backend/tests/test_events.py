"""ADR-0013 real-time notifications: state change -> relay -> Redis pub/sub -> SSE stream. Needs Redis."""
import asyncio
import json

import pytest

from app.core.redis import get_redis
from app.events.relay import LAST_KEY, company_channel, relay_once, user_channel
from app.events.router import event_stream
from tests.evalstate import to_eval_done
from tests.test_handlers import setup_app


@pytest.fixture(autouse=True)
async def need_redis():
    r = get_redis()
    try:
        await r.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis is not running")
    return r


async def _next_message(pubsub, timeout: float = 3.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if msg:
            return json.loads(msg["data"])
    raise AssertionError("no message")


async def test_state_changes_reach_the_candidate_and_the_company(client, make_client, need_redis):
    company_id, app_id, cand = await setup_app(client, make_client)
    me = (await cand.get("/api/auth/me")).json()["user"]
    assert await relay_once("w1") == 0  # first run starts from the newest row: no history replay
    assert await need_redis.get(LAST_KEY) is not None
    sub = need_redis.pubsub()
    await sub.subscribe(user_channel(me["id"]), company_channel(company_id))
    await to_eval_done(app_id)  # several EVAL transitions through the DB trigger
    assert await relay_once("w2") == 0  # another worker holds the relay lease
    n = await relay_once("w1")
    assert n >= 1
    got = [await _next_message(sub) for _ in range(2 * n)]  # one per channel per change
    assert {"type": "application_changed", "application_id": app_id} in got  # candidate: no state values (SEC-04)
    assert any(m.get("machine") == "EVAL" and m["application_id"] == app_id for m in got)
    assert await relay_once("w1") == 0  # nothing new
    await sub.aclose()


async def test_sse_stream_formats_events_and_keepalives(need_redis):
    sub = need_redis.pubsub()
    await sub.subscribe("aihire:ev:user:test")

    async def connected() -> bool:
        return False

    gen = event_stream(sub, connected, max_sec=5, keepalive_sec=0.2)
    assert await gen.__anext__() == "retry: 3000\n\n"
    assert await gen.__anext__() == ": keepalive\n\n"
    await need_redis.publish("aihire:ev:user:test", json.dumps({"type": "application_changed", "application_id": "a1"}))
    chunk = await gen.__anext__()
    while chunk.startswith(":"):
        chunk = await gen.__anext__()
    assert chunk == 'event: application\ndata: {"type": "application_changed", "application_id": "a1"}\n\n'
    await gen.aclose()


async def test_stream_requires_a_session(client):
    r = await client.get("/api/events/stream")
    assert r.status_code == 401
