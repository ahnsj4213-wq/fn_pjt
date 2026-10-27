"""Personal-information consent (SEC-01, SEC-02). The wording is a pre-legal-review draft (X-13, OQ-04)."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import CandidateCtx, require_candidate
from app.core.db import get_db
from app.core.errors import AppError, conflict, not_found
from app.models import ConsentDocument, ConsentRecord, JobPosting
from app.postings.service import is_open

router = APIRouter(prefix="/api", tags=["candidate"])


async def current_document(db: AsyncSession) -> ConsentDocument:
    doc = (await db.execute(select(ConsentDocument).order_by(ConsentDocument.published_at.desc()).limit(1))).scalar_one_or_none()
    if doc is None:
        raise AppError(503, "CONSENT_UNAVAILABLE", "동의 문서를 불러올 수 없습니다.")
    return doc


@router.get("/consent/current")
async def get_current(ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    doc = await current_document(db)
    return {"version": doc.version, "is_draft": doc.is_draft, "body_md": doc.body_md, "items": doc.items}


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    answers: dict[str, bool]


@router.post("/candidate/postings/{posting_id}/consents", status_code=201)
async def record_consent(posting_id: UUID, body: ConsentIn, ctx: CandidateCtx = Depends(require_candidate),
                         db: AsyncSession = Depends(get_db)) -> dict:
    posting = await db.get(JobPosting, posting_id)
    if posting is None or posting.deleted_at is not None or posting.status == "DRAFT":
        raise not_found()
    if not is_open(posting):
        raise conflict("POSTING_CLOSED", "마감된 공고에는 지원할 수 없습니다.")
    doc = await current_document(db)
    if body.version != doc.version:
        raise conflict("CONSENT_VERSION_STALE", "동의 문구가 바뀌었습니다. 다시 확인해 주세요.", current=doc.version)
    codes = {i["code"] for i in doc.items}
    if set(body.answers) != codes:
        raise AppError(422, "VALIDATION", "모든 항목에 동의 여부를 표시해 주세요.")
    required = {i["code"] for i in doc.items if i["required"]}
    all_ok = all(body.answers[c] for c in required)
    rec = ConsentRecord(candidate_id=ctx.user_id, posting_id=posting_id, document_version=doc.version,
                        answers=body.answers, all_required_agreed=all_ok)
    db.add(rec)
    await db.flush()
    await audit(db, action="CONSENT_RECORD", target_type="consent", target_id=rec.id, actor_id=ctx.user_id,
                actor_role="CANDIDATE", version=doc.version, all_required_agreed=all_ok)
    await db.commit()
    return {"consent_id": str(rec.id), "all_required_agreed": all_ok, "version": doc.version,
            "agreed_at": rec.agreed_at}
