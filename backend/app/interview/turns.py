"""Transport-agnostic turn engine (TRD 06 §5, ADR-0009). The WebRTC voice runtime and the text-answer mode
both drive interviews through these functions, so ordering, timing and limits are enforced in one place.

Turn = one presentation of one question. Re-presentation after a disconnect creates a new turn (cap: 3
presentations per question, T11). Answers are stored only when a turn is finalised (OQ-21)."""
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.lifecycle import register_evaluation
from app.core.constants import FOLLOWUP_ANSWER_LIMIT_SEC, FOLLOWUP_DECISION_TIMEOUT_SEC, MAX_FOLLOWUPS_TOTAL
from app.core.db import session_scope
from app.core.logging import log_event
from app.core.timeutil import utcnow
from app.interview import slots
from app.jobs.queue import enqueue
from app.models import (
    Application, CodingAttempt, FollowupDecision, Interview, InterviewAnswer, InterviewQuestion, InterviewTurn, JobPosting,
    User,
)

log = logging.getLogger("app.interview")
MAX_PRESENTATIONS = 3


class TextModeRequired(Exception):
    """The current question already used its re-ask allowance (T11)."""


@dataclass
class TurnView:
    turn_id: UUID
    question_id: UUID
    main_no: int
    is_followup: bool
    text: str
    limit_sec: int
    presentation_no: int
    input_mode: str


async def next_question(db: AsyncSession, app_id: UUID) -> InterviewQuestion | None:
    """First main (in seq order) without an answer, or its follow-up when one was asked but not answered."""
    iv = await db.get(Interview, app_id)
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.question_set_id == iv.question_set_id)
                           .order_by(InterviewQuestion.seq))).scalars().all()
    answered = set((await db.execute(select(InterviewAnswer.question_id).where(
        InterviewAnswer.application_id == app_id))).scalars())
    mains = sorted((q for q in qs if q.role == "MAIN"), key=lambda q: q.seq)
    followups = {q.parent_id: q for q in qs if q.role == "FOLLOWUP"}
    for m in mains:
        if m.id not in answered:
            return m
        fu = followups.get(m.id)
        if fu is not None and fu.id not in answered:
            return fu
    return None


async def present_next(db: AsyncSession, app_id: UUID, input_mode: str = "VOICE") -> TurnView | None:
    """Create the next PRESENTING turn (or ANSWERING immediately in text mode). None = all questions answered."""
    q = await next_question(db, app_id)
    if q is None:
        return None
    n = await db.scalar(select(func.count()).select_from(InterviewTurn).where(InterviewTurn.question_id == q.id))
    if n >= MAX_PRESENTATIONS:
        raise TextModeRequired(str(q.id))
    await db.execute(update(InterviewTurn).where(
        InterviewTurn.application_id == app_id, InterviewTurn.state.in_(["PRESENTING", "ANSWERING", "FINALIZING"]))
        .values(state="ABANDONED", end_reason="DISCONNECT", closed_at=utcnow()))
    turn = InterviewTurn(application_id=app_id, question_id=q.id, presentation_no=n + 1, input_mode=input_mode,
                         state="PRESENTING")
    db.add(turn)
    await db.flush()
    await db.execute(update(Interview).where(Interview.application_id == app_id).values(
        current_question_id=q.id, current_phase="PRESENTING", presentation_count=n + 1,
        answer_opened_at=None, answer_deadline_at=None))
    view = TurnView(turn.id, q.id, q.seq, q.role == "FOLLOWUP", q.text, q.answer_limit_sec, n + 1, input_mode)
    if input_mode == "TEXT":
        await open_answer(db, turn.id)
    return view


async def open_answer(db: AsyncSession, turn_id: UUID) -> tuple:
    """Question audio finished (+ playout guard, applied by the caller): open the mic and start the clock."""
    turn = await db.get(InterviewTurn, turn_id, with_for_update=True)
    if turn.state != "PRESENTING":
        return turn.opened_at, turn.deadline_at
    q = await db.get(InterviewQuestion, turn.question_id)
    now = utcnow()
    turn.state, turn.tts_ended_at, turn.opened_at = "ANSWERING", turn.tts_ended_at or now, now
    turn.deadline_at = now + timedelta(seconds=q.answer_limit_sec)
    await db.execute(update(Interview).where(Interview.application_id == turn.application_id).values(
        current_phase="ANSWERING", answer_opened_at=turn.opened_at, answer_deadline_at=turn.deadline_at))
    return turn.opened_at, turn.deadline_at


async def finalize(db: AsyncSession, turn_id: UUID, *, end_reason: str, transcript: str, stt_failed: bool = False,
                   audio_key: str | None = None, audio_ms: int | None = None, stt_model: str | None = None,
                   ) -> InterviewAnswer | None:
    """Confirm the answer exactly once (button/timeout race safe). Returns None if the turn was already closed."""
    from app.ai.textrules import make_eval_text

    turn = await db.get(InterviewTurn, turn_id, with_for_update=True)
    if turn is None or turn.state not in ("ANSWERING", "FINALIZING"):
        return None
    app = await db.get(Application, turn.application_id)
    q = await db.get(InterviewQuestion, turn.question_id)
    transcript = (transcript or "").strip()
    no_answer = not transcript
    names = [n for n in [(await db.get(User, app.candidate_id)).name, app.resume_snapshot.get("name")] if n]
    now = utcnow()
    res = await db.execute(insert(InterviewAnswer).values(
        turn_id=turn.id, question_id=q.id, application_id=app.id, company_id=app.company_id,
        input_mode=turn.input_mode, end_reason=end_reason, is_no_answer=no_answer, stt_failed=stt_failed and no_answer,
        transcript=None if no_answer else transcript,
        eval_text=None if no_answer else make_eval_text(transcript, names),
        audio_key=audio_key if turn.input_mode == "VOICE" else None, audio_duration_ms=audio_ms, stt_model=stt_model,
        opened_at=turn.opened_at or now, confirmed_at=now, reask_count=turn.presentation_no - 1,
    ).on_conflict_do_nothing(index_elements=["question_id"]).returning(InterviewAnswer.id))
    answer_id = res.scalar_one_or_none()
    turn.state, turn.closed_at, turn.end_reason = "CLOSED", now, end_reason
    if answer_id is None:
        return None
    if q.role == "MAIN":
        app.answered_main_count += 1
    await db.execute(update(Interview).where(Interview.application_id == app.id).values(current_phase="DECIDING"))
    await enqueue(db, "pre_evaluate", {"answer_id": str(answer_id)}, application_id=app.id, dedupe_key=f"pre:{answer_id}")
    await db.flush()
    return await db.get(InterviewAnswer, answer_id)


async def decide_followup(app_id: UUID, answer_id: UUID) -> InterviewQuestion | None:
    """6.2: at most 1 per main and 4 in total, grounded in the answer/resume, decided within 5 s or skipped."""
    from app.ai.textrules import is_short_or_no_content, looks_like_question, shares_content

    async with session_scope() as db:
        ans = await db.get(InterviewAnswer, answer_id)
        q = await db.get(InterviewQuestion, ans.question_id)
        iv = await db.get(Interview, app_id)
        if q.role != "MAIN":
            return None
        has_fu = await db.scalar(select(func.count()).select_from(InterviewQuestion).where(InterviewQuestion.parent_id == q.id))
        if has_fu or iv.followups_used >= MAX_FOLLOWUPS_TOTAL:
            cause, reason = "LIMIT", "LIMIT"
        elif ans.is_no_answer or is_short_or_no_content(ans.eval_text or ""):
            cause, reason = "RULE", "NO_CONTENT"  # INT-10: move on, never repeat the question
        else:
            cause, reason = None, None
        evidence = [g["text"] for g in (q.grounding or {}).get("evidence", []) if g.get("text")]
        payload = {"question": q.text, "question_type": q.slot_type, "answer": ans.eval_text or "",
                   "resume_evidence": evidence, "followups_left": MAX_FOLLOWUPS_TOTAL - iv.followups_used}
        deadline_ms = int((ans.confirmed_at.timestamp() + FOLLOWUP_DECISION_TIMEOUT_SEC) * 1000)
    t0 = time.monotonic()
    out = None
    if cause is None:
        from app.ai.client import AIUnavailable, get_ai_client

        try:
            r = await get_ai_client().llm("followup_decide", payload, lane="RT", deadline_ms=deadline_ms)
            if r.status != "OK":
                cause, reason = ("TIMEOUT" if r.status in ("TIMEOUT", "DEADLINE_EXCEEDED") else "INVALID"), r.status
            else:
                out = r.output
        except AIUnavailable:
            cause, reason = "INFRA", "LLM_UNAVAILABLE"
        if out is not None:
            if out.get("decision") != "ASK":
                cause, reason = "LLM", out.get("reason", "NONE")
            else:
                fq = (out.get("question") or "").strip()
                # The task returns the anchor as the verified source span {start, end, text} (ADR-0008).
                anchor = (out.get("anchor") or {}).get("text") or ""
                if not fq or not looks_like_question(fq) or not (shares_content(fq, anchor) or shares_content(fq, q.text)):
                    cause, reason = "INVALID", "NEW_TOPIC_OR_FORMAT"
                else:
                    reason = out.get("reason", "VAGUE")
    latency = int((time.monotonic() - t0) * 1000)
    async with session_scope() as db:
        fu = None
        if cause is None:
            ok = (await db.execute(update(Interview).where(Interview.application_id == app_id,
                                                           Interview.followups_used < MAX_FOLLOWUPS_TOTAL)
                                   .values(followups_used=Interview.followups_used + 1))).rowcount
            if ok:
                fu = InterviewQuestion(question_set_id=q.question_set_id, application_id=app_id, company_id=q.company_id,
                                       seq=q.seq, role="FOLLOWUP", parent_id=q.id, slot_type=q.slot_type, source="AI",
                                       text=out["question"].strip(), grounding={"anchor": out.get("anchor")},
                                       answer_limit_sec=FOLLOWUP_ANSWER_LIMIT_SEC, prompt_version="followup_decide")
                db.add(fu)
            else:
                cause, reason = "LIMIT", "LIMIT"
        await db.execute(insert(FollowupDecision).values(
            main_question_id=q.id, application_id=app_id, decision="ASK" if fu else "SKIP", reason=str(reason)[:40],
            skip_cause=None if fu else cause, latency_ms=latency).on_conflict_do_nothing())
    log_event(log, "followup_decided", application_id=str(app_id), status="ASK" if fu else "SKIP",
              reason=cause, latency_ms=latency)
    return fu


async def finish_interview(db: AsyncSession, app_id: UUID) -> str:
    """All questions done: go to the coding test (COD-01) or submit and register evaluation atomically (7.2)."""
    app = await db.get(Application, app_id, with_for_update=True)
    if app.attempt_status != "INTERVIEWING":
        return app.attempt_status
    posting = await db.get(JobPosting, app.posting_id)
    now = utcnow()
    await db.execute(update(Interview).where(Interview.application_id == app_id).values(current_phase="DONE", ended_at=now))
    await slots.release(db, app_id)
    if posting.coding_enabled:
        app.attempt_status = "CODING"
        db.add(CodingAttempt(application_id=app.id, company_id=app.company_id, problem_id=posting.coding_problem_id,
                             language="python", started_at=now,
                             deadline_at=now + timedelta(minutes=posting.coding_time_limit_min)))
        await db.execute(update(Interview).where(Interview.application_id == app_id).values(last_heartbeat_at=now))
    else:
        # One UPDATE with eval_status: the table CHECK forbids a SUBMITTED row without an evaluation status (7.2).
        app.attempt_status, app.submitted_at = "SUBMITTED", now
        await register_evaluation(db, app)
    await db.flush()
    return app.attempt_status


async def interrupt(db: AsyncSession, app_id: UUID) -> None:
    """Connection lost: the live turn is abandoned (partial answer discarded, OQ-21); the slot keeps its lease
    until it expires (grace, T10) so a quick reconnect gets it back."""
    app = await db.get(Application, app_id, with_for_update=True)
    if app.attempt_status not in ("INTERVIEWING", "CODING"):
        return
    await db.execute(text("UPDATE interview_turns SET state='ABANDONED', end_reason='DISCONNECT', closed_at=now() "
                           "WHERE application_id=:a AND state IN ('PRESENTING','ANSWERING','FINALIZING')"), {"a": app_id})
    app.interrupted_from, app.attempt_status = app.attempt_status, "INTERRUPTED"
    await db.execute(update(Interview).where(Interview.application_id == app_id).values(
        interruption_count=Interview.interruption_count + 1, current_phase=None))
