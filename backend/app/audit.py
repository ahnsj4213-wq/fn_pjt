"""Audit log writer (SEC-08). Records IDs and state values only — never answer text or contact details."""
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def audit(db: AsyncSession, *, action: str, target_type: str, target_id: Any, actor_id: UUID | None,
                actor_role: str, company_id: UUID | None = None, request_id: str | None = None,
                **detail: Any) -> None:
    db.add(AuditLog(actor_id=actor_id, actor_role=actor_role, company_id=company_id, action=action,
                    target_type=target_type, target_id=str(target_id), detail={k: _plain(v) for k, v in detail.items()},
                    request_id=request_id))


def _plain(v: Any) -> Any:
    return str(v) if isinstance(v, UUID) else v
