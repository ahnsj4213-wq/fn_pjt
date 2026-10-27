"""Test harness: a fresh `aihire_test` database built from the real migration, tables truncated between tests.

Requires the dev PostgreSQL (deploy/docker-compose.dev.yml). Tests talk to the ASGI app over httpx,
so routing, dependencies, middleware and error handlers are all exercised.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

PG = "postgresql://aihire:aihire_dev_only@127.0.0.1:55432"
TEST_DB = "aihire_test"
os.environ["AIHIRE_DATABASE_URL"] = f"postgresql+asyncpg://aihire:aihire_dev_only@127.0.0.1:55432/{TEST_DB}"
os.environ["AIHIRE_ENV"] = "test"
os.environ.setdefault("AIHIRE_DATA_DIR", str(Path(__file__).parent / ".data"))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.main import create_app  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
ORIGIN = "http://localhost:5173"
KEEP = {"state_transitions", "periodic_tasks", "alembic_version", "consent_documents", "question_bank", "coding_problems", "coding_testcases"}


async def _recreate_db() -> None:
    conn = await asyncpg.connect(f"{PG}/postgres")
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    asyncio.run(_recreate_db())
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True,
                   capture_output=True)


@pytest.fixture(autouse=True)
async def clean_tables(database):
    yield
    async with session_scope() as db:
        rows = (await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))).scalars()
        # `companies` is deleted, not truncated: TRUNCATE ... CASCADE on it would also empty question_bank
        # (company-scoped AI questions reference it) and wipe the seed questions for every later test.
        names = [n for n in rows if n not in KEEP and n != "companies"]
        await db.execute(text(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE"))
        # Seeds stay; rows created by tests in kept tables do not.
        await db.execute(text("DELETE FROM question_bank WHERE source <> 'SEED'"))
        await db.execute(text("DELETE FROM companies"))
        await db.execute(text("UPDATE question_bank SET usage_count = 0, last_used_at = NULL"))


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                 headers={"Origin": ORIGIN}) as c:
        yield c


@pytest.fixture
def make_client(app):
    """Extra independent clients (separate cookie jars) for multi-actor tests."""
    clients: list[httpx.AsyncClient] = []

    def _make() -> httpx.AsyncClient:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                              headers={"Origin": ORIGIN})
        clients.append(c)
        return c

    yield _make
    for c in clients:
        asyncio.get_event_loop().create_task(c.aclose())
