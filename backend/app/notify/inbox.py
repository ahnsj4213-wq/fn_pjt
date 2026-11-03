"""Company notifications (SC-R12). Called inside the transaction of the change that causes them; an event is written once
(UNIQUE company_id + dedupe_key). The text carries a name and a posting title, never a score or a result."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import KST
from app.models import Application, JobPosting, Notification, User


async def notify(db: AsyncSession, company_id: UUID, kind: str, text: str, dedupe: str, *,
                 application_id: UUID | None = None, posting_id: UUID | None = None) -> None:
    await db.execute(insert(Notification).values(company_id=company_id, kind=kind, text=text[:300], dedupe_key=dedupe,
                                                 application_id=application_id, posting_id=posting_id)
                     .on_conflict_do_nothing(index_elements=["company_id", "dedupe_key"]))


async def _who(db: AsyncSession, app: Application) -> tuple[str, str]:
    name = await db.scalar(select(User.name).where(User.id == app.candidate_id))
    title = await db.scalar(select(JobPosting.title).where(JobPosting.id == app.posting_id))
    return name or "지원자", title or "공고"


async def interview_finished(db: AsyncSession, app: Application) -> None:
    if not app.share_result:  # practice mode: the company never sees it
        return
    name, title = await _who(db, app)
    await notify(db, app.company_id, "APPLY", f"{name}님이 「{title}」 AI 면접을 마쳤습니다", f"apply:{app.id}:done",
                 application_id=app.id, posting_id=app.posting_id)


async def interview_stopped(db: AsyncSession, app: Application) -> None:
    if not app.share_result:
        return
    name, title = await _who(db, app)
    await notify(db, app.company_id, "APPLY", f"{name}님이 「{title}」 면접을 시작했지만 중간에 멈췄습니다",
                 f"apply:{app.id}:stopped", application_id=app.id, posting_id=app.posting_id)


async def schedule_answered(db: AsyncSession, company_id: UUID, application_id: UUID, name: str, starts_at: datetime,
                            accepted: bool, schedule_id: UUID) -> None:
    when = starts_at.astimezone(KST).strftime("%m.%d %H:%M")
    text = (f"{name}님이 2차 면접 일정({when})을 수락했습니다" if accepted
            else f"{name}님이 2차 면접 일정({when})의 조율을 요청했습니다")
    await notify(db, company_id, "SCHEDULE", text, f"schedule:{schedule_id}:{'ok' if accepted else 'ask'}",
                 application_id=application_id)
