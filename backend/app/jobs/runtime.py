"""Worker process main loop: `python -m app.jobs.runtime`.

Each resource (LLM, RUNNER, CPU, NONE) has a few lanes. A lane claims work only while the resource's probe says
the infrastructure is up, so stopping the model server leaves jobs QUEUED with attempts untouched (AC-18).
Lanes wait on Redis for job ids (ADR-0013) and fall back to claiming from PostgreSQL when Redis is off or down.
Periodic tasks run in the same process; `periodic_tasks.next_run_at` makes them single-runner across workers.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import configure_logging, log_event
from app.core.redis import get_redis
from app.jobs import dispatch, worker

log = logging.getLogger("app.worker")
Probe = Callable[[], Awaitable[bool]]
LANES = {"LLM": 2, "RUNNER": 2, "CPU": 1, "NONE": 1}  # LLM: one interactive + one batch job; ai gateway orders them
PERIODIC: dict[str, Callable[[], Awaitable[None]]] = {}
DISPATCH_TICK_SEC = 0.5


def periodic(name: str):
    def deco(fn):
        PERIODIC[name] = fn
        return fn
    return deco


class CachedProbe:
    def __init__(self, check: Probe, ttl: float = 10.0) -> None:
        self.check, self.ttl, self.value, self.at = check, ttl, False, 0.0

    async def __call__(self) -> bool:
        if time.monotonic() - self.at > self.ttl:
            try:
                self.value = await self.check()
            except Exception:  # noqa: BLE001 - unreachable means down
                self.value = False
            self.at = time.monotonic()
        return self.value


def default_probes() -> dict[str, Probe]:
    s = get_settings()

    async def ai_ok(part: str) -> bool:
        from app.ai.client import get_ai_client

        body = await get_ai_client().health()
        return bool(body.get(part, {}).get("up"))

    async def runner_ok() -> bool:
        if not s.code_execution_enabled:
            return False
        async with httpx.AsyncClient(timeout=3) as c:
            body = (await c.get(f"{s.runner_url}/v1/health",
                                headers={"X-Runner-Token": s.runner_token.get_secret_value()})).json()
        return bool(body.get("sandbox_verified"))

    async def always() -> bool:
        return True

    return {"LLM": CachedProbe(lambda: ai_ok("llm")), "CPU": CachedProbe(lambda: ai_ok("tts")),
            "RUNNER": CachedProbe(runner_ok), "NONE": always}


_redis_up: bool | None = None


def _note_redis(up: bool, error: BaseException | None = None) -> None:
    """Logs only when Redis comes or goes, not on every lane iteration."""
    global _redis_up
    if up != _redis_up:
        _redis_up = up
        log_event(log, "redis_up" if up else "redis_down", logging.INFO if up else logging.WARNING,
                  error_code=type(error).__name__ if error else None)


async def _next_job(resource: str, wid: str, stop: asyncio.Event, idle_sec: float) -> worker.JobCtx | None:
    kinds = list(worker.HANDLERS) or None
    if get_redis() is not None:
        try:
            job_id = await dispatch.pop(resource, timeout_sec=idle_sec)  # blocks while idle (ADR-0013)
            _note_redis(True)
        except Exception as e:  # noqa: BLE001 - Redis unreachable: fall through to the SQL claim
            _note_redis(False, e)
        else:
            # A stale id (claimed elsewhere, not yet due) yields None; the dispatcher re-sends live jobs.
            return None if job_id is None else await worker.claim([resource], wid, kinds, job_id=job_id)
    ctx = await worker.claim([resource], wid, kinds)
    if ctx is None:
        await _sleep(stop, idle_sec)
    return ctx


async def _lane(resource: str, probe: Probe, wid: str, stop: asyncio.Event, idle_sec: float) -> None:
    while not stop.is_set():
        if not await probe():
            await _sleep(stop, 2.0)
            continue
        ctx = await _next_job(resource, wid, stop, idle_sec)
        if ctx is not None:
            await worker.run_one(ctx)


async def _dispatch_loop(stop: asyncio.Event, tick: float, wid: str) -> None:
    """Outbox relays (ADR-0013): QUEUED jobs -> Redis queues, state changes -> Redis pub/sub. Both are safe with
    several workers (row locks + idempotent ZADD; a single relay lease for events)."""
    from app.events.relay import relay_once

    while not stop.is_set():
        try:
            await dispatch.dispatch_due()
            await relay_once(wid)
        except Exception:  # noqa: BLE001 - logged by dispatch_due / the lanes; retry after a pause
            await _sleep(stop, 5.0)
            continue
        await _sleep(stop, tick)


async def _periodic_loop(stop: asyncio.Event, tick: float) -> None:
    while not stop.is_set():
        async with session_scope() as db:
            due = (await db.execute(text(
                "UPDATE periodic_tasks SET next_run_at = now() + make_interval(secs => interval_sec) "
                "WHERE next_run_at <= now() AND name = ANY(:names) RETURNING name"), {"names": list(PERIODIC)})).scalars().all()
        for name in due:
            try:
                await PERIODIC[name]()
                async with session_scope() as db:
                    await db.execute(text("UPDATE periodic_tasks SET last_ok_at = now(), last_error_code = NULL "
                                          "WHERE name = :n"), {"n": name})
            except Exception as e:  # noqa: BLE001
                log_event(log, "periodic_failed", logging.WARNING, job_kind=name, error_code=type(e).__name__)
                async with session_scope() as db:
                    await db.execute(text("UPDATE periodic_tasks SET last_error_code = :e WHERE name = :n"),
                                     {"n": name, "e": type(e).__name__})
        await _sleep(stop, tick)


async def _sleep(stop: asyncio.Event, sec: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=sec)
    except TimeoutError:
        pass


async def run(stop: asyncio.Event, probes: dict[str, Probe] | None = None, lanes: dict[str, int] | None = None,
              idle_sec: float = 2.0, periodic_tick: float = 5.0) -> None:
    import app.jobs.tasks  # noqa: F401  registers handlers and periodic tasks

    probes = probes or default_probes()
    wid = worker.worker_id()
    await worker.reap()  # restart recovery: reclaim leases left by a dead worker (8.3)
    tasks = [asyncio.create_task(_lane(r, probes[r], f"{wid}/{r}{i}", stop, idle_sec))
             for r, n in (lanes or LANES).items() for i in range(n)]
    tasks.append(asyncio.create_task(_periodic_loop(stop, periodic_tick)))
    if get_redis() is not None:
        tasks.append(asyncio.create_task(_dispatch_loop(stop, DISPATCH_TICK_SEC, wid)))
    log_event(log, "worker_started", count=len(tasks))
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    configure_logging()
    asyncio.run(run(asyncio.Event()))


if __name__ == "__main__":
    main()
