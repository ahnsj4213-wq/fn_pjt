"""Celery tasks (app/celery_app.py). Each worker process keeps one event loop, so the async engine, the Redis client
and the in-process LLM gateways live on a single loop for the life of the process."""
import asyncio
import logging
import os
import socket

from celery.signals import worker_process_init

from app.celery_app import celery
from app.core.logging import configure_logging, log_event

log = logging.getLogger("app.worker")
_loop: asyncio.AbstractEventLoop | None = None
_probes: dict | None = None


@worker_process_init.connect
def _init_process(**_) -> None:
    configure_logging()


def run_async(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


def _wid() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _run_job(job_id: int, resource: str) -> str:
    global _probes
    import app.jobs.tasks  # noqa: F401  registers the handlers and periodic tasks
    from app.jobs import runtime, worker

    _probes = _probes or runtime.default_probes()
    if not await _probes[resource]():
        return "deferred"  # infrastructure down (OQ-23): the job stays QUEUED and the relay sends it again later
    ctx = await worker.claim([resource], _wid(), list(worker.HANDLERS) or None, job_id=job_id)
    if ctx is None:
        return "stale"  # already claimed, finished or not due: duplicate messages are harmless
    await worker.run_one(ctx)
    return "ran"


@celery.task(name="jobs.run", acks_late=True)
def run_job(job_id: int, resource: str) -> str:
    return run_async(_run_job(job_id, resource))


async def _relay() -> dict:
    from app.events.relay import relay_once
    from app.jobs import dispatch

    sent = await dispatch.dispatch_due(send=dispatch.to_celery)
    published = await relay_once(_wid())
    return {"jobs": sent, "events": published}


@celery.task(name="relay.tick", ignore_result=True)
def relay_tick() -> dict:
    return run_async(_relay())


async def _periodic(name: str) -> None:
    from sqlalchemy import text

    import app.jobs.tasks  # noqa: F401
    from app.core.db import session_scope
    from app.jobs.runtime import PERIODIC

    if name == "sandbox_verify":
        from app.coding.sandbox_local import verify_and_publish

        await verify_and_publish()
        return
    try:
        await PERIODIC[name]()
        async with session_scope() as db:
            await db.execute(text("UPDATE periodic_tasks SET last_ok_at = now(), last_error_code = NULL WHERE name = :n"),
                             {"n": name})
    except Exception as e:
        log_event(log, "periodic_failed", logging.WARNING, job_kind=name, error_code=type(e).__name__)
        async with session_scope() as db:
            await db.execute(text("UPDATE periodic_tasks SET last_error_code = :e WHERE name = :n"),
                             {"n": name, "e": type(e).__name__})
        raise


@celery.task(name="periodic.run", ignore_result=True)
def periodic_run(name: str) -> None:
    run_async(_periodic(name))


async def _sandbox_execute(code: str, cases: list[dict], wall_ms: int, mem_mb: int) -> dict:
    from dataclasses import asdict

    from app.coding.runner import RunnerUnavailable
    from app.coding.sandbox_local import execute_local

    try:
        results = await execute_local(code, cases, wall_ms=wall_ms, mem_mb=mem_mb)
    except RunnerUnavailable as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "results": [asdict(r) for r in results]}


@celery.task(name="sandbox.execute")
def sandbox_execute(code: str, cases: list[dict], wall_ms: int, mem_mb: int) -> dict:
    """[실행] for the API (runner_mode=celery): the public examples only, never expected outputs (C8)."""
    return run_async(_sandbox_execute(code, cases, wall_ms, mem_mb))
