"""Runner HTTP API (loopback / internal network only, X-Runner-Token).

POST /v1/execute  {code, cases: [{case_id, stdin}], limits: {wall_ms, mem_mb}}
  -> {results: [{case_id, verdict, stdout, stderr, exit_code, time_ms}], sandbox_fingerprint}
  One test case = one container. Expected outputs are not accepted (extra fields -> 422) and never stored (C8).
GET  /v1/health   -> {sandbox_verified, status, fingerprint, verified_at, runtime, failed_checks, reason}
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from runner import config as C
from runner.sandbox import Limits
from runner.selftest import RunnerService

log = logging.getLogger("runner")
RECHECK_EVERY_SEC = 3600


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=64)
    stdin: str = ""

    @field_validator("stdin")
    @classmethod
    def _stdin_size(cls, v: str) -> str:
        if len(v.encode()) > C.MAX_STDIN_BYTES:
            raise ValueError("stdin too large")
        return v


class LimitsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wall_ms: int = Field(C.DEFAULT_WALL_MS, ge=C.WALL_MS_RANGE[0], le=C.WALL_MS_RANGE[1])
    mem_mb: int = Field(C.DEFAULT_MEM_MB, ge=C.MEM_MB_RANGE[0], le=C.MEM_MB_RANGE[1])


class ExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")   # e.g. "expected" is rejected: the runner never sees answers
    code: str
    cases: list[CaseIn] = Field(min_length=1, max_length=50)
    limits: LimitsIn = LimitsIn()

    @field_validator("code")
    @classmethod
    def _code_size(cls, v: str) -> str:
        if len(v.encode()) > C.MAX_CODE_BYTES:
            raise ValueError("code larger than 64 KiB")
        return v


class CaseOut(BaseModel):
    case_id: str
    verdict: str
    stdout: str
    stderr: str
    exit_code: int | None
    time_ms: int


class ExecuteOut(BaseModel):
    results: list[CaseOut]
    sandbox_fingerprint: str | None


def create_app(service: RunnerService | None = None, run_selftest: bool = True) -> FastAPI:
    svc = service or RunnerService()

    async def reverify_loop() -> None:
        while True:
            try:
                if not await svc.ensure_valid():
                    st = await svc.verify()
                    log.warning("sandbox self-test: %s %s", st.status, st.reason)
            except Exception:  # noqa: BLE001 - keep the loop alive; state stays unverified
                log.exception("self-test loop error")
            await asyncio.sleep(RECHECK_EVERY_SEC)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(reverify_loop()) if run_selftest else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="aihire-runner", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = svc

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [{"loc": [str(p) for p in e["loc"]], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": "VALIDATION", "message": "invalid request", "fields": fields}},
                            status_code=422)

    def require_token(x_runner_token: str | None = Header(default=None)) -> None:
        if not x_runner_token or not hmac.compare_digest(x_runner_token.encode(), svc.cfg.token.encode()):
            raise ApiError(401, "UNAUTHORIZED", "runner token required")

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    async def health() -> dict:
        await svc.ensure_valid()
        return svc.state.public()

    @app.post("/v1/execute", dependencies=[Depends(require_token)])
    async def execute(body: ExecuteIn) -> ExecuteOut:
        if not await svc.ensure_valid():
            raise ApiError(503, "SANDBOX_UNVERIFIED", "sandbox self-test has not passed")
        limits = Limits(body.limits.wall_ms, body.limits.mem_mb)
        results = await asyncio.gather(*(svc.executor.run_case(body.code, c.stdin.encode(), limits)
                                         for c in body.cases))
        return ExecuteOut(
            results=[CaseOut(case_id=c.case_id, verdict=r.verdict, stdout=r.stdout, stderr=r.stderr,
                             exit_code=r.exit_code, time_ms=r.time_ms) for c, r in zip(body.cases, results)],
            sandbox_fingerprint=svc.state.fingerprint)

    return app
