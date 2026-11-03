"""Signup (candidates only), login with role selection, logout, current user (USR-01~06)."""
import hashlib
import re
import secrets
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import throttle
from app.auth.deps import check_origin, get_principal
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import Principal, create_session, revoke
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.timeutil import utcnow
from app.models import Company, PasswordResetToken, User
from app.models import Session as SessionRow
from app.notify.mail import queue_account_mail

router = APIRouter(prefix="/api/auth", tags=["common"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^01[016789]-\d{3,4}-\d{4}$")
_PASSWORD = re.compile(r"^[A-Za-z\d!@#$%^&*._-]{8,20}$")
AUTH_FAILED_MESSAGE = "이메일 또는 비밀번호가 올바르지 않습니다."


class SignupIn(BaseModel):
    """SC-A01. Birth date and gender stay on the account; nothing in the evaluation path reads them."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    birth_date: date
    email: str
    password: str
    password_confirm: str
    gender: str
    phone: str
    terms_agreed: bool

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not 1 <= len(v) <= 50:
            raise ValueError("name length")
        return v

    @field_validator("birth_date")
    @classmethod
    def _birth(cls, v: date) -> date:
        if v > date.today() or v.year < 1900:
            raise ValueError("birth_date range")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return check_email(v)

    @field_validator("gender")
    @classmethod
    def _gender(cls, v: str) -> str:
        if v not in ("M", "F"):
            raise ValueError("gender M|F")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return check_phone(v)

    @field_validator("terms_agreed")
    @classmethod
    def _terms(cls, v: bool) -> bool:
        if not v:
            raise ValueError("terms must be agreed")
        return v

    @model_validator(mode="after")
    def _passwords(self) -> "SignupIn":
        check_password(self.password)
        if self.password != self.password_confirm:
            raise ValueError("password_confirm mismatch")
        return self


def check_email(v: str) -> str:
    v = v.lower()
    if len(v) > 254 or not _EMAIL.match(v):
        raise ValueError("email format")
    return v


def check_phone(v: str) -> str:
    if not _PHONE.match(v):
        raise ValueError("phone format 010-0000-0000")
    return v


def check_password(v: str) -> str:
    """Screen spec: letters and digits mixed, 8~20 characters, specials only from !@#$%^&*._-"""
    if not _PASSWORD.match(v) or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("password: letters+digits, 8-20")
    return v


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: str
    email: str
    password: str

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ("CANDIDATE", "RECRUITER"):
            raise ValueError("role")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return check_email(v)  # USR-01 format check


class UserOut(BaseModel):
    id: UUID
    role: str
    name: str
    company_id: UUID | None = None
    company_name: str | None = None


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str


@router.post("/signup", status_code=201)
async def signup(body: SignupIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    check_origin(request)
    user = User(role="CANDIDATE", email=body.email, password_hash=await hash_password(body.password),
                name=body.name, phone=body.phone, birth_date=body.birth_date, gender=body.gender,
                terms_agreed_at=utcnow())
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # OQ-34: generic wording; signup necessarily reveals duplicates, mitigated by rate limiting.
        raise AppError(409, "EMAIL_UNAVAILABLE", "사용할 수 없는 이메일입니다.")
    return {"user_id": str(user.id)}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> SessionOut:
    check_origin(request)
    ekey, ikey = throttle.email_key(body.email), throttle.ip_key(_client_ip(request))
    if await throttle.is_locked(db, [ekey, ikey]):
        raise AppError(429, "TOO_MANY_ATTEMPTS", "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    # Always run the hash check (dummy hash for unknown users) before comparing role/status (USR-04).
    ok = await verify_password(user.password_hash if user else None, body.password)
    # Operators sign in on the 기업회원 tab (screen spec SC-C01 has two tabs); the session carries the real role.
    role_ok = user is not None and (user.role == body.role or (user.role == "OPERATOR" and body.role == "RECRUITER"))
    if not ok or not role_ok or user.status != "ACTIVE":
        await throttle.record_failure(db, ekey, throttle.EMAIL_LIMIT)
        await throttle.record_failure(db, ikey, throttle.IP_LIMIT)
        await db.commit()
        raise AppError(401, "AUTH_FAILED", AUTH_FAILED_MESSAGE)
    await throttle.clear(db, ekey)
    if needs_rehash(user.password_hash):  # G-07: upgrade hashes when Argon2 parameters change
        user.password_hash = await hash_password(body.password)
    return await start_session(db, user, response)


async def start_session(db: AsyncSession, user: User, response: Response) -> SessionOut:
    """Opens the session, commits, and sets the cookie. Shared by login and company signup."""
    token, csrf = await create_session(db, user)
    company_name = None
    if user.company_id:
        company_name = (await db.get(Company, user.company_id)).name
    await db.commit()
    s = get_settings()
    response.set_cookie(s.cookie_name, token, httponly=True, secure=s.cookie_secure, samesite="strict", path="/")
    return SessionOut(user=UserOut(id=user.id, role=user.role, name=user.name or "", company_id=user.company_id,
                                   company_name=company_name), csrf_token=csrf)


@router.post("/logout", status_code=204)
async def logout(response: Response, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> None:
    await revoke(db, p.token_hash)
    await db.commit()
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.get("/me")
async def me(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> SessionOut:
    company_name = None
    if p.company_id:
        company_name = (await db.get(Company, p.company_id)).name
    return SessionOut(user=UserOut(id=p.user_id, role=p.role, name=p.name, company_id=p.company_id,
                                   company_name=company_name), csrf_token=p.csrf_token)


# ── account recovery (SC-A12, SC-A11) ─────────────────────────────────────────
RESET_TTL = timedelta(minutes=30)
RESET_SENT_MESSAGE = "비밀번호 재설정 링크를 보냈습니다."


class FindIdIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=50)
    phone: str

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return check_phone(v)


class ResetRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return check_email(v)


class ResetConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)
    password: str
    password_confirm: str

    @model_validator(mode="after")
    def _passwords(self) -> "ResetConfirmIn":
        check_password(self.password)
        if self.password != self.password_confirm:
            raise ValueError("password_confirm mismatch")
        return self


def mask_email(email: str) -> str:
    """jiwoo@gmail.com -> jiw***@gmail.com (the first three characters of the local part stay)."""
    local, _, domain = email.partition("@")
    return f"{local[:3]}***@{domain}"


async def _throttled(db: AsyncSession, request: Request, scope: str) -> None:
    key = f"{scope}:{throttle.ip_key(_client_ip(request))}"
    if await throttle.is_locked(db, [key]):
        raise AppError(429, "TOO_MANY_ATTEMPTS", "시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    await throttle.record_failure(db, key, throttle.IP_LIMIT)  # every lookup counts, found or not


@router.post("/find-id")
async def find_id(body: FindIdIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    check_origin(request)
    await _throttled(db, request, "find-id")
    rows = (await db.execute(select(User.email).where(User.name == body.name, User.phone == body.phone,
                                                      User.role == "CANDIDATE", User.status == "ACTIVE"))).scalars().all()
    await db.commit()
    return {"emails": [mask_email(e) for e in rows if e]}


async def issue_reset(db: AsyncSession, user: User) -> None:
    """Stores only the hash; the raw token travels in the mail job payload and expires in 30 minutes."""
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(token_hash=hashlib.sha256(raw.encode()).digest(), user_id=user.id,
                              expires_at=utcnow() + RESET_TTL))
    await queue_account_mail(db, "PASSWORD_RESET", user.id, {"token": raw})


@router.post("/password-reset", status_code=202)
async def request_reset(body: ResetRequestIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    check_origin(request)
    await _throttled(db, request, "reset")
    user = (await db.execute(select(User).where(User.email == body.email, User.status == "ACTIVE"))).scalar_one_or_none()
    if user is not None:
        await issue_reset(db, user)
    await db.commit()
    return {"message": RESET_SENT_MESSAGE}  # same answer whether the address exists or not


@router.post("/password-reset/confirm", status_code=204)
async def confirm_reset(body: ResetConfirmIn, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    check_origin(request)
    row = await db.get(PasswordResetToken, hashlib.sha256(body.token.encode()).digest(), with_for_update=True)
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        raise AppError(400, "RESET_TOKEN_INVALID", "링크가 만료되었거나 이미 사용되었습니다. 다시 요청해 주세요.")
    user = await db.get(User, row.user_id)
    user.password_hash = await hash_password(body.password)
    row.used_at = utcnow()
    await db.execute(delete(SessionRow).where(SessionRow.user_id == user.id))  # sign out everywhere
    await db.commit()
