"""Test candidate accounts (AIHIRE_TESTER_EMAILS, dev/test only) that can apply to the same posting again.

Everyone else keeps one application per posting (JOB-02, the UNIQUE (posting_id, candidate_id) constraint stays).
For a tester, applying again deletes the earlier application of that account and everything under it, so the new one
starts clean; the constraint is never relaxed. audit_logs and consent_records stay (immutable, not owned by the
application). An application with a pass/fail decision is refused: decision_history is append-only (ADM-04). Files go through the deletion outbox like the retention purge, so a rolled-back apply loses nothing.
"""
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User
from app.retention.service import purge_content

# Left after purge_content, children first. The mypage, schedule and notification tables cascade.
_REST = (
    "DELETE FROM jobs WHERE application_id = :a",
    "DELETE FROM interviews WHERE application_id = :a",
    "DELETE FROM question_sets WHERE application_id = :a",
    "DELETE FROM evaluation_runs WHERE application_id = :a",
    "DELETE FROM application_state_events WHERE application_id = :a",
    "DELETE FROM applications WHERE id = :a",
)


async def is_tester(db: AsyncSession, user_id: UUID) -> bool:
    emails = get_settings().tester_email_set
    if not emails:
        return False
    email = (await db.execute(select(User.email).where(User.id == user_id))).scalar_one_or_none()
    return bool(email) and email.lower() in emails


async def has_decision(db: AsyncSession, app_id: UUID) -> bool:
    """decision_history is append-only even for purges (ADM-04), so a decided application cannot be deleted."""
    return bool((await db.execute(text("SELECT 1 FROM decision_history WHERE application_id = :a LIMIT 1"),
                                  {"a": app_id})).first())


async def delete_application(db: AsyncSession, app_id: UUID) -> None:
    """In the caller's transaction. The caller has checked that the application belongs to a tester."""
    await purge_content(db, [app_id], "TESTER_REAPPLY")  # sets app.purge for the immutable tables
    for sql in _REST:
        await db.execute(text(sql), {"a": app_id})
