"""Code sandbox inside the worker container (target diagram: the worker starts sandbox containers through
docker.sock; there is no separate runner service). Uses the runner package (ADR-0007) in-process: the same
container options, inspect validation and self-test SBX-01~14.

The worker publishes the self-test result to Redis so the API (which has no Docker access) can tell whether
[실행] and coding interviews may start (OQ-32).
"""
import asyncio
import json

from app.coding.runner import CaseResult, RunnerUnavailable
from app.core.redis import get_redis

STATUS_KEY = "aihire:sandbox:status"
STATUS_TTL_SEC = 26 * 3600  # the runner itself expires a verification after 24 h

_svc = None
_verify_lock = asyncio.Lock()


def service():
    global _svc
    if _svc is None:
        from runner.config import RunnerConfig  # the runner package ships in the worker image
        from runner.selftest import RunnerService

        cfg = RunnerConfig()
        cfg.check()
        _svc = RunnerService(cfg)
    return _svc


async def verify_and_publish(force: bool = False) -> dict:
    """Runs the self-test when needed and publishes the public state for the API."""
    svc = service()
    if force or not await svc.ensure_valid():
        await svc.verify()
    state = svc.state.public()
    r = get_redis()
    if r is not None:
        await r.set(STATUS_KEY, json.dumps(state), ex=STATUS_TTL_SEC)
    return state


async def local_ready() -> bool:
    """This process's own verification. Every Celery child keeps its own RunnerService, so a child that has not run
    the self-test yet runs it once when the published result (from another child) says the sandbox passed."""
    svc = service()
    if await svc.ensure_valid():
        return True
    if not await published_ready():
        return False
    async with _verify_lock:
        if not await svc.ensure_valid():
            await svc.verify()
    return await svc.ensure_valid()


async def execute_local(code: str, cases: list[dict], *, wall_ms: int, mem_mb: int) -> list[CaseResult]:
    from runner.sandbox import Limits

    if not await local_ready():
        raise RunnerUnavailable("SANDBOX_UNVERIFIED")
    svc = service()
    limits = Limits(wall_ms, mem_mb)
    results = await asyncio.gather(*(svc.executor.run_case(code, c["stdin"].encode(), limits) for c in cases))
    out = [CaseResult(case_id=c["case_id"], verdict=r.verdict, stdout=r.stdout, stderr=r.stderr,
                      exit_code=r.exit_code, time_ms=r.time_ms) for c, r in zip(cases, results)]
    if any(x.verdict == "SANDBOX_ERROR" for x in out):
        raise RunnerUnavailable("SANDBOX_ERROR")
    return out


async def published_ready() -> bool:
    r = get_redis()
    if r is None:
        return False
    try:
        raw = await r.get(STATUS_KEY)
    except Exception:  # noqa: BLE001 - Redis down: treat as not ready (fail closed)
        return False
    return bool(raw) and bool(json.loads(raw).get("sandbox_verified"))
