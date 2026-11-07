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

from app.applications.lifecycle import register_evaluation, submit_live_code
from app.core.constants import FOLLOWUP_ANSWER_LIMIT_SEC, FOLLOWUP_DECISION_TIMEOUT_SEC, FOLLOWUP_THINK_SEC
from app.core.db import session_scope
from app.core.logging import log_event
from app.core.timeutil import utcnow
from app.interview import slots
from app.jobs.queue import enqueue
from app.notify import inbox
from app.coding.selection import base_problem_for, test_minutes
from app.coding.variants import ensure_variant, get_variant
from app.models import (
    Application, CodingAttempt, CodingProblem, FollowupDecision, Interview, InterviewAnswer, InterviewQuestion, InterviewTurn, JobPosting,
    QuestionSet, User,
)

log = logging.getLogger("app.interview")
MAX_PRESENTATIONS = 3
FOLLOWUP_PREVIOUS_ANSWERS = 2
FOLLOWUP_PREVIOUS_CHARS = 200
FOLLOWUP_KNOWLEDGE_COUNT = 2


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
    area: str = "PERSONALITY"
    answer_mode: str = "SPEECH"
    think_sec: int = 0
    main_count: int = 10


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
    if n >= MAX_PRESENTATIONS and input_mode != "TEXT":
        raise TextModeRequired(str(q.id))
    await db.execute(update(InterviewTurn).where(
        InterviewTurn.application_id == app_id, InterviewTurn.state.in_(["PRESENTING", "ANSWERING", "FINALIZING"]))
        .values(state="ABANDONED", end_reason="DISCONNECT", closed_at=utcnow()))
    if n >= MAX_PRESENTATIONS:
        # The cap limits voice attempts and is what sends a question to text mode, so text mode must still be able to ask it:
        # the last attempt's row is used again (at most three rows per question, CHECK presentation_no).
        turn = (await db.execute(select(InterviewTurn).where(InterviewTurn.question_id == q.id)
                                 .order_by(InterviewTurn.presentation_no.desc()).limit(1))).scalar_one()
        turn.state, turn.input_mode, turn.end_reason, turn.closed_at = "PRESENTING", input_mode, None, None
        turn.opened_at, turn.deadline_at = None, None
    else:
        turn = InterviewTurn(application_id=app_id, question_id=q.id, presentation_no=n + 1, input_mode=input_mode,
                             state="PRESENTING")
        db.add(turn)
    await db.flush()
    await db.execute(update(Interview).where(Interview.application_id == app_id).values(
        current_question_id=q.id, current_phase="PRESENTING", presentation_count=turn.presentation_no,
        answer_opened_at=None, answer_deadline_at=None))
    qset = await db.get(QuestionSet, q.question_set_id)
    view = TurnView(turn.id, q.id, q.seq, q.role == "FOLLOWUP", q.text, q.answer_limit_sec, turn.presentation_no, input_mode,
                    q.slot_type, q.answer_mode, q.think_sec if input_mode == "VOICE" else 0,
                    qset.main_count)
    if input_mode == "TEXT":
        await open_answer(db, turn.id)
    return view


async def view_of(db: AsyncSession, turn_id: UUID) -> TurnView:
    """The TurnView of an existing turn (what `present_next` returned when it was made)."""
    turn = await db.get(InterviewTurn, turn_id)
    q = await db.get(InterviewQuestion, turn.question_id)
    qset = await db.get(QuestionSet, q.question_set_id)
    return TurnView(turn.id, q.id, q.seq, q.role == "FOLLOWUP", q.text, q.answer_limit_sec, turn.presentation_no,
                    turn.input_mode, q.slot_type, q.answer_mode, q.think_sec if turn.input_mode == "VOICE" else 0, qset.main_count)


async def open_think(db: AsyncSession, turn_id: UUID, think_sec: float):
    """Question audio finished: the think time runs (mic still closed). Returns its end, or None if the turn moved on."""
    turn = await db.get(InterviewTurn, turn_id, with_for_update=True)
    if turn.state != "PRESENTING":
        return None
    now = utcnow()
    turn.tts_ended_at = turn.tts_ended_at or now
    turn.think_until = now + timedelta(seconds=think_sec)
    await db.execute(update(Interview).where(Interview.application_id == turn.application_id).values(
        current_phase="THINKING"))
    return turn.think_until


async def mark_replayed(db: AsyncSession, turn_id: UUID) -> bool:
    """질문 다시 듣기: once per question (REPLAY_MAX), only while thinking."""
    res = await db.execute(update(InterviewTurn).where(
        InterviewTurn.id == turn_id, InterviewTurn.state == "PRESENTING", InterviewTurn.think_until.is_not(None),
        InterviewTurn.replayed_at.is_(None)).values(replayed_at=utcnow()))
    return res.rowcount == 1


async def interject(db: AsyncSession, turn_id: UUID, deadline_at) -> bool:
    """The interviewer cut in once: the answer deadline moves to `deadline_at` (never later than before + speech)."""
    turn = await db.get(InterviewTurn, turn_id, with_for_update=True)
    if turn.state != "ANSWERING" or turn.interjected_at is not None:
        return False
    turn.interjected_at, turn.deadline_at = utcnow(), deadline_at
    await db.execute(update(Interview).where(Interview.application_id == turn.application_id).values(
        answer_deadline_at=deadline_at))
    return True


async def save_code(db: AsyncSession, turn_id: UUID, code: str, language: str, grace_sec: float) -> bool:
    """Live coding [코드 제출]: kept on the turn, any number of times inside the answer window; finalize copies it."""
    turn = await db.get(InterviewTurn, turn_id, with_for_update=True)
    q = await db.get(InterviewQuestion, turn.question_id) if turn else None
    if turn is None or q.answer_mode != "CODE" or turn.state not in ("ANSWERING", "FINALIZING"):
        return False
    if turn.deadline_at and utcnow() > turn.deadline_at + timedelta(seconds=grace_sec):
        return False
    turn.code_text, turn.code_language = code, language
    return True



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
    code = (turn.code_text or "").strip()
    no_answer = not transcript and not code and not turn.whiteboard_key
    names = [n for n in [(await db.get(User, app.candidate_id)).name, app.resume_snapshot.get("name")] if n]
    eval_text = make_eval_text(transcript, names) if transcript else ""
    if code:  # the code is part of the answer evaluation and of the quote check (live coding, SC-A06)
        eval_text = f"{eval_text}\n[제출 코드 · {turn.code_language}]\n{code}".strip()
    now = utcnow()
    res = await db.execute(insert(InterviewAnswer).values(
        turn_id=turn.id, question_id=q.id, application_id=app.id, company_id=app.company_id,
        input_mode=turn.input_mode, end_reason=end_reason, is_no_answer=no_answer, stt_failed=stt_failed and no_answer,
        transcript=transcript or None,
        eval_text=eval_text or None,
        code_text=code or None, code_language=turn.code_language if code else None,
        whiteboard_key=turn.whiteboard_key, interjected=turn.interjected_at is not None,
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


async def _followup_context(db: AsyncSession, app: Application, ans: InterviewAnswer) -> dict:
    """What else the interviewer knows at this point (ADR-0020): the last answers, the follow-ups already asked, and the
    company documents closest to this answer. Small on purpose: this call runs while the candidate waits (ADR-0019)."""
    from app.ai.client import get_ai_client
    from app.core.config import get_settings
    from app.questions import knowledge

    previous = (await db.execute(select(InterviewAnswer.id, InterviewAnswer.eval_text).where(
        InterviewAnswer.application_id == app.id, InterviewAnswer.id != ans.id, InterviewAnswer.eval_text.is_not(None))
        .order_by(InterviewAnswer.confirmed_at.desc()).limit(FOLLOWUP_PREVIOUS_ANSWERS))).all()
    asked = (await db.execute(select(InterviewQuestion.text).where(
        InterviewQuestion.application_id == app.id, InterviewQuestion.role == "FOLLOWUP"))).scalars().all()
    chunks = await knowledge.search(db, get_ai_client(), get_settings().embed_model, company_id=app.company_id,
                                    posting_id=app.posting_id, query=ans.eval_text or "", k=FOLLOWUP_KNOWLEDGE_COUNT, kind="FOLLOWUP")
    return {"previous_answers": [{"id": str(i), "text": t[:FOLLOWUP_PREVIOUS_CHARS]} for i, t in previous], "asked": list(asked),
            "company_context": [c.as_input() for c in chunks]}


async def decide_followup(app_id: UUID, answer_id: UUID) -> InterviewQuestion | None:
    """6.2: at most 1 per main and the set's cap in total (posting setting, 0~4), grounded in the answer/resume,
    decided within the time limit (a guard against a stuck model, ADR-0019) or skipped."""
    from app.ai.textrules import is_short_or_no_content, looks_like_question, shares_content

    async with session_scope() as db:
        ans = await db.get(InterviewAnswer, answer_id)
        q = await db.get(InterviewQuestion, ans.question_id)
        iv = await db.get(Interview, app_id)
        if q.role != "MAIN" or q.answer_mode == "CODE":  # a coding answer is the code and its comments: nothing to ask aloud
            return None
        cap = (await db.get(QuestionSet, q.question_set_id)).followup_max
        has_fu = await db.scalar(select(func.count()).select_from(InterviewQuestion).where(InterviewQuestion.parent_id == q.id))
        if has_fu or iv.followups_used >= cap:
            cause, reason = "LIMIT", "LIMIT"
        elif ans.is_no_answer or is_short_or_no_content(ans.eval_text or ""):
            cause, reason = "RULE", "NO_CONTENT"  # INT-10: move on, never repeat the question
        else:
            cause, reason = None, None
        evidence = [g["text"] for g in (q.grounding or {}).get("evidence", []) if g.get("text")]
        payload = {"question": q.text, "question_type": q.slot_type, "answer": ans.eval_text or "",
                   "resume_evidence": evidence, "followups_left": cap - iv.followups_used}
        if cause is None:  # the follow-up goes where the posting and the resume meet
            from app.ai.tasks.questions import flatten_resume
            from app.ai.textrules import shared_terms
            from app.questions.compose import job_brief
            from app.resumes.schemas import to_ai_resume

            app = await db.get(Application, app_id)
            brief = job_brief(await db.get(JobPosting, app.posting_id))
            payload |= {"job_brief": brief, "application_id": str(app_id), "answer_id": str(answer_id),
                        "shared_terms": shared_terms(brief, flatten_resume(to_ai_resume(app.resume_snapshot))[0]),
                        **await _followup_context(db, app, ans)}
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
                                                           Interview.followups_used < cap)
                                   .values(followups_used=Interview.followups_used + 1))).rowcount
            if ok:
                fu = InterviewQuestion(question_set_id=q.question_set_id, application_id=app_id, company_id=q.company_id,
                                       seq=q.seq, role="FOLLOWUP", parent_id=q.id, slot_type=q.slot_type, source="AI",
                                       text=out["question"].strip(),
                                       grounding={"anchor": out.get("anchor"), "sources": out.get("sources") or [],
                                                  "retrieved": [d["id"] for d in payload.get("company_context") or []]},
                                       answer_limit_sec=FOLLOWUP_ANSWER_LIMIT_SEC, prompt_version="followup_decide",
                                       think_sec=FOLLOWUP_THINK_SEC if q.think_sec else 0)
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
    live_coding = await db.scalar(select(func.count()).select_from(InterviewQuestion).where(
        InterviewQuestion.application_id == app_id, InterviewQuestion.answer_mode == "CODE"))
    if posting.coding_enabled and not live_coding:  # a live-coding question replaces the separate test (SC-A06)
        app.attempt_status = "CODING"
        variant = await get_variant(db, app.id) or await ensure_variant(db, app.id, await base_problem_for(db, app, posting))  # own copy, hidden cases generated for it
        db.add(CodingAttempt(application_id=app.id, company_id=app.company_id, problem_id=variant.id,
                             language="python", started_at=now, draft_code=variant.starter_code or "",
                             deadline_at=now + timedelta(minutes=test_minutes(variant.level, posting.coding_time_limit_min))))
        await db.execute(update(Interview).where(Interview.application_id == app_id).values(last_heartbeat_at=now))
    else:
        # One UPDATE with eval_status: the table CHECK forbids a SUBMITTED row without an evaluation status (7.2).
        grade_job = await submit_live_code(db, app) if posting.coding_enabled else None
        app.attempt_status, app.submitted_at = "SUBMITTED", now
        await register_evaluation(db, app, depends_on=grade_job)
        await inbox.interview_finished(db, app)
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
    await inbox.interview_stopped(db, app)
    await db.execute(update(Interview).where(Interview.application_id == app_id).values(
        interruption_count=Interview.interruption_count + 1, current_phase=None))
