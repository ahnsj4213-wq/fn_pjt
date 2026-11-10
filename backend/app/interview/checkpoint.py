"""PostgreSQL checkpoints of the interview graph (ADR-0016, ai-architecture 6.1).

A checkpoint is the place the graph stopped (state of the interview progress graph: ids, mode, route; never an answer
text), kept so that a dropped connection resumes at the step it stopped at. The application row stays the source of
truth for the attempt; the graph's tables are `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations` (created by the library's own `setup()`, once per process, idempotent).
"""
import asyncio
import logging
import os
import sys
import threading

# LangChain's tracing client would send run data to a remote service when these are set; never in this product (AC-22).
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING_V2"] = "false"

from langgraph.checkpoint.base import BaseCheckpointSaver  # noqa: E402
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg_pool import AsyncConnectionPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402

log = logging.getLogger("app.interview")
_lock: asyncio.Lock | None = None
_pool: AsyncConnectionPool | None = None
_saver: BaseCheckpointSaver | None = None


def conninfo() -> str:
    """The SQLAlchemy URL (postgresql+asyncpg://...) as a libpq URL for psycopg."""
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class OwnLoopSaver(BaseCheckpointSaver):
    """psycopg's async mode cannot run on Windows' default (Proactor) event loop, and the rest of the app needs that loop
    (subprocesses). So on Windows the real saver lives on a small selector loop in its own thread and every call is
    forwarded to it. Linux (the containers) uses the saver directly; this class is not used there."""

    def __init__(self) -> None:
        self._loop = asyncio.SelectorEventLoop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="checkpoint-loop", daemon=True)
        self._thread.start()
        self._inner: AsyncPostgresSaver | None = None
        self._pool: AsyncConnectionPool | None = None
        super().__init__()

    async def _call(self, coro_fn, *a, **k):
        fut = asyncio.run_coroutine_threadsafe(coro_fn(*a, **k), self._loop)
        return await asyncio.wrap_future(fut)

    async def start(self) -> None:
        async def make():
            pool = AsyncConnectionPool(conninfo(), min_size=1, max_size=4, open=False, timeout=10,
                                       kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
            await pool.open()
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            await saver.setup()
            return pool, saver
        self._pool, self._inner = await self._call(make)
        self.serde = self._inner.serde

    async def close(self) -> None:
        if self._pool is not None:
            await self._call(self._pool.close)
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def aget_tuple(self, config):
        return await self._call(self._inner.aget_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):  # noqa: A002
        async def collect():
            return [x async for x in self._inner.alist(config, filter=filter, before=before, limit=limit)]
        for item in await self._call(collect):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self._call(self._inner.aput, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await self._call(self._inner.aput_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await self._call(self._inner.adelete_thread, thread_id)

    async def adelete_for_runs(self, run_ids):
        return await self._call(self._inner.adelete_for_runs, run_ids)

    async def acopy_thread(self, source_thread_id, target_thread_id):
        return await self._call(self._inner.acopy_thread, source_thread_id, target_thread_id)

    async def aprune(self, thread_ids, *, strategy="keep_latest"):
        return await self._call(self._inner.aprune, thread_ids, strategy=strategy)

    async def aget_delta_channel_history(self, *a, **k):
        return await self._call(self._inner.aget_delta_channel_history, *a, **k)

    def get_next_version(self, current, channel):
        return self._inner.get_next_version(current, channel)


async def get_checkpointer() -> BaseCheckpointSaver:
    global _lock, _pool, _saver
    if _saver is not None:
        return _saver
    _lock = _lock or asyncio.Lock()
    async with _lock:
        if _saver is None:
            if sys.platform == "win32" and "Selector" not in type(asyncio.get_running_loop()).__name__:
                own = OwnLoopSaver()
                await own.start()
                _saver = own
            else:
                pool = AsyncConnectionPool(conninfo(), min_size=1, max_size=4, open=False, timeout=10,
                                           kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
                await pool.open()
                saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
                await saver.setup()
                _pool, _saver = pool, saver
    return _saver


async def close_checkpointer() -> None:
    global _lock, _pool, _saver
    if isinstance(_saver, OwnLoopSaver):
        await _saver.close()
    elif _pool is not None:
        await _pool.close()
    _lock = _pool = _saver = None


async def purge_finished(db) -> None:
    """Checkpoints of runs that are over (the attempt finished, expired or was deleted) are not needed any more. Called by the periodic attempt sweeper; the tables exist once the graph has run."""
    from sqlalchemy import text

    if not await db.scalar(text("SELECT to_regclass('public.checkpoints') IS NOT NULL")):
        return
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        await db.execute(text(
            f"DELETE FROM {table} WHERE "
            # composition threads (compose:<application>:<settings>) are needed while the questions can still be composed
            "(thread_id LIKE 'compose:%' AND split_part(thread_id, ':', 2) NOT IN "
            "  (SELECT id::text FROM applications WHERE attempt_status IN ('PREPARING', 'READY'))) "
            # evaluation threads (eval:<application>:<job>) while their job can still run (queued or running)
            "OR (thread_id LIKE 'eval:%' AND split_part(thread_id, ':', 3) NOT IN "
            "  (SELECT id::text FROM jobs WHERE kind = 'evaluate_application' AND state IN ('QUEUED', 'RUNNING'))) "
            # interview threads (<application>) while the interview is in progress or can be resumed
            "OR (thread_id NOT LIKE 'compose:%' AND thread_id NOT LIKE 'eval:%' AND thread_id NOT IN "
            "  (SELECT id::text FROM applications WHERE attempt_status IN ('INTERVIEWING', 'INTERRUPTED')))"))
