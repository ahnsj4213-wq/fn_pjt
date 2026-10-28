"""WebRTC signaling and the precheck utterance command (TRD 06 §3·§7 IV-3, IV-18). Same cookie session, CSRF and
Origin checks as every other candidate command (D-06). Non-trickle: the answer carries all ICE candidates."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CandidateCtx, require_candidate
from app.auth.scope import get_for_owner
from app.core.db import get_db
from app.core.errors import AppError, conflict
from app.models import Application, Interview
from app.voice import registry

router = APIRouter(prefix="/api/candidate/applications/{aid}", tags=["candidate"])


class OfferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["PRECHECK", "INTERVIEW"]
    sdp: str = Field(max_length=20_000)
    type: Literal["offer"]


def check_sdp(sdp: str) -> None:
    """Exactly one audio m-line and one data channel; video never reaches the server (INT-08)."""
    kinds = [line[2:].split(" ", 1)[0] for line in sdp.splitlines() if line.startswith("m=")]
    if sorted(kinds) != ["application", "audio"]:
        raise AppError(422, "SDP_INVALID", "오디오와 데이터 채널만 연결할 수 있습니다.")


@router.post("/rtc/offer")
async def offer(aid: UUID, body: OfferIn, ctx: CandidateCtx = Depends(require_candidate),
                db: AsyncSession = Depends(get_db)) -> dict:
    app = await get_for_owner(db, Application, aid, ctx.user_id)
    check_sdp(body.sdp)
    if body.purpose == "PRECHECK":
        if app.attempt_status != "PREPARING":
            raise conflict("STATE_CONFLICT", "면접 준비 단계에서만 연결을 점검할 수 있습니다.")
    else:
        iv = await db.get(Interview, app.id)
        slot = await db.scalar(text("SELECT lease_expires_at > now() FROM interview_slots WHERE application_id=:a"),
                               {"a": app.id})
        if app.attempt_status != "INTERVIEWING" or not slot:
            raise conflict("STATE_CONFLICT", "면접을 진행할 수 있는 상태가 아닙니다.")  # C4: slot required
        if iv is not None and iv.text_mode_since is not None:
            raise conflict("STATE_CONFLICT", "텍스트 모드로 진행 중입니다.")
    await db.commit()  # release the row before the (slower) WebRTC negotiation
    try:
        answer, session_id = await registry.open_session(app.id, body.purpose.lower(), body.sdp)
    except registry.PrecheckBusy:
        raise AppError(429, "PRECHECK_BUSY", "점검 연결이 많습니다. 잠시 후 다시 시도해 주세요.") from None
    return {"sdp": answer, "type": "answer", "rtc_session_id": session_id}


@router.post("/precheck/utterance", status_code=202)
async def precheck_utterance(aid: UUID, ctx: CandidateCtx = Depends(require_candidate),
                             db: AsyncSession = Depends(get_db)) -> dict:
    app = await get_for_owner(db, Application, aid, ctx.user_id)
    s = registry.get(app.id)
    if app.attempt_status != "PREPARING" or s is None or s.closed or s.purpose != "precheck" or s.dc_open.is_set() is False:
        raise conflict("PRECHECK_NOT_CONNECTED", "음성 연결 점검을 먼저 시작해 주세요.")
    if s.gate is not None:
        raise conflict("PRECHECK_BUSY", "이미 듣고 있습니다.")
    s._spawn(s.precheck_listen())
    return {"window_sec": 6}
