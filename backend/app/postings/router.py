"""Posting endpoints: recruiter management (company-scoped) and the public view (any signed-in user, T21)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import RecruiterCtx, get_principal, require_recruiter
from app.auth.scope import get_for_company
from app.auth.sessions import Principal
from app.core.db import get_db
from app.core.errors import not_found
from app.core.timeutil import utcnow
from app.models import Application, CodingProblem, CodingTestcase, JobPosting
from app.postings import service
from app.postings.schemas import (
    JOB_FAMILIES, EvalSettingsIn, EvalSettingsOut, PostingDetailOut, PostingIn, PostingPublicOut, PostingSummaryOut,
)

recruiter_router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
public_router = APIRouter(prefix="/api", tags=["common"])


async def _posting(db: AsyncSession, ctx: RecruiterCtx, posting_id: UUID, lock: bool = False) -> JobPosting:
    p = await get_for_company(db, JobPosting, posting_id, ctx.company_id, for_update=lock)
    if p.deleted_at is not None:
        raise not_found()
    return p


@recruiter_router.get("/postings")
async def list_postings(status: str | None = Query(None, pattern="^(DRAFT|OPEN|CLOSED)$"),
                        ctx: RecruiterCtx = Depends(require_recruiter),
                        db: AsyncSession = Depends(get_db)) -> list[PostingSummaryOut]:
    rows = (await db.execute(select(JobPosting).where(JobPosting.company_id == ctx.company_id,
                                                      JobPosting.deleted_at.is_(None))
                             .order_by(JobPosting.updated_at.desc()))).scalars().all()
    c = await service.counts(db, [p.id for p in rows])
    out = [PostingSummaryOut(**service.summary_out(p, c.get(p.id, (0, 0)))) for p in rows]
    return [o for o in out if status is None or o.effective_status == status]  # JOB-07 tabs


@recruiter_router.post("/postings", status_code=201)
async def create_posting(body: PostingIn, ctx: RecruiterCtx = Depends(require_recruiter),
                         db: AsyncSession = Depends(get_db)) -> PostingDetailOut:
    out = await service.create_posting(db, ctx.company_id, ctx.user_id, body)
    await db.commit()
    return out


@recruiter_router.get("/postings/{posting_id}")
async def get_posting(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                      db: AsyncSession = Depends(get_db)) -> PostingDetailOut:
    return await service.detail_out(db, await _posting(db, ctx, posting_id))


@recruiter_router.put("/postings/{posting_id}")
async def update_posting(posting_id: UUID, body: PostingIn, ctx: RecruiterCtx = Depends(require_recruiter),
                         db: AsyncSession = Depends(get_db)) -> PostingDetailOut:
    p = await _posting(db, ctx, posting_id, lock=True)
    out = await service.update_posting(db, p, ctx.user_id, body)
    await db.commit()
    return out


@recruiter_router.get("/postings/{posting_id}/preview")
async def preview_posting(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)) -> PostingPublicOut:
    return await service.public_out(db, await _posting(db, ctx, posting_id))


@recruiter_router.post("/postings/{posting_id}/publish")
async def publish_posting(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)) -> PostingDetailOut:
    p = await _posting(db, ctx, posting_id, lock=True)
    await service.publish(db, p)
    await db.commit()
    return await service.detail_out(db, p)


@recruiter_router.post("/postings/{posting_id}/close")
async def close_posting(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                        db: AsyncSession = Depends(get_db)) -> PostingDetailOut:
    p = await _posting(db, ctx, posting_id, lock=True)
    await service.close(db, p, "MANUAL")
    await audit(db, action="POSTING_CLOSE", target_type="posting", target_id=p.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()
    return await service.detail_out(db, p)


@recruiter_router.delete("/postings/{posting_id}", status_code=204)
async def delete_posting(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                         db: AsyncSession = Depends(get_db)) -> None:
    p = await _posting(db, ctx, posting_id, lock=True)
    await service.delete(db, p)
    await audit(db, action="POSTING_DELETE", target_type="posting", target_id=p.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()


@recruiter_router.get("/postings/{posting_id}/evaluation-settings")
async def get_settings(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                       db: AsyncSession = Depends(get_db)) -> EvalSettingsOut:
    p = await _posting(db, ctx, posting_id)
    return service.settings_out(await service.current_settings(db, p), p)


@recruiter_router.put("/postings/{posting_id}/evaluation-settings")
async def put_settings(posting_id: UUID, body: EvalSettingsIn, ctx: RecruiterCtx = Depends(require_recruiter),
                       db: AsyncSession = Depends(get_db)) -> EvalSettingsOut:
    p = await _posting(db, ctx, posting_id, lock=True)
    weights = service.validate_weights(body.weights.model_dump(), p.coding_enabled)
    if body.custom_question is not None and body.custom_question and len(body.custom_question) < 5:
        from app.core.errors import AppError
        raise AppError(422, "CUSTOM_QUESTION_TOO_SHORT", "커스텀 질문은 5자 이상이어야 합니다.")
    s = await service.add_settings(db, p, weights, body.custom_question, ctx.user_id)
    p.updated_at = utcnow()
    await audit(db, action="SETTINGS_CHANGE", target_type="posting", target_id=p.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id, version=s.version)
    await db.commit()
    return service.settings_out(s, p)


@recruiter_router.post("/postings/{posting_id}/evaluation-settings/reset")
async def reset_settings(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                         db: AsyncSession = Depends(get_db)) -> EvalSettingsOut:
    p = await _posting(db, ctx, posting_id, lock=True)
    s = await service.add_settings(db, p, service.default_weights(p.coding_enabled), None, ctx.user_id)
    await audit(db, action="SETTINGS_CHANGE", target_type="posting", target_id=p.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id, version=s.version, reset=True)
    await db.commit()
    return service.settings_out(s, p)


@recruiter_router.get("/coding-problems")
async def list_coding_problems(ctx: RecruiterCtx = Depends(require_recruiter),
                               db: AsyncSession = Depends(get_db)) -> list[dict]:
    problems = (await db.execute(select(CodingProblem).where(CodingProblem.is_active))).scalars().all()
    out = []
    for pr in problems:
        examples = (await db.execute(select(CodingTestcase).where(
            CodingTestcase.problem_id == pr.id, CodingTestcase.visibility == "PUBLIC").order_by(CodingTestcase.ordinal))).scalars()
        out.append({"id": pr.id, "title": pr.title, "statement_md": pr.statement_md, "language": pr.language,
                    "public_examples": [{"input": e.input, "output": e.expected_output} for e in examples]})
    return out


@public_router.get("/job-families")
async def job_families(p: Principal = Depends(get_principal)) -> list[dict]:
    return [{"code": k, "label": v} for k, v in JOB_FAMILIES.items()]


@public_router.get("/postings")
async def list_public(q: str | None = Query(None, max_length=100), job_family: str | None = None,
                      p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> list[PostingPublicOut]:
    stmt = (select(JobPosting).where(JobPosting.status == "OPEN", JobPosting.deleted_at.is_(None),
                                     JobPosting.deadline_at > utcnow()).order_by(JobPosting.deadline_at))
    if job_family:
        stmt = stmt.where(JobPosting.job_family == job_family)  # JOB-08
    if q:
        like = f"%{q}%"
        stmt = stmt.where(JobPosting.title.ilike(like) | JobPosting.duties.ilike(like) | JobPosting.requirements.ilike(like))
    rows = (await db.execute(stmt)).scalars().all()
    applied = set()
    if p.role == "CANDIDATE" and rows:
        applied = set((await db.execute(select(Application.posting_id).where(
            Application.candidate_id == p.user_id, Application.posting_id.in_([r.id for r in rows])))).scalars())
    return [await service.public_out(db, r, r.id in applied) for r in rows]


@public_router.get("/postings/{posting_id}")
async def get_public(posting_id: UUID, p: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)) -> PostingPublicOut:
    posting = await db.get(JobPosting, posting_id)
    if posting is None or posting.deleted_at is not None or posting.status == "DRAFT":
        raise not_found()
    has_applied = False
    if p.role == "CANDIDATE":
        has_applied = await db.scalar(select(exists().where(Application.posting_id == posting.id,
                                                            Application.candidate_id == p.user_id)))
    if posting.status == "CLOSED" and not has_applied:
        raise not_found()
    return await service.public_out(db, posting, bool(has_applied))
