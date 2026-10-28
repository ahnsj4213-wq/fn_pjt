"""Recruiter application endpoints (ADM-01~08, RES-08, RPT-02, AC-03, AC-20). Every query is company-scoped.
Contract: docs/trd/04-api-contract.md 4.2·4.3."""
from datetime import date
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.lifecycle import register_evaluation
from app.audit import audit
from app.auth.deps import RecruiterCtx, require_recruiter
from app.auth.scope import get_for_company
from app.core.config import get_settings
from app.core.constants import RECRUITER_AI_NOTICE
from app.core.db import get_db
from app.core.errors import AppError, conflict, not_found
from app.core.timeutil import KST, utcnow
from app.models import (
    AnswerEvaluation, Application, CodingSubmission, CodingTestcase, CodingTestResult, DecisionHistory,
    EvaluationRun, FollowupDecision, InterviewAnswer, InterviewQuestion, JobPosting, PostingEvalSettings, ProctorEvent,
    RecruiterNote, RecruiterReport, ResumeFile, User,
)

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
WITHDRAWN_NAME = "탈퇴한 지원자"  # USR-07: the account is anonymised; the application row stays (OQ-12)


async def _app(db: AsyncSession, ctx: RecruiterCtx, aid: UUID, lock: bool = False) -> Application:
    return await get_for_company(db, Application, aid, ctx.company_id, for_update=lock)


def _audit(db, ctx: RecruiterCtx, request: Request, action: str, target_type: str, target_id, **detail):
    return audit(db, action=action, target_type=target_type, target_id=target_id, actor_id=ctx.user_id,
                 actor_role="RECRUITER", company_id=ctx.company_id, request_id=getattr(request.state, "request_id", None),
                 **detail)


@router.get("/dashboard")
async def dashboard(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> dict:
    open_postings = await db.scalar(select(func.count()).select_from(JobPosting).where(
        JobPosting.company_id == ctx.company_id, JobPosting.status == "OPEN", JobPosting.deleted_at.is_(None),
        JobPosting.deadline_at > utcnow()))
    awaiting = await db.scalar(select(func.count()).select_from(Application).where(
        Application.company_id == ctx.company_id, Application.eval_status == "DONE",
        Application.decision_status == "UNDECIDED"))
    return {"open_postings": open_postings, "awaiting_review": awaiting}


@router.get("/applications")
async def list_applications(posting_id: UUID | None = None,
                            decision: str | None = Query(None, pattern="^(UNDECIDED|PASS|FAIL)$"),
                            eval_status: str | None = Query(None, pattern="^(PENDING|RUNNING|DONE|ON_HOLD|NONE)$"),
                            attempt_status: str | None = None,
                            sort: str = Query("applied_at", pattern="^(applied_at|total_score)$"),
                            page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=50),
                            ctx: RecruiterCtx = Depends(require_recruiter),
                            db: AsyncSession = Depends(get_db)) -> dict:
    conds = [Application.company_id == ctx.company_id]
    if posting_id:
        conds.append(Application.posting_id == posting_id)
    if decision:
        conds.append(Application.decision_status == decision)
    if eval_status:
        conds.append(Application.eval_status.is_(None) if eval_status == "NONE" else Application.eval_status == eval_status)
    if attempt_status:
        conds.append(Application.attempt_status == attempt_status)
    base = (select(Application, User.name, JobPosting.title, RecruiterReport.total_score)
            .join(User, User.id == Application.candidate_id)
            .join(JobPosting, JobPosting.id == Application.posting_id)
            .outerjoin(RecruiterReport, and_(RecruiterReport.application_id == Application.id,
                                             Application.eval_status == "DONE"))
            .where(*conds))
    order = (RecruiterReport.total_score.desc().nulls_last() if sort == "total_score" else Application.applied_at.desc())
    total = await db.scalar(select(func.count()).select_from(Application).where(*conds))
    rows = (await db.execute(base.order_by(order).offset((page - 1) * size).limit(size))).all()
    items = [{"id": a.id, "candidate_name": name or WITHDRAWN_NAME, "posting": {"id": a.posting_id, "title": title},
              "applied_at": a.applied_at, "attempt_status": a.attempt_status, "answered_main_count": a.answered_main_count,
              "eval_status": a.eval_status,
              "total_score": str(score) if a.eval_status == "DONE" and score is not None else None,  # ADM-02
              "decision": a.decision_status} for a, name, title, score in rows]
    return {"items": items, "total": total, "page": page, "size": size}


async def _decision_version(db: AsyncSession, aid: UUID) -> int:
    return await db.scalar(select(func.count()).select_from(DecisionHistory).where(DecisionHistory.application_id == aid))


def _can_decide(a: Application) -> bool:
    return a.eval_status == "DONE" or (a.attempt_status == "EXPIRED" and a.eval_status is None)


@router.get("/applications/{aid}")
async def get_application(aid: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid)
    cand = await db.get(User, a.candidate_id)
    posting = await db.get(JobPosting, a.posting_id)
    text_used = await db.scalar(select(func.count()).select_from(InterviewAnswer).where(
        InterviewAnswer.application_id == a.id, InterviewAnswer.input_mode == "TEXT"))
    return {"id": a.id, "candidate": {"name": cand.name or WITHDRAWN_NAME, "email": cand.email, "phone": cand.phone},
            "posting": {"id": posting.id, "title": posting.title}, "attempt_status": a.attempt_status,
            "interrupted_from": a.interrupted_from, "eval_status": a.eval_status, "decision": a.decision_status,
            "decision_version": await _decision_version(db, a.id), "answered_main_count": a.answered_main_count,
            "text_mode_used": bool(text_used), "can_decide": _can_decide(a), "can_re_evaluate": a.eval_status == "ON_HOLD",
            "applied_at": a.applied_at, "submitted_at": a.submitted_at, "notice": RECRUITER_AI_NOTICE}


@router.get("/applications/{aid}/resume")
async def get_resume(aid: UUID, ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid)
    return {"snapshot": a.resume_snapshot, "has_file": a.resume_file_id is not None}


@router.get("/applications/{aid}/resume-file")
async def get_resume_file(aid: UUID, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)):
    a = await _app(db, ctx, aid)
    if a.resume_file_id is None:
        raise not_found()
    rf = await db.get(ResumeFile, a.resume_file_id)
    path = Path(get_settings().data_dir) / "resumes" / rf.storage_key
    if not path.is_file():
        raise not_found()
    await _audit(db, ctx, request, "RESUME_FILE_VIEW", "application", a.id)
    await db.commit()
    # T26: inline view in a sandboxed document, never sniffed or cached
    return FileResponse(path, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="resume.pdf"', "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/applications/{aid}/report")
async def get_report(aid: UUID, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                     db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid)
    rep = await db.get(RecruiterReport, a.id)
    if rep is None or a.eval_status != "DONE":
        raise conflict("REPORT_NOT_READY", "평가가 아직 끝나지 않았습니다.")
    run = await db.get(EvaluationRun, rep.run_id)
    settings = await db.get(PostingEvalSettings, run.eval_settings_id)
    weights = rep.weights
    crit_keys = [("EXPERTISE", "expertise", rep.score_expertise), ("LOGIC", "logic", rep.score_logic),
                 ("SPECIFICITY", "specificity", rep.score_specificity), ("DELIVERY", "delivery", rep.score_delivery)]
    questions = await _question_report(db, a.id, rep)
    proctor = await db.execute(select(func.count(), func.coalesce(func.sum(ProctorEvent.away_ms), 0)).where(
        ProctorEvent.application_id == a.id, ProctorEvent.kind == "LEAVE"))
    leave_count, away_ms = proctor.one()
    await _audit(db, ctx, request, "REPORT_VIEW", "application", a.id)
    await db.commit()
    return {
        "application_id": a.id, "total_score": str(rep.total_score),
        "criteria": [{"key": k, "score": str(v), "weight": weights[w], "insufficient": rep.insufficient_by_criterion.get(w, 0)}
                     for k, w, v in crit_keys],
        "coding": {"score": str(rep.score_coding), "weight": weights.get("coding")} if rep.score_coding is not None else None,
        "insufficient_count": rep.insufficient_count, "no_answer_count": rep.no_answer_count,
        "answered_main_count": rep.answered_main_count, "summary": rep.summary,
        "strengths": rep.strengths, "improvements": rep.improvements, "questions": questions,
        "code_review": rep.code_review, "proctor": {"leave_count": leave_count, "away_ms": int(away_ms)},
        "versions": {"model": run.model_name, "model_digest": run.model_digest, "prompt": run.prompt_version,
                     "rubric": run.rubric_version, "scoring": run.scoring_version, "weights_version": settings.version},
        "notice": RECRUITER_AI_NOTICE,
    }


async def _question_report(db: AsyncSession, aid: UUID, rep: RecruiterReport) -> list[dict]:
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == aid)
                           .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars().all()
    answers = {x.question_id: x for x in (await db.execute(select(InterviewAnswer).where(
        InterviewAnswer.application_id == aid))).scalars()}
    evals: dict[UUID, list[AnswerEvaluation]] = {}
    for e in (await db.execute(select(AnswerEvaluation).where(AnswerEvaluation.run_id == rep.run_id))).scalars():
        evals.setdefault(e.answer_id, []).append(e)
    fdec = {f.main_question_id: f for f in (await db.execute(select(FollowupDecision).where(
        FollowupDecision.application_id == aid))).scalars()}
    qscores = {q["seq"]: q for q in rep.question_scores}
    order = ["EXPERTISE", "LOGIC", "SPECIFICITY", "DELIVERY"]
    out: dict[int, dict] = {}
    for q in qs:
        if q.role == "MAIN":
            d = fdec.get(q.id)
            out[q.seq] = {"seq": q.seq, "slot_type": q.slot_type, "question_text": q.text, "reasked": 0,
                          "question_scores": {k.upper(): v for k, v in qscores.get(q.seq, {}).items() if k != "seq"},
                          "followup": {"asked": False, "skip_cause": d.skip_cause if d else None}, "answers": []}
        else:
            out[q.seq]["followup"] = {"asked": True, "skip_cause": None, "question_text": q.text}
        ans = answers.get(q.id)
        if ans is None:
            continue
        if q.role == "MAIN":
            out[q.seq]["reasked"] = ans.reask_count
        items = sorted(evals.get(ans.id, []), key=lambda e: order.index(e.criterion))
        out[q.seq]["answers"].append({
            "role": q.role, "input_mode": ans.input_mode, "end_reason": ans.end_reason, "no_answer": ans.is_no_answer,
            "stt_failed": ans.stt_failed, "display_text": ans.eval_text, "transcript": ans.transcript,
            "items": [{"key": e.criterion, "score": e.score, "evidence": e.evidence_status,
                       "insufficient_reason": e.insufficient_reason,
                       "quote": {"text": e.quote, "start": e.quote_start, "end": e.quote_end} if e.quote else None,
                       "comment": e.comment} for e in items]})
    return [out[k] for k in sorted(out)]


@router.get("/applications/{aid}/coding-result")
async def coding_result(aid: UUID, ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid)
    posting = await db.get(JobPosting, a.posting_id)
    sub = (await db.execute(select(CodingSubmission).where(CodingSubmission.application_id == a.id))).scalar_one_or_none()
    if not posting.coding_enabled:
        return {"enabled": False}
    tests = []
    if sub is not None:
        rows = await db.execute(select(CodingTestResult, CodingTestcase).join(
            CodingTestcase, CodingTestcase.id == CodingTestResult.testcase_id).where(
            CodingTestResult.submission_id == sub.id).order_by(CodingTestcase.ordinal))
        tests = [{"ordinal": tc.ordinal, "visibility": tc.visibility, "passed": r.passed, "outcome": r.outcome,
                  "time_ms": r.time_ms, "input": tc.input, "expected_output": tc.expected_output} for r, tc in rows]
    rep = await db.get(RecruiterReport, a.id)
    return {"enabled": True, "submission": None if sub is None else {
        "kind": sub.kind, "submitted_at": sub.submitted_at, "code": sub.code, "compile_ok": sub.compile_ok,
        "grading_status": sub.grading_status, "passed": sub.passed, "total": sub.total,
        "score": str(sub.score) if sub.score is not None else None},
        "tests": tests, "code_review": rep.code_review if rep else None}


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(PASS|FAIL)$")
    expected_version: int = Field(ge=0)


@router.post("/applications/{aid}/decision")
async def decide(aid: UUID, body: DecisionIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                 db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid, lock=True)
    if not _can_decide(a):
        raise conflict("EVAL_NOT_DONE", "평가가 완료된 뒤 결정할 수 있습니다.")  # 7.3
    version = await _decision_version(db, a.id)
    if body.decision == a.decision_status:
        return {"decision": a.decision_status, "version": version, "decided_at": a.decided_at}
    if body.expected_version != version:
        raise conflict("DECISION_CONFLICT", "다른 담당자가 먼저 변경했습니다. 최신 내용을 불러옵니다.", current_version=version)
    now = utcnow()
    db.add(DecisionHistory(application_id=a.id, company_id=a.company_id, from_status=a.decision_status,
                           to_status=body.decision, decided_by=ctx.user_id, decided_at=now))
    await _audit(db, ctx, request, "DECISION_SET", "application", a.id, **{"from": a.decision_status, "to": body.decision})
    a.decision_status, a.decided_at, a.decided_by = body.decision, now, ctx.user_id
    a.first_decided_at = a.first_decided_at or now
    await db.commit()
    return {"decision": a.decision_status, "version": version + 1, "decided_at": now}


@router.get("/applications/{aid}/decision-history")
async def decision_history(aid: UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                           db: AsyncSession = Depends(get_db)) -> list[dict]:
    await _app(db, ctx, aid)
    rows = await db.execute(select(DecisionHistory, User.name).join(User, User.id == DecisionHistory.decided_by)
                            .where(DecisionHistory.application_id == aid).order_by(DecisionHistory.id))
    return [{"from": h.from_status, "to": h.to_status, "decided_by": {"id": h.decided_by, "name": name},
             "decided_at": h.decided_at} for h, name in rows]


@router.post("/applications/{aid}/re-evaluate", status_code=202)
async def re_evaluate(aid: UUID, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                      db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid, lock=True)
    if a.eval_status != "ON_HOLD":
        raise conflict("NOT_ON_HOLD", "평가 보류 상태에서만 재평가할 수 있습니다.")
    await register_evaluation(db, a, trigger="RECRUITER")  # ADM-06
    await _audit(db, ctx, request, "RE_EVALUATE", "application", a.id)
    await db.commit()
    return {"eval_status": "PENDING"}


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=2000)


def _note_out(n: RecruiterNote, author_name: str) -> dict:
    return {"id": n.id, "author": {"id": n.author_id, "name": author_name}, "body": n.body,
            "created_at": n.created_at, "updated_at": n.updated_at}


@router.get("/applications/{aid}/notes")
async def list_notes(aid: UUID, ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> list[dict]:
    await _app(db, ctx, aid)
    rows = await db.execute(select(RecruiterNote, User.name).join(User, User.id == RecruiterNote.author_id).where(
        RecruiterNote.application_id == aid, RecruiterNote.company_id == ctx.company_id,
        RecruiterNote.deleted_at.is_(None)).order_by(RecruiterNote.created_at))
    return [_note_out(n, name) for n, name in rows]


@router.post("/applications/{aid}/notes", status_code=201)
async def create_note(aid: UUID, body: NoteIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                      db: AsyncSession = Depends(get_db)) -> dict:
    a = await _app(db, ctx, aid)
    n = RecruiterNote(application_id=a.id, company_id=ctx.company_id, author_id=ctx.user_id, body=body.body)
    db.add(n)
    await db.flush()
    await _audit(db, ctx, request, "NOTE_CREATE", "note", n.id)
    await db.commit()
    return _note_out(n, ctx.principal.name)


async def _own_note(db: AsyncSession, ctx: RecruiterCtx, nid: UUID) -> RecruiterNote:
    n = await get_for_company(db, RecruiterNote, nid, ctx.company_id, for_update=True)
    if n.deleted_at is not None:
        raise not_found()
    if n.author_id != ctx.user_id:
        raise AppError(403, "NOT_AUTHOR", "작성자만 메모를 수정하거나 삭제할 수 있습니다.")
    return n


@router.patch("/notes/{nid}")
async def update_note(nid: UUID, body: NoteIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                      db: AsyncSession = Depends(get_db)) -> dict:
    n = await _own_note(db, ctx, nid)
    n.body, n.updated_at = body.body, utcnow()
    await _audit(db, ctx, request, "NOTE_UPDATE", "note", n.id)
    await db.commit()
    return _note_out(n, ctx.principal.name)


@router.delete("/notes/{nid}", status_code=204)
async def delete_note(nid: UUID, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                      db: AsyncSession = Depends(get_db)) -> None:
    n = await _own_note(db, ctx, nid)
    n.deleted_at = utcnow()
    await _audit(db, ctx, request, "NOTE_DELETE", "note", n.id)
    await db.commit()


@router.get("/stats")
async def stats(from_: str | None = Query(None, alias="from", pattern=r"^\d{4}-\d{2}$"),
                to: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
                ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> dict:
    """ADM-08 (P1): monthly applicants, average total by job family, pass rate over decided applications."""
    month = func.to_char(func.timezone("Asia/Seoul", Application.applied_at), "YYYY-MM")
    conds = [Application.company_id == ctx.company_id]
    if from_:
        conds.append(month >= from_)
    if to:
        conds.append(month <= to)
    monthly = await db.execute(select(month, func.count()).where(*conds, Application.attempt_status.in_(
        ["SUBMITTED", "EXPIRED"])).group_by(month).order_by(month))
    avg = await db.execute(select(JobPosting.job_family, func.avg(RecruiterReport.total_score), func.count())
                           .join(Application, Application.posting_id == JobPosting.id)
                           .join(RecruiterReport, RecruiterReport.application_id == Application.id)
                           .where(*conds).group_by(JobPosting.job_family))
    decided = Application.decision_status.in_(["PASS", "FAIL"])
    by_posting = await db.execute(select(JobPosting.id, JobPosting.title, func.count(),
                                         func.count().filter(Application.decision_status == "PASS"))
                                  .join(Application, Application.posting_id == JobPosting.id)
                                  .where(*conds, decided).group_by(JobPosting.id, JobPosting.title))
    by_family = await db.execute(select(JobPosting.job_family, func.count(),
                                        func.count().filter(Application.decision_status == "PASS"))
                                 .join(Application, Application.posting_id == JobPosting.id)
                                 .where(*conds, decided).group_by(JobPosting.job_family))
    return {
        "monthly_applicants": [{"month": m, "count": c} for m, c in monthly],
        "avg_score_by_job_family": [{"job_family": f, "avg": str(round(v, 2)), "n": n} for f, v, n in avg],
        "pass_rate_by_posting": [{"posting_id": i, "title": t, "decided": d, "passed": p} for i, t, d, p in by_posting],
        "pass_rate_by_job_family": [{"job_family": f, "decided": d, "passed": p} for f, d, p in by_family],
    }
