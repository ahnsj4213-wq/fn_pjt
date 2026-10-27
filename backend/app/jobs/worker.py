"""Durable job worker (ADR-0006, 7.2, EVL-06, AC-18).

- claim: `FOR UPDATE SKIP LOCKED`, only for resources whose infrastructure is healthy (OQ-23)
- lease: 60 s, renewed every 15 s; a lost lease stops the handler
- fencing: every claim gets a new lease_token; completion is conditional on it (R-14)
- failures: InfraUnavailable / lost lease do not consume attempts; model/timeout failures do
- attempt limit: 10 minutes from claim (EVL-06)
"""
import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import EVAL_ATTEMPT_TIMEOUT_SEC
from app.core.db import session_scope
from app.core.logging import log_event

log = logging.getLogger("app.worker")
LEASE = timedelta(seconds=60)
HEARTBEAT_SEC = 15
INFRA_BACKOFF_SEC = (30, 60, 120, 300)


class InfraUnavailable(Exception):
    """The model server / runner is down: retry later without consuming an attempt."""


class LeaseLost(Exception):
    pass


@dataclass
class JobCtx:
    id: int
    kind: str
    payload: dict
    application_id: object
    attempts: int
    max_attempts: int
    lease_token: int
    worker_id: str


Handler = Callable[[JobCtx], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}
# Hooks run inside the claim / final-failure transaction (e.g. eval_status PENDING->RUNNING, RUNNING->ON_HOLD).
ON_CLAIM: dict[str, Callable[[AsyncSession, JobCtx], Awaitable[None]]] = {}
ON_EXHAUSTED: dict[str, Callable[[AsyncSession, JobCtx, str], Awaitable[None]]] = {}
ON_REQUEUE: dict[str, Callable[[AsyncSession, JobCtx], Awaitable[None]]] = {}


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim(resources: list[str], wid: str, kinds: list[str] | None = None) -> JobCtx | None:
    async with session_scope() as db:
        row = (await db.execute(text("""
            WITH next AS (
              SELECT j.id FROM jobs j
              WHERE j.state = 'QUEUED' AND j.run_after <= now() AND j.resource = ANY(:res)
                AND (CAST(:kinds AS text[]) IS NULL OR j.kind = ANY(CAST(:kinds AS text[])))
                AND (j.depends_on IS NULL OR EXISTS (SELECT 1 FROM jobs d WHERE d.id = j.depends_on
                                                     AND d.state IN ('SUCCEEDED','FAILED','CANCELLED')))
              ORDER BY j.priority, j.run_after, j.id LIMIT 1 FOR UPDATE SKIP LOCKED)
            UPDATE jobs j SET state = 'RUNNING', attempts = j.attempts + 1, lease_owner = :wid,
              lease_token = nextval('job_lease_token_seq'), lease_expires_at = now() + :lease,
              attempt_deadline_at = now() + :limit, started_at = now()
            FROM next WHERE j.id = next.id
            RETURNING j.id, j.kind, j.payload, j.application_id, j.attempts, j.max_attempts, j.lease_token"""),
            {"res": resources, "kinds": kinds, "wid": wid, "lease": LEASE,
             "limit": timedelta(seconds=EVAL_ATTEMPT_TIMEOUT_SEC)})).first()
        if row is None:
            return None
        ctx = JobCtx(*row, worker_id=wid)
        if ctx.kind in ON_CLAIM:
            await ON_CLAIM[ctx.kind](db, ctx)
        return ctx


async def heartbeat(ctx: JobCtx) -> None:
    async with session_scope() as db:
        n = (await db.execute(text("UPDATE jobs SET lease_expires_at = now() + :lease "
                                   "WHERE id = :id AND lease_token = :tok AND state = 'RUNNING'"),
                              {"lease": LEASE, "id": ctx.id, "tok": ctx.lease_token})).rowcount
    if n == 0:
        raise LeaseLost(ctx.id)


async def fenced_complete(db: AsyncSession, ctx: JobCtx) -> None:
    """Call inside the handler's final write transaction. Raises LeaseLost (-> rollback) for a stale worker."""
    n = (await db.execute(text("UPDATE jobs SET state = 'SUCCEEDED', lease_token = NULL, lease_owner = NULL, "
                               "lease_expires_at = NULL, finished_at = now() "
                               "WHERE id = :id AND lease_token = :tok AND state = 'RUNNING'"),
                          {"id": ctx.id, "tok": ctx.lease_token})).rowcount
    if n == 0:
        raise LeaseLost(ctx.id)


async def _requeue(ctx: JobCtx, *, consume: bool, error_code: str, delay_sec: int) -> None:
    async with session_scope() as db:
        job = (await db.execute(text("SELECT attempts, max_attempts, infra_deferrals FROM jobs "
                                     "WHERE id = :id AND lease_token = :tok AND state = 'RUNNING' FOR UPDATE"),
                                {"id": ctx.id, "tok": ctx.lease_token})).first()
        if job is None:
            return  # someone else (reaper) already took over
        attempts, max_attempts, deferrals = job
        if not consume:
            attempts -= 1
            deferrals += 1
        if consume and attempts >= max_attempts:
            await db.execute(text("UPDATE jobs SET state = 'FAILED', lease_token = NULL, lease_owner = NULL, "
                                  "lease_expires_at = NULL, finished_at = now(), last_error_code = :e WHERE id = :id"),
                             {"id": ctx.id, "e": error_code})
            if ctx.kind in ON_EXHAUSTED:
                await ON_EXHAUSTED[ctx.kind](db, ctx, error_code)
            log_event(log, "job_failed", job_id=ctx.id, job_kind=ctx.kind, error_code=error_code)
            return
        await db.execute(text("UPDATE jobs SET state = 'QUEUED', attempts = :a, infra_deferrals = :d, "
                              "lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL, "
                              "run_after = now() + make_interval(secs => :delay), last_error_code = :e WHERE id = :id"),
                         {"id": ctx.id, "a": attempts, "d": deferrals, "delay": delay_sec, "e": error_code})
        if ctx.kind in ON_REQUEUE:
            await ON_REQUEUE[ctx.kind](db, ctx)


async def run_one(ctx: JobCtx) -> None:
    from app.ai.client import AIUnavailable

    fn = HANDLERS[ctx.kind]
    beat = asyncio.create_task(_beat_loop(ctx))
    try:
        await asyncio.wait_for(fn(ctx), timeout=EVAL_ATTEMPT_TIMEOUT_SEC)
        log_event(log, "job_done", job_id=ctx.id, job_kind=ctx.kind, attempt=ctx.attempts)
    except LeaseLost:
        log_event(log, "job_lease_lost", logging.WARNING, job_id=ctx.id, job_kind=ctx.kind)
    except (InfraUnavailable, AIUnavailable):
        await _requeue(ctx, consume=False, error_code="INFRA_UNAVAILABLE", delay_sec=INFRA_BACKOFF_SEC[0])
    except TimeoutError:
        await _requeue(ctx, consume=True, error_code="ATTEMPT_TIMEOUT", delay_sec=0)
    except Exception as e:  # noqa: BLE001 - model/format/code failure consumes the attempt
        log_event(log, "job_error", logging.WARNING, job_id=ctx.id, job_kind=ctx.kind, error_code=type(e).__name__)
        await _requeue(ctx, consume=True, error_code=type(e).__name__[:60], delay_sec=5)
    finally:
        beat.cancel()


async def _beat_loop(ctx: JobCtx) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SEC)
        try:
            await heartbeat(ctx)
        except LeaseLost:
            return


async def reap() -> int:
    """Return expired leases to the queue (worker died: no attempt consumed) and time out over-long attempts."""
    async with session_scope() as db:
        rows = (await db.execute(text("""
            SELECT id, kind, payload, application_id, attempts, max_attempts, lease_token,
                   attempt_deadline_at < now() AS timed_out
            FROM jobs WHERE state = 'RUNNING' AND (lease_expires_at < now() OR attempt_deadline_at < now())
            FOR UPDATE SKIP LOCKED"""))).all()
    for r in rows:
        ctx = JobCtx(r.id, r.kind, r.payload, r.application_id, r.attempts, r.max_attempts, r.lease_token, "reaper")
        if r.timed_out:
            await _requeue(ctx, consume=True, error_code="ATTEMPT_TIMEOUT", delay_sec=0)
        else:
            await _requeue(ctx, consume=False, error_code="LEASE_EXPIRED", delay_sec=0)
            async with session_scope() as db:
                await db.execute(text("UPDATE jobs SET lost_leases = lost_leases + 1 WHERE id = :id"), {"id": r.id})
    return len(rows)
