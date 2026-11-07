"""Live composition (ADR-0022): each main question after the first is composed when the candidate reaches it.

Before the interview the composition graph writes only the first main question and keeps the plan (area, custom text, mode per
seq) on the question set. When every question so far is answered (and the follow-up decision of the last one is made), the
interview graph calls `ensure_next`, which composes the next slot of the plan with the same sources as before — the question bank
fitted to the resume and documents (T12), the resume (T3), the company documents (T2 with the RAG graph) — plus what the candidate
has just said: the latest answers steer the document search, which resume item is asked about, and which bank question fits.

Rules kept from up-front composition: the counts per area come from the plan (QST-02's 10 questions by type), a question once
written is never changed (QST-06), a failed or slow generation falls back to the question bank (QST-07), and every question is
checked against the ones already asked. Idempotent: one advisory lock per application, and the slot is looked up again inside it.
"""
import asyncio
import logging
from uuid import UUID

from sqlalchemy import select, text

from app.ai.client import get_ai_client
from app.core.config import get_settings
from app.core.constants import Area
from app.core.db import session_scope
from app.core.logging import log_event
from app.models import (
    Application, Company, Interview, InterviewAnswer, InterviewQuestion, JobPosting, PostingEvalSettings, QuestionSet,
)
from app.questions import compose as C

log = logging.getLogger("app.questions")
ANSWERS_USED = 2
ANSWER_CHARS = 400
# The question bank a slot falls back to when its generation fails or takes too long.
FALLBACK_KIND = {Area.PERSONALITY: "PERSONALITY", Area.COLLABORATION: "COLLABORATION", Area.PROJECT: "JOB",
                 Area.MOTIVATION: "COMPANY", Area.JOB_SKILL: "JOB"}


async def _next_slot(db, app_id: UUID) -> tuple[QuestionSet, C.Slot] | None:
    """The plan's next slot when the interview is ready for it: every question so far answered, no follow-up pending."""
    iv = await db.get(Interview, app_id)
    if iv is None or iv.question_set_id is None:
        return None
    qset = await db.get(QuestionSet, iv.question_set_id)
    if qset is None or not qset.plan:
        return None
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.question_set_id == qset.id))).scalars().all()
    answered = set((await db.execute(select(InterviewAnswer.question_id).where(InterviewAnswer.application_id == app_id))).scalars())
    if any(q.id not in answered for q in qs):
        return None  # a question (or its follow-up) is still open
    # The follow-up decision of the last answer comes first: the interview graph calls this after decide_followup (or, after a
    # reconnect, at present, which never decides a follow-up either way).
    mains = {q.seq: q for q in qs if q.role == "MAIN"}
    rest = [s for s in C.plan_from_json(qset.plan) if s.seq not in mains]
    return (qset, min(rest, key=lambda s: s.seq)) if rest else None


async def _answers(db, app_id: UUID) -> list[str]:
    rows = (await db.execute(select(InterviewAnswer.transcript).where(InterviewAnswer.application_id == app_id,
                                                                      InterviewAnswer.is_no_answer.is_(False))
                             .order_by(InterviewAnswer.confirmed_at.desc()).limit(ANSWERS_USED))).scalars().all()
    return [(t or "").strip()[:ANSWER_CHARS] for t in rows if (t or "").strip()]


async def _fill(db, c: C._Ctx, sl: C.Slot) -> None:
    one = [(sl.seq, sl.area.value)]
    if sl.text is not None:
        c.picked.append(C.custom_pick(c.settings, sl))
        return
    if sl.area in (Area.PERSONALITY, Area.COLLABORATION):
        resume, docs, about = await C.adapt_context(c)
        await C._fill_bank_only(db, c, sl.area.value, one, about)
        await C._adapt_bank_picks(c, resume, docs, only={sl.seq})
        return
    if sl.area == Area.PROJECT:
        await C._fill_resume(db, c, one)
    else:
        await C._fill_generated_kind(db, c, "COMPANY" if sl.area == Area.MOTIVATION else "JOB", one, c.embed_model)
    if any(p.seq == sl.seq and p.source in ("BANK", "BANK_FALLBACK") for p in c.picked):
        resume, docs, _ = await C.adapt_context(c)
        await C._adapt_bank_picks(c, resume, docs, only={sl.seq})


async def _fallback(db, c: C._Ctx, sl: C.Slot) -> None:
    """Generation failed or took too long: the least used bank question of the slot's kind (any job family last)."""
    kind = FALLBACK_KIND[sl.area]
    c.picked = [p for p in c.picked if p.seq != sl.seq]
    rows = await C._bank_candidates(db, kind, c.company.id, c.posting.job_family)
    if not C._take_from_bank(c, rows, 1, [(sl.seq, sl.area.value)], "BANK_FALLBACK"):
        rows = await C._bank_candidates(db, kind, c.company.id, None, any_family=True)
        if not C._take_from_bank(c, rows, 1, [(sl.seq, sl.area.value)], "BANK_FALLBACK"):
            raise C.ComposeError(f"live: no question for seq {sl.seq} ({sl.area.value})")


async def ensure_next(app_id: UUID) -> bool:
    """Compose and write the next main question if the interview has reached it. True when one was written now."""
    async with session_scope() as db:
        if await _next_slot(db, app_id) is None:
            return False
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"live:{app_id}"})
        found = await _next_slot(db, app_id)  # again inside the lock: another request may have written it meanwhile
        if found is None:
            return False
        qset, sl = found
        app = await db.get(Application, app_id)
        settings = await db.get(PostingEvalSettings, qset.settings_id)
        posting = await db.get(JobPosting, app.posting_id)
        company = await db.get(Company, app.company_id)
        c = C._Ctx(app, posting, company, settings, get_ai_client(), get_settings().embed_model, db)
        asked = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.question_set_id == qset.id))).scalars().all()
        c.picked = [C.Picked(q.seq if q.role == "MAIN" else 0, q.slot_type, q.text, q.source, q.bank_question_id) for q in asked]
        c.answers = await _answers(db, app_id)
        before = len(c.picked)
        savepoint = await db.begin_nested()  # what a failed generation wrote (bank rows) goes; the lock stays
        try:
            await asyncio.wait_for(_fill(db, c, sl), get_settings().live_compose_timeout_seconds)
            await savepoint.commit()
            ok = any(p.seq == sl.seq for p in c.picked[before:])
        except Exception as e:  # noqa: BLE001 - a model down, slow or invalid only means the question bank
            log_event(log, "live_compose_fallback", logging.WARNING, application_id=str(app_id), seq=sl.seq, error_code=type(e).__name__)
            await savepoint.rollback()
            c.picked = c.picked[:before]
            ok = False
        if not ok:
            await _fallback(db, c, sl)
        pick = next(p for p in c.picked[before:] if p.seq == sl.seq)
        db.add(C.question_row(qset, app, settings, sl, pick))
        await db.flush()
        log_event(log, "live_question", application_id=str(app_id), seq=sl.seq, area=sl.area.value, source=pick.source,
                  answers_used=len(c.answers))
        return True
