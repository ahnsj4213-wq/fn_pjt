"""Signup (candidates only), login with role selection, logout, current user (USR-01~06)."""
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import throttle
from app.auth.deps import check_origin, get_principal
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import Principal, create_session, revoke
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.models import Company, User

router = APIRouter(prefix="/api/auth", tags=["common"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^010-\d{4}-\d{4}$")
AUTH_FAILED_MESSAGE = "이메일 또는 비밀번호가 올바르지 않습니다."


class SignupIn(BaseModel):
    # extra="forbid": birth date, gender, role or any other field is rejected (USR-02, AC-01, SC-13).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    email: str
    password: str
    password_confirm: str
    phone: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not 1 <= len(v) <= 50:
            raise ValueError("name length")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.lower()
        if len(v) > 254 or not _EMAIL.match(v):
            raise ValueError("email format")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if not _PHONE.match(v):
            raise ValueError("phone format 010-0000-0000")
        return v

    @model_validator(mode="after")
    def _passwords(self) -> "SignupIn":
        if not 8 <= len(self.password) <= 128:
            raise ValueError("password length 8-128")
        if self.password != self.password_confirm:
            raise ValueError("password_confirm mismatch")
        if self.password.lower() == self.email:
            raise ValueError("password equals email")
        return self


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
        v = v.lower()
        if len(v) > 254 or not _EMAIL.match(v):
            raise ValueError("email format")  # USR-01 format check
        return v


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
                name=body.name, phone=body.phone)
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
    if not ok or user is None or user.role != body.role or user.status != "ACTIVE":
        await throttle.record_failure(db, ekey, throttle.EMAIL_LIMIT)
        await throttle.record_failure(db, ikey, throttle.IP_LIMIT)
        await db.commit()
        raise AppError(401, "AUTH_FAILED", AUTH_FAILED_MESSAGE)
    await throttle.clear(db, ekey)
    if needs_rehash(user.password_hash):  # G-07: upgrade hashes when Argon2 parameters change
        user.password_hash = await hash_password(body.password)
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
