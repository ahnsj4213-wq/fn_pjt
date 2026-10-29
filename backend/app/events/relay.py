"""Publishes application state changes to Redis (ADR-0013).

The source is `application_state_events`, which the DB trigger fills on every attempt / eval / decision transition
(TRD 03 §4), so no code path can change a state without producing a notification. The worker tails that table
(an outbox) and publishes one small message per change:

  aihire:ev:user:{candidate_id}     {"type": "application_changed", "application_id": ...}
  aihire:ev:company:{company_id}    the same plus "machine" (ATTEMPT / EVAL / DECISION)

Messages carry no state values and no personal data: screens refetch through the allow-listed APIs (SEC-04).
Only one worker relays at a time (a short Redis lease); the last relayed id lives in Redis, starting from the newest
row the first time (history is not replayed).
"""
import json

from sqlalchemy import text

from app.core.db import session_scope
from app.core.redis import get_redis

LAST_KEY = "aihire:ev:last_id"
LOCK_KEY = "aihire:ev:relay_lock"
LOCK_MS = 5_000
BATCH = 500


def user_channel(user_id) -> str:
    return f"aihire:ev:user:{user_id}"


def company_channel(company_id) -> str:
    return f"aihire:ev:company:{company_id}"


async def relay_once(owner: str) -> int:
    """Publishes the state changes since the last run. Returns how many were published."""
    r = get_redis()
    if r is None:
        return 0
    if not await r.set(LOCK_KEY, owner, nx=True, px=LOCK_MS):
        if await r.get(LOCK_KEY) != owner:
            return 0  # another worker relays
        await r.pexpire(LOCK_KEY, LOCK_MS)
    last = await r.get(LAST_KEY)
    async with session_scope() as db:
        if last is None:
            newest = (await db.execute(text("SELECT COALESCE(max(id), 0) FROM application_state_events"))).scalar_one()
            await r.set(LAST_KEY, newest)
            return 0
        rows = (await db.execute(text(
            "SELECT e.id, e.application_id, e.machine, a.candidate_id, a.company_id "
            "FROM application_state_events e JOIN applications a ON a.id = e.application_id "
            "WHERE e.id > :last ORDER BY e.id LIMIT :n"), {"last": int(last), "n": BATCH})).all()
    if not rows:
        return 0
    async with r.pipeline(transaction=False) as p:
        for _, app_id, machine, candidate_id, company_id in rows:
            base = {"type": "application_changed", "application_id": str(app_id)}
            p.publish(user_channel(candidate_id), json.dumps(base))
            p.publish(company_channel(company_id), json.dumps(base | {"machine": machine}))
        p.set(LAST_KEY, rows[-1][0])
        await p.execute()
    return len(rows)
