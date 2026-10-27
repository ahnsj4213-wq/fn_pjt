"""Global concurrent-interview cap (INT-13, AC-14). Slots are held only while INTERVIEWING (T10)."""
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

SLOT_LOCK_KEY = 7301001


async def acquire(db: AsyncSession, application_id: UUID) -> bool:
    """Inside the caller's transaction. Serialised by an advisory lock so N concurrent starts admit exactly `limit`."""
    s = get_settings()
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SLOT_LOCK_KEY})
    held = await db.scalar(text("SELECT count(*) FROM interview_slots WHERE lease_expires_at > now() "
                                "AND application_id <> :a"), {"a": application_id})
    if held >= s.max_concurrent_interviews:
        return False
    await db.execute(text(
        "INSERT INTO interview_slots(application_id, lease_expires_at) VALUES (:a, now() + :l) "
        "ON CONFLICT (application_id) DO UPDATE SET lease_expires_at = EXCLUDED.lease_expires_at"),
        {"a": application_id, "l": timedelta(seconds=s.slot_lease_seconds)})
    return True


async def renew(db: AsyncSession, application_id: UUID) -> None:
    await db.execute(text("UPDATE interview_slots SET lease_expires_at = now() + :l WHERE application_id = :a"),
                     {"a": application_id, "l": timedelta(seconds=get_settings().slot_lease_seconds)})


async def release(db: AsyncSession, application_id: UUID) -> None:
    await db.execute(text("DELETE FROM interview_slots WHERE application_id = :a"), {"a": application_id})
