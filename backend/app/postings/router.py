"""Posting endpoints: recruiter management (company-scoped) and the public view (anyone, signed in or not: screen spec SC-A02/A03)."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIUnavailable, get_ai_client
from app.audit import audit
from app.auth.deps import RecruiterCtx, get_optional_principal, require_recruiter
from app.auth.scope import get_for_company
from app.auth.sessions import Principal
from app.core.config import get_settings as app_settings
from app.core.db import get_db
from app.core.errors import AppError, conflict, not_found
from app.core.timeutil import utcnow
from app.models import Application, CodingProblem, CodingTestcase, JobPosting
from app.postings import service
from app.questions import knowledge
from app.postings.schemas import (
    JOB_FAMILIES, EvalSettingsIn, EvalSettingsOut, InterviewSettings, PostingDetailOut, PostingDraftIn, PostingDraftOut, PostingIn, PostingPublicOut,
    PostingSummaryOut,
)

recruiter_router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
public_router = APIRouter(prefix="/api", tags=["common"])
DRAFT_DOCUMENTS = 6
DRAFT_MIN_SIMILARITY = 0.45  # a bare job title scores lower than a posting does (measured in ADR-0020)


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


@recruiter_router.post("/postings/ai-draft")
async def draft_from_documents(body: PostingDraftIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                               db: AsyncSession = Depends(get_db)) -> PostingDraftOut:
    """Text for the form from this company's documents (ADR-0020). Saves no posting; the recruiter edits and saves it."""
    ai = get_ai_client()
    family = JOB_FAMILIES.get(body.job_family or "", "")
    try:
        chunks = await knowledge.search_company(db, ai, app_settings().embed_model, company_id=ctx.company_id,
                                                query=f"{body.title} {family}".strip(), k=DRAFT_DOCUMENTS, floor=DRAFT_MIN_SIMILARITY)
        if not chunks:
            raise conflict("NO_COMPANY_DOCUMENTS", "이 직무와 관련된 회사 자료가 없어 초안을 만들 수 없습니다. 직접 작성해 주십시오.")
        r = await ai.llm("posting_draft", {"title": body.title, "job_family": family, "experience_level": body.experience_level,
                                           "knowledge": [c.as_input() for c in chunks]}, lane="INTERACTIVE")
    except AIUnavailable as e:
        raise AppError(503, "AI_UNAVAILABLE", "AI 서버에 연결할 수 없습니다. 잠시 후 다시 시도하거나 직접 작성해 주십시오.") from e
    if r.status != "OK" or not (r.output["duties"] and r.output["requirements"]):
        raise AppError(422, "DRAFT_FAILED", "회사 자료로 초안을 만들지 못했습니다. 다시 시도하거나 직접 작성해 주십시오.")
    await audit(db, action="POSTING_AI_DRAFT", target_type="company", target_id=ctx.company_id, actor_id=ctx.user_id, actor_role="RECRUITER",
                company_id=ctx.company_id, request_id=getattr(request.state, "request_id", None))
    await db.commit()
    return PostingDraftOut(**{f: "\n".join(r.output[f]) for f in ("duties", "requirements", "preferred")},
                           sources=list(dict.fromkeys(c.title for c in chunks)))


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
    prev = await service.current_settings(db, p)
    items = service.clean_custom_items(body.custom_items, prev.custom_items)
    weights = service.validate_weights(body.weights.model_dump(), p.coding_enabled, sum(x["weight"] for x in items))
    if body.custom_question is not None and body.custom_question and len(body.custom_question) < 5:
        from app.core.errors import AppError
        raise AppError(422, "CUSTOM_QUESTION_TOO_SHORT", "커스텀 질문은 5자 이상이어야 합니다.")
    s = await service.add_settings(db, p, weights, body.custom_question, ctx.user_id,
                                   service.interview_values(body.interview, prev), custom_items=items)
    if body.question_areas is not None:
        p.question_areas = list(dict.fromkeys(body.question_areas))
    p.updated_at = utcnow()
    await audit(db, action="SETTINGS_CHANGE", target_type="posting", target_id=p.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id, version=s.version)
    await db.commit()
    return service.settings_out(s, p)


@recruiter_router.post("/postings/{posting_id}/evaluation-settings/reset")
async def reset_settings(posting_id: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                         db: AsyncSession = Depends(get_db)) -> EvalSettingsOut:
    p = await _posting(db, ctx, posting_id, lock=True)
    prev = await service.current_settings(db, p)  # SC-R07: reset leaves the weights as they are
    s = await service.add_settings(db, p, prev.weights(), None, ctx.user_id,
                                   service.interview_values(InterviewSettings(), None), custom_items=list(prev.custom_items or []))
    p.question_areas = None  # back to all five
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
        out.append({"id": pr.id, "title": pr.title, "statement_md": pr.statement_md, "language": pr.language, "level": pr.level, "tags": pr.tags, "source": pr.source,
                    "public_examples": [{"input": e.input, "output": e.expected_output} for e in examples]})
    return out


@public_router.get("/job-families")
async def job_families(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """SC-A02 category buttons: label and the number of postings on the main page."""
    rows = dict((await db.execute(select(JobPosting.job_family, func.count()).where(*_listed())
                                  .group_by(JobPosting.job_family))).all())
    return [{"code": k, "label": v, "count": rows.get(k, 0)} for k, v in JOB_FAMILIES.items()]


def _listed() -> tuple:
    """Postings on the main page: published, not deleted, deadline ahead."""
    return (JobPosting.status == "OPEN", JobPosting.deleted_at.is_(None), JobPosting.hidden_at.is_(None),
            JobPosting.deadline_at > utcnow())


async def _applied(db: AsyncSession, p: Principal | None, posting_ids: list[UUID]) -> set[UUID]:
    if p is None or p.role != "CANDIDATE" or not posting_ids:
        return set()
    return set((await db.execute(select(Application.posting_id).where(
        Application.candidate_id == p.user_id, Application.posting_id.in_(posting_ids)))).scalars())


@public_router.get("/postings")
async def list_public(q: str | None = Query(None, max_length=100), job_family: str | None = None,
                      p: Principal | None = Depends(get_optional_principal),
                      db: AsyncSession = Depends(get_db)) -> list[PostingPublicOut]:
    """No login needed (SC-A02)."""
    stmt = select(JobPosting).where(*_listed()).order_by(JobPosting.deadline_at)
    if job_family:
        stmt = stmt.where(JobPosting.job_family == job_family)  # JOB-08
    if q:
        like = f"%{q}%"
        stmt = stmt.where(JobPosting.title.ilike(like) | JobPosting.duties.ilike(like) | JobPosting.requirements.ilike(like))
    rows = (await db.execute(stmt)).scalars().all()
    applied = await _applied(db, p, [r.id for r in rows])
    return [await service.public_out(db, r, r.id in applied) for r in rows]


@public_router.get("/postings/{posting_id}")
async def get_public(posting_id: UUID, p: Principal | None = Depends(get_optional_principal),
                     db: AsyncSession = Depends(get_db)) -> PostingPublicOut:
    """No login needed (SC-A03). Each view adds one to the posting's view count (SC-R03, SC-R13)."""
    posting = await db.get(JobPosting, posting_id)
    if posting is None or posting.deleted_at is not None or posting.status == "DRAFT":
        raise not_found()
    has_applied = posting.id in await _applied(db, p, [posting.id])
    if (posting.status == "CLOSED" or posting.hidden_at is not None) and not has_applied:  # hidden by the operator (SC-O02)
        raise not_found()
    if p is None or p.role == "CANDIDATE":
        await db.execute(update(JobPosting).where(JobPosting.id == posting.id)
                         .values(view_count=JobPosting.view_count + 1))
        await db.commit()
    return await service.public_out(db, posting, has_applied)
