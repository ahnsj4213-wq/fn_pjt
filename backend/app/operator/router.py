"""운영자 · 기업 인증 (screen spec SC-O01).

An operator sees every company signup, newest PENDING first and REJECTED last, opens the attached evidence and approves
or rejects. The role comes from the server session only. Operators have no company; they reach no applicant data.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import get_principal
from app.auth.sessions import Principal
from app.core.db import get_db
from app.core.errors import AppError
from app.core.storage import get_storage
from app.core.timeutil import utcnow
from app.recruiter.inquiries import open_operator_thread
from app.models import Company, JobPosting, User

router = APIRouter(prefix="/api/operator", tags=["operator"])
MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


async def require_operator(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != "OPERATOR":
        raise AppError(403, "ROLE_FORBIDDEN", "운영자만 사용할 수 있는 기능입니다.")
    return p


class VerificationRow(BaseModel):
    company_id: uuid.UUID
    company_name: str
    contact_name: str | None
    email: str | None
    biz_no: str | None
    method: str  # DOMAIN_MAIL (회사 메일 인증) | DOCUMENT (개인 메일, 증빙) | ISSUED (운영자가 발급한 기업)
    status: str  # PENDING | APPROVED | REJECTED
    evidence_name: str | None
    reject_reason: str | None
    applied_at: datetime


class VerificationList(BaseModel):
    counts: dict[str, int]
    items: list[VerificationRow]


def _bucket(c: Company) -> str:
    """Screen buckets: 검토 필요 / 자동 승인 / 승인 / 반려."""
    if c.verification == "PENDING":
        return "pending"
    if c.verification == "REJECTED":
        return "rejected"
    return "auto" if c.verification_method == "DOMAIN_MAIL" else "approved"


@router.get("/companies")
async def list_companies(_: Principal = Depends(require_operator), db: AsyncSession = Depends(get_db)) -> VerificationList:
    order = case((Company.verification == "PENDING", 0), (Company.verification == "APPROVED", 1), else_=2)
    rows = (await db.execute(
        select(Company, User).join(User, (User.company_id == Company.id) & (User.role == "RECRUITER"), isouter=True)
        .order_by(order, Company.created_at.desc()))).all()
    seen: set[uuid.UUID] = set()
    items, counts = [], {"pending": 0, "auto": 0, "approved": 0, "rejected": 0}
    for c, u in rows:
        if c.id in seen:  # one row per company
            continue
        seen.add(c.id)
        counts[_bucket(c)] += 1
        items.append(VerificationRow(
            company_id=c.id, company_name=c.name, contact_name=u.name if u else None, email=u.email if u else None,
            biz_no=c.biz_no, method=("DOMAIN_MAIL" if c.verification_method == "DOMAIN_MAIL"
                    else "ISSUED" if c.verification_method is None and c.verification == "APPROVED" and not c.evidence_key
                    else "DOCUMENT"),
            status=c.verification, evidence_name=c.evidence_name, reject_reason=c.reject_reason, applied_at=c.created_at))
    return VerificationList(counts=counts, items=items)


@router.get("/companies/{company_id}/evidence")
async def evidence(company_id: uuid.UUID, p: Principal = Depends(require_operator), db: AsyncSession = Depends(get_db)) -> Response:
    c = await db.get(Company, company_id)
    if c is None or not c.evidence_key:
        raise AppError(404, "NOT_FOUND", "증빙 파일이 없습니다.")
    data = await get_storage().get(c.evidence_key)
    if data is None:
        raise AppError(404, "NOT_FOUND", "증빙 파일이 없습니다.")
    await audit(db, action="COMPANY_EVIDENCE_VIEW", target_type="company", target_id=c.id, actor_id=p.user_id,
                actor_role="OPERATOR")
    await db.commit()
    ext = "." + c.evidence_key.rsplit(".", 1)[-1]
    return Response(data, media_type=MEDIA.get(ext, "application/octet-stream"),
                    headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "sandbox"})


async def _pending(db: AsyncSession, company_id: uuid.UUID) -> Company:
    c = await db.get(Company, company_id, with_for_update=True)
    if c is None:
        raise AppError(404, "NOT_FOUND", "기업을 찾을 수 없습니다.")
    if c.verification != "PENDING":
        raise AppError(409, "INVALID_STATE", "검토 필요 상태인 기업만 처리할 수 있습니다.", current=c.verification)
    return c


@router.post("/companies/{company_id}/approve")
async def approve(company_id: uuid.UUID, p: Principal = Depends(require_operator), db: AsyncSession = Depends(get_db)) -> dict:
    c = await _pending(db, company_id)
    c.verification, c.verification_method, c.verified_at, c.reject_reason = "APPROVED", "OPERATOR", utcnow(), None
    await audit(db, action="COMPANY_APPROVE", target_type="company", target_id=c.id, actor_id=p.user_id,
                actor_role="OPERATOR", company_id=c.id)
    await db.commit()
    return {"company_id": str(c.id), "status": c.verification}


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/companies/{company_id}/reject")
async def reject(company_id: uuid.UUID, body: RejectIn, p: Principal = Depends(require_operator),
                 db: AsyncSession = Depends(get_db)) -> dict:
    c = await _pending(db, company_id)
    c.verification, c.reject_reason = "REJECTED", body.reason
    await open_operator_thread(db, c.id, "기업 인증이 반려되었습니다", f"반려 사유: {body.reason}\n증빙 서류를 다시 올리면 운영자가 다시 확인합니다.")
    await audit(db, action="COMPANY_REJECT", target_type="company", target_id=c.id, actor_id=p.user_id,
                actor_role="OPERATOR", company_id=c.id)
    await db.commit()
    return {"company_id": str(c.id), "status": c.verification}


# ---- SC-O02 공고 관리: hide a posting, suspend a company ----
class PostingRow(BaseModel):
    id: uuid.UUID
    title: str | None
    company_id: uuid.UUID
    company_name: str
    job_family: str | None
    status: str  # OPEN | CLOSED (drafts are not for the operator)
    hidden: bool
    company_suspended: bool


@router.get("/postings")
async def list_postings(_: Principal = Depends(require_operator), db: AsyncSession = Depends(get_db)) -> list[PostingRow]:
    rows = (await db.execute(
        select(JobPosting, Company).join(Company, Company.id == JobPosting.company_id)
        .where(JobPosting.deleted_at.is_(None), JobPosting.status != "DRAFT")
        .order_by(JobPosting.created_at.desc()))).all()
    return [PostingRow(id=p.id, title=p.title, company_id=c.id, company_name=c.name, job_family=p.job_family,
                       status=p.status, hidden=p.hidden_at is not None, company_suspended=c.suspended_at is not None)
            for p, c in rows]


async def _posting(db: AsyncSession, posting_id: uuid.UUID) -> JobPosting:
    p = await db.get(JobPosting, posting_id, with_for_update=True)
    if p is None or p.deleted_at is not None or p.status == "DRAFT":
        raise AppError(404, "NOT_FOUND", "공고를 찾을 수 없습니다.")
    return p


@router.post("/postings/{posting_id}/hide")
async def hide_posting(posting_id: uuid.UUID, p: Principal = Depends(require_operator), db: AsyncSession = Depends(get_db)) -> dict:
    post = await _posting(db, posting_id)
    if post.hidden_at is None:
        post.hidden_at, post.hidden_by = utcnow(), "OPERATOR"
    await audit(db, action="POSTING_HIDE", target_type="posting", target_id=post.id, actor_id=p.user_id,
                actor_role="OPERATOR", company_id=post.company_id)
    await db.commit()
    return {"id": str(post.id), "hidden": True}


@router.post("/postings/{posting_id}/show")
async def show_posting(posting_id: uuid.UUID, p: Principal = Depends(require_operator), db: AsyncSession = Depends(get_db)) -> dict:
    post = await _posting(db, posting_id)
    company = await db.get(Company, post.company_id)
    if company.suspended_at is not None:
        raise AppError(409, "COMPANY_SUSPENDED", "정지된 기업의 공고는 정지를 풀어야 다시 보이게 할 수 있습니다.")
    post.hidden_at = post.hidden_by = None
    await audit(db, action="POSTING_SHOW", target_type="posting", target_id=post.id, actor_id=p.user_id,
                actor_role="OPERATOR", company_id=post.company_id)
    await db.commit()
    return {"id": str(post.id), "hidden": False}


async def _company(db: AsyncSession, company_id: uuid.UUID) -> Company:
    c = await db.get(Company, company_id, with_for_update=True)
    if c is None:
        raise AppError(404, "NOT_FOUND", "기업을 찾을 수 없습니다.")
    return c


@router.post("/companies/{company_id}/suspend")
async def suspend_company(company_id: uuid.UUID, p: Principal = Depends(require_operator),
                          db: AsyncSession = Depends(get_db)) -> dict:
    """Takes every posting of the company down (they come back when the suspension is lifted) and blocks publishing."""
    c = await _company(db, company_id)
    now = utcnow()
    if c.suspended_at is None:
        c.suspended_at = now
    await db.execute(update(JobPosting).where(JobPosting.company_id == c.id, JobPosting.hidden_at.is_(None))
                     .values(hidden_at=now, hidden_by="SUSPENSION"))
    await audit(db, action="COMPANY_SUSPEND", target_type="company", target_id=c.id, actor_id=p.user_id,
                actor_role="OPERATOR", company_id=c.id)
    await db.commit()
    return {"company_id": str(c.id), "suspended": True}


@router.post("/companies/{company_id}/unsuspend")
async def unsuspend_company(company_id: uuid.UUID, p: Principal = Depends(require_operator),
                            db: AsyncSession = Depends(get_db)) -> dict:
    c = await _company(db, company_id)
    c.suspended_at = None
    await db.execute(update(JobPosting).where(JobPosting.company_id == c.id, JobPosting.hidden_by == "SUSPENSION")
                     .values(hidden_at=None, hidden_by=None))  # postings hidden by hand stay hidden
    await audit(db, action="COMPANY_UNSUSPEND", target_type="company", target_id=c.id, actor_id=p.user_id,
                actor_role="OPERATOR", company_id=c.id)
    await db.commit()
    return {"company_id": str(c.id), "suspended": False}
