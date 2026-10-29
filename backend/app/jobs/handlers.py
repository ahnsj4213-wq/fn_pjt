"""Domain job handlers (ADR-0005/0006, TRD 03 §7, TRD 05 §10). Imported by the worker (app.jobs.tasks).

Every handler ends with `fenced_complete` inside its final write transaction: a worker whose lease was taken over
rolls back instead of writing twice (R-14). AIUnavailable (app.ai.client) / RunnerUnavailable / CodingNotGraded defer the job
without consuming an attempt (OQ-23); any other exception consumes it.
"""
import logging
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import rubric
from app.ai.client import get_ai_client
from app.coding import grading
from app.coding.runner import RunnerUnavailable
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import log_event
from app.core.storage import get_storage
from app.core.timeutil import utcnow
from app.evaluation import pipeline
from app.evaluation.scoring import SCORING_VERSION
from app.jobs.queue import enqueue
from app.jobs.worker import ON_CLAIM, ON_EXHAUSTED, ON_REQUEUE, InfraUnavailable, JobCtx, fenced_complete, handler
from app.models import (
    AnswerEvaluation,
    AnswerPreEvaluation,
    Application,
    CandidateFeedback,
    CodingProblem,
    CodingSubmission,
    CodingTestcase,
    CodingTestResult,
    InterviewAnswer,
    InterviewQuestion,
    JobPosting,
    PostingEvalSettings,
    ProctorEvent,
    QuestionSet,
    QuestionTtsAsset,
    RecruiterReport,
    ResumeFile,
)
from app.questions import compose as composer
from app.resumes.schemas import from_ai_resume

log = logging.getLogger("app.worker")



# ── compose_questions (ADR-0005) ───────────────────────────────────────────────
@handler("compose_questions")
async def compose_questions(ctx: JobCtx) -> None:
    app_id, settings_id = UUID(ctx.payload["application_id"]), UUID(ctx.payload["settings_id"])
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        live = (await db.execute(select(QuestionSet).where(QuestionSet.application_id == app_id,
                                                           QuestionSet.status.in_(["COMPOSING", "READY"])))).scalar_one_or_none()
        stale = app.attempt_status not in ("READY", "PREPARING")
        if stale or (live is not None and live.settings_id == settings_id):
            await fenced_complete(db, ctx)  # already composed, or the attempt moved on: nothing to do
            return
        if live is not None:
            live.status = "SUPERSEDED"  # weights/custom question changed before the interview (start() re-enqueues)
            await db.flush()
        settings = await db.get(PostingEvalSettings, settings_id)
        picked = await composer.compose(db, app, settings, get_ai_client(), get_settings().embed_model)
        qset = await composer.write_set(db, app, settings_id, picked)
        await enqueue(db, "tts_pregen", {"question_set_id": str(qset.id)}, application_id=app_id,
                      dedupe_key=f"tts:{qset.id}")
        await fenced_complete(db, ctx)
    log_event(log, "questions_composed", application_id=str(app_id), count=len(picked))


# ── tts_pregen (T20: not a start condition) ────────────────────────────────────
@handler("tts_pregen")
async def tts_pregen(ctx: JobCtx) -> None:
    qset_id = UUID(ctx.payload["question_set_id"])
    async with session_scope() as db:
        qset = await db.get(QuestionSet, qset_id)
        app = await db.get(Application, qset.application_id)
        if qset.status != "READY" or app.attempt_status in ("SUBMITTED", "EXPIRED"):
            await fenced_complete(db, ctx)
            return
        done = set((await db.execute(select(QuestionTtsAsset.question_id).join(
            InterviewQuestion, InterviewQuestion.id == QuestionTtsAsset.question_id).where(
            InterviewQuestion.question_set_id == qset_id))).scalars())
        todo = [(q.id, q.text) for q in (await db.execute(select(InterviewQuestion).where(
            InterviewQuestion.question_set_id == qset_id, InterviewQuestion.role == "MAIN")
            .order_by(InterviewQuestion.seq))).scalars() if q.id not in done]
    ai = get_ai_client()
    for qid, qtext in todo:  # one question per transaction: progress survives a restart
        out = await ai.tts(qtext)
        key = f"tts/{qid}.pcm"
        await get_storage().put(key, out.pcm48k)
        async with session_scope() as db:
            await db.execute(insert(QuestionTtsAsset).values(question_id=qid, audio_key=key, duration_ms=out.duration_ms,
                                                             voice_model=out.voice_model).on_conflict_do_nothing())
    async with session_scope() as db:
        await fenced_complete(db, ctx)


# ── extract_resume (T1, RES-03) ────────────────────────────────────────────────
@handler("extract_resume")
async def extract_resume(ctx: JobCtx) -> None:
    file_id = UUID(ctx.payload["file_id"])
    async with session_scope() as db:
        rf = await db.get(ResumeFile, file_id)
        if rf is None or rf.extraction_status != "PENDING" or not rf.extracted_text:
            await fenced_complete(db, ctx)
            return
        source = rf.extracted_text
    r = await get_ai_client().llm("resume_extract", {"source_text": source}, lane="INTERACTIVE")
    async with session_scope() as db:
        await fenced_complete(db, ctx)
        if r.status == "OK":
            values = {"extraction_status": "DONE", "extracted_fields": from_ai_resume(r.output)}
        else:  # the candidate types the resume instead (RES-05); no automatic retry of a model failure
            values = {"extraction_status": "FAILED"}
        await db.execute(update(ResumeFile).where(ResumeFile.id == file_id, ResumeFile.extraction_status == "PENDING")
                         .values(**values))
    log_event(log, "resume_extracted", status=r.status, count=r.flags.get("dropped_values", 0))


# ── pre_evaluate (EVL-05, T15) ─────────────────────────────────────────────────
@handler("pre_evaluate")
async def pre_evaluate(ctx: JobCtx) -> None:
    answer_id = UUID(ctx.payload["answer_id"])
    async with session_scope() as db:
        a = await db.get(InterviewAnswer, answer_id)
        q = await db.get(InterviewQuestion, a.question_id)
        app = await db.get(Application, a.application_id)
        posting = await db.get(JobPosting, app.posting_id)
    payload, manifest = await pipeline.evaluate_unit(get_ai_client(), q, a, posting)
    async with session_scope() as db:
        await fenced_complete(db, ctx)
        await db.execute(insert(AnswerPreEvaluation).values(answer_id=answer_id, manifest_hash=manifest, payload=payload)
                         .on_conflict_do_nothing())


# ── evaluate_application (EVL-06, ADR-0006 §4~7) ───────────────────────────────
async def _eval_on_claim(db: AsyncSession, ctx: JobCtx) -> None:
    """Same transaction as the claim: new run row (fencing token copy) and eval PENDING -> RUNNING."""
    s = get_settings()
    trigger = "AUTO_RETRY" if ctx.attempts > 1 else ctx.payload.get("trigger", "SUBMIT")
    await db.execute(text("UPDATE evaluation_runs SET status='FENCED_OUT', finished_at=now() "
                          "WHERE application_id=:a AND status='RUNNING'"), {"a": ctx.application_id})
    await db.execute(text("""
        INSERT INTO evaluation_runs(application_id, company_id, run_no, job_id, lease_token, trigger, status,
          model_name, prompt_version, rubric_version, eval_settings_id, scoring_version)
        SELECT a.id, a.company_id, COALESCE((SELECT max(run_no) FROM evaluation_runs WHERE application_id=a.id), 0) + 1,
               :job, :tok, :trig, 'RUNNING', :model, 'pending', :rubric, a.eval_settings_id, :scoring
        FROM applications a WHERE a.id = :a"""),
        {"a": ctx.application_id, "job": ctx.id, "tok": ctx.lease_token, "trig": trigger, "model": s.llm_model,
         "rubric": rubric.RUBRIC_VERSION, "scoring": SCORING_VERSION})
    await db.execute(text("UPDATE applications SET eval_status='RUNNING' WHERE id=:a AND eval_status='PENDING'"),
                     {"a": ctx.application_id})


_RUN_STATUS = {"ATTEMPT_TIMEOUT": "TIMED_OUT", "INFRA_UNAVAILABLE": "ABORTED_INFRA", "LEASE_EXPIRED": "FENCED_OUT"}


async def _close_run(db: AsyncSession, ctx: JobCtx, code: str) -> None:
    await db.execute(text("UPDATE evaluation_runs SET status=:st, finished_at=now(), error_code=:e "
                          "WHERE application_id=:a AND lease_token=:tok AND status='RUNNING'"),
                     {"st": _RUN_STATUS.get(code, "FAILED"), "e": code[:60], "a": ctx.application_id,
                      "tok": ctx.lease_token})


async def _eval_on_requeue(db: AsyncSession, ctx: JobCtx) -> None:
    code = await db.scalar(text("SELECT last_error_code FROM jobs WHERE id=:id"), {"id": ctx.id}) or "FAILED"
    await _close_run(db, ctx, code)
    await db.execute(text("UPDATE applications SET eval_status='PENDING' WHERE id=:a AND eval_status='RUNNING'"),
                     {"a": ctx.application_id})


async def _eval_on_exhausted(db: AsyncSession, ctx: JobCtx, code: str) -> None:
    """EVL-06: two failed attempts -> 평가 보류 (no score rows); the recruiter can re-run it (ADM-06)."""
    await _close_run(db, ctx, code)
    await db.execute(text("UPDATE applications SET eval_status='ON_HOLD' WHERE id=:a AND eval_status='RUNNING'"),
                     {"a": ctx.application_id})


ON_CLAIM["evaluate_application"] = _eval_on_claim
ON_REQUEUE["evaluate_application"] = _eval_on_requeue
ON_EXHAUSTED["evaluate_application"] = _eval_on_exhausted


@handler("evaluate_application")
async def evaluate_application(ctx: JobCtx) -> None:
    app_id = UUID(ctx.payload["application_id"])
    ai = get_ai_client()
    health = await ai.health()
    manifest = health.get("manifest_hash") or ""
    try:
        async with session_scope() as db:
            app = await db.get(Application, app_id)
            run_id = await db.scalar(text("SELECT id FROM evaluation_runs WHERE application_id=:a AND lease_token=:t"),
                                     {"a": app_id, "t": ctx.lease_token})
            if run_id is None:
                raise RuntimeError("evaluation run row missing")
            rep = await pipeline.build_report(db, ai, app, manifest)
            await fenced_complete(db, ctx)
            await _write_report(db, app, run_id, rep, health)
    except pipeline.CodingNotGraded as e:
        raise InfraUnavailable("coding submission not graded yet") from e
    log_event(log, "evaluation_done", application_id=str(app_id), count=len(rep.units))


async def _write_report(db: AsyncSession, app: Application, run_id: UUID, rep: pipeline.Report, health: dict) -> None:
    for answer_id, payload in rep.units.items():
        for crit, item in payload["items"].items():
            quote = item.get("quote")
            db.add(AnswerEvaluation(run_id=run_id, answer_id=answer_id, criterion=crit, score=item["score"],
                                    evidence_status=item["evidence_status"],
                                    insufficient_reason=item.get("insufficient_reason"),
                                    quote=quote["text"] if quote else None, quote_start=quote["start"] if quote else None,
                                    quote_end=quote["end"] if quote else None, comment=item.get("comment") or None,
                                    llm_attempts=payload.get("llm_calls", 0)))
    sc = rep.scores
    settings = await db.get(PostingEvalSettings, app.eval_settings_id)
    leaves = await db.scalar(select(ProctorEvent.id).where(ProctorEvent.application_id == app.id).limit(1))
    db.add(RecruiterReport(
        application_id=app.id, company_id=app.company_id, run_id=run_id, score_expertise=sc.criteria["expertise"],
        score_logic=sc.criteria["logic"], score_specificity=sc.criteria["specificity"],
        score_delivery=sc.criteria["delivery"], score_coding=sc.coding, total_score=sc.total,
        insufficient_count=sc.insufficient_count, no_answer_count=sc.no_answer_count,
        insufficient_by_criterion=sc.insufficient_by_criterion, answered_main_count=rep.answered_main,
        question_scores=sc.question_scores, weights=settings.weights(), summary=rep.summary["overall"],
        strengths=[x["text"] for x in rep.summary["strengths"]],
        improvements=[x["text"] for x in rep.summary["improvements"]],
        code_review=rep.code_review, proctor_summary={"has_events": leaves is not None}))
    coding_all = None
    if rep.coding is not None:
        coding_all = bool(rep.coding.get("reached")) and rep.coding["passed"] == rep.coding["total"]
    db.add(CandidateFeedback(
        application_id=app.id, company_id=app.company_id, run_id=run_id, score_expertise=sc.criteria["expertise"],
        score_logic=sc.criteria["logic"], score_specificity=sc.criteria["specificity"],
        score_delivery=sc.criteria["delivery"], strengths=rep.feedback["strengths"],
        improvements=rep.feedback["improvements"], coding_all_passed=coding_all))
    await db.flush()
    await db.execute(text("UPDATE evaluation_runs SET status='SUCCEEDED', finished_at=now(), prompt_version=:p, "
                          "model_digest=:d WHERE id=:r"),
                     {"p": rep.manifest_hash or "unknown", "d": (health.get("llm") or {}).get("digest"), "r": run_id})
    await db.execute(update(Application).where(Application.id == app.id, Application.eval_status == "RUNNING")
                     .values(eval_status="DONE", eval_done_at=utcnow()))


# ── grade_coding (6.5, COD-04·06) ──────────────────────────────────────────────
@handler("grade_coding")
async def grade_coding(ctx: JobCtx) -> None:
    sub_id = UUID(ctx.payload["submission_id"])
    async with session_scope() as db:
        sub = await db.get(CodingSubmission, sub_id)
        if sub.grading_status == "DONE":
            await fenced_complete(db, ctx)
            return
        app = await db.get(Application, sub.application_id)
        posting = await db.get(JobPosting, app.posting_id)
        problem = await db.get(CodingProblem, posting.coding_problem_id)
        cases = [{"id": t.id, "input": t.input, "expected_output": t.expected_output}
                 for t in (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == problem.id)
                                            .order_by(CodingTestcase.ordinal))).scalars()]
        code = sub.code
    try:
        g = await grading.grade(code, cases, wall_ms=problem.time_limit_ms, mem_mb=problem.memory_mb)
    except RunnerUnavailable as e:
        raise InfraUnavailable(str(e)) from e
    async with session_scope() as db:
        await fenced_complete(db, ctx)
        for c in g.cases:
            await db.execute(insert(CodingTestResult).values(submission_id=sub_id, testcase_id=UUID(c.testcase_id),
                                                             passed=c.passed, outcome=c.outcome, time_ms=c.time_ms)
                             .on_conflict_do_nothing())
        await db.execute(update(CodingSubmission).where(CodingSubmission.id == sub_id).values(
            grading_status="DONE", compile_ok=g.compile_ok, passed=g.passed, total=g.total, score=g.score,
            graded_at=utcnow()))
    log_event(log, "coding_graded", count=g.total, status=f"{g.passed}/{g.total}")

