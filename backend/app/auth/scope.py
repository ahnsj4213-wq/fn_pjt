"""Company and owner scoping helpers (SEC-03, SC-12).

Recruiter code loads company-owned rows only through `get_for_company`; candidate code loads its own rows only
through `get_for_owner`. Missing and out-of-scope rows produce the same 404 body so existence is not revealed.
"""
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found

T = TypeVar("T")


def _uuid(value: Any) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError):
        raise not_found() from None


async def get_for_company(db: AsyncSession, model: type[T], id_: Any, company_id: UUID, *,
                          for_update: bool = False) -> T:
    stmt = select(model).where(model.id == _uuid(id_), model.company_id == company_id)  # type: ignore[attr-defined]
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise not_found()
    return row


async def get_for_owner(db: AsyncSession, model: type[T], id_: Any, owner_id: UUID, *, owner_col: str = "candidate_id",
                        for_update: bool = False) -> T:
    stmt = select(model).where(model.id == _uuid(id_), getattr(model, owner_col) == owner_id)  # type: ignore[attr-defined]
    if for_update:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise not_found()
    return row
