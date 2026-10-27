"""Authentication, role guards, CSRF and company scope (SEC-03, USR-05, SC-10~13).

Recruiter routes receive a RecruiterCtx whose company_id comes from the server session only; request
bodies and query strings never supply company scope.
"""
import hmac
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import Principal, load_session
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().trusted_origins:
        raise AppError(403, "ORIGIN_REJECTED", "허용되지 않은 출처의 요청입니다.")


async def get_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    principal = await load_session(db, request.cookies.get(get_settings().cookie_name))
    if principal is None:
        raise AppError(401, "UNAUTHENTICATED", "로그인이 필요합니다.")
    if request.method in UNSAFE_METHODS:
        check_origin(request)
        sent = request.headers.get("x-csrf-token") or ""
        if not hmac.compare_digest(sent.encode(), principal.csrf_token.encode()):
            raise AppError(403, "CSRF_REJECTED", "요청을 확인할 수 없습니다. 새로고침 후 다시 시도해 주세요.")
    request.state.principal = principal
    return principal


@dataclass(frozen=True)
class CandidateCtx:
    user_id: UUID
    principal: Principal


@dataclass(frozen=True)
class RecruiterCtx:
    user_id: UUID
    company_id: UUID
    principal: Principal


async def require_candidate(p: Principal = Depends(get_principal)) -> CandidateCtx:
    if p.role != "CANDIDATE":
        # USR-05: recruiter accounts cannot use candidate features; the UI shows a guidance message.
        raise AppError(403, "ROLE_FORBIDDEN", "담당자 계정으로는 지원자 기능을 사용할 수 없습니다.")
    return CandidateCtx(p.user_id, p)


async def require_recruiter(p: Principal = Depends(get_principal)) -> RecruiterCtx:
    if p.role != "RECRUITER" or p.company_id is None:
        raise AppError(403, "ROLE_FORBIDDEN", "담당자만 사용할 수 있는 기능입니다.")
    return RecruiterCtx(p.user_id, p.company_id, p)
