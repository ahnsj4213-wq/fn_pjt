"""Test postings only (AIHIRE_RETAKE_POSTING_IDS): a stopped interview starts over with newly composed questions.

Every other posting keeps the one-attempt rule (INT-01 지원 건당 1회 응시); the setting is empty by default. Only an unsubmitted
attempt qualifies, so no evaluation, report or decision is ever removed. The application keeps its id, consent and resume
snapshot. The state machine has no way back to READY on purpose, so the row is replaced instead of widening it for everyone.
"""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.storage import get_storage
from app.interview.checkpoint import get_checkpointer

RETAKE_FROM = ("INTERVIEWING", "INTERRUPTED", "CODING")

# Children first. `:a` is the application id.
_ANSWERS = "SELECT id FROM interview_answers WHERE application_id = :a"
_DELETES = (
    f"DELETE FROM answer_evaluations WHERE answer_id IN ({_ANSWERS})",
    f"DELETE FROM answer_pre_evaluations WHERE answer_id IN ({_ANSWERS})",
    "DELETE FROM interview_answers WHERE application_id = :a",
    "DELETE FROM followup_decisions WHERE application_id = :a",
    "DELETE FROM interview_turns WHERE application_id = :a",
    "DELETE FROM question_tts_assets WHERE question_id IN (SELECT id FROM interview_questions WHERE application_id = :a)",
    "DELETE FROM interviews WHERE application_id = :a",
    "DELETE FROM interview_slots WHERE application_id = :a",
    "DELETE FROM proctor_events WHERE application_id = :a",
    "DELETE FROM interview_questions WHERE application_id = :a",
    "DELETE FROM question_sets WHERE application_id = :a",
    "DELETE FROM coding_test_results WHERE submission_id IN (SELECT id FROM coding_submissions WHERE application_id = :a)",
    "DELETE FROM coding_submissions WHERE application_id = :a",
    "DELETE FROM coding_runs WHERE application_id = :a",
    "DELETE FROM coding_attempts WHERE application_id = :a",
    "DELETE FROM application_state_events WHERE application_id = :a",
    "DELETE FROM jobs WHERE application_id = :a AND state = 'QUEUED'",
)


def retake_allowed(posting_id: UUID, attempt_status: str) -> bool:
    return attempt_status in RETAKE_FROM and str(posting_id) in get_settings().retake_posting_id_set


async def reset_attempt(db: AsyncSession, app_id: UUID) -> list[str]:
    """Back to READY in the caller's transaction. Returns the stored files to remove once it has committed."""
    a = {"a": app_id}
    await db.execute(text("SET LOCAL app.purge = 'on'"))  # the immutable answer and question tables allow purges only
    rows = await db.execute(text("SELECT audio_key, whiteboard_key FROM interview_answers WHERE application_id = :a"), a)
    keys = [k for row in rows for k in row if k]
    for sql in _DELETES:
        await db.execute(text(sql), a)
    await db.execute(text("CREATE TEMP TABLE retake_row ON COMMIT DROP AS SELECT * FROM applications WHERE id = :a"), a)
    await db.execute(text("DELETE FROM applications WHERE id = :a"), a)
    await db.execute(text("UPDATE retake_row SET attempt_status = 'READY', interrupted_from = NULL, answered_main_count = 0, "
                          "eval_settings_id = NULL, interview_started_at = NULL"))
    await db.execute(text("INSERT INTO applications SELECT * FROM retake_row"))
    return keys


async def clean_up(app_id: UUID, settings_id: UUID | None, keys: list[str]) -> None:
    """After the commit: the interview's graph thread and files. A leftover file is harmless, a leftover thread is not."""
    from app.questions.graph import thread_id

    saver = await get_checkpointer()
    await saver.adelete_thread(str(app_id))
    if settings_id is not None:
        await saver.adelete_thread(thread_id(app_id, settings_id))
    storage = get_storage()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:  # noqa: BLE001, S110 - best effort
            pass
