"""Posting lifecycle (JOB-01~07, 7.4) and versioned evaluation settings (ADM-05, JOB-06, 6.4)."""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DEFAULT_WEIGHTS_CODING, DEFAULT_WEIGHTS_NO_CODING, WEIGHT_TOTAL
from app.core.errors import AppError, conflict
from app.core.timeutil import kst_end_of_day, to_kst_date, utcnow
from app.models import Application, Company, JobPosting, PostingEvalSettings
from app.postings.schemas import (
    JOB_FAMILIES, RUBRIC_VERSION, EvalSettingsOut, PostingDetailOut, PostingIn, PostingPublicOut, PostingSummaryOut,
)

SETTINGS_LOCKED = ("SETTINGS_LOCKED", "첫 응시가 시작된 공고는 코딩테스트 설정과 평가 가중치를 바꿀 수 없습니다.")


def is_open(p: JobPosting) -> bool:
    return p.status == "OPEN" and p.deleted_at is None and p.deadline_at is not None and p.deadline_at > utcnow()


def effective_status(p: JobPosting) -> str:
    if p.status == "OPEN" and not is_open(p):
        return "CLOSED"
    return p.status


def default_weights(coding: bool) -> dict[str, int]:
    return dict(DEFAULT_WEIGHTS_CODING if coding else DEFAULT_WEIGHTS_NO_CODING)


def validate_weights(weights: dict[str, int | None], coding_enabled: bool) -> dict[str, int]:
    w = {k: v for k, v in weights.items() if v is not None}
    if ("coding" in w) != coding_enabled:
        raise AppError(422, "WEIGHTS_CODING_MISMATCH",
                       "코딩테스트를 켠 공고에서만 코딩테스트 가중치를 설정할 수 있습니다." if not coding_enabled
                       else "코딩테스트를 켠 공고는 코딩테스트 가중치가 필요합니다.")
    if sum(w.values()) != WEIGHT_TOTAL:
        raise AppError(422, "WEIGHT_SUM_NOT_100", "가중치의 합이 100이어야 저장됩니다.", total=sum(w.values()))
    return w


async def _next_version(db: AsyncSession, posting_id: UUID) -> int:
    v = await db.scalar(select(func.max(PostingEvalSettings.version)).where(PostingEvalSettings.posting_id == posting_id))
    return (v or 0) + 1


async def add_settings(db: AsyncSession, posting: JobPosting, weights: dict[str, int], custom_question: str | None,
                       actor: UUID) -> PostingEvalSettings:
    if posting.settings_locked_at is not None:
        raise conflict(*SETTINGS_LOCKED)
    s = PostingEvalSettings(posting_id=posting.id, company_id=posting.company_id, version=await _next_version(db, posting.id),
                            w_expertise=weights["expertise"], w_logic=weights["logic"],
                            w_specificity=weights["specificity"], w_delivery=weights["delivery"],
                            w_coding=weights.get("coding"), custom_question=custom_question or None,
                            rubric_version=RUBRIC_VERSION, created_by=actor)
    db.add(s)
    await db.flush()
    posting.current_settings_id = s.id
    return s


async def current_settings(db: AsyncSession, posting: JobPosting) -> PostingEvalSettings:
    return await db.get(PostingEvalSettings, posting.current_settings_id)


def settings_out(s: PostingEvalSettings, posting: JobPosting) -> EvalSettingsOut:
    return EvalSettingsOut(version=s.version, weights=s.weights(), custom_question=s.custom_question,
                           rubric_version=s.rubric_version, locked=posting.settings_locked_at is not None,
                           coding_enabled=posting.coding_enabled)


async def counts(db: AsyncSession, posting_ids: list[UUID]) -> dict[UUID, tuple[int, int]]:
    if not posting_ids:
        return {}
    rows = await db.execute(
        select(Application.posting_id, func.count(),
               func.count().filter(Application.attempt_status == "SUBMITTED"))
        .where(Application.posting_id.in_(posting_ids)).group_by(Application.posting_id))
    return {pid: (n, done) for pid, n, done in rows}


def summary_out(p: JobPosting, c: tuple[int, int]) -> dict:
    return dict(id=p.id, title=p.title, job_family=p.job_family, status=p.status, effective_status=effective_status(p),
                deadline_date=to_kst_date(p.deadline_at), headcount=p.headcount, coding_enabled=p.coding_enabled,
                applicant_count=c[0], completed_count=c[1], updated_at=p.updated_at)


async def detail_out(db: AsyncSession, p: JobPosting, weights_reset: bool = False) -> PostingDetailOut:
    c = (await counts(db, [p.id])).get(p.id, (0, 0))
    s = await current_settings(db, p)
    return PostingDetailOut(**summary_out(p, c), requirements=p.requirements, preferred=p.preferred, duties=p.duties,
                            coding_problem_id=p.coding_problem_id, coding_language=p.coding_language,
                            coding_time_limit_min=p.coding_time_limit_min, settings_locked=p.settings_locked_at is not None,
                            row_version=p.row_version, evaluation_settings=settings_out(s, p), weights_reset=weights_reset)


async def public_out(db: AsyncSession, p: JobPosting, has_applied: bool = False) -> PostingPublicOut:
    company = await db.get(Company, p.company_id)
    return PostingPublicOut(
        id=p.id, title=p.title or "", company_name=company.name, company_intro=company.intro,
        job_family=p.job_family or "", job_family_label=JOB_FAMILIES.get(p.job_family or "", ""),
        requirements=p.requirements, preferred=p.preferred, duties=p.duties, headcount=p.headcount,
        deadline_date=to_kst_date(p.deadline_at), deadline_at=p.deadline_at, is_open=is_open(p),
        coding_enabled=p.coding_enabled, coding_language=p.coding_language if p.coding_enabled else None,
        coding_time_limit_min=p.coding_time_limit_min if p.coding_enabled else None, has_applied=has_applied)


def _apply_fields(p: JobPosting, body: PostingIn) -> bool:
    """Copy editable fields; returns True when coding_enabled flipped (weights item set changes)."""
    coding_changed = body.coding_enabled != p.coding_enabled
    locked = p.settings_locked_at is not None
    coding_fields_changed = coding_changed or (
        body.coding_enabled and (body.coding_problem_id != p.coding_problem_id
                                 or body.coding_time_limit_min != p.coding_time_limit_min))
    if locked and coding_fields_changed:
        raise conflict(*SETTINGS_LOCKED)
    p.title, p.job_family = body.title, body.job_family
    p.requirements, p.preferred, p.duties, p.headcount = body.requirements, body.preferred, body.duties, body.headcount
    p.deadline_at = kst_end_of_day(body.deadline_date) if body.deadline_date else None
    p.coding_enabled = body.coding_enabled
    p.coding_problem_id = body.coding_problem_id if body.coding_enabled else None
    p.coding_language = "python" if body.coding_enabled else None
    p.coding_time_limit_min = body.coding_time_limit_min if body.coding_enabled else None
    return coding_changed


async def create_posting(db: AsyncSession, company_id: UUID, actor: UUID, body: PostingIn) -> PostingDetailOut:
    p = JobPosting(company_id=company_id, created_by=actor, status="DRAFT")
    _apply_fields(p, body)
    db.add(p)
    await db.flush()
    await add_settings(db, p, default_weights(p.coding_enabled), None, actor)
    await db.flush()
    return await detail_out(db, p)


async def update_posting(db: AsyncSession, p: JobPosting, actor: UUID, body: PostingIn) -> PostingDetailOut:
    if body.row_version is None or body.row_version != p.row_version:
        raise conflict("VERSION_CONFLICT", "다른 곳에서 공고가 수정되었습니다. 새로고침 후 다시 시도해 주세요.",
                       current_row_version=p.row_version)
    if p.status == "CLOSED":
        raise conflict("POSTING_CLOSED", "마감된 공고는 수정할 수 없습니다.")
    if p.status == "OPEN" and body.deadline_date and kst_end_of_day(body.deadline_date) <= utcnow():
        raise AppError(422, "DEADLINE_IN_PAST", "마감일은 오늘 이후여야 합니다.")
    coding_changed = _apply_fields(p, body)
    if coding_changed:
        prev = await current_settings(db, p)
        await add_settings(db, p, default_weights(p.coding_enabled), prev.custom_question, actor)
    p.row_version += 1
    p.updated_at = utcnow()
    try:
        await db.flush()
    except DBAPIError as e:  # trigger AP003: locked coding settings (race with interview start)
        if "AP003" in str(e.orig):
            raise conflict(*SETTINGS_LOCKED) from None
        raise
    return await detail_out(db, p, weights_reset=coding_changed)


async def publish(db: AsyncSession, p: JobPosting) -> None:
    if p.status != "DRAFT":
        raise conflict("INVALID_STATE", "임시저장 상태의 공고만 게시할 수 있습니다.", current=p.status)
    missing = [f for f in ("title", "job_family", "deadline_at") if not getattr(p, f)]
    if p.coding_enabled:
        missing += [f for f in ("coding_problem_id", "coding_time_limit_min") if not getattr(p, f)]
    if missing:
        raise AppError(422, "INCOMPLETE_POSTING", "게시에 필요한 항목을 채워 주세요.", missing=missing)
    if p.deadline_at <= utcnow():
        raise AppError(422, "DEADLINE_IN_PAST", "마감일은 오늘 이후여야 합니다.")
    s = await current_settings(db, p)
    if (s.w_coding is not None) != p.coding_enabled:
        raise AppError(422, "WEIGHTS_CODING_MISMATCH", "코딩테스트 설정과 가중치 항목이 맞지 않습니다.")
    p.status, p.published_at, p.updated_at = "OPEN", utcnow(), utcnow()
    p.row_version += 1


async def close(db: AsyncSession, p: JobPosting, reason: str) -> None:
    from app.applications.lifecycle import expire_unfinished  # local import: lifecycle depends on postings

    if p.status != "OPEN":
        raise conflict("INVALID_STATE", "진행 중인 공고만 마감할 수 있습니다.", current=p.status)
    p.status, p.closed_at, p.close_reason, p.updated_at = "CLOSED", utcnow(), reason, utcnow()
    p.row_version += 1
    await db.flush()
    await expire_unfinished(db, p.id)


async def delete(db: AsyncSession, p: JobPosting) -> None:
    n = await db.scalar(select(func.count()).select_from(Application).where(Application.posting_id == p.id))
    if n:
        raise conflict("HAS_APPLICATIONS", "지원자가 있는 공고는 삭제할 수 없고 마감만 할 수 있습니다.")  # JOB-04
    p.deleted_at = utcnow()
