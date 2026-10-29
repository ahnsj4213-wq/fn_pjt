"""Job dispatch through Redis (ADR-0013, supersedes the polling part of ADR-0006).

  PostgreSQL `jobs`   the durable record and the outbox: `enqueue` still commits in the caller's transaction (7.2).
  dispatcher          moves due QUEUED jobs (run_after passed, dependency finished) into Redis: one sorted set per
                      resource, score = priority, then due time, so ZPOPMIN hands out the same order the SQL claim used.
                      ZADD is idempotent, so re-sending is harmless; a job still QUEUED 30 s after its hand-off (Redis
                      restarted, a popped id was never claimed) is sent again.
  worker lane         BZPOPMIN on its resource's set, then claims that id in PostgreSQL with the same lease and fencing
                      as before. An id whose row is no longer claimable is dropped.
  Redis down          lanes fall back to the SQL claim, so work never stops.
"""
import logging

from sqlalchemy import text

from app.core.db import session_scope
from app.core.logging import log_event
from app.core.redis import get_redis

log = logging.getLogger("app.dispatch")
PREFIX = "aihire:q:"
RESEND_AFTER_SEC = 30
BATCH = 500


def queue_key(resource: str) -> str:
    return f"{PREFIX}{resource}"


def score(priority: int, due_epoch_ms: int) -> float:
    # priority 0..999 first, then the due time; float keeps integer precision up to 2^53 (> 999e13 + epoch ms).
    return priority * 1e13 + due_epoch_ms


async def dispatch_due() -> int:
    """One pass: hand every claimable QUEUED job to Redis. Returns how many were sent (0 when Redis is off)."""
    r = get_redis()
    if r is None:
        return 0
    async with session_scope() as db:
        rows = (await db.execute(text(f"""
            WITH due AS (
              SELECT j.id FROM jobs j
              WHERE j.state = 'QUEUED' AND j.run_after <= now()
                AND (j.depends_on IS NULL OR EXISTS (SELECT 1 FROM jobs d WHERE d.id = j.depends_on
                                                     AND d.state IN ('SUCCEEDED','FAILED','CANCELLED')))
                AND (j.dispatched_at IS NULL OR j.dispatched_at < j.run_after
                     OR j.dispatched_at < now() - make_interval(secs => {RESEND_AFTER_SEC}))
              ORDER BY j.priority, j.run_after, j.id LIMIT {BATCH} FOR UPDATE SKIP LOCKED)
            UPDATE jobs j SET dispatched_at = now() FROM due WHERE j.id = due.id
            RETURNING j.id, j.resource, j.priority, (extract(epoch FROM j.run_after) * 1000)::bigint AS due_ms"""))).all()
    if not rows:
        return 0
    try:
        async with r.pipeline(transaction=False) as p:
            for job_id, resource, priority, due_ms in rows:
                p.zadd(queue_key(resource), {str(job_id): score(priority, due_ms)})
            await p.execute()
    except Exception as e:  # Redis down: forget the hand-off so the next pass (or the SQL claim) gets it
        async with session_scope() as db:
            await db.execute(text("UPDATE jobs SET dispatched_at = NULL WHERE id = ANY(:ids)"),
                             {"ids": [row[0] for row in rows]})
        log_event(log, "dispatch_failed", logging.WARNING, error_code=type(e).__name__)
        raise
    return len(rows)


async def pop(resource: str, timeout_sec: float) -> int | None:
    """Blocks up to `timeout_sec` for the next job id of `resource`. Raises if Redis is unreachable."""
    r = get_redis()
    if r is None:
        return None
    got = await r.bzpopmin(queue_key(resource), timeout=timeout_sec)
    return int(got[1]) if got else None
