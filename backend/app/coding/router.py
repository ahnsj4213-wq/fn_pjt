"""Candidate coding test API (TRD 04 §5.3, COD-01~06, AC-15). Every call also counts as the coding heartbeat (C7).

- GET  …/coding            problem, PUBLIC examples only, server deadline, draft, whether [실행] is available
- PUT  …/coding/draft      autosave; applied only when the revision is newer (D-11); allowed until deadline + grace
- POST …/coding/runs       [실행]: public examples in the sandbox, synchronous; NORMAL / ABNORMAL / ENV_ERROR only —
                           never whether the output is correct (AC-15), never hidden tests
- GET  …/coding/runs/{id}  the same result again (page refresh)
- POST …/coding/submit     final snapshot + grading job + evaluation (waits for grading) in one transaction
The deadline runs on the server clock regardless of connection state; `coding_autosubmit` submits the last draft
when it passes (COD-05).
"""
import hashlib
import json
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.lifecycle import register_evaluation, touch
from app.auth.deps import CandidateCtx, require_candidate
from app.auth.scope import get_for_owner
from app.coding import runner
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, conflict, not_found
from app.core.timeutil import utcnow
from app.interview import infra
from app.jobs.queue import enqueue
from app.models import Application, CodingAttempt, CodingProblem, CodingRun, CodingSubmission, CodingTestcase

router = APIRouter(prefix="/api/candidate/applications/{aid}/coding", tags=["candidate"])
MAX_CODE_BYTES = 64 * 1024
RUN_WALL_MS = 10_000  # [실행] is synchronous: the whole call stays within ~10 s
OUTPUT_HEAD = 8_000


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    revision: int = Field(ge=1)


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    revision: int = Field(ge=0)
    confirmed: bool


def _check_size(code: str) -> None:
    if len(code.encode()) > MAX_CODE_BYTES:
        raise AppError(413, "CODE_TOO_LARGE", "코드는 64KB 이하여야 합니다.")


async def _coding(db: AsyncSession, ctx: CandidateCtx, aid: UUID, lock: bool = False) -> tuple[Application, CodingAttempt]:
    app = await get_for_owner(db, Application, aid, ctx.user_id, for_update=lock)
    if app.attempt_status != "CODING":
        raise conflict("INVALID_STATE", "코딩테스트를 진행 중인 상태가 아닙니다.", current=app.attempt_status)
    attempt = await db.get(CodingAttempt, app.id, with_for_update=lock)
    await touch(db, app.id)  # C7: coding calls keep the attempt alive
    return app, attempt


def _grace() -> timedelta:
    return timedelta(seconds=get_settings().answer_grace_seconds)


async def _public_examples(db: AsyncSession, problem_id: UUID) -> list[CodingTestcase]:
    return list((await db.execute(select(CodingTestcase).where(
        CodingTestcase.problem_id == problem_id, CodingTestcase.visibility == "PUBLIC")
        .order_by(CodingTestcase.ordinal))).scalars())


@router.get("")
async def get_coding(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    _, attempt = await _coding(db, ctx, aid)
    problem = await db.get(CodingProblem, attempt.problem_id)
    examples = await _public_examples(db, problem.id)
    run_available = await infra.sandbox_ready()
    await db.commit()
    return {"problem": {"title": problem.title, "statement_md": problem.statement_md,
                        "examples": [{"input": t.input, "output": t.expected_output} for t in examples]},
            "language": attempt.language, "languages": [attempt.language], "deadline_at": attempt.deadline_at,
            "server_now": utcnow(),
            "draft": {"code": attempt.draft_code, "revision": attempt.draft_revision, "saved_at": attempt.draft_saved_at},
            "run_available": run_available}


@router.put("/draft")
async def put_draft(aid: UUID, body: DraftIn, ctx: CandidateCtx = Depends(require_candidate),
                    db: AsyncSession = Depends(get_db)) -> dict:
    _check_size(body.code)
    _, attempt = await _coding(db, ctx, aid, lock=True)
    now = utcnow()
    if now > attempt.deadline_at + _grace():
        raise conflict("TIME_OVER", "제한시간이 지났습니다. 마지막으로 저장된 코드가 제출됩니다.")
    applied = body.revision > attempt.draft_revision  # an older autosave arriving late never overwrites (D-11)
    if applied:
        attempt.draft_code, attempt.draft_revision, attempt.draft_saved_at = body.code, body.revision, now
    await db.commit()
    return {"revision": attempt.draft_revision, "saved_at": attempt.draft_saved_at, "applied": applied}


def _run_view(run: CodingRun) -> dict:
    detail = json.loads(run.stdout_head) if run.stdout_head else {}
    verdict = {"OK": "NORMAL", "SANDBOX_ERROR": "ENV_ERROR", None: "ENV_ERROR"}.get(run.verdict, "ABNORMAL")
    return {"run_id": run.id, "verdict": verdict, "outcome": run.verdict, "stdout": detail.get("stdout", ""),
            "stderr": detail.get("stderr", ""), "truncated": detail.get("truncated", False),
            "duration_ms": detail.get("duration_ms"), "examples": detail.get("examples", [])}


@router.post("/runs")
async def run_code(aid: UUID, body: RunIn, ctx: CandidateCtx = Depends(require_candidate),
                   db: AsyncSession = Depends(get_db)) -> dict:
    _check_size(body.code)
    app, attempt = await _coding(db, ctx, aid)
    if utcnow() > attempt.deadline_at:
        raise conflict("TIME_OVER", "제한시간이 지나 실행할 수 없습니다.")
    if not await infra.sandbox_ready():
        raise AppError(503, "CODE_EXECUTION_UNAVAILABLE", "지금은 코드 실행 환경을 사용할 수 없습니다.")
    problem = await db.get(CodingProblem, attempt.problem_id)
    examples = await _public_examples(db, problem.id)
    run = CodingRun(application_id=app.id, code_sha256=hashlib.sha256(body.code.encode()).digest(), status="RUNNING")
    db.add(run)
    try:
        await db.commit()  # one run in flight per application (ux_coding_run_inflight)
    except IntegrityError:
        await db.rollback()
        raise AppError(429, "RUN_BUSY", "이전 실행이 끝난 뒤 다시 실행해 주세요.") from None

    detail: dict = {}
    verdict: str | None = None
    try:
        results = await runner.execute(body.code, [{"case_id": str(t.ordinal), "stdin": t.input} for t in examples],
                                       wall_ms=min(problem.time_limit_ms, RUN_WALL_MS), mem_mb=problem.memory_mb)
        worst = next((r for r in results if r.verdict != "OK"), results[0])
        verdict = worst.verdict
        detail = {"stdout": worst.stdout[:OUTPUT_HEAD], "stderr": worst.stderr[:OUTPUT_HEAD],
                  "truncated": any(len(r.stdout) > OUTPUT_HEAD or r.verdict == "OUTPUT_LIMIT" for r in results),
                  "duration_ms": max(r.time_ms for r in results),
                  # Per public example: what the program printed. No pass/fail judgement here (AC-15).
                  "examples": [{"input": t.input, "stdout": r.stdout[:OUTPUT_HEAD], "stderr": r.stderr[:OUTPUT_HEAD],
                                "outcome": r.verdict} for t, r in zip(examples, results)]}
    except Exception:  # noqa: BLE001 - runner down or broken mid-run: the environment failed, not the code
        verdict = "SANDBOX_ERROR"
    finally:
        await db.execute(update(CodingRun).where(CodingRun.id == run.id).values(
            status="DONE" if verdict != "SANDBOX_ERROR" else "ERROR", verdict=verdict,
            stdout_head=json.dumps(detail, ensure_ascii=False), finished_at=utcnow()))
        await db.commit()
    await db.refresh(run)
    return _run_view(run)


@router.get("/runs/{run_id}")
async def get_run(aid: UUID, run_id: UUID, ctx: CandidateCtx = Depends(require_candidate),
                  db: AsyncSession = Depends(get_db)) -> dict:
    app = await get_for_owner(db, Application, aid, ctx.user_id)
    run = await db.get(CodingRun, run_id)
    if run is None or run.application_id != app.id:
        raise not_found()
    return _run_view(run)


async def submit_snapshot(db: AsyncSession, app: Application, code: str, kind: str) -> bool:
    """Snapshot + grading + SUBMITTED + evaluation waiting for the grade, in the caller's transaction (7.2).
    False if a submission already exists (manual and automatic submits race on the unique key)."""
    res = await db.execute(insert(CodingSubmission).values(
        application_id=app.id, company_id=app.company_id, code=code, code_sha256=hashlib.sha256(code.encode()).digest(),
        kind=kind).on_conflict_do_nothing(index_elements=["application_id"]).returning(CodingSubmission.id))
    sub_id = res.scalar_one_or_none()
    if sub_id is None:
        return False
    grade_job = await enqueue(db, "grade_coding", {"submission_id": str(sub_id)}, application_id=app.id,
                              dedupe_key=f"grade:{app.id}")
    app.attempt_status, app.interrupted_from, app.submitted_at = "SUBMITTED", None, utcnow()
    await register_evaluation(db, app, depends_on=grade_job)
    return True


@router.post("/submit")
async def submit(aid: UUID, body: SubmitIn, ctx: CandidateCtx = Depends(require_candidate),
                 db: AsyncSession = Depends(get_db)) -> dict:
    _check_size(body.code)
    if not body.confirmed:
        raise AppError(422, "CONFIRMATION_REQUIRED", "최종 제출 확인이 필요합니다.")
    app = await get_for_owner(db, Application, aid, ctx.user_id, for_update=True)
    existing = (await db.execute(select(CodingSubmission).where(CodingSubmission.application_id == app.id))).scalar_one_or_none()
    if existing is not None:
        raise conflict("ALREADY_SUBMITTED", "이미 제출했습니다.", kind=existing.kind)
    app, attempt = await _coding(db, ctx, aid, lock=True)
    now = utcnow()
    if now > attempt.deadline_at + _grace():
        raise conflict("TIME_OVER", "제한시간이 지났습니다. 마지막으로 저장된 코드가 제출됩니다.")
    attempt.draft_code, attempt.draft_revision, attempt.draft_saved_at = body.code, max(body.revision,
                                                                                        attempt.draft_revision), now
    await submit_snapshot(db, app, body.code, "MANUAL")
    await db.commit()
    return {"attempt_status": "SUBMITTED"}
