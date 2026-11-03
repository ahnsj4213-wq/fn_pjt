"""Candidate interview control (TRD 04 §5.1, TRD 06 §7). Commands come over HTTPS with CSRF; the voice runtime
pushes events over the data channel. All state lives in the DB so refresh/reconnect resume works (INT-11)."""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.lifecycle import touch
from app.auth.deps import CandidateCtx, require_candidate
from app.auth.scope import get_for_owner
from app.core.constants import (
    CAPACITY_FULL_MESSAGE, CODE_ANSWER_LIMIT_SEC, FOLLOWUP_ANSWER_LIMIT_SEC, FOLLOWUP_THINK_SEC, GAZE_SUSPECT_COUNT,
    WARNING_REMAINING_SEC,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, conflict
from app.core.timeutil import utcnow
from app.interview import graph as igraph
from app.interview import infra, slots, turns
from app.jobs.queue import enqueue
from app.core.storage import get_storage
from app.models import (
    Application, CodingAttempt, Company, Interview, InterviewAnswer, InterviewQuestion, InterviewTurn, JobPosting,
    PostingEvalSettings, ProctorEvent, QuestionSet,
)
from app.questions.compose import plan_of
from app.postings.service import is_open

router = APIRouter(prefix="/api/candidate/applications/{aid}", tags=["candidate"])
PRECHECK_VALID = timedelta(minutes=30)


def invalid_state(app: Application):
    return conflict("INVALID_STATE", "지금 상태에서는 할 수 없는 요청입니다.", current=app.attempt_status)


async def _own(db: AsyncSession, ctx: CandidateCtx, aid: UUID, lock: bool = False) -> Application:
    return await get_for_owner(db, Application, aid, ctx.user_id, for_update=lock)


async def _posting_open(db: AsyncSession, app: Application) -> JobPosting:
    posting = await db.get(JobPosting, app.posting_id)
    if not is_open(posting):
        raise conflict("POSTING_CLOSED", "마감된 공고는 응시할 수 없습니다.")
    return posting


async def interview_state(db: AsyncSession, app: Application) -> dict:
    iv = await db.get(Interview, app.id)
    posting = await db.get(JobPosting, app.posting_id)
    turn_view = None
    if iv is not None and iv.current_question_id and app.attempt_status == "INTERVIEWING":
        turn = (await db.execute(select(InterviewTurn).where(
            InterviewTurn.application_id == app.id, InterviewTurn.state.in_(["PRESENTING", "ANSWERING", "FINALIZING"]))
        )).scalar_one_or_none()
        if turn is not None:
            q = await db.get(InterviewQuestion, turn.question_id)
            turn_view = {"turn_id": turn.id, "main_no": q.seq, "is_followup": q.role == "FOLLOWUP",
                         "presentation_no": turn.presentation_no, "phase": turn.state, "question_text": q.text,
                         "area": q.slot_type, "answer_mode": q.answer_mode,
                         "limit_sec": q.answer_limit_sec, "think_until": turn.think_until,
                         "opened_at": turn.opened_at, "deadline_at": turn.deadline_at}
    qset = await db.get(QuestionSet, iv.question_set_id) if iv is not None and iv.question_set_id else None
    live_coding = await db.scalar(select(func.count()).select_from(InterviewQuestion).where(
        InterviewQuestion.question_set_id == qset.id, InterviewQuestion.answer_mode == "CODE")) if qset else 0
    coding = await db.get(CodingAttempt, app.id)
    slot_until = await db.scalar(text("SELECT lease_expires_at FROM interview_slots WHERE application_id=:a"), {"a": app.id})
    return {"attempt_status": app.attempt_status, "interrupted_from": app.interrupted_from,
            "mode": "TEXT" if iv is not None and iv.text_mode_since else "VOICE", "server_now": utcnow(),
            "total_main": qset.main_count if qset else None, "answered_main": app.answered_main_count,
            "followups_used": iv.followups_used if iv else 0, "followup_max": qset.followup_max if qset else None,
            "gaze_warnings": iv.gaze_warning_count if iv else 0,
            "next_after_interview": "CODING" if posting.coding_enabled and not live_coding else "DONE", "warnings": [],
            "turn": turn_view, "slot_grace_until": slot_until if app.attempt_status == "INTERRUPTED" else None,
            "coding_deadline_at": coding.deadline_at if coding else None}


@router.get("/interview/guide")
async def guide(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    app = await _own(db, ctx, aid)
    posting = await db.get(JobPosting, app.posting_id)
    st = await db.get(PostingEvalSettings, posting.current_settings_id)
    return {"main_count": len(plan_of(st)), "main_answer_sec": st.answer_sec, "think_sec": st.think_sec,
            "followup_answer_sec": FOLLOWUP_ANSWER_LIMIT_SEC, "followup_think_sec": FOLLOWUP_THINK_SEC,
            "followup_max_total": st.followup_max, "code_answer_sec": CODE_ANSWER_LIMIT_SEC,
            "warning_sec": WARNING_REMAINING_SEC,
            "coding": {"enabled": posting.coding_enabled, "time_limit_min": posting.coding_time_limit_min,
                       "language": posting.coding_language}}


@router.get("/interview/summary")
async def summary(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    """SC-A07 면접 요약: what the candidate did in this attempt. No scores (the report is SC-A08)."""
    app = await _own(db, ctx, aid)
    posting = await db.get(JobPosting, app.posting_id)
    company = await db.get(Company, app.company_id)
    iv = await db.get(Interview, app.id)
    qset = await db.get(QuestionSet, iv.question_set_id) if iv is not None and iv.question_set_id else None
    interjections = await db.scalar(select(func.count()).select_from(InterviewAnswer).where(
        InterviewAnswer.application_id == app.id, InterviewAnswer.interjected.is_(True)))
    end = app.submitted_at or (iv.ended_at if iv else None)
    start = iv.started_at if iv and iv.started_at else app.interview_started_at
    return {"posting": {"title": posting.title, "company_name": company.name},
            "attempt_status": app.attempt_status, "eval_status": app.eval_status,
            "main_count": qset.main_count if qset else None, "answered_main": app.answered_main_count,
            "followups_used": iv.followups_used if iv else 0, "interjections": interjections or 0,
            "gaze_warnings": iv.gaze_warning_count if iv else 0,
            "gaze_suspected": bool(iv and iv.gaze_warning_count >= GAZE_SUSPECT_COUNT),
            "share_result": app.share_result,
            "duration_sec": int((end - start).total_seconds()) if start and end else None}


@router.post("/interview/prepare")
async def prepare(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    app = await _own(db, ctx, aid, lock=True)
    if app.attempt_status not in ("READY", "PREPARING"):
        raise invalid_state(app)
    posting = await _posting_open(db, app)
    if app.attempt_status == "READY":
        app.attempt_status = "PREPARING"
        if await db.get(Interview, app.id) is None:
            db.add(Interview(application_id=app.id, company_id=app.company_id))
        await db.flush()
        await enqueue(db, "compose_questions", {"application_id": str(app.id), "settings_id": str(posting.current_settings_id)},
                      application_id=app.id, dedupe_key=f"compose:{app.id}:{posting.current_settings_id}")
    await touch(db, app.id)
    ready = await db.scalar(select(QuestionSet.id).where(QuestionSet.application_id == app.id,
                                                         QuestionSet.status == "READY"))
    await db.commit()
    return {"attempt_status": "PREPARING", "questions_ready": ready is not None}


@router.post("/interview/leave-prepare")
async def leave_prepare(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    app = await _own(db, ctx, aid, lock=True)
    if app.attempt_status == "PREPARING":
        app.attempt_status = "READY"
        await db.commit()
    elif app.attempt_status != "READY":
        raise invalid_state(app)
    return {"attempt_status": "READY"}


class MediaCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mic_ok: bool
    camera_ok: bool
    speaker_ok: bool | None = None


@router.post("/precheck/media", status_code=204)
async def precheck_media(aid: UUID, body: MediaCheckIn, ctx: CandidateCtx = Depends(require_candidate),
                         db: AsyncSession = Depends(get_db)) -> None:
    app = await _own(db, ctx, aid)
    if app.attempt_status != "PREPARING":
        raise invalid_state(app)
    # INT-01: camera/mic results are client-reported (video never leaves the browser); WebRTC is server-verified.
    await db.execute(update(Interview).where(Interview.application_id == app.id).values(
        precheck_media_ok_at=utcnow() if body.mic_ok and body.camera_ok else None))
    await db.commit()


@router.post("/interview/start")
async def start(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    # Checks that call other services run outside the transaction.
    app = await _own(db, ctx, aid)
    if app.attempt_status == "INTERVIEWING":
        return await interview_state(db, app)
    posting = await db.get(JobPosting, app.posting_id)
    ai = await infra.ai_status()
    if not (ai["llm"] and ai["stt"]):
        raise AppError(503, "AI_UNAVAILABLE", "AI 서버를 사용할 수 없어 면접을 시작할 수 없습니다. 잠시 후 다시 시도해 주세요.")
    if posting.coding_enabled and not await infra.sandbox_ready():
        raise AppError(503, "CODE_EXECUTION_UNAVAILABLE", "코딩테스트 실행 환경을 점검 중이라 지금은 시작할 수 없습니다.")
    await db.rollback()

    app = await _own(db, ctx, aid, lock=True)
    posting = (await db.execute(select(JobPosting).where(JobPosting.id == app.posting_id).with_for_update())).scalar_one()
    if app.attempt_status != "PREPARING":
        raise invalid_state(app)
    if not is_open(posting):
        raise conflict("POSTING_CLOSED", "마감된 공고는 응시할 수 없습니다.")
    iv = await db.get(Interview, app.id, with_for_update=True)
    now = utcnow()
    # Screen spec SC-A04: camera, mic and speaker are checked in the browser; the voice connection itself is
    # verified when the interview session connects (the separate server utterance check is optional now).
    if iv.precheck_media_ok_at is None or now - iv.precheck_media_ok_at > PRECHECK_VALID:
        raise conflict("PRECHECK_REQUIRED", "카메라·마이크·스피커 점검을 먼저 마쳐 주십시오.")  # AC-08
    qset = (await db.execute(select(QuestionSet).where(QuestionSet.application_id == app.id,
                                                       QuestionSet.status.in_(["COMPOSING", "READY"])))).scalar_one_or_none()
    if qset is None or qset.status != "READY" or qset.settings_id != posting.current_settings_id:
        if qset is not None and qset.settings_id != posting.current_settings_id:
            qset.status = "SUPERSEDED"
            await enqueue(db, "compose_questions", {"application_id": str(app.id),
                                                    "settings_id": str(posting.current_settings_id)},
                          application_id=app.id, dedupe_key=f"compose:{app.id}:{posting.current_settings_id}")
            await db.commit()
        raise conflict("QUESTIONS_NOT_READY", "질문을 준비하고 있습니다.", retry_after_sec=3)
    if not await slots.acquire(db, app.id):
        await db.rollback()
        app = await _own(db, ctx, aid, lock=True)
        if app.attempt_status == "PREPARING":
            app.attempt_status = "READY"  # 7.1: stays "응시 전" and can start later from My Page
            await db.commit()
        raise conflict("CAPACITY_FULL", CAPACITY_FULL_MESSAGE)  # INT-13, AC-14
    if posting.settings_locked_at is None:
        posting.settings_locked_at = now  # JOB-06 / OQ-05: first interview locks weights and coding settings
    app.attempt_status, app.eval_settings_id, app.interview_started_at = "INTERVIEWING", posting.current_settings_id, now
    iv.question_set_id, iv.started_at, iv.last_heartbeat_at = qset.id, now, now
    await db.execute(text(
        "UPDATE question_bank SET usage_count = usage_count + 1, last_used_at = now() WHERE id IN "
        "(SELECT bank_question_id FROM interview_questions WHERE question_set_id = :q AND bank_question_id IS NOT NULL)"),
        {"q": qset.id})
    await db.commit()
    state = await interview_state(db, app)
    if not ai["tts"]:
        state["warnings"].append("TTS_UNAVAILABLE")  # OQ-08: text-only questions
    return state


@router.get("/interview/state")
async def get_state(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    return await interview_state(db, await _own(db, ctx, aid))


@router.post("/interview/resume")
async def resume(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    app = await _own(db, ctx, aid, lock=True)
    if app.attempt_status in ("INTERVIEWING", "CODING"):
        return await interview_state(db, app)
    if app.attempt_status != "INTERRUPTED":
        raise invalid_state(app)
    posting = await db.get(JobPosting, app.posting_id)
    if posting.status != "OPEN":  # OQ-27: after close, no resume
        raise conflict("POSTING_CLOSED", "마감된 공고는 응시를 이어갈 수 없습니다.")
    if app.interrupted_from == "INTERVIEWING" and not await slots.acquire(db, app.id):
        await db.rollback()
        raise conflict("CAPACITY_FULL", CAPACITY_FULL_MESSAGE)
    app.attempt_status, app.interrupted_from = app.interrupted_from, None
    await touch(db, app.id)
    await db.commit()
    return await interview_state(db, app)


@router.post("/interview/text-mode")
async def text_mode(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    """INT-18 (P1): the server decides (T12): re-ask cap reached, or no live voice connection for 20 s."""
    from app.voice.registry import last_seen_connected

    app = await _own(db, ctx, aid, lock=True)
    if app.attempt_status not in ("INTERVIEWING", "INTERRUPTED") or (
            app.attempt_status == "INTERRUPTED" and app.interrupted_from != "INTERVIEWING"):
        raise invalid_state(app)
    iv = await db.get(Interview, app.id, with_for_update=True)
    q = await turns.next_question(db, app.id)
    presentations = 0
    if q is not None:
        presentations = await db.scalar(select(text("count(*)")).select_from(InterviewTurn).where(InterviewTurn.question_id == q.id))
    seen = last_seen_connected(app.id)
    no_link = seen is None or (utcnow() - seen).total_seconds() >= 20
    if iv.text_mode_since is None and not (presentations >= turns.MAX_PRESENTATIONS or no_link):
        raise conflict("TEXT_MODE_NOT_ALLOWED", "음성 연결을 다시 시도해 주세요.")
    if app.attempt_status == "INTERRUPTED":
        if not await slots.acquire(db, app.id):
            await db.rollback()
            raise conflict("CAPACITY_FULL", CAPACITY_FULL_MESSAGE)
        app.attempt_status, app.interrupted_from = "INTERVIEWING", None
    iv.text_mode_since = iv.text_mode_since or utcnow()
    await touch(db, app.id)
    await db.commit()
    return await interview_state(db, app)


@router.post("/interview/next")
async def text_next(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    """Text mode only: present the next question (the voice runtime does this itself)."""
    app = await _own(db, ctx, aid, lock=True)
    iv = await db.get(Interview, app.id)
    if app.attempt_status != "INTERVIEWING" or iv.text_mode_since is None:
        raise invalid_state(app)
    live = (await db.execute(select(InterviewTurn).where(InterviewTurn.application_id == app.id,
                                                         InterviewTurn.state == "ANSWERING"))).scalar_one_or_none()
    await db.commit()  # the graph's nodes use their own sessions: the application lock must be released first
    if live is None:
        async with igraph.app_lock(app.id):
            await igraph.begin(app.id, "TEXT", igraph.TextIO())  # present -> ask -> wait_answer (or finish when all answered)
        db.expunge_all()  # the graph wrote through other sessions: read the rows again
        app = await db.get(Application, app.id)
    return await interview_state(db, app)


async def _turn(db: AsyncSession, app: Application, turn_id: UUID) -> InterviewTurn:
    turn = await db.get(InterviewTurn, turn_id)
    if turn is None or turn.application_id != app.id:
        raise conflict("STALE_TURN", "이미 지난 질문에 대한 요청입니다.")
    return turn


@router.post("/interview/turns/{turn_id}/done", status_code=202)
async def turn_done(aid: UUID, turn_id: UUID, ctx: CandidateCtx = Depends(require_candidate),
                    db: AsyncSession = Depends(get_db)) -> dict:
    """[답변 완료]. The voice runtime owns the audio and finalises; a stale turn id never closes a new turn (C3)."""
    from app.voice.registry import request_finish

    app = await _own(db, ctx, aid)
    turn = await _turn(db, app, turn_id)
    if turn.state in ("FINALIZING", "CLOSED"):
        return {"turn_id": turn.id, "state": turn.state, "end_reason": turn.end_reason}
    if turn.state != "ANSWERING":
        raise conflict("STALE_TURN" if turn.state == "ABANDONED" else "STATE_CONFLICT",
                       "지금은 답변을 마칠 수 없습니다.", state=turn.state)
    if not await request_finish(app.id, turn.id):
        raise conflict("STALE_TURN", "음성 연결이 끊겨 이 질문은 다시 진행됩니다.")
    return {"turn_id": turn.id, "state": "FINALIZING"}


@router.post("/interview/turns/{turn_id}/replay", status_code=202)
async def turn_replay(aid: UUID, turn_id: UUID, ctx: CandidateCtx = Depends(require_candidate),
                      db: AsyncSession = Depends(get_db)) -> dict:
    """[질문 다시 듣기]: once per question, only during the think time (SC-A06)."""
    return await _think_command(db, ctx, aid, turn_id, "replay")


@router.post("/interview/turns/{turn_id}/start-answer", status_code=202)
async def turn_start_answer(aid: UUID, turn_id: UUID, ctx: CandidateCtx = Depends(require_candidate),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """[바로 답변 시작]: skip the rest of the think time."""
    return await _think_command(db, ctx, aid, turn_id, "start")


async def _think_command(db: AsyncSession, ctx: CandidateCtx, aid: UUID, turn_id: UUID, cmd: str) -> dict:
    from app.voice.registry import think_command

    app = await _own(db, ctx, aid)
    turn = await _turn(db, app, turn_id)
    if turn.state != "PRESENTING" or turn.think_until is None:
        raise conflict("STATE_CONFLICT", "생각할 시간에만 할 수 있습니다.", state=turn.state)
    if cmd == "replay" and turn.replayed_at is not None:
        raise conflict("REPLAY_USED", "질문 다시 듣기는 한 번만 할 수 있습니다.")
    if not await think_command(app.id, turn.id, cmd):
        raise conflict("STALE_TURN", "이미 지난 질문에 대한 요청입니다.")
    return {"turn_id": turn.id}


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(pattern="^(python|java|javascript)$")
    code: str = Field(max_length=20_000)


@router.put("/interview/turns/{turn_id}/code")
async def turn_code(aid: UUID, turn_id: UUID, body: CodeIn, ctx: CandidateCtx = Depends(require_candidate),
                    db: AsyncSession = Depends(get_db)) -> dict:
    """Live coding [코드 제출] / [다시 제출]: the last submission inside the answer window counts (SC-A06)."""
    app = await _own(db, ctx, aid)
    await _turn(db, app, turn_id)
    if not await turns.save_code(db, turn_id, body.code, body.language, get_settings().answer_grace_seconds):
        raise conflict("TURN_CLOSED", "이 질문의 답변 시간이 끝났습니다.")
    await db.commit()
    return {"turn_id": turn_id, "saved_at": utcnow()}



class TextAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2000)


@router.post("/interview/turns/{turn_id}/text-answer", status_code=202)
async def text_answer(aid: UUID, turn_id: UUID, body: TextAnswerIn, ctx: CandidateCtx = Depends(require_candidate),
                      db: AsyncSession = Depends(get_db)) -> dict:
    app = await _own(db, ctx, aid, lock=True)
    turn = await _turn(db, app, turn_id)
    if turn.input_mode != "TEXT" or turn.state != "ANSWERING":
        raise conflict("TURN_CLOSED", "이미 마감된 질문입니다.")
    late = turn.deadline_at and utcnow() > turn.deadline_at + timedelta(seconds=get_settings().answer_grace_seconds)
    view = await turns.view_of(db, turn.id)
    await db.commit()  # the graph's nodes use their own sessions: the application lock must be released first
    io = igraph.TextIO("" if late else body.text)
    async with igraph.app_lock(app.id):
        await db.refresh(turn)
        if turn.state != "ANSWERING":  # a second request for the same turn waited for the first one
            return {"turn_id": turn.id, "state": "CLOSED"}
        await igraph.ensure_waiting(app.id, view, "TEXT", io)
        await igraph.resume(app.id, "TIMEOUT" if late else "BUTTON", io)  # transcribe -> decide_followup (-> handed back)
    return {"turn_id": turn.id, "state": "CLOSED"}


@router.post("/heartbeat", status_code=204)
async def heartbeat(aid: UUID, ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> None:
    app = await _own(db, ctx, aid)
    if app.attempt_status not in ("PREPARING", "INTERVIEWING", "CODING"):
        raise invalid_state(app)
    await touch(db, app.id)  # C7: coding and text mode have no voice runtime to renew liveness
    if app.attempt_status == "INTERVIEWING":
        await slots.renew(db, app.id)
    await db.commit()


class ProctorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(LEAVE|RETURN|GAZE_AWAY)$")
    away_ms: int | None = Field(None, ge=0, le=86_400_000)


@router.post("/interview/proctor-events")
async def proctor(aid: UUID, body: ProctorIn, ctx: CandidateCtx = Depends(require_candidate),
                  db: AsyncSession = Depends(get_db)) -> dict:
    """Tab leave/return and gaze-away (browser face detection, SC-A06): warn and record only (X-06)."""
    app = await _own(db, ctx, aid)
    iv = await db.get(Interview, app.id)
    if app.attempt_status not in ("INTERVIEWING", "CODING") or iv is None:
        return {"gaze_warnings": iv.gaze_warning_count if iv else 0, "suspected": False}
    db.add(ProctorEvent(application_id=app.id, kind=body.kind, occurred_at=utcnow(), away_ms=body.away_ms))
    if body.kind == "GAZE_AWAY":
        await db.execute(update(Interview).where(Interview.application_id == app.id).values(
            gaze_warning_count=Interview.gaze_warning_count + 1))
    await db.commit()
    count = await db.scalar(select(Interview.gaze_warning_count).where(Interview.application_id == app.id))
    return {"gaze_warnings": count, "suspected": count >= GAZE_SUSPECT_COUNT}
