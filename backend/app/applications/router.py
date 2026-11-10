"""Candidate application endpoints: apply (JOB-02, RES-01/07, SEC-02), list/detail, result (6.6, RPT-03, AC-19).

Responses use candidate-only schemas built from allow-listed fields; recruiter tables (reports, notes,
weights) are never read here.
"""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications import tester
from app.auth.deps import CandidateCtx, require_candidate
from app.auth.scope import get_for_owner
from app.core.constants import CANDIDATE_FEEDBACK_NOTICE
from app.core.db import get_db
from app.core.errors import conflict, not_found
from app.core.timeutil import utcnow
from app.models import (
    Application,
    CandidateFeedback,
    Company,
    ConsentRecord,
    InterviewSchedule,
    JobPosting,
    QuestionSet,
    Resume,
    ResumeFile,
)
from app.notify.mail import queue_mail
from app.postings.service import is_open
from app.resumes.router import copy_for_application, parse_resume_content
from app.resumes.schemas import flatten

router = APIRouter(prefix="/api/candidate", tags=["candidate"])


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent_id: UUID
    resume: dict
    resume_file_id: UUID | None = None
    update_registered: bool = False
    share_result: bool = False  # SC-A05: unchecked = practice mode, the company never sees this application


@router.post("/postings/{posting_id}/applications", status_code=201)
async def apply(posting_id: UUID, body: ApplyIn, ctx: CandidateCtx = Depends(require_candidate),
                db: AsyncSession = Depends(get_db)) -> dict:
    # CP-2: FOR SHARE serialises with posting delete/close (FOR UPDATE) so no application slips past them.
    posting = (await db.execute(select(JobPosting).where(JobPosting.id == posting_id).with_for_update(read=True))
               ).scalar_one_or_none()
    if posting is None or posting.deleted_at is not None or posting.status == "DRAFT":
        raise not_found()
    if posting.hidden_at is not None:  # SC-O02: taken down by the operator
        raise not_found()
    if not is_open(posting):
        raise conflict("POSTING_CLOSED", "마감된 공고에는 지원할 수 없습니다.")
    consent = await db.get(ConsentRecord, body.consent_id)
    if (consent is None or consent.candidate_id != ctx.user_id or consent.posting_id != posting_id
            or not consent.all_required_agreed):
        raise conflict("CONSENT_REQUIRED", "필수 항목에 모두 동의해야 지원할 수 있습니다.")  # SEC-02, AC-05
    if await tester.is_tester(db, ctx.user_id):  # dev/test accounts only: the earlier application goes (JOB-02 for the rest)
        old = (await db.execute(select(Application).where(Application.posting_id == posting_id,
                                                          Application.candidate_id == ctx.user_id).with_for_update())
               ).scalar_one_or_none()
        if old is not None:
            if old.attempt_status in ("INTERVIEWING", "CODING"):
                raise conflict("ATTEMPT_IN_PROGRESS", "진행 중인 면접이 있습니다. 끝난 뒤 다시 지원해 주십시오.")
            if await tester.has_decision(db, old.id):
                raise conflict("ALREADY_DECIDED", "합격·불합격 결정이 기록된 지원은 지울 수 없어 다시 지원할 수 없습니다.")
            await tester.delete_application(db, old.id)
    content = parse_resume_content(body.resume)
    snapshot_file_id = None
    if body.resume_file_id:
        src = await get_for_owner(db, ResumeFile, body.resume_file_id, ctx.user_id, owner_col="owner_id")
        snapshot_file_id = await copy_for_application(db, src)  # CP-3: retention independent of the upload
    app = Application(company_id=posting.company_id, posting_id=posting_id, candidate_id=ctx.user_id,
                      consent_record_id=consent.id, resume_snapshot=content.model_dump(),
                      resume_snapshot_text=flatten(content), resume_file_id=snapshot_file_id,
                      share_result=body.share_result)
    db.add(app)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise conflict("ALREADY_APPLIED", "이미 지원한 공고입니다.") from None  # JOB-02, AC-04
    if body.update_registered:  # RES-07: the registered copy changes only when the candidate asks
        values = {"content": content.model_dump(), "source": "PDF" if body.resume_file_id else "MANUAL",
                  "file_id": body.resume_file_id, "updated_at": utcnow()}
        await db.execute(insert(Resume).values(candidate_id=ctx.user_id, **values)
                         .on_conflict_do_update(index_elements=["candidate_id"], set_=values))
    await queue_mail(db, "APPLICATION_RECEIVED", app.id, dedupe=f"mail:received:{app.id}")
    await db.commit()
    return {"application_id": str(app.id)}


class PostingRef(BaseModel):
    id: UUID
    title: str
    company_name: str
    coding_enabled: bool


class CandidateApplicationOut(BaseModel):
    """Allow-listed candidate view. No scores, weights, quotes or notes (SEC-04)."""
    id: UUID
    posting: PostingRef
    applied_at: datetime
    attempt_status: str
    status_label: str
    evaluation_label: str | None
    decision: str
    answered_main_count: int
    can_start: bool
    main_count: int | None            # questions in this attempt's set (SC-A09 "중단 (질문 3/5)")
    share_result: bool
    progress_label: str               # SC-A09 진행 상태
    progress_kind: str                # good | bad | hold | none (pill colour)


def published_decision(app: Application) -> str:
    """What the candidate may see: only PASS and FAIL. The recruiter's HOLD stays internal and reads as undecided."""
    return app.decision_status if app.decision_status in ("PASS", "FAIL") else "UNDECIDED"


def status_label(app: Application) -> tuple[str, str | None]:
    a, e, d = app.attempt_status, app.eval_status, published_decision(app)
    if d != "UNDECIDED":
        return "결과 확인", "평가 완료"
    if a == "READY":
        return "응시 전", None
    if a in ("PREPARING", "INTERVIEWING", "CODING"):
        return "응시 중", None
    if a in ("EXPIRED", "INTERRUPTED"):  # left in the middle or the posting closed: ended, nothing to continue (ADR-0024)
        label = f"중도 종료({app.answered_main_count}문항 응답)"
        return label, ("평가 완료" if e == "DONE" else "평가 진행 중" if e else None)
    # SUBMITTED: ON_HOLD is an internal state and is shown as "in progress" (no re-test is ever requested)
    return ("검토 중", "평가 완료") if e == "DONE" else ("제출 완료", "평가 진행 중")


def progress(app: Application, schedule_accepted: bool) -> tuple[str, str]:
    """SC-A09 진행 상태: 기업 검토 중, 2차 면접 일정 확정, 면접 중단, 연습 모드, 합격, 불합격 (+ before/while the interview)."""
    a = app.attempt_status
    if published_decision(app) == "PASS":
        return "합격", "good"
    if published_decision(app) == "FAIL":
        return "불합격", "bad"
    if schedule_accepted:
        return "2차 면접 일정 확정", "good"
    if a in ("INTERRUPTED", "EXPIRED"):
        return "면접 중단", "none"
    if a == "SUBMITTED":
        return ("기업 검토 중", "hold") if app.share_result else ("연습 모드", "none")
    if a in ("INTERVIEWING", "CODING"):
        return "면접 진행 중", "hold"
    return "면접 전", "none"


async def _out(db: AsyncSession, app: Application) -> CandidateApplicationOut:
    posting = await db.get(JobPosting, app.posting_id)
    company = await db.get(Company, posting.company_id)
    label, ev = status_label(app)
    main_count = await db.scalar(select(QuestionSet.main_count).where(QuestionSet.application_id == app.id,
                                                                      QuestionSet.status == "READY"))
    accepted = await db.scalar(select(InterviewSchedule.id).where(InterviewSchedule.application_id == app.id,
                                                                  InterviewSchedule.status == "ACCEPTED").limit(1))
    plabel, pkind = progress(app, accepted is not None)
    return CandidateApplicationOut(
        id=app.id, posting=PostingRef(id=posting.id, title=posting.title or "", company_name=company.name,
                                      coding_enabled=posting.coding_enabled),
        applied_at=app.applied_at, attempt_status=app.attempt_status, status_label=label, evaluation_label=ev,
        decision=published_decision(app), answered_main_count=app.answered_main_count,
        can_start=app.attempt_status in ("READY", "PREPARING") and is_open(posting),
        main_count=main_count, share_result=app.share_result, progress_label=plabel, progress_kind=pkind)


@router.get("/applications")
async def list_applications(ctx: CandidateCtx = Depends(require_candidate),
                            db: AsyncSession = Depends(get_db)) -> list[CandidateApplicationOut]:
    rows = (await db.execute(select(Application).where(Application.candidate_id == ctx.user_id)
                             .order_by(Application.applied_at.desc()))).scalars().all()
    return [await _out(db, a) for a in rows]


@router.get("/applications/{application_id}")
async def get_application(application_id: UUID, ctx: CandidateCtx = Depends(require_candidate),
                          db: AsyncSession = Depends(get_db)) -> CandidateApplicationOut:
    return await _out(db, await get_for_owner(db, Application, application_id, ctx.user_id))


class CriteriaScores(BaseModel):
    expertise: str
    logic: str
    attitude: str
    delivery: str


class FeedbackOut(BaseModel):
    criteria: CriteriaScores          # 100-point scale per criterion (6.6) — never the total
    strengths: list[str]
    improvements: list[str]
    coding: str | None                # ALL_PASSED | NOT_ALL_PASSED | None (OQ-24)
    notice: str


class CandidateResultOut(BaseModel):
    application_id: UUID
    decision: str
    status_label: str
    feedback: FeedbackOut | None


@router.get("/applications/{application_id}/result")
async def get_result(application_id: UUID, ctx: CandidateCtx = Depends(require_candidate),
                     db: AsyncSession = Depends(get_db)) -> CandidateResultOut:
    app = await get_for_owner(db, Application, application_id, ctx.user_id)
    feedback = None
    if published_decision(app) != "UNDECIDED":  # X-04: published only after the recruiter's decision (HOLD is not one)
        fb = await db.get(CandidateFeedback, app.id)
        if fb is not None:
            coding = None if fb.coding_all_passed is None else ("ALL_PASSED" if fb.coding_all_passed else "NOT_ALL_PASSED")
            feedback = FeedbackOut(
                criteria=CriteriaScores(expertise=str(fb.score_expertise), logic=str(fb.score_logic),
                                        attitude=str(fb.score_attitude), delivery=str(fb.score_delivery)),
                strengths=list(fb.strengths), improvements=list(fb.improvements), coding=coding,
                notice=CANDIDATE_FEEDBACK_NOTICE)
    return CandidateResultOut(application_id=app.id, decision=published_decision(app),
                              status_label=status_label(app)[0], feedback=feedback)
