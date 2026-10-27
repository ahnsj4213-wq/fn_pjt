"""Client for the code runner (ADR-0007, TRD 07 §13). Only code and stdin go out — never expected outputs (C8);
the worker compares outputs itself. Runner down / unverified / sandbox errors raise RunnerUnavailable so the
grading job is deferred without consuming an attempt (OQ-23)."""
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

_transport: httpx.AsyncBaseTransport | None = None


class RunnerUnavailable(Exception):
    pass


@dataclass
class CaseResult:
    case_id: str
    verdict: str  # OK | RUNTIME_ERROR | TIMEOUT | MEMORY_LIMIT | OUTPUT_LIMIT | SANDBOX_ERROR
    stdout: str
    stderr: str
    exit_code: int | None
    time_ms: int


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests route runner calls to an in-process fake."""
    global _transport
    _transport = transport


async def execute(code: str, cases: list[dict], *, wall_ms: int, mem_mb: int) -> list[CaseResult]:
    s = get_settings()
    body = {"code": code, "cases": [{"case_id": c["case_id"], "stdin": c["stdin"]} for c in cases],
            "limits": {"wall_ms": wall_ms, "mem_mb": mem_mb}}
    timeout = wall_ms / 1000 * len(cases) + 30
    try:
        async with httpx.AsyncClient(base_url=s.runner_url, timeout=timeout, transport=_transport,
                                     headers={"X-Runner-Token": s.runner_token.get_secret_value()}) as c:
            r = await c.post("/v1/execute", json=body)
    except httpx.TransportError as e:
        raise RunnerUnavailable(type(e).__name__) from e
    if r.status_code in (502, 503, 504):
        raise RunnerUnavailable(f"HTTP {r.status_code}")
    r.raise_for_status()
    out = [CaseResult(**x) for x in r.json()["results"]]
    if any(x.verdict == "SANDBOX_ERROR" for x in out):
        raise RunnerUnavailable("SANDBOX_ERROR")  # the environment failed, not the candidate's code
    return out
