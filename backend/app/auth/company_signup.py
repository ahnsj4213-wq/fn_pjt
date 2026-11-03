"""기업회원 가입 (screen spec SC-R01).

A recruiter proves the mailbox with a 6-digit code, then signs up with the company. Company-domain mail is approved
at once; personal mail (FREE_MAIL_DOMAINS) leaves the company PENDING until an operator approves it, and a PENDING
company cannot publish postings (app/postings/service.py publish). The 재직증명서 or 사업자등록증 goes up after signup,
with the new session, to /api/recruiter/company/evidence.

Codes: only the SHA-256 is stored, 10 minutes, 5 wrong tries per code. Sending is throttled per IP like the other
anonymous account endpoints.
"""
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import RecruiterCtx, check_origin, require_recruiter
from app.auth.passwords import hash_password
from app.auth.router import SessionOut, _throttled, check_email, check_password, start_session
from app.core.db import get_db
from app.core.errors import AppError
from app.core.storage import get_storage
from app.core.timeutil import utcnow
from app.models import Company, SignupCode, User
from app.notify.mail import queue_signup_code

router = APIRouter(prefix="/api", tags=["common"])

# Screen spec SC-R01: personal mail services are never approved automatically.
FREE_MAIL_DOMAINS = frozenset({"gmail.com", "naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com",
                               "outlook.com", "hotmail.com"})
CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
_BIZ_NO = re.compile(r"^\d{3}-\d{2}-\d{5}$")
EVIDENCE_MAX_BYTES = 10 * 1024 * 1024
EVIDENCE_TYPES = {
    ".pdf": (lambda d: b"%PDF-" in d[:1024], "application/pdf"),
    ".png": (lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"),
    ".jpg": (lambda d: d.startswith(b"\xff\xd8\xff"), "image/jpeg"),
    ".jpeg": (lambda d: d.startswith(b"\xff\xd8\xff"), "image/jpeg"),
}
CODE_INVALID = ("SIGNUP_CODE_INVALID", "인증번호가 맞지 않거나 만료되었습니다. 다시 받아 주십시오.")


def is_free_mail(email: str) -> bool:
    return email.rsplit("@", 1)[-1].lower() in FREE_MAIL_DOMAINS


def _hash(code: str) -> bytes:
    return hashlib.sha256(code.encode()).digest()


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return check_email(v)


class CompanySignupIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company_name: str = Field(min_length=1, max_length=100)
    biz_no: str
    name: str = Field(min_length=1, max_length=50)
    email: str
    code: str = Field(pattern=r"^\d{6}$")
    password: str
    password_confirm: str

    @field_validator("biz_no")
    @classmethod
    def _biz(cls, v: str) -> str:
        if not _BIZ_NO.match(v):
            raise ValueError("biz_no format 000-00-00000")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return check_email(v)

    @model_validator(mode="after")
    def _passwords(self) -> "CompanySignupIn":
        check_password(self.password)
        if self.password != self.password_confirm:
            raise ValueError("password_confirm mismatch")
        return self


@router.post("/auth/company-signup/code", status_code=202)
async def send_code(body: CodeIn, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    check_origin(request)
    await _throttled(db, request, "signup-code")
    raw = f"{secrets.randbelow(1_000_000):06d}"
    row = SignupCode(email=body.email, code_hash=_hash(raw), expires_at=utcnow() + CODE_TTL)
    db.add(row)
    await db.flush()
    await queue_signup_code(db, row.id, raw)
    await db.commit()
    return {"free_mail": is_free_mail(body.email), "expires_in_sec": int(CODE_TTL.total_seconds())}


async def _consume_code(db: AsyncSession, email: str, code: str) -> None:
    """The newest unused code for the address must match. A wrong try counts against that code (committed even
    though the signup fails), so guessing stops after CODE_MAX_ATTEMPTS."""
    row = (await db.execute(select(SignupCode).where(SignupCode.email == email, SignupCode.used_at.is_(None))
                            .order_by(SignupCode.created_at.desc()).limit(1).with_for_update())).scalar_one_or_none()
    if row is None or row.expires_at < utcnow() or row.attempts >= CODE_MAX_ATTEMPTS:
        raise AppError(400, *CODE_INVALID)
    if not hmac.compare_digest(row.code_hash, _hash(code)):
        row.attempts += 1
        await db.commit()
        raise AppError(400, *CODE_INVALID)
    row.used_at = utcnow()


@router.post("/auth/company-signup", status_code=201)
async def company_signup(body: CompanySignupIn, request: Request, response: Response,
                         db: AsyncSession = Depends(get_db)) -> SessionOut:
    check_origin(request)
    await _consume_code(db, body.email, body.code)
    free = is_free_mail(body.email)
    now = utcnow()
    company = Company(name=body.company_name, biz_no=body.biz_no, verification="PENDING" if free else "APPROVED",
                      verification_method=None if free else "DOMAIN_MAIL", verified_at=None if free else now)
    db.add(company)
    await db.flush()
    user = User(role="RECRUITER", company_id=company.id, email=body.email, name=body.name,
                password_hash=await hash_password(body.password), terms_agreed_at=now)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppError(409, "EMAIL_UNAVAILABLE", "사용할 수 없는 이메일입니다.")
    await audit(db, action="COMPANY_SIGNUP", target_type="company", target_id=company.id, actor_id=user.id,
                actor_role="RECRUITER", company_id=company.id)
    return await start_session(db, user, response)


class CompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    biz_no: str | None
    verification: str
    evidence_name: str | None
    reject_reason: str | None
    suspended: bool


def company_out(c: Company) -> CompanyOut:
    return CompanyOut(id=c.id, name=c.name, biz_no=c.biz_no, verification=c.verification, evidence_name=c.evidence_name,
                      reject_reason=c.reject_reason, suspended=c.suspended_at is not None)


@router.get("/recruiter/company")
async def my_company(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> CompanyOut:
    return company_out(await db.get(Company, ctx.company_id))


@router.post("/recruiter/company/evidence")
async def upload_evidence(file: UploadFile, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)) -> CompanyOut:
    """재직증명서 또는 사업자등록증 for the operator (SC-O01). Replaces an earlier file while the company is PENDING;
    a REJECTED company may apply again with a new file, which puts it back to PENDING."""
    company = await db.get(Company, ctx.company_id, with_for_update=True)
    if company.verification not in ("PENDING", "REJECTED"):
        raise AppError(409, "INVALID_STATE", "승인 대기 중이거나 반려된 기업만 증빙 서류를 올릴 수 있습니다.", current=company.verification)
    buf = bytearray()
    while chunk := await file.read(64 * 1024):
        buf += chunk
        if len(buf) > EVIDENCE_MAX_BYTES:
            raise AppError(413, "FILE_TOO_LARGE", "파일 크기는 10MB 이하여야 합니다.")
    ext = Path((file.filename or "").lower()).suffix
    if ext not in EVIDENCE_TYPES or not EVIDENCE_TYPES[ext][0](bytes(buf)):
        raise AppError(415, "UNSUPPORTED_FILE", "PDF, JPG, PNG 파일만 올릴 수 있습니다.")
    key = f"company-evidence/{uuid.uuid4().hex}{'.jpg' if ext == '.jpeg' else ext}"  # never the user's filename
    await get_storage().put(key, bytes(buf), EVIDENCE_TYPES[ext][1])
    old = company.evidence_key
    company.evidence_key, company.evidence_name = key, Path(file.filename or "evidence").name[:200]
    company.verification, company.reject_reason = "PENDING", None
    await audit(db, action="COMPANY_EVIDENCE_UPLOAD", target_type="company", target_id=company.id,
                actor_id=ctx.user_id, actor_role="RECRUITER", company_id=company.id)
    await db.commit()
    if old:
        await get_storage().delete(old)
    return company_out(company)


async def approve_company(db: AsyncSession, company_id: uuid.UUID) -> None:
    """Operator approval (CLI until the operator screen SC-O01 exists)."""
    await db.execute(update(Company).where(Company.id == company_id)
                     .values(verification="APPROVED", verification_method="OPERATOR", verified_at=utcnow()))
