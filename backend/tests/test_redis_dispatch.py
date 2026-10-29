"""ADR-0013: job hand-off through Redis on top of the PostgreSQL outbox. Needs a Redis server (skips otherwise)."""
import asyncio

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.core.redis import get_redis, set_redis
from app.jobs import dispatch, queue, runtime, worker

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def need_redis():
    r = get_redis()
    try:
        await r.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis is not running")
    queue.JOB_KINDS["t_high"] = ("LLM", 20, 2)
    queue.JOB_KINDS["t_low"] = ("LLM", 60, 2)

    async def noop(ctx) -> None:
        return None

    for k in ("t_high", "t_low"):
        worker.HANDLERS[k] = noop  # lanes only claim kinds this worker can run
    yield r
    for k in ("t_high", "t_low"):
        queue.JOB_KINDS.pop(k, None)
        worker.HANDLERS.pop(k, None)


async def _enqueue(kind: str, **kw) -> int:
    async with session_scope() as db:
        return await queue.enqueue(db, kind, {}, **kw)


async def _q(sql: str, **p):
    async with session_scope() as db:
        res = await db.execute(text(sql), p)
        return res.all() if res.returns_rows else None


async def test_dispatch_keeps_sql_order_and_claims_the_popped_id(need_redis):
    low = await _enqueue("t_low")
    high = await _enqueue("t_high")
    assert await dispatch.dispatch_due() == 2
    assert await dispatch.dispatch_due() == 0  # handed off already: nothing re-sent within 30 s
    assert await dispatch.pop("LLM", 1) == high  # priority first, like the SQL claim
    ctx = await worker.claim(["LLM"], "w1", job_id=high)
    assert ctx is not None and ctx.id == high
    assert await worker.claim(["LLM"], "w2", job_id=high) is None  # a duplicate id is harmless
    assert await dispatch.pop("LLM", 1) == low
    assert await dispatch.pop("LLM", 0.1) is None


async def test_dependency_and_delayed_retry_are_sent_only_when_due(need_redis):
    first = await _enqueue("t_high")
    after = await _enqueue("t_low", depends_on=first)
    assert await dispatch.dispatch_due() == 1  # `after` waits for `first` (A7: evaluation waits for grading)
    ctx = await worker.claim(["LLM"], "w1", job_id=await dispatch.pop("LLM", 1))
    await worker._requeue(ctx, consume=False, error_code="LLM_DOWN", delay_sec=60)  # infra deferral, not due yet
    assert await dispatch.dispatch_due() == 0
    # 60 s later (moved back instead of waiting): the retry is due and its last hand-off predates it.
    await _q("UPDATE jobs SET run_after = now() - interval '1 second', dispatched_at = now() - interval '61 seconds' "
             "WHERE id = :i", i=first)
    assert await dispatch.dispatch_due() == 1  # due again -> sent again
    ctx = await worker.claim(["LLM"], "w1", job_id=await dispatch.pop("LLM", 1))
    async with session_scope() as db:
        await worker.fenced_complete(db, ctx)
    assert await dispatch.dispatch_due() == 1
    assert await dispatch.pop("LLM", 1) == after


async def test_lost_redis_data_is_resent_from_the_outbox(need_redis):
    job = await _enqueue("t_high")
    assert await dispatch.dispatch_due() == 1
    await need_redis.flushdb()  # Redis restarted without persistence
    assert await dispatch.dispatch_due() == 0
    await _q("UPDATE jobs SET dispatched_at = now() - interval '31 seconds' WHERE id = :i", i=job)
    assert await dispatch.dispatch_due() == 1
    assert await dispatch.pop("LLM", 1) == job


async def test_lanes_fall_back_to_sql_when_redis_is_down(need_redis):
    import redis.asyncio as aioredis

    job = await _enqueue("t_high")
    set_redis(aioredis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5))
    try:
        with pytest.raises(Exception):  # noqa: B017 - any connection error
            await dispatch.dispatch_due()
        assert (await _q("SELECT dispatched_at FROM jobs WHERE id = :i", i=job)) == [(None,)]  # hand-off undone
        ctx = await runtime._next_job("LLM", "w1", asyncio.Event(), 0.1)
        assert ctx is not None and ctx.id == job
    finally:
        set_redis(None)
