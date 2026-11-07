"""Posting lifecycle (JOB-01~07, 7.4) and versioned evaluation settings (ADM-05, JOB-06, 6.4)."""
import secrets
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DEFAULT_WEIGHTS_CODING, DEFAULT_WEIGHTS_NO_CODING, WEIGHT_TOTAL
from app.core.errors import AppError, conflict
from app.core.timeutil import kst_end_of_day, to_kst_date, utcnow
from app.models import Application, CodingProblem, Company, JobPosting, PostingEvalSettings
from app.postings.schemas import (
    InterviewSettings,
    DEFAULT_QUESTION_AREAS, JOB_FAMILIES, QUESTION_AREAS, RUBRIC_VERSION, EvalSettingsOut, PostingDetailOut, PostingIn, PostingPublicOut, PostingSummaryOut,
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


def validate_weights(weights: dict[str, int | None], coding_enabled: bool, custom_total: int = 0) -> dict[str, int]:
    w = {k: v for k, v in weights.items() if v is not None}
    if ("coding" in w) != coding_enabled:
        raise AppError(422, "WEIGHTS_CODING_MISMATCH",
                       "코딩테스트를 켠 공고에서만 코딩테스트 가중치를 설정할 수 있습니다." if not coding_enabled
                       else "코딩테스트를 켠 공고는 코딩테스트 가중치가 필요합니다.")
    if sum(w.values()) + custom_total != WEIGHT_TOTAL:
        raise AppError(422, "WEIGHT_SUM_NOT_100", "가중치의 합이 100이어야 저장됩니다.", total=sum(w.values()) + custom_total)
    if any(v > 60 or v % 5 for v in w.values()):  # SC-R07: 0~60% in steps of 5
        raise AppError(422, "WEIGHT_STEP", "가중치는 0~60%를 5% 단위로 정할 수 있습니다.")
    return w


def clean_custom_items(items: list | None, keep: list | None = None) -> list[dict]:
    """The stored form of the company's own items: ids given to new ones, names unique, weights as validated numbers."""
    if items is None:
        return [dict(x) for x in keep or []]
    out, names = [], set()
    for it in items:
        d = it if isinstance(it, dict) else it.model_dump()
        if d["name"] in names:
            raise AppError(422, "CUSTOM_ITEM_DUPLICATE", "같은 이름의 평가 항목이 있습니다.", name=d["name"])
        names.add(d["name"])
        d["id"] = d.get("id") or secrets.token_hex(4)
        out.append(d)
    if any(x["weight"] % 5 for x in out):
        raise AppError(422, "WEIGHT_STEP", "가중치는 0~60%를 5% 단위로 정할 수 있습니다.")
    return out


def zero_weights(items: list[dict] | None) -> list[dict]:
    """Items stay when the base weights are reset to their defaults, but with weight 0 (not evaluated) so the sum is 100."""
    return [{**x, "weight": 0} for x in items or []]


async def _next_version(db: AsyncSession, posting_id: UUID) -> int:
    v = await db.scalar(select(func.max(PostingEvalSettings.version)).where(PostingEvalSettings.posting_id == posting_id))
    return (v or 0) + 1


def interview_values(iv: InterviewSettings | None, prev: PostingEvalSettings | None) -> dict:
    """Columns of SC-R07's interview settings: from the request, else carried over from the previous version."""
    if iv is not None:
        plan = [x.model_dump() for x in iv.question_plan] if iv.question_plan else None
        for x in plan or []:
            if x["text"] is not None and len(x["text"]) < 5:
                raise AppError(422, "CUSTOM_QUESTION_TOO_SHORT", "직접 입력한 질문은 5자 이상이어야 합니다.")
            x["text"] = x["text"] or None
        return dict(question_plan=plan, think_sec=iv.think_sec, answer_sec=iv.answer_sec, followup_max=iv.followup_max)
    if prev is not None:
        return dict(question_plan=prev.question_plan, think_sec=prev.think_sec, answer_sec=prev.answer_sec,
                    followup_max=prev.followup_max)
    return {}


async def add_settings(db: AsyncSession, posting: JobPosting, weights: dict[str, int], custom_question: str | None,
                       actor: UUID, interview: dict | None = None, custom_items: list[dict] | None = None) -> PostingEvalSettings:
    if posting.settings_locked_at is not None:
        raise conflict(*SETTINGS_LOCKED)
    if interview is None and posting.current_settings_id:
        interview = interview_values(None, await db.get(PostingEvalSettings, posting.current_settings_id))
    s = PostingEvalSettings(posting_id=posting.id, company_id=posting.company_id, version=await _next_version(db, posting.id),
                            w_expertise=weights["expertise"], w_logic=weights["logic"],
                            w_attitude=weights["attitude"], w_delivery=weights["delivery"],
                            w_coding=weights.get("coding"), custom_question=custom_question or None,
                            custom_items=custom_items or [], w_custom=sum(x["weight"] for x in custom_items or []),
                            rubric_version=RUBRIC_VERSION, created_by=actor, **(interview or {}))
    db.add(s)
    await db.flush()
    posting.current_settings_id = s.id
    return s


async def current_settings(db: AsyncSession, posting: JobPosting) -> PostingEvalSettings:
    return await db.get(PostingEvalSettings, posting.current_settings_id)


def settings_out(s: PostingEvalSettings, posting: JobPosting) -> EvalSettingsOut:
    iv = InterviewSettings(question_plan=s.question_plan, think_sec=s.think_sec, answer_sec=s.answer_sec,
                           followup_max=s.followup_max)
    return EvalSettingsOut(version=s.version, weights=s.weights(), custom_items=list(s.custom_items or []),
                           custom_question=s.custom_question, interview=iv,
                           question_areas=list(posting.question_areas or DEFAULT_QUESTION_AREAS),
                           rubric_version=s.rubric_version, locked=posting.settings_locked_at is not None,
                           coding_enabled=posting.coding_enabled)


async def counts(db: AsyncSession, posting_ids: list[UUID]) -> dict[UUID, tuple[int, int]]:
    if not posting_ids:
        return {}
    rows = await db.execute(
        select(Application.posting_id, func.count(),
               func.count().filter(Application.attempt_status == "SUBMITTED"))
        .where(Application.posting_id.in_(posting_ids), Application.share_result).group_by(Application.posting_id))
    return {pid: (n, done) for pid, n, done in rows}


def summary_out(p: JobPosting, c: tuple[int, int]) -> dict:
    return dict(id=p.id, title=p.title, job_family=p.job_family, status=p.status, effective_status=effective_status(p),
                deadline_date=to_kst_date(p.deadline_at), headcount=p.headcount, experience_level=p.experience_level,
                employment_type=p.employment_type, location=p.location, view_count=p.view_count, hidden=p.hidden_at is not None,
                coding_enabled=p.coding_enabled, applicant_count=c[0], completed_count=c[1], updated_at=p.updated_at)


async def detail_out(db: AsyncSession, p: JobPosting, weights_reset: bool = False) -> PostingDetailOut:
    c = (await counts(db, [p.id])).get(p.id, (0, 0))
    s = await current_settings(db, p)
    return PostingDetailOut(**summary_out(p, c), requirements=p.requirements, preferred=p.preferred, duties=p.duties,
                            coding_problem_id=p.coding_problem_id, coding_selection=p.coding_selection, coding_difficulty=p.coding_difficulty,
                            coding_language=p.coding_language,
                            coding_time_limit_min=p.coding_time_limit_min, settings_locked=p.settings_locked_at is not None,
                            row_version=p.row_version, evaluation_settings=settings_out(s, p), weights_reset=weights_reset)


async def public_out(db: AsyncSession, p: JobPosting, has_applied: bool = False) -> PostingPublicOut:
    company = await db.get(Company, p.company_id)
    return PostingPublicOut(
        id=p.id, title=p.title or "", company_name=company.name, company_intro=company.intro,
        job_family=p.job_family or "", job_family_label=JOB_FAMILIES.get(p.job_family or "", ""),
        requirements=p.requirements, preferred=p.preferred, duties=p.duties, headcount=p.headcount,
        experience_level=p.experience_level, employment_type=p.employment_type, location=p.location,
        image_url=f"/api/postings/{p.id}/image" if p.image_key else None,
        question_areas=[{"code": c, "label": QUESTION_AREAS[c]} for c in (p.question_areas or DEFAULT_QUESTION_AREAS)
                        if c in QUESTION_AREAS],
        deadline_date=to_kst_date(p.deadline_at), deadline_at=p.deadline_at, is_open=is_open(p),
        coding_enabled=p.coding_enabled, coding_language=p.coding_language if p.coding_enabled else None,
        coding_time_limit_min=p.coding_time_limit_min if p.coding_enabled else None, has_applied=has_applied)


def _apply_fields(p: JobPosting, body: PostingIn) -> bool:
    """Copy editable fields; returns True when coding_enabled flipped (weights item set changes)."""
    coding_changed = body.coding_enabled != p.coding_enabled
    locked = p.settings_locked_at is not None
    # Companies do not pick a problem: the server assigns one (ensure_problem); an id the client omits keeps the current one.
    problem_id = body.coding_problem_id or p.coding_problem_id
    coding_fields_changed = coding_changed or (
        body.coding_enabled and (problem_id != p.coding_problem_id or body.coding_time_limit_min != p.coding_time_limit_min))
    if locked and coding_fields_changed:
        raise conflict(*SETTINGS_LOCKED)
    p.title, p.job_family = body.title, body.job_family
    p.requirements, p.preferred, p.duties, p.headcount = body.requirements, body.preferred, body.duties, body.headcount
    p.experience_level, p.employment_type, p.location = body.experience_level, body.employment_type, body.location
    p.deadline_at = kst_end_of_day(body.deadline_date) if body.deadline_date else None
    p.coding_enabled = body.coding_enabled
    p.coding_problem_id = problem_id if body.coding_enabled else None
    if body.coding_problem_id:  # naming a problem pins it; otherwise each application gets its own (coding/selection.py)
        p.coding_selection = "FIXED"
    elif p.coding_selection is None:
        p.coding_selection = "AUTO"
    p.coding_difficulty = body.coding_difficulty or p.coding_difficulty or "NORMAL"
    p.coding_language = "python" if body.coding_enabled else None
    p.coding_time_limit_min = body.coding_time_limit_min if body.coding_enabled else None
    return coding_changed


async def ensure_problem(db: AsyncSession, p: JobPosting) -> None:
    """`coding_problem_id` must be set on a coding posting (publish checks it). With coding_selection AUTO it is only the fallback;
    what an applicant gets comes from coding/selection.py, at one level for the whole posting so applicants stay comparable."""
    if not p.coding_enabled or p.coding_problem_id:
        return
    with db.no_autoflush:  # the posting row is half edited (coding on, no problem yet): do not flush it before the id is set
        p.coding_problem_id = await db.scalar(select(CodingProblem.id).where(CodingProblem.is_active).order_by(func.random()).limit(1))


async def create_posting(db: AsyncSession, company_id: UUID, actor: UUID, body: PostingIn) -> PostingDetailOut:
    p = JobPosting(company_id=company_id, created_by=actor, status="DRAFT")
    _apply_fields(p, body)
    await ensure_problem(db, p)
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
    await ensure_problem(db, p)
    if coding_changed:
        prev = await current_settings(db, p)
        await add_settings(db, p, default_weights(p.coding_enabled), prev.custom_question, actor,
                           custom_items=zero_weights(prev.custom_items))
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
    company = await db.get(Company, p.company_id)
    if company is not None and company.verification != "APPROVED":
        raise AppError(403, "COMPANY_NOT_VERIFIED", "기업 인증이 끝나기 전에는 공고를 게시할 수 없습니다.")
    if company is not None and company.suspended_at is not None:
        raise AppError(403, "COMPANY_SUSPENDED", "운영자가 정지한 기업은 공고를 게시할 수 없습니다.")
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
    """화면정의서 SC-R04 삭제: the posting leaves the applicant screens at once and cannot come back, and the applicants'
    results stay in 응시자 목록 (screen spec over the TRD's JOB-04, which only allowed closing). An open posting is closed first,
    so unfinished interviews end the way they do on a normal close."""
    if p.status == "OPEN":
        await close(db, p, "MANUAL")
    p.deleted_at = utcnow()
