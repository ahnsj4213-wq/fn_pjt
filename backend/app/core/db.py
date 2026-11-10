"""Async SQLAlchemy engine/session. asyncpg works on the Windows Proactor loop that aiortc and
asyncio subprocesses also need (psycopg async does not)."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
# How many `session_scope` transactions the current task is inside. A model call made while this is > 0 holds a database
# transaction (and its locks) open for seconds: tests and tools/bench_ai.py check it.
_open_scopes: ContextVar[int] = ContextVar("aihire_open_scopes", default=0)


def open_scopes() -> int:
    return _open_scopes.get()


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
    _open_scopes.set(_open_scopes.get() + 1)
    try:
        async with sessionmaker()() as session:
            async with session.begin():
                yield session
    finally:  # not reset(token): a generator closed from another context would raise there
        _open_scopes.set(max(0, _open_scopes.get() - 1))
