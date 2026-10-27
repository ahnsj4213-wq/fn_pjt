"""Periodic maintenance tasks (posting auto-close, reaper, session GC). Domain job handlers register themselves
from their own modules (imported here so the worker sees them)."""
from sqlalchemy import select, text

from app.core.db import session_scope
from app.core.timeutil import utcnow
from app.jobs import worker
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
