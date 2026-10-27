"""Async SQLAlchemy engine/session. asyncpg works on the Windows Proactor loop that aiortc and
asyncio subprocesses also need (psycopg async does not)."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
            # Server-side guards against long locks (statement/idle-in-transaction timeouts).
            connect_args={"server_settings": {"statement_timeout": "60000", "idle_in_transaction_session_timeout": "30000",
                                              "timezone": "UTC"}},
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = _sessionmaker = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Services own their transaction boundaries (`async with db.begin()`)."""
    async with sessionmaker()() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session for workers/CLI: commits on success, rolls back on error."""
    async with sessionmaker()() as session:
        async with session.begin():
            yield session
