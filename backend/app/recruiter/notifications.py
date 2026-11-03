"""알림 창 (SC-R12): the company's inbox of new interviews, applicant answers to mailed dates and posting deadlines.

Deadline alerts are written when the inbox is read (one per posting and deadline date, so an extended posting alerts
again): a published posting whose deadline falls within the next 24 hours. Everything is scoped by the session's company.
"""
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import RecruiterCtx, require_recruiter
from app.core.db import get_db
from app.core.errors import AppError, not_found
from app.core.timeutil import KST, kst_end_of_day, utcnow
from app.models import JobPosting, Notification
from app.notify.inbox import notify
from app.postings import service

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
EXTEND_DAYS = 7


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str  # DEADLINE | APPLY | SCHEDULE
    text: str
    created_at: datetime
    unread: bool
    application_id: uuid.UUID | None
    posting_id: uuid.UUID | None
    posting_open: bool | None  # DEADLINE only: can the buttons still be used
    done_text: str | None


class Inbox(BaseModel):
    unread: int
    items: list[NotificationOut]


async def _deadline_alerts(db: AsyncSession, ctx: RecruiterCtx) -> None:
    now = utcnow()
    posts = (await db.execute(select(JobPosting).where(
        JobPosting.company_id == ctx.company_id, JobPosting.status == "OPEN", JobPosting.deleted_at.is_(None),
        JobPosting.deadline_at > now, JobPosting.deadline_at <= now + timedelta(hours=24)))).scalars().all()
    for p in posts:
        day = p.deadline_at.astimezone(KST).date()
        await notify(db, ctx.company_id, "DEADLINE", f"「{p.title}」 공고가 내일 마감됩니다. 기한을 늘리거나 지금 마감할 수 있습니다.",
                     f"deadline:{p.id}:{day}", posting_id=p.id)


@router.get("/notifications")
async def inbox(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> Inbox:
    await _deadline_alerts(db, ctx)
    await db.commit()
    rows = (await db.execute(select(Notification, JobPosting).join(JobPosting, JobPosting.id == Notification.posting_id, isouter=True)
                             .where(Notification.company_id == ctx.company_id)
                             .order_by(Notification.created_at.desc()).limit(30))).all()
    items = [NotificationOut(
        id=n.id, kind=n.kind, text=n.text, created_at=n.created_at, unread=n.read_at is None, application_id=n.application_id,
        posting_id=n.posting_id, done_text=n.done_text,
        posting_open=(service.is_open(p) if n.kind == "DEADLINE" and p is not None else None)) for n, p in rows]
    return Inbox(unread=sum(i.unread for i in items), items=items)


@router.post("/notifications/read", status_code=204)
async def read_all(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> None:
    """Closing the window marks everything read (SC-R12)."""
    await db.execute(update(Notification).where(Notification.company_id == ctx.company_id, Notification.read_at.is_(None))
                     .values(read_at=utcnow()))
    await db.commit()


async def _deadline_note(db: AsyncSession, ctx: RecruiterCtx, nid: uuid.UUID) -> tuple[Notification, JobPosting]:
    n = await db.get(Notification, nid, with_for_update=True)
    if n is None or n.company_id != ctx.company_id or n.kind != "DEADLINE" or n.posting_id is None:
        raise not_found()
    p = await db.get(JobPosting, n.posting_id, with_for_update=True)
    if p is None or p.deleted_at is not None:
        raise not_found()
    if not service.is_open(p):
        raise AppError(409, "POSTING_CLOSED", "이미 마감된 공고입니다.")
    return n, p


@router.post("/notifications/{nid}/extend")
async def extend(nid: uuid.UUID, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                 db: AsyncSession = Depends(get_db)) -> dict:
    """'7일 연장': the deadline moves 7 days later (end of that day, Korea time)."""
    n, p = await _deadline_note(db, ctx, nid)
    new_day: date = p.deadline_at.astimezone(KST).date() + timedelta(days=EXTEND_DAYS)
    p.deadline_at = kst_end_of_day(new_day)
    p.updated_at = utcnow()
    p.row_version += 1
    n.done_text = f"마감일을 {new_day.month}.{new_day.day}로 늘렸습니다"
    n.read_at = n.read_at or utcnow()
    await audit(db, action="POSTING_EXTEND", target_type="posting", target_id=p.id, actor_id=ctx.user_id, actor_role="RECRUITER",
                company_id=ctx.company_id, days=EXTEND_DAYS)
    await db.commit()
    return {"done_text": n.done_text}


@router.post("/notifications/{nid}/close")
async def close_now(nid: uuid.UUID, ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> dict:
    n, p = await _deadline_note(db, ctx, nid)
    await service.close(db, p, "MANUAL")
    n.done_text = "공고를 마감했습니다"
    n.read_at = n.read_at or utcnow()
    await audit(db, action="POSTING_CLOSE", target_type="posting", target_id=p.id, actor_id=ctx.user_id, actor_role="RECRUITER",
                company_id=ctx.company_id, via="notification")
    await db.commit()
    return {"done_text": n.done_text}
