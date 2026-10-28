"""Account withdrawal (USR-07, TRD 04 `DELETE /api/candidate/me`, TRD 03 §8, OQ-12 provisional)."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import CandidateCtx, require_candidate
from app.auth.passwords import verify_password
from app.auth.sessions import revoke_all
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, conflict
from app.models import User
from app.retention.service import purge_content

router = APIRouter(prefix="/api/candidate", tags=["candidate"])

ACTIVE = ("PREPARING", "INTERVIEWING", "CODING")


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


@router.delete("/me", status_code=202)
async def withdraw(body: WithdrawIn, request: Request, response: Response,
                   ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    """Personal data and every application's content go at once; consent, decision and audit records stay without
    personal data. An attempt in progress must finish first (OQ-12, OQ-B7)."""
    user = await db.get(User, ctx.user_id, with_for_update=True)
    if not await verify_password(user.password_hash, body.password):
        raise AppError(403, "REAUTH_FAILED", "비밀번호가 올바르지 않습니다.")
    apps = (await db.execute(text("SELECT id, attempt_status FROM applications WHERE candidate_id = :u ORDER BY id "
                                  "FOR UPDATE"), {"u": user.id})).all()
    if any(st in ACTIVE for _, st in apps):
        raise conflict("ACTIVE_ATTEMPT", "응시가 끝난 뒤 탈퇴할 수 있습니다.")
    ids = [a for a, _ in apps]
    # Unfinished attempts end here so posting close / auto-submit never act on them again (7.1: -> EXPIRED).
    await db.execute(text("UPDATE applications SET attempt_status = 'EXPIRED', interrupted_from = NULL, "
                          "expired_at = now() WHERE candidate_id = :u AND attempt_status IN ('READY', 'INTERRUPTED')"),
                     {"u": user.id})
    files = await purge_content(db, ids, "WITHDRAWAL")
    if ids:
        await db.execute(text("UPDATE applications SET withdrawn_at = now() WHERE id = ANY(:ids)"), {"ids": ids})
    files += (await db.execute(text(
        "INSERT INTO file_deletions (storage_key, reason) SELECT 'resumes/' || storage_key, 'WITHDRAWAL' "
        "FROM resume_files WHERE owner_id = :u"), {"u": user.id})).rowcount or 0
    await db.execute(text("DELETE FROM resumes WHERE candidate_id = :u"), {"u": user.id})
    await db.execute(text("DELETE FROM resume_files WHERE owner_id = :u"), {"u": user.id})
    user.email = user.name = user.phone = user.password_hash = None
    user.status = "WITHDRAWN"
    await db.execute(text("UPDATE users SET withdrawn_at = now() WHERE id = :u"), {"u": user.id})
    await revoke_all(db, user.id)
    await audit(db, action="ACCOUNT_WITHDRAW", target_type="user", target_id=user.id, actor_id=user.id,
                actor_role="CANDIDATE", request_id=getattr(request.state, "request_id", None),
                applications=len(ids), files=files)
    await db.commit()
    response.delete_cookie(get_settings().cookie_name, path="/")
    return {"status": "WITHDRAWN"}
