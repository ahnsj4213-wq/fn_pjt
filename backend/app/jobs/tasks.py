"""Periodic maintenance tasks (posting auto-close, reaper, session GC). Domain job handlers register themselves
from their own modules (imported here so the worker sees them)."""
from datetime import timedelta

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.timeutil import utcnow
from app.jobs import handlers, worker  # noqa: F401  handlers register themselves on import
from app.jobs.runtime import periodic
from app.models import JobPosting


@periodic("job_reaper")
async def job_reaper() -> None:
    await worker.reap()


@periodic("posting_autoclose")
async def posting_autoclose() -> None:
    """JOB-03: postings past their deadline close automatically (queries also check the deadline directly)."""
    from app.postings.service import close

    async with session_scope() as db:
        ids = (await db.execute(select(JobPosting.id).where(JobPosting.status == "OPEN",
                                                            JobPosting.deadline_at <= utcnow()))).scalars().all()
    for pid in ids:
        async with session_scope() as db:
            p = await db.get(JobPosting, pid, with_for_update=True)
            if p.status == "OPEN" and p.deadline_at <= utcnow():
                await close(db, p, "DEADLINE")


@periodic("session_gc")
async def session_gc() -> None:
    async with session_scope() as db:
        await db.execute(text("DELETE FROM sessions WHERE expires_at < now()"))
        # Login-throttle rows live 24 h (data-retention 2절) unless still locked.
        await db.execute(text("DELETE FROM auth_throttle WHERE window_started_at < now() - interval '24 hours' "
                              "AND (locked_until IS NULL OR locked_until < now())"))


@periodic("coding_autosubmit")
async def coding_autosubmit() -> None:
    """COD-05 / AC-15: at the server deadline the last saved draft is submitted, connected or not (OQ-25)."""
    from app.coding.router import submit_snapshot
    from app.models import Application, CodingAttempt

    async with session_scope() as db:
        due = (await db.execute(select(CodingAttempt.application_id).join(
            Application, Application.id == CodingAttempt.application_id).where(
            CodingAttempt.deadline_at < utcnow() - timedelta(seconds=get_settings().answer_grace_seconds),
            (Application.attempt_status == "CODING")
            | ((Application.attempt_status == "INTERRUPTED") & (Application.interrupted_from == "CODING"))))).scalars().all()
    for app_id in due:
        async with session_scope() as db:
            app = await db.get(Application, app_id, with_for_update=True)
            if app.attempt_status == "CODING" or (app.attempt_status == "INTERRUPTED" and app.interrupted_from == "CODING"):
                attempt = await db.get(CodingAttempt, app_id, with_for_update=True)
                await submit_snapshot(db, app, attempt.draft_code or "", "AUTO_TIMEOUT")


@periodic("attempt_sweeper")
async def attempt_sweeper() -> None:
    """Liveness (TRD 06 §11, C7): INTERVIEWING / CODING without any signal for 120 s -> INTERRUPTED (voice keeps it
    fresh every 15 s, text mode and coding through their calls); PREPARING idle for 30 min -> READY (B)."""
    from app.interview import turns

    async with session_scope() as db:
        stale = (await db.execute(text(
            "SELECT a.id FROM applications a JOIN interviews i ON i.application_id = a.id "
            "WHERE a.attempt_status IN ('INTERVIEWING', 'CODING') "
            "AND COALESCE(i.last_heartbeat_at, a.interview_started_at) < now() - interval '120 seconds'"))).scalars().all()
        await db.execute(text(
            "UPDATE applications a SET attempt_status = 'READY' FROM interviews i WHERE i.application_id = a.id "
            "AND a.attempt_status = 'PREPARING' AND COALESCE(i.last_heartbeat_at, a.applied_at) < now() - interval '30 minutes'"))
    for app_id in stale:
        async with session_scope() as db:
            await turns.interrupt(db, app_id)


@periodic("retention_purge")
async def retention_purge() -> None:
    """SEC-07 (8.5): audio and content past their retention period, per application (TRD 03 §8)."""
    from app.audit import audit
    from app.core.db import sessionmaker
    from app.retention.service import run_retention

    async with sessionmaker()() as db:  # run_retention commits per batch
        counts = await run_retention(db)
        if counts.audio_apps or counts.content_apps or counts.tts_assets:
            await audit(db, action="DATA_PURGE", target_type="retention", target_id="periodic", actor_id=None,
                        actor_role="SYSTEM", **counts.as_detail())
            await db.commit()


@periodic("file_gc")
async def file_gc() -> None:
    from app.core.db import sessionmaker
    from app.retention.service import run_file_gc

    async with sessionmaker()() as db:
        await run_file_gc(db)
