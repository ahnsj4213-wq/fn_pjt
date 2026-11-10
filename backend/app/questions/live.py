"""Live composition (ADR-0022): each main question after the first is composed when the candidate reaches it.

Before the interview the composition graph writes only the first main question and keeps the plan (area, custom text, mode per
seq) on the question set. When every question so far is answered (and the follow-up decision of the last one is made), the
interview graph calls `ensure_next`, which composes the next slot of the plan with the same sources as before — the resume (T3),
the company documents (T2 with the corrective RAG loop), code-made common questions — plus what the candidate has just said: the
latest answers steer the document search and which resume item is asked about.

Rules kept from up-front composition: the counts per area come from the plan (QST-02's 10 questions by type), a question once
written is never changed (QST-06), a failed or slow generation falls back to a code-made question (QST-07), and every question is
checked against the ones already asked.

Composing ahead (2026-11-10): composition on a 6 GB GPU takes 10~40 s, so `prefetch` starts the next main question as soon as the
current one is shown, while the candidate is still answering it. That question is steered by the answers before the current one;
the follow-up of the current answer (T4) still reacts to it at once. Its voice is queued for synthesis as soon as it is written.

Transactions and concurrency (2026-11-10): no database transaction is open while a model runs.
  1. read     a short transaction: the next slot, the context, the questions asked so far, the latest answers
  2. compose  no transaction (searches read one statement at a time, questions/dbio.ShortReads), under the time limit;
              a failure or timeout means the code-made fallback
  3. write    a short transaction: advisory lock live:<application>, check again that the slot is still empty, the set is still
              the interview's and the interview is running, then the row and its tts_pregen job
Within one process the calls for one application run one at a time (an asyncio lock per application): the prefetch and the
ensure_next after the answer never compose the same slot twice on the GPU; the second finds the row written and returns. Across
processes the write step's lock and check keep one row per slot (the losing composition is dropped and logged).
"""
import asyncio
import logging
import time
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
from app.jobs.queue import enqueue
from app.questions import compose as C
from app.questions.dbio import ShortReads
from app.resumes.schemas import to_ai_resume

log = logging.getLogger("app.questions")
ANSWERS_USED = 2
ANSWER_CHARS = 400
# The code-made question a slot falls back to when its generation fails or takes too long (경험: compose.experience_fallback).
FALLBACK_KIND = {Area.PERSONALITY: "PERSONALITY", Area.COLLABORATION: "COLLABORATION",
                 Area.MOTIVATION: "COMPANY", Area.JOB_SKILL: "JOB"}


async def _next_slot(db, app_id: UUID, ahead: bool = False) -> tuple[QuestionSet, C.Slot] | None:
    """The plan's next slot when the interview is ready for it: every question so far answered, no follow-up pending.
    `ahead`: the newest main question may still be open (it is being asked); nothing older may be."""
    iv = await db.get(Interview, app_id)
    if iv is None or iv.question_set_id is None:
        return None
    qset = await db.get(QuestionSet, iv.question_set_id)
    if qset is None or not qset.plan:
        return None
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.question_set_id == qset.id))).scalars().all()
    answered = set((await db.execute(select(InterviewAnswer.question_id).where(InterviewAnswer.application_id == app_id))).scalars())
    newest = max((q.seq for q in qs if q.role == "MAIN"), default=0)
    if any(q.id not in answered and not (ahead and q.role == "MAIN" and q.seq == newest) for q in qs):
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
        await C._fill_common(db, c, sl.area.value, one)
        return
    if sl.area == Area.PROJECT:
        await C._fill_resume(db, c, one)
    else:
        await C._fill_generated_kind(db, c, "COMPANY" if sl.area == Area.MOTIVATION else "JOB", one, c.embed_model)
    if any(p.seq == sl.seq and p.source in ("BANK", "BANK_FALLBACK") for p in c.picked):
        resume, docs, _ = await C.adapt_context(c)
        await C._adapt_bank_picks(c, resume, docs, only={sl.seq})


async def _fallback(db, c: C._Ctx, sl: C.Slot) -> None:
    """Generation failed or took too long: a question made by code (no model, no database)."""
    c.picked = [p for p in c.picked if p.seq != sl.seq]
    one = [(sl.seq, sl.area.value)]
    if sl.area == Area.PROJECT:  # 경험 stays an experience question (OQ-37, revised 2026-11-10)
        C.experience_fallback(c, to_ai_resume(c.app.resume_snapshot), one)
    elif sl.area in (Area.PERSONALITY, Area.COLLABORATION):
        C.common_fallback(c, one, sl.area.value)
    else:  # made by code from the posting, never from the question bank
        C.posting_fallback(c, FALLBACK_KIND[sl.area], one)


# ── one composition at a time per application in this process ─────────────────
_locks: dict[UUID, asyncio.Lock] = {}
_users: dict[UUID, int] = {}


async def _serialized(app_id: UUID, fn):
    lock = _locks.setdefault(app_id, asyncio.Lock())
    _users[app_id] = _users.get(app_id, 0) + 1
    try:
        async with lock:
            return await fn()
    finally:
        _users[app_id] -= 1
        if not _users[app_id]:  # nobody waits: forget the lock (one entry per running interview at most)
            _users.pop(app_id, None)
            _locks.pop(app_id, None)


async def _read(app_id: UUID, ahead: bool) -> tuple[C._Ctx, QuestionSet, C.Slot] | None:
    async with session_scope() as db:
        found = await _next_slot(db, app_id, ahead)
        if found is None:
            return None
        qset, sl = found
        app = await db.get(Application, app_id)
        settings = await db.get(PostingEvalSettings, qset.settings_id)
        posting = await db.get(JobPosting, app.posting_id)
        company = await db.get(Company, app.company_id)
        asked = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.question_set_id == qset.id))).scalars().all()
        answers = await _answers(db, app_id)
    c = C._Ctx(app, posting, company, settings, get_ai_client(), get_settings().embed_model, ShortReads())
    # with their grounding: the resume parts and documents already asked about are not asked about again
    c.picked = [C.Picked(q.seq if q.role == "MAIN" else 0, q.slot_type, q.text, q.source, q.bank_question_id, q.grounding) for q in asked]
    c.answers = answers
    return c, qset, sl


async def _write(app_id: UUID, qset_id: UUID, sl: C.Slot, pick: C.Picked) -> bool:
    async with session_scope() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"live:{app_id}"})
        app = await db.get(Application, app_id)
        iv = await db.get(Interview, app_id)
        qset = await db.get(QuestionSet, qset_id)
        if app is None or app.attempt_status != "INTERVIEWING" or iv is None or iv.question_set_id != qset_id or qset is None:
            return False  # the interview ended or moved to another set meanwhile
        taken = (await db.execute(select(InterviewQuestion.id).where(InterviewQuestion.question_set_id == qset_id,
                                                                     InterviewQuestion.role == "MAIN", InterviewQuestion.seq == sl.seq))).first()
        if taken is not None:
            return False  # another process wrote this slot first
        settings = await db.get(PostingEvalSettings, qset.settings_id)
        db.add(C.question_row(qset, app, settings, sl, pick))
        await db.flush()
        if sl.mode != "CODE":  # its voice is made now, not when it is shown
            await enqueue(db, "tts_pregen", {"question_set_id": str(qset_id)}, application_id=app_id, dedupe_key=f"tts:{qset_id}:{sl.seq}")
        return True


async def _ensure_next(app_id: UUID, ahead: bool) -> bool:
    t0 = time.monotonic()
    found = await _read(app_id, ahead)
    if found is None:
        return False
    c, qset, sl = found
    before = len(c.picked)
    try:
        await asyncio.wait_for(_fill(c.db, c, sl), get_settings().live_compose_timeout_seconds)
        ok = any(p.seq == sl.seq for p in c.picked[before:])
    except Exception as e:  # noqa: BLE001 - a model down, slow or invalid only means the code-made question
        log_event(log, "live_compose_fallback", logging.WARNING, application_id=str(app_id), seq=sl.seq, error_code=type(e).__name__)
        c.picked = c.picked[:before]
        ok = False
    if not ok:
        await _fallback(c.db, c, sl)
    pick = next(p for p in c.picked[before:] if p.seq == sl.seq)
    for code, n in sorted(c.rejects.items()):  # counts by failure code only
        log_event(log, "question_reject", application_id=str(app_id), stage="live", reason=code, count=n)
    written = await _write(app_id, qset.id, sl, pick)
    log_event(log, "live_question" if written else "live_question_dropped", application_id=str(app_id), seq=sl.seq,
              area=sl.area.value, source=pick.source, count=len(c.answers), duration_ms=int((time.monotonic() - t0) * 1000))
    return written


async def ensure_next(app_id: UUID, ahead: bool = False) -> bool:
    """Compose and write the next main question if the interview has reached it (`ahead`: while the current one is being
    answered). True when one was written now."""
    return await _serialized(app_id, lambda: _ensure_next(app_id, ahead))


_running: set[asyncio.Task] = set()


def prefetch(app_id: UUID) -> None:
    """Start composing the next main question in the background (the candidate is answering the current one)."""
    async def run() -> None:
        try:
            await ensure_next(app_id, ahead=True)
        except Exception as e:  # noqa: BLE001 - ensure_next after the answer composes it then (or takes the fallback)
            log_event(log, "live_prefetch_failed", logging.WARNING, application_id=str(app_id), error_code=type(e).__name__)
    task = asyncio.get_running_loop().create_task(run())
    _running.add(task)  # a task nobody holds may be collected before it ends
    task.add_done_callback(_running.discard)
