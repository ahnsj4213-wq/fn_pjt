"""기업 일정 관리 (SC-R11), 응시자에게 메일 보내기 (SC-R14), and the applicant's answer to a mailed interview date.

The calendar of a company = its own entries (면접, 결과 발표, 내부 일정) + posting deadlines of published postings
(automatic, read-only) + interview dates mailed to applicants (automatic, editable). Everything is scoped by the
company of the session. A mailed date carries a random token (only its SHA-256 is stored); the applicant answers with
the link in the mail, without logging in.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import RecruiterCtx, check_origin, require_recruiter
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, not_found
from app.core.timeutil import KST, utcnow
from app.jobs.queue import enqueue
from app.notify.inbox import schedule_answered
from app.models import Application, Company, InterviewSchedule, JobPosting, RecruiterMail, User
from app.recruiter.router import _app

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
public_router = APIRouter(prefix="/api", tags=["common"])


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class Entry(BaseModel):
    id: str
    starts_at: datetime
    kind: Literal["DEADLINE", "INTERVIEW", "RESULT", "INTERNAL"]
    content: str
    place: str | None = None
    auto: bool
    editable: bool
    status: str | None = None  # PROPOSED | ACCEPTED | RESCHEDULE_REQUESTED for mailed dates
    application_id: uuid.UUID | None = None
    candidate_name: str | None = None


def _month_range(month: str | None) -> tuple[datetime, datetime]:
    today = datetime.now(KST).date()
    y, m = (int(month[:4]), int(month[5:7])) if month else (today.year, today.month)
    start = datetime(y, m, 1, tzinfo=KST)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=KST)
    return start, end


async def _entries(db: AsyncSession, ctx: RecruiterCtx, start: datetime, end: datetime) -> list[Entry]:
    out: list[Entry] = []
    rows = (await db.execute(
        select(InterviewSchedule, User.name).join(Application, Application.id == InterviewSchedule.application_id, isouter=True)
        .join(User, User.id == InterviewSchedule.candidate_id, isouter=True)
        .where(InterviewSchedule.company_id == ctx.company_id, InterviewSchedule.starts_at >= start,
               InterviewSchedule.starts_at < end))).all()
    for s, name in rows:
        mailed = s.application_id is not None
        out.append(Entry(id=str(s.id), starts_at=s.starts_at, kind=s.kind, content=s.title, place=s.place, auto=mailed,
                         editable=True, status=s.status, application_id=s.application_id, candidate_name=name))
    postings = (await db.execute(select(JobPosting).where(
        JobPosting.company_id == ctx.company_id, JobPosting.status == "OPEN", JobPosting.deleted_at.is_(None),
        JobPosting.deadline_at >= start, JobPosting.deadline_at < end))).scalars()
    for p in postings:
        out.append(Entry(id=f"deadline:{p.id}", starts_at=p.deadline_at, kind="DEADLINE", content=f"{p.title} 공고 마감",
                         auto=True, editable=False))
    return sorted(out, key=lambda e: e.starts_at)


@router.get("/schedule")
async def month_schedule(month: str | None = None, ctx: RecruiterCtx = Depends(require_recruiter),
                         db: AsyncSession = Depends(get_db)) -> list[Entry]:
    if month is not None and not (len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:].isdigit()
                                  and 1 <= int(month[5:]) <= 12):
        raise AppError(422, "VALIDATION", "month는 YYYY-MM 형식이어야 합니다.")
    return await _entries(db, ctx, *_month_range(month))


@router.get("/schedule/upcoming")
async def upcoming(limit: int = 4, ctx: RecruiterCtx = Depends(require_recruiter),
                   db: AsyncSession = Depends(get_db)) -> list[Entry]:
    """SC-R13 다가오는 일정: from today (KST), looking 90 days ahead."""
    start = datetime.combine(datetime.now(KST).date(), datetime.min.time(), tzinfo=KST)
    items = await _entries(db, ctx, start, start + timedelta(days=90))
    return items[:max(1, min(limit, 20))]


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    starts_at: datetime
    kind: Literal["INTERVIEW", "RESULT", "INTERNAL"] = "INTERNAL"
    content: str = Field(min_length=1, max_length=200)
    place: str | None = Field(None, max_length=200)


@router.post("/schedule", status_code=201)
async def add_entry(body: EntryIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                    db: AsyncSession = Depends(get_db)) -> Entry:
    s = InterviewSchedule(company_id=ctx.company_id, title=body.content, starts_at=body.starts_at, place=body.place,
                          kind=body.kind, created_by=ctx.user_id)
    db.add(s)
    await db.flush()
    await audit(db, action="SCHEDULE_ADD", target_type="schedule", target_id=s.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()
    return Entry(id=str(s.id), starts_at=s.starts_at, kind=s.kind, content=s.title, place=s.place, auto=False, editable=True)


async def _own(db: AsyncSession, ctx: RecruiterCtx, sid: str, lock: bool = False) -> InterviewSchedule:
    try:
        key = uuid.UUID(sid)
    except ValueError:
        raise not_found() from None
    s = await db.get(InterviewSchedule, key, with_for_update=lock)
    if s is None or s.company_id != ctx.company_id:
        raise not_found()
    return s


@router.put("/schedule/{sid}")
async def edit_entry(sid: str, body: EntryIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                     db: AsyncSession = Depends(get_db)) -> Entry:
    if sid.startswith("deadline:"):
        raise AppError(409, "AUTO_ENTRY", "공고 마감일은 공고 관리에서 공고를 수정하면 바뀝니다.")
    s = await _own(db, ctx, sid, lock=True)
    moved = s.starts_at != body.starts_at
    s.title, s.starts_at, s.place = body.content, body.starts_at, body.place
    if s.application_id is None:
        s.kind = body.kind  # a mailed date stays an interview
    elif moved and s.status == "ACCEPTED":
        # SC-R11: the applicant already accepted another time, so tell them (a new answer link comes with the mail).
        token = secrets.token_urlsafe(32)
        s.response_token_hash = _hash(token)
        company = await db.get(Company, ctx.company_id)
        mail = RecruiterMail(application_id=s.application_id, company_id=ctx.company_id, sender_id=ctx.user_id, kind="INTERVIEW",
                             subject=f"[{company.name}] 면접 일정이 변경되었습니다",
                             body="안내드린 면접 일정이 아래와 같이 변경되었습니다.", schedule_id=s.id)
        db.add(mail)
        await db.flush()
        await _queue(db, mail, token)
    await audit(db, action="SCHEDULE_EDIT", target_type="schedule", target_id=s.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()
    return Entry(id=str(s.id), starts_at=s.starts_at, kind=s.kind, content=s.title, place=s.place, auto=s.application_id is not None,
                 editable=True, status=s.status, application_id=s.application_id)


@router.delete("/schedule/{sid}", status_code=204)
async def delete_entry(sid: str, ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> None:
    if sid.startswith("deadline:"):
        raise AppError(409, "AUTO_ENTRY", "공고 마감일은 공고 관리에서 공고를 수정하면 바뀝니다.")
    s = await _own(db, ctx, sid, lock=True)
    await audit(db, action="SCHEDULE_DELETE", target_type="schedule", target_id=s.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.delete(s)
    await db.commit()


# ---- SC-R14 메일 보내기 ----
class MailIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["INTERVIEW", "RESULT", "FREE"]
    subject: str = Field(min_length=1, max_length=180)  # without the [회사명] prefix
    body: str = Field(max_length=5000)
    starts_at: datetime | None = None
    place: str | None = Field(None, max_length=200)


async def _queue(db: AsyncSession, mail: RecruiterMail, token: str | None) -> None:
    if not get_settings().mail_enabled:
        return
    await enqueue(db, "send_email", {"template": "RECRUITER_MAIL", "mail_id": str(mail.id), "token": token},
                  application_id=mail.application_id)


@router.post("/applications/{aid}/mail", status_code=201)
async def send_mail(aid: uuid.UUID, body: MailIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                    db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid, lock=True)
    if body.kind == "INTERVIEW" and body.starts_at is None:
        raise AppError(422, "VALIDATION", "면접 날짜를 골라 주십시오.", fields=[{"loc": ["body", "starts_at"], "type": "missing"}])
    company = await db.get(Company, ctx.company_id)
    schedule, token = None, None
    if body.kind == "INTERVIEW":
        # A pending date for this applicant is replaced by the new one (SC-R14).
        await db.execute(delete(InterviewSchedule).where(
            InterviewSchedule.application_id == a.id, InterviewSchedule.status.in_(["PROPOSED", "RESCHEDULE_REQUESTED"])))
        token = secrets.token_urlsafe(32)
        schedule = InterviewSchedule(application_id=a.id, company_id=a.company_id, candidate_id=a.candidate_id, title="2차 면접",
                                     starts_at=body.starts_at, place=body.place, status="PROPOSED", kind="INTERVIEW",
                                     response_token_hash=_hash(token), created_by=ctx.user_id)
        db.add(schedule)
        await db.flush()
    mail = RecruiterMail(application_id=a.id, company_id=a.company_id, sender_id=ctx.user_id, kind=body.kind,
                         subject=f"[{company.name}] {body.subject}", body=body.body, schedule_id=schedule.id if schedule else None)
    db.add(mail)
    await db.flush()
    await _queue(db, mail, token)
    await audit(db, action="MAIL_SEND", target_type="application", target_id=a.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id, kind=body.kind)  # never the text
    await db.commit()
    return {"mail_id": str(mail.id), "schedule_id": str(schedule.id) if schedule else None}


# ---- the applicant's answer, from the link in the mail (no login) ----
class ScheduleInfo(BaseModel):
    company_name: str
    title: str
    starts_at: datetime
    place: str | None
    status: str


async def _by_token(db: AsyncSession, token: str, lock: bool = False) -> InterviewSchedule:
    stmt = select(InterviewSchedule).where(InterviewSchedule.response_token_hash == _hash(token))
    s = (await db.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if s is None:
        raise not_found()
    return s


def _info(s: InterviewSchedule, company: Company) -> ScheduleInfo:
    return ScheduleInfo(company_name=company.name, title=s.title, starts_at=s.starts_at, place=s.place, status=s.status or "")


@public_router.get("/schedule-response")
async def schedule_info(token: str, db: AsyncSession = Depends(get_db)) -> ScheduleInfo:
    s = await _by_token(db, token)
    return _info(s, await db.get(Company, s.company_id))


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=100)
    answer: Literal["ACCEPT", "RESCHEDULE"]


@public_router.post("/schedule-response")
async def schedule_answer(body: AnswerIn, request: Request, db: AsyncSession = Depends(get_db)) -> ScheduleInfo:
    check_origin(request)
    s = await _by_token(db, body.token, lock=True)
    if s.starts_at <= utcnow():
        raise AppError(409, "SCHEDULE_PAST", "이미 지난 일정입니다.")
    s.status = "ACCEPTED" if body.answer == "ACCEPT" else "RESCHEDULE_REQUESTED"
    name = await db.scalar(select(User.name).where(User.id == s.candidate_id))
    await schedule_answered(db, s.company_id, s.application_id, name or "지원자", s.starts_at, body.answer == "ACCEPT", s.id)
    await audit(db, action="SCHEDULE_ANSWER", target_type="schedule", target_id=s.id, actor_id=s.candidate_id,
                actor_role="CANDIDATE", company_id=s.company_id, answer=body.answer)
    await db.commit()
    return _info(s, await db.get(Company, s.company_id))

