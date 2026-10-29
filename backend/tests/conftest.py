"""Test harness: a fresh `aihire_test` database built from the real migration, tables truncated between tests.

Requires the dev PostgreSQL (deploy/docker-compose.dev.yml). Tests talk to the ASGI app over httpx,
so routing, dependencies, middleware and error handlers are all exercised.
"""
import asyncio
import contextlib
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
# Redis DB 15 keeps tests away from development data; tests needing Redis skip when it is not running.
os.environ.setdefault("AIHIRE_REDIS_URL", "redis://127.0.0.1:6379/15")
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
    await _flush_redis()


async def _flush_redis() -> None:
    from app.core.redis import get_redis

    r = get_redis()
    if r is None:
        return
    with contextlib.suppress(Exception):  # Redis not running: nothing to clean
        keys = [k async for k in r.scan_iter("aihire:*")]
        if keys:
            await r.delete(*keys)


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


@pytest.fixture
def voice_ai():
    """FakeAIClient for voice tests: 0.2 s TTS clips (keeps real-time tests fast) and an STT call counter."""
    from app.ai.client import TTSResult, set_ai_client
    from app.ai.fake import FakeAIClient

    fake = FakeAIClient()
    calls = {"stt": 0}
    real_stt = fake.stt

    async def short_tts(text: str) -> TTSResult:
        return TTSResult(pcm48k=bytes(9600 * 2), duration_ms=200, voice_model="fake-tts")

    async def counted_stt(pcm: bytes, *, purpose: str = "answer"):
        calls["stt"] += 1
        return await real_stt(pcm, purpose=purpose)

    fake.tts, fake.stt = short_tts, counted_stt
    fake.calls_count = calls
    set_ai_client(fake)
    yield fake
    set_ai_client(None)


@pytest.fixture
def fake_runner():
    """Runs the candidate's Python locally (trusted test code). The request must never carry expected output."""
    state = {"sandbox_error": False, "requests": []}

    import json
    import subprocess
    import sys

    from app.coding import runner as runner_client

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["requests"].append(body)
        assert set(body) == {"code", "cases", "limits"} and all(set(c) == {"case_id", "stdin"} for c in body["cases"])
        results = []
        for c in body["cases"]:
            if state["sandbox_error"]:
                results.append({"case_id": c["case_id"], "verdict": "SANDBOX_ERROR", "stdout": "", "stderr": "",
                                "exit_code": None, "time_ms": 0})
                continue
            p = subprocess.run([sys.executable, "-I", "-c", body["code"]], input=c["stdin"], capture_output=True, check=False,
                               text=True, timeout=10)
            results.append({"case_id": c["case_id"], "verdict": "OK" if p.returncode == 0 else "RUNTIME_ERROR",
                            "stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode, "time_ms": 5})
        return httpx.Response(200, json={"results": results, "sandbox_fingerprint": "test"})

    runner_client.set_transport(httpx.MockTransport(handle))
    yield state
    runner_client.set_transport(None)
