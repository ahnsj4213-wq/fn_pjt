"""State changes that span several applications (posting close) and the eval-job registration rule (7.2)."""
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utcnow
from app.jobs.queue import enqueue
from app.models import (
    Application,
    CodingAttempt,
    CodingSubmission,
    InterviewAnswer,
    InterviewQuestion,
    InterviewSlot,
    JobPosting,
)


async def register_evaluation(db: AsyncSession, app: Application, trigger: str = "SUBMIT",
                              depends_on: int | None = None) -> None:
    """eval NULL/ON_HOLD -> PENDING and the evaluation job, in the caller's transaction (7.2, AC-18).
    `depends_on` = the grading job of a coding submission: the evaluation waits for it (TRD 03 A7)."""
    app.eval_status = "PENDING"
    await db.flush()
    await enqueue(db, "evaluate_application", {"application_id": str(app.id), "trigger": trigger},
                  application_id=app.id, dedupe_key=f"eval:{app.id}", depends_on=depends_on)


async def expire_unfinished(db: AsyncSession, posting_id: UUID) -> int:
    """Posting closed (7.4, OQ-26/27): not-started and interrupted attempts expire; attempts in progress finish."""
    rows = (await db.execute(select(Application).where(
        Application.posting_id == posting_id,
        Application.attempt_status.in_(["READY", "PREPARING", "INTERRUPTED"])).with_for_update())).scalars().all()
    now = utcnow()
    for app in rows:
        was_coding = app.interrupted_from == "CODING"
        was_interview = app.interrupted_from == "INTERVIEWING"
        app.attempt_status, app.interrupted_from, app.expired_at = "EXPIRED", None, now
        await db.flush()
        grade_job = await auto_submit_draft(db, app, kind="AUTO_EXPIRED") if was_coding else None
        if was_interview and (await db.get(JobPosting, posting_id)).coding_enabled:
            grade_job = await submit_live_code(db, app, only_if_written=True)
        if app.answered_main_count > 0:  # OQ-07: evaluate answered questions only; zero answers -> no evaluation
            await register_evaluation(db, app, depends_on=grade_job)
        await db.execute(delete(InterviewSlot).where(InterviewSlot.application_id == app.id))
    return len(rows)


async def auto_submit_draft(db: AsyncSession, app: Application, kind: str) -> int | None:
    """COD-05: submit the last saved draft once (manual and automatic submits race safely on the unique key).
    Returns the grading job id, or None when a submission already existed."""
    attempt = await db.get(CodingAttempt, app.id, with_for_update=True)
    if attempt is None:
        return None
    return await _snapshot_and_grade(db, app, attempt.draft_code or "", kind)


async def submit_live_code(db: AsyncSession, app: Application, *, only_if_written: bool = False) -> int | None:
    """A live-coding question replaces the separate test (SC-A06), so its code is what gets graded in a coding posting
    (screen-spec decision 2026-10-30, ADR-0017). No code = an empty submission (scores 0) unless `only_if_written`."""
    code = await db.scalar(select(InterviewAnswer.code_text).join(
        InterviewQuestion, InterviewQuestion.id == InterviewAnswer.question_id).where(
        InterviewAnswer.application_id == app.id, InterviewQuestion.answer_mode == "CODE", InterviewQuestion.role == "MAIN"))
    if not code and only_if_written:
        return None
    return await _snapshot_and_grade(db, app, code or "", "LIVE")


async def _snapshot_and_grade(db: AsyncSession, app: Application, code: str, kind: str) -> int | None:
    import hashlib

    from sqlalchemy.dialects.postgresql import insert

    res = await db.execute(insert(CodingSubmission).values(
        application_id=app.id, company_id=app.company_id, code=code, code_sha256=hashlib.sha256(code.encode()).digest(),
        kind=kind).on_conflict_do_nothing(index_elements=["application_id"]).returning(CodingSubmission.id))
    sub_id = res.scalar_one_or_none()
    if sub_id is None:
        return None
    return await enqueue(db, "grade_coding", {"submission_id": str(sub_id)}, application_id=app.id,
                         dedupe_key=f"grade:{app.id}")


async def release_slot(db: AsyncSession, application_id: UUID) -> None:
    await db.execute(delete(InterviewSlot).where(InterviewSlot.application_id == application_id))


async def touch(db: AsyncSession, application_id: UUID) -> None:
    from app.models import Interview
    await db.execute(update(Interview).where(Interview.application_id == application_id).values(last_heartbeat_at=utcnow()))
