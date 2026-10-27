"""Login throttling (SC-04). Keys are an HMAC of the normalised email and the client IP; the same counter
applies to unknown emails, so throttling never reveals whether an account exists."""
import hashlib
import hmac
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import now
from app.models import AuthThrottle

WINDOW = timedelta(minutes=15)
EMAIL_LIMIT = 5
IP_LIMIT = 30
def email_key(email: str) -> str:
    from app.core.config import get_settings

    pepper = get_settings().throttle_key.get_secret_value().encode()
    return "e:" + hmac.new(pepper, email.strip().lower().encode(), hashlib.sha256).hexdigest()


def ip_key(ip: str) -> str:
    return "i:" + ip


async def is_locked(db: AsyncSession, keys: list[str]) -> bool:
    t = now()
    rows = (await db.execute(select(AuthThrottle).where(AuthThrottle.key.in_(keys)))).scalars()
    return any(r.locked_until and r.locked_until > t for r in rows)


async def record_failure(db: AsyncSession, key: str, limit: int) -> None:
    t = now()
    row = await db.get(AuthThrottle, key, with_for_update=True)
    if row is None:
        await db.execute(insert(AuthThrottle).values(key=key, failures=1, window_started_at=t).on_conflict_do_nothing())
        return
    if t - row.window_started_at > WINDOW:
        row.failures, row.window_started_at, row.locked_until = 1, t, None
    else:
        row.failures += 1
        if row.failures >= limit:
            row.locked_until = t + WINDOW


async def clear(db: AsyncSession, key: str) -> None:
    row = await db.get(AuthThrottle, key)
    if row is not None:
        await db.delete(row)
