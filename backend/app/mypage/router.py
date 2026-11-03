"""My Page (screen spec SC-A11 프로필, SC-A15 면접 일정). The candidate's own rows only, by the session user id."""
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CandidateCtx, require_candidate
from app.auth.router import check_phone, issue_reset
from app.core.db import get_db
from app.models import Company, InterviewSchedule, User

router = APIRouter(prefix="/api/candidate", tags=["candidate"])


class ProfileOut(BaseModel):
    name: str
    email: str
    birth_date: date | None
    gender: str | None
    phone: str | None


class ProfileIn(BaseModel):
    """SC-A11: the id (email) cannot change. Birth date and gender stay on the account, never in AI inputs."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    birth_date: date | None = None
    gender: str | None = None
    phone: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not 1 <= len(v) <= 50:
            raise ValueError("name length")
        return v

    @field_validator("birth_date")
    @classmethod
    def _birth(cls, v: date | None) -> date | None:
        if v is not None and (v > date.today() or v.year < 1900):
            raise ValueError("birth_date range")
        return v

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str | None) -> str | None:
        if v is not None and v not in ("M", "F"):
            raise ValueError("gender M|F")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return check_phone(v)


def _profile(u: User) -> ProfileOut:
    return ProfileOut(name=u.name or "", email=u.email or "", birth_date=u.birth_date, gender=u.gender, phone=u.phone)


@router.get("/profile")
async def get_profile(ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> ProfileOut:
    return _profile(await db.get(User, ctx.user_id))


@router.put("/profile")
async def put_profile(body: ProfileIn, ctx: CandidateCtx = Depends(require_candidate),
                      db: AsyncSession = Depends(get_db)) -> ProfileOut:
    u = await db.get(User, ctx.user_id, with_for_update=True)
    u.name, u.birth_date, u.gender, u.phone = body.name, body.birth_date, body.gender, body.phone
    await db.commit()
    return _profile(u)


@router.post("/profile/password-link", status_code=202)
async def password_link(ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    """SC-A11 '비밀번호 변경': the reset mail (same token flow as SC-A12) goes to the account's own address."""
    u = await db.get(User, ctx.user_id)
    await issue_reset(db, u)
    await db.commit()
    return {"email": u.email}


class ScheduleOut(BaseModel):
    id: UUID
    application_id: UUID
    company_name: str
    title: str
    starts_at: datetime
    place: str | None
    status: str  # PROPOSED | ACCEPTED | RESCHEDULE_REQUESTED


@router.get("/schedules")
async def list_schedules(ctx: CandidateCtx = Depends(require_candidate),
                         db: AsyncSession = Depends(get_db)) -> list[ScheduleOut]:
    rows = (await db.execute(select(InterviewSchedule, Company.name).join(Company, Company.id == InterviewSchedule.company_id)
                             .where(InterviewSchedule.candidate_id == ctx.user_id)
                             .order_by(InterviewSchedule.starts_at))).all()
    return [ScheduleOut(id=s.id, application_id=s.application_id, company_name=name, title=s.title,
                        starts_at=s.starts_at, place=s.place, status=s.status) for s, name in rows]
