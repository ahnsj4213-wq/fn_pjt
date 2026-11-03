"""문의 쪽지 (SC-R15): conversations of a company with applicants and with the operator.

The recruiter side lists the company's threads, opens one (which marks it read) and answers. The applicant side starts
or continues a thread about one of their own applications; the operator writes from the operator screens
(`open_operator_thread`). A thread is always about exactly one company, so every query is scoped by it.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import CandidateCtx, RecruiterCtx, require_candidate, require_recruiter
from app.auth.scope import get_for_company, get_for_owner
from app.core.db import get_db
from app.core.errors import AppError
from app.core.timeutil import utcnow
from app.models import Application, InquiryMessage, InquiryThread, JobPosting, User

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
candidate_router = APIRouter(prefix="/api/candidate", tags=["candidate"])

OPERATOR_LABEL = "운영팀"


class ThreadOut(BaseModel):
    id: uuid.UUID
    kind: str  # APPLICANT | OPERATOR
    sender: str
    subject: str
    last_at: datetime
    unread: bool


class MessageOut(BaseModel):
    id: uuid.UUID
    mine: bool
    sender: str
    body: str
    created_at: datetime


class ThreadDetail(BaseModel):
    id: uuid.UUID
    kind: str
    sender: str
    subject: str
    messages: list[MessageOut]


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=2000)


class NewInquiryIn(ReplyIn):
    subject: str = Field(min_length=1, max_length=100)


def _sender(t: InquiryThread) -> str:
    return OPERATOR_LABEL if t.kind == "OPERATOR" else (t.applicant_name or "응시자")


def _unread(t: InquiryThread) -> bool:
    return t.last_from == "OTHER" and (t.company_read_at is None or t.company_read_at < t.last_at)


async def _add_message(db: AsyncSession, t: InquiryThread, side: str, label: str, body: str) -> None:
    now = utcnow()
    db.add(InquiryMessage(thread_id=t.id, from_side=side, sender_label=label, body=body, created_at=now))
    t.last_from, t.last_at = side, now
    if side == "COMPANY":
        t.company_read_at = now


async def open_operator_thread(db: AsyncSession, company_id: uuid.UUID, subject: str, body: str) -> None:
    """The operator writes to a company (for example the reason a verification was refused). Caller commits."""
    t = InquiryThread(company_id=company_id, kind="OPERATOR", subject=subject[:100], last_from="OTHER")
    db.add(t)
    await db.flush()
    await _add_message(db, t, "OTHER", OPERATOR_LABEL, body[:2000])


# ---- recruiter side ----
@router.get("/inquiries")
async def list_threads(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> list[ThreadOut]:
    rows = (await db.execute(select(InquiryThread).where(InquiryThread.company_id == ctx.company_id)
                             .order_by(InquiryThread.last_at.desc()).limit(200))).scalars().all()
    return [ThreadOut(id=t.id, kind=t.kind, sender=_sender(t), subject=t.subject, last_at=t.last_at, unread=_unread(t)) for t in rows]


@router.get("/inquiries/unread-count")
async def unread_count(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> dict:
    n = await db.scalar(select(func.count()).select_from(InquiryThread).where(
        InquiryThread.company_id == ctx.company_id, InquiryThread.last_from == "OTHER",
        (InquiryThread.company_read_at.is_(None)) | (InquiryThread.company_read_at < InquiryThread.last_at)))
    return {"unread": int(n or 0)}


async def _messages(db: AsyncSession, t: InquiryThread, mine_side: str) -> list[MessageOut]:
    rows = (await db.execute(select(InquiryMessage).where(InquiryMessage.thread_id == t.id)
                             .order_by(InquiryMessage.created_at, InquiryMessage.id))).scalars().all()
    return [MessageOut(id=m.id, mine=m.from_side == mine_side, sender=m.sender_label, body=m.body, created_at=m.created_at) for m in rows]


@router.get("/inquiries/{thread_id}")
async def open_thread(thread_id: uuid.UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                      db: AsyncSession = Depends(get_db)) -> ThreadDetail:
    t = await get_for_company(db, InquiryThread, thread_id, ctx.company_id, for_update=True)
    if _unread(t):
        t.company_read_at = utcnow()
        await db.commit()
    return ThreadDetail(id=t.id, kind=t.kind, sender=_sender(t), subject=t.subject, messages=await _messages(db, t, "COMPANY"))


@router.post("/inquiries/{thread_id}/messages", status_code=201)
async def reply(thread_id: uuid.UUID, body: ReplyIn, ctx: RecruiterCtx = Depends(require_recruiter),
                db: AsyncSession = Depends(get_db)) -> ThreadDetail:
    t = await get_for_company(db, InquiryThread, thread_id, ctx.company_id, for_update=True)
    company = await db.scalar(select(User.name).where(User.id == ctx.user_id))
    await _add_message(db, t, "COMPANY", company or "채용담당자", body.body)
    await audit(db, action="INQUIRY_REPLY", target_type="inquiry_thread", target_id=t.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()
    return ThreadDetail(id=t.id, kind=t.kind, sender=_sender(t), subject=t.subject, messages=await _messages(db, t, "COMPANY"))


# ---- applicant side: ask the company about one's own application ----
@candidate_router.get("/applications/{application_id}/inquiry")
async def my_thread(application_id: uuid.UUID, ctx: CandidateCtx = Depends(require_candidate),
                    db: AsyncSession = Depends(get_db)) -> ThreadDetail | None:
    app = await get_for_owner(db, Application, application_id, ctx.user_id)
    t = await _applicant_thread(db, app)
    if t is None:
        return None
    return ThreadDetail(id=t.id, kind=t.kind, sender=_sender(t), subject=t.subject, messages=await _messages(db, t, "OTHER"))


async def _applicant_thread(db: AsyncSession, app: Application) -> InquiryThread | None:
    return (await db.execute(select(InquiryThread).where(
        InquiryThread.kind == "APPLICANT", InquiryThread.applicant_id == app.candidate_id,
        InquiryThread.posting_id == app.posting_id, InquiryThread.company_id == app.company_id))).scalar_one_or_none()


@candidate_router.post("/applications/{application_id}/inquiry", status_code=201)
async def ask(application_id: uuid.UUID, body: NewInquiryIn, ctx: CandidateCtx = Depends(require_candidate),
              db: AsyncSession = Depends(get_db)) -> ThreadDetail:
    app = await get_for_owner(db, Application, application_id, ctx.user_id)
    if not app.share_result:  # practice mode: the company never sees it, so there is nobody to ask
        raise AppError(409, "PRACTICE_MODE", "연습 모드로 본 면접은 기업에 문의할 수 없습니다.")
    name = await db.scalar(select(User.name).where(User.id == ctx.user_id))
    title = await db.scalar(select(JobPosting.title).where(JobPosting.id == app.posting_id))
    t = await _applicant_thread(db, app)
    if t is None:
        t = InquiryThread(company_id=app.company_id, kind="APPLICANT", applicant_id=ctx.user_id, applicant_name=name or "응시자",
                          posting_id=app.posting_id, subject=(body.subject or f"{title} 문의")[:100], last_from="OTHER")
        db.add(t)
        await db.flush()
    await _add_message(db, t, "OTHER", name or "응시자", body.body)
    await db.commit()
    return ThreadDetail(id=t.id, kind=t.kind, sender=_sender(t), subject=t.subject, messages=await _messages(db, t, "OTHER"))
