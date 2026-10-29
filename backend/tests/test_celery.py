"""ADR-0014: Celery worker + beat on top of the PostgreSQL outbox. Starts a real Celery worker process against the
test database and Redis DB 15 (skips without Redis). The sandbox case also needs Docker and the sandbox image."""
import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.core.redis import get_redis
from app.jobs import dispatch, queue

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def celery_worker():
    import redis

    url = os.environ["AIHIRE_REDIS_URL"]
    try:
        redis.Redis.from_url(url).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis is not running")
    env = {**os.environ, "AIHIRE_WORKER_MODE": "celery", "AIHIRE_RUNNER_MODE": "local",
           "AIHIRE_CODE_EXECUTION_ENABLED": "true", "AIHIRE_AI_MODE": "fake",
           "PYTHONPATH": f"{BACKEND}{os.pathsep}{BACKEND.parent / 'runner'}"}
    proc = subprocess.Popen([sys.executable, "-m", "celery", "-A", "app.celery_app", "worker", "-Q",
                             "none,relay,periodic,runner,sandbox", "-c", "1", "--pool", "solo", "-n", "test@%h",
                             "--loglevel", "WARNING"], cwd=BACKEND, env=env)
    time.sleep(4)
    assert proc.poll() is None, "celery worker did not start"
    yield proc
    proc.terminate()
    proc.wait(timeout=20)


async def _state(job_id: int) -> str:
    async with session_scope() as db:
        return (await db.execute(text("SELECT state FROM jobs WHERE id = :i"), {"i": job_id})).scalar_one()


async def test_outbox_job_runs_on_a_celery_worker(celery_worker):
    async with session_scope() as db:  # a real kind with a harmless handler: unknown application -> skipped
        job = await queue.enqueue(db, "send_email", {"template": "APPLICATION_RECEIVED",
                                                     "application_id": str(uuid.uuid4())})
    assert await dispatch.dispatch_due(send=dispatch.to_celery) == 1
    for _ in range(80):
        if await _state(job) == "SUCCEEDED":
            break
        await asyncio.sleep(0.25)
    assert await _state(job) == "SUCCEEDED"
    # A duplicate message for a finished job is dropped by the claim (fencing), not run twice.
    from app.celery_app import celery

    res = celery.send_task("jobs.run", args=[job, "NONE"], queue="none")
    assert await asyncio.to_thread(res.get, timeout=20) == "stale"


@pytest.mark.docker
async def test_run_code_goes_through_the_worker_sandbox(celery_worker):
    if shutil.which("docker") is None or subprocess.run(["docker", "image", "inspect", "aihire-sandbox:py311"],
                                                        capture_output=True).returncode != 0:
        pytest.skip("Docker or the sandbox image is not available")
    from app.celery_app import celery
    from app.coding.runner import RunnerUnavailable, _execute_via_worker
    from app.coding.sandbox_local import published_ready

    await get_redis().delete("aihire:sandbox:status")
    with pytest.raises(RunnerUnavailable):  # not verified yet: fail closed (OQ-32)
        await _execute_via_worker("print(1)", [{"case_id": "1", "stdin": ""}], wall_ms=2000, mem_mb=128)
    celery.send_task("periodic.run", args=["sandbox_verify"], queue="runner")
    for _ in range(360):  # the self-test starts ~14 sandbox containers
        if await published_ready():
            break
        await asyncio.sleep(0.5)
    assert await published_ready()
    out = await _execute_via_worker("print(int(input()) * 2)", [{"case_id": "1", "stdin": "21\n"}],
                                    wall_ms=3000, mem_mb=128)
    assert out[0].verdict == "OK" and out[0].stdout.strip() == "42"
