"""Database access for question composition without a transaction held across model calls (2026-11-10).

A composition step reads (posting, documents, examples), calls the models (embeddings, T2/T3: seconds each on the 6 GB GPU) and
writes a little. Holding one `session_scope` around all of it kept a transaction idle for the length of a model call: past the
server's 30 s idle-in-transaction limit (core/db.py) the connection was cut and the job retried from scratch (measured: a 45 s call).

`ShortReads` stands in for the AsyncSession the search helpers take (knowledge.search_many, rag.find_examples, rag.copied, ...):
each `execute`/`get` is its own short read transaction, so between two queries — where those helpers call the embedding model —
no transaction is open. The rows come back buffered (AsyncSession.execute pre-buffers) and ORM objects detached with their loaded
columns (expire_on_commit=False). Writing through it is refused: a write opens its own `session_scope` after the model calls.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope


class ShortReads:
    """Read-only, one transaction per statement. Never use it for something that must see one consistent snapshot."""

    async def execute(self, statement, params=None, **kw):
        async with session_scope() as db:
            return await (db.execute(statement, params, **kw) if params is not None else db.execute(statement, **kw))

    async def get(self, model, ident, **kw):
        async with session_scope() as db:
            return await db.get(model, ident, **kw)

    async def scalar(self, statement, params=None, **kw):
        async with session_scope() as db:
            return await (db.scalar(statement, params, **kw) if params is not None else db.scalar(statement, **kw))

    def add(self, *_a, **_k):  # pragma: no cover - a programming error
        raise RuntimeError("ShortReads is read-only: write in a session_scope after the model calls")

    async def flush(self, *_a, **_k):  # pragma: no cover
        raise RuntimeError("ShortReads is read-only: write in a session_scope after the model calls")


def is_session(db) -> bool:
    return isinstance(db, AsyncSession)
