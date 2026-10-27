"""Durable queue semantics behind AC-18, EVL-06 and R-14, using a test-only job kind."""
import asyncio

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.jobs import queue, runtime, worker

pytestmark = pytest.mark.integration
KIND = "test_llm"


@pytest.fixture(autouse=True)
def test_kind():
    queue.JOB_KINDS[KIND] = ("LLM", 50, 2)
    calls: list[int] = []
    yield calls
    for reg in (worker.HANDLERS, worker.ON_EXHAUSTED, queue.JOB_KINDS):
        reg.pop(KIND, None)


async def _enqueue() -> int:
    async with session_scope() as db:
        return await queue.enqueue(db, KIND, {"x": 1}, dedupe_key=f"t:{asyncio.get_running_loop().time()}")


async def _job(job_id: int):
    async with session_scope() as db:
        return (await db.execute(text("SELECT state, attempts, infra_deferrals, last_error_code FROM jobs WHERE id=:i"),
                                 {"i": job_id})).one()


async def _run_until(stop_when, probe_state: dict, timeout: float = 15.0) -> None:
    stop = asyncio.Event()

    async def probe() -> bool:
        return probe_state["up"]

    async def none_probe() -> bool:
        return True

    task = asyncio.create_task(runtime.run(stop, probes={"LLM": probe, "NONE": none_probe},
                                           lanes={"LLM": 1}, idle_sec=0.1, periodic_tick=60))
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while not await stop_when():
            assert asyncio.get_running_loop().time() < deadline, "timed out"
            await asyncio.sleep(0.1)
    finally:
        stop.set()
        await task


async def test_ac18_llm_down_keeps_job_queued_then_completes(test_kind):
    @worker.handler(KIND)
    async def ok(ctx):
        async with session_scope() as db:
            await worker.fenced_complete(db, ctx)

    job_id = await _enqueue()
    probe = {"up": False}
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.run(stop, probes={"LLM": lambda: _const(probe["up"])}, lanes={"LLM": 1},
                                           idle_sec=0.1, periodic_tick=60))
    await asyncio.sleep(1.0)
    state = await _job(job_id)
    assert state.state == "QUEUED" and state.attempts == 0  # server down: nothing consumed
    probe["up"] = True
    for _ in range(100):
        if (await _job(job_id)).state == "SUCCEEDED":
            break
        await asyncio.sleep(0.1)
    stop.set()
    await task
    assert (await _job(job_id)).state == "SUCCEEDED"


async def _const(v: bool) -> bool:
    return v


async def test_infra_errors_do_not_consume_attempts(test_kind):
    seen = {"n": 0}

    @worker.handler(KIND)
    async def flaky(ctx):
        seen["n"] += 1
        raise worker.InfraUnavailable()

    job_id = await _enqueue()
    ctx = await worker.claim(["LLM"], "w1")
    await worker.run_one(ctx)
    s = await _job(job_id)
    assert s.state == "QUEUED" and s.attempts == 0 and s.infra_deferrals == 1 and s.last_error_code == "INFRA_UNAVAILABLE"


async def test_two_model_failures_exhaust_and_call_hook(test_kind):
    exhausted = []

    @worker.handler(KIND)
    async def broken(ctx):
        raise ValueError("bad json")

    async def on_exhausted(db, ctx, code):
        exhausted.append((ctx.id, code))
    worker.ON_EXHAUSTED[KIND] = on_exhausted

    job_id = await _enqueue()
    for _ in range(2):  # first attempt + one automatic regeneration (EVL-06)
        async with session_scope() as db:
            await db.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": job_id})
        ctx = await worker.claim(["LLM"], "w1")
        assert ctx is not None
        await worker.run_one(ctx)
    s = await _job(job_id)
    assert s.state == "FAILED" and s.attempts == 2 and exhausted == [(job_id, "ValueError")]


async def test_zombie_worker_write_is_fenced_out(test_kind):
    job_id = await _enqueue()
    a = await worker.claim(["LLM"], "worker-a")
    async with session_scope() as db:  # worker A stalls past its lease
        await db.execute(text("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :i"),
                         {"i": job_id})
    assert await worker.reap() == 1
    s = await _job(job_id)
    assert s.state == "QUEUED" and s.attempts == 0  # lost lease: not counted as a failed attempt
    b = await worker.claim(["LLM"], "worker-b")
    assert b.lease_token != a.lease_token
    async with session_scope() as db:
        await worker.fenced_complete(db, b)
    with pytest.raises(worker.LeaseLost):
        async with session_scope() as db:
            await db.execute(text("INSERT INTO audit_logs(actor_role, action, target_type, target_id) "
                                  "VALUES ('SYSTEM', 'ZOMBIE_WRITE', 'job', 'x')"))
            await worker.fenced_complete(db, a)
    async with session_scope() as db:
        assert await db.scalar(text("SELECT count(*) FROM audit_logs WHERE action = 'ZOMBIE_WRITE'")) == 0
    assert (await _job(job_id)).state == "SUCCEEDED"


async def test_restart_recovers_running_job_with_dead_lease(test_kind):
    @worker.handler(KIND)
    async def ok(ctx):
        async with session_scope() as db:
            await worker.fenced_complete(db, ctx)

    job_id = await _enqueue()
    await worker.claim(["LLM"], "crashed-worker")
    async with session_scope() as db:
        await db.execute(text("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :i"),
                         {"i": job_id})

    async def done() -> bool:
        return (await _job(job_id)).state == "SUCCEEDED"

    await _run_until(done, {"up": True})
    assert (await _job(job_id)).attempts == 1


async def test_dedupe_key_prevents_duplicate_live_jobs(test_kind):
    async with session_scope() as db:
        first = await queue.enqueue(db, KIND, {}, dedupe_key="eval:same")
        second = await queue.enqueue(db, KIND, {}, dedupe_key="eval:same")
    assert first is not None and second is None
