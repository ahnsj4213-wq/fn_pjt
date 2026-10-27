"""Opaque server sessions: the cookie holds a random token, the DB holds only its SHA-256 (SC-05).
Idle timeout 2 hours, sliding (USR-06)."""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SESSION_IDLE_SECONDS
from app.models import Session, User

_IDLE = timedelta(seconds=SESSION_IDLE_SECONDS)
_TOUCH_EVERY = timedelta(seconds=60)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    role: str
    company_id: UUID | None
    name: str
    csrf_token: str
    token_hash: bytes


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def now() -> datetime:
    return datetime.now(UTC)


async def create_session(db: AsyncSession, user: User) -> tuple[str, str]:
    """Always issues a fresh token (prevents session fixation). Returns (cookie_token, csrf_token)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    t = now()
    db.add(Session(token_hash=_hash(token), user_id=user.id, role=user.role, company_id=user.company_id,
                   csrf_token=csrf, last_seen_at=t, expires_at=t + _IDLE))
    return token, csrf


async def load_session(db: AsyncSession, token: str | None) -> Principal | None:
    if not token or len(token) > 128:
        return None
    th = _hash(token)
    row = (await db.execute(
        select(Session, User.name, User.status).join(User, User.id == Session.user_id).where(Session.token_hash == th)
    )).first()
    if row is None:
        return None
    sess, name, status = row
    t = now()
    if sess.expires_at <= t or status != "ACTIVE":
        await db.execute(delete(Session).where(Session.token_hash == th))
        await db.commit()
        return None
    if t - sess.last_seen_at >= _TOUCH_EVERY:
        await db.execute(update(Session).where(Session.token_hash == th).values(last_seen_at=t, expires_at=t + _IDLE))
        await db.commit()
    return Principal(sess.user_id, sess.role, sess.company_id, name or "", sess.csrf_token, th)


async def revoke(db: AsyncSession, token_hash: bytes) -> None:
    await db.execute(delete(Session).where(Session.token_hash == token_hash))


async def revoke_all(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(delete(Session).where(Session.user_id == user_id))
