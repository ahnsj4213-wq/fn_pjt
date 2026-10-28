"""`ai` service HTTP API (TRD 05 §4). Only app and worker call it, over the internal network with X-AI-Token.

    POST /v1/llm/{task}  envelope -> envelope        (gateway lanes/deadlines, task validators, manifest hash)
    POST /v1/stt         audio/L16 16 kHz mono       -> {text, segments, dropped, model, device, elapsed_ms}
    POST /v1/tts         {text, voice}               -> audio/L16 48 kHz mono + X-Duration-Ms, X-Voice-Model
    POST /v1/embed       {texts}                     -> {vectors, model, dim}
    GET  /v1/health      cached 10 s                 -> {llm, stt, tts, embed, queue, checked_at, manifest_hash}
    GET  /healthz /readyz                            (readyz: 503 until the LLM is warm and STT is loaded)

No database credentials here (T1). Logs carry task names, token counts, timings and codes only (SEC-08).
"""
import asyncio
import hmac
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.ai.manifest import build_manifest, manifest_hash
from app.ai.tasks import TASKS, run_task
from app.aisvc.gateway import CallStats, DeadlineExceeded, Gateway, LLMUnavailable
from app.aisvc.ollama import ThinkingLeak
from app.aisvc.speech import MAX_STT_SECONDS, STT_RATE
from app.core.logging import log_event

log = logging.getLogger("app.aisvc")
HEALTH_TTL_SEC = 10.0
WARMUP_RETRY_SEC = 5.0
MAX_TTS_CHARS = 500
MAX_EMBED_TEXTS = 256


class LLMBackend(Protocol):
    async def chat(self, messages: list[dict], schema: dict, options: dict) -> tuple[str, dict]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def status(self) -> dict: ...
    async def warmup(self) -> None: ...


@dataclass
class Engines:
    backend: LLMBackend
    stt: Any  # app.aisvc.speech.STTEngine or a test double with the same surface
    tts: Any
    gateway: Gateway
    embed_model: str = ""
    gpu_policy: str = "shared"


@dataclass
class _State:
    ready: bool = False
    llm_status: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    manifest_hash: str = ""
    health: dict | None = None
    health_at: float = 0.0
    warm_task: asyncio.Task | None = None


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_version: str = Field(max_length=120)
    lane: str
    deadline_ms: int
    trace_id: str = Field("", max_length=64)
    input: dict


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    voice: str = "default"


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_EMBED_TEXTS)


class _Unauthorized(Exception):
    pass


def _raise_site(exc: BaseException) -> str:
    """'file.py:123 in func' for the innermost frame, plus the first frame inside this project if different."""
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    fmt = lambda f: f"{f.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{f.lineno} in {f.name}"
    own = next((f for f in reversed(frames) if "/app/" in f.filename.replace(chr(92), "/")), None)
    inner = fmt(frames[-1])
    return inner if own is None or own is frames[-1] else f"{inner} (via {fmt(own)})"


def _err(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _default_est_ms(num_predict: int) -> float:
    return 300.0 + num_predict * 5.0  # first-call guess; the gateway learns per task (EWMA)


def create_app(engines: Engines, *, token: str, warmup: bool = True) -> FastAPI:
    st = _State()

    def refresh_manifest() -> None:
        s = st.llm_status
        llm = {"runtime": "ollama", "model": s.get("model"), "digest": s.get("digest"),
               "options": {"think": False, "keep_alive": -1}}
        st.manifest = build_manifest(llm=llm, stt=engines.stt.describe(), tts=engines.tts.describe(),
                                     embed={"model": engines.embed_model, "device": "cpu"})
        st.manifest_hash = manifest_hash(st.manifest)

    async def warm() -> None:
        """Order matters (TRD 05 §3): LLM fully on the GPU first, then STT, then TTS."""
        while True:
            try:
                await engines.backend.warmup()
                engines.gateway.mark_up()
                break
            except Exception as e:  # noqa: BLE001 - keep trying until Ollama is up
                log_event(log, "llm_warmup_failed", logging.WARNING, error_code=type(e).__name__)
                await asyncio.sleep(WARMUP_RETRY_SEC)
        await engines.stt.load()
        await engines.tts.load()
        st.llm_status = await engines.backend.status()
        refresh_manifest()
        st.ready = engines.stt.up
        log_event(log, "ai_ready", status="ready" if st.ready else "stt_down")

    async def lifespan(app: FastAPI):
        refresh_manifest()
        if warmup:
            st.warm_task = asyncio.create_task(warm())
        else:
            st.llm_status = await engines.backend.status()
            refresh_manifest()
            st.ready = True
        yield
        if st.warm_task:
            st.warm_task.cancel()

    app = FastAPI(title="aihire-ai", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ai = st

    async def require_token(x_ai_token: str = Header("")) -> None:
        if not hmac.compare_digest(x_ai_token.encode(), token.encode()):
            raise _Unauthorized()

    @app.exception_handler(_Unauthorized)
    async def _unauthorized(request: Request, exc: Exception):
        return _err(401, "UNAUTHENTICATED")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # never echo inputs back
        where = _raise_site(exc)  # code location only: exception messages can embed request data (SEC-08)
        log_event(log, "ai_error", logging.ERROR, path=request.url.path, error_code=type(exc).__name__, reason=where)
        return _err(500, "INTERNAL", f"{type(exc).__name__} at {where}")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz():
        return {"ready": True} if st.ready else _err(503, "NOT_READY")

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    async def health():
        now = time.monotonic()
        if st.health is None or now - st.health_at > HEALTH_TTL_SEC:
            s = await engines.backend.status()
            if not s.get("up"):
                engines.gateway.mark_down()
            elif engines.gateway.is_down() and s.get("loaded"):
                engines.gateway.mark_up()
            if s.get("digest") != st.llm_status.get("digest"):
                st.llm_status = s
                refresh_manifest()
            st.llm_status = s
            llm_up = bool(s.get("up")) and not engines.gateway.is_down()
            st.health = {
                "llm": {"up": llm_up, "loaded": s.get("loaded", False), "full_gpu": s.get("full_gpu", False),
                        "model": s.get("model"), "digest": s.get("digest")},
                "stt": {"up": engines.stt.up, "model": engines.stt.active.get("model"),
                        "device": engines.stt.active.get("device"), "degraded": engines.stt.degraded},
                "tts": {"up": engines.tts.up},
                "embed": {"up": bool(s.get("up")) and s.get("embed_available", True)},
                "ready": st.ready,
                "queue": engines.gateway.snapshot(),
                "manifest_hash": st.manifest_hash,
                "checked_at": datetime.now(UTC).isoformat(),
            }
            st.health_at = now
        return st.health

    @app.post("/v1/llm/{task}", dependencies=[Depends(require_token)])
    async def llm(task: str, env: Envelope):
        spec = TASKS.get(task)
        if spec is None:
            return _err(404, "UNKNOWN_TASK")
        if env.task_version not in (task, spec.version, spec.full_version):
            return _err(409, "TASK_VERSION_MISMATCH", f"server has {spec.full_version}")
        if env.lane != spec.lane:
            return _err(422, "LANE_MISMATCH", f"{task} runs in {spec.lane}")
        stats = CallStats()
        est = _default_est_ms(spec.num_predict)

        async def call(messages: list[dict], schema: dict, options: dict) -> tuple[str, dict]:
            return await engines.gateway.call(spec.lane, messages, schema, options, deadline_ms=env.deadline_ms,
                                              stats=stats, est_key=task, est_default_ms=est)

        t0 = time.perf_counter()
        status, http, error, outcome = "OK", 200, None, None
        try:
            outcome = await run_task(spec, env.input, call)
            status, error = outcome.status, outcome.error
            http = 200 if status == "OK" else 422
        except DeadlineExceeded:
            status, http, error = "TIMEOUT", 504, "DEADLINE_EXCEEDED"
        except LLMUnavailable:
            status, http, error = "LLM_UNAVAILABLE", 503, "LLM_UNAVAILABLE"
        except ThinkingLeak:
            status, http, error = "INVALID", 422, "THINKING_OUTPUT"
        except (KeyError, TypeError) as e:
            status, http, error = "INVALID", 422, f"INPUT_INVALID:{type(e).__name__}"
        usage = dict(outcome.usage) if outcome else {}
        usage.update(queue_ms=stats.queue_ms, preempted=stats.preempted)
        log_event(log, "llm_task", task=task, lane=spec.lane, status=status, error_code=error,
                  trace_id=env.trace_id, latency_ms=int((time.perf_counter() - t0) * 1000),
                  prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                  queue_ms=stats.queue_ms, preempted=stats.preempted,
                  llm_calls=(outcome.attempts.get("llm_calls", 0) if outcome else 0))
        body = {"status": status, "output": outcome.output if outcome and status == "OK" else {},
                "attempts": outcome.attempts if outcome else {}, "flags": outcome.flags if outcome else {},
                "usage": usage, "manifest_hash": st.manifest_hash, "error": error}
        return JSONResponse(body, status_code=http)

    @app.post("/v1/stt", dependencies=[Depends(require_token)])
    async def stt(request: Request, purpose: str = "answer"):
        if purpose not in ("answer", "precheck"):
            return _err(422, "PURPOSE_INVALID")
        if not request.headers.get("content-type", "").lower().startswith("audio/l16"):
            return _err(415, "UNSUPPORTED_MEDIA_TYPE", "audio/L16; rate=16000; channels=1")
        pcm = await request.body()
        if len(pcm) > MAX_STT_SECONDS * STT_RATE * 2:
            return _err(413, "AUDIO_TOO_LONG")
        if len(pcm) % 2:
            return _err(422, "PCM_INVALID")
        if not engines.stt.up:
            return _err(503, "STT_UNAVAILABLE")
        if engines.gpu_policy == "stt_preempts_batch" and engines.stt.active.get("device") == "cuda":
            async with engines.gateway.pause_lane("BATCH"):
                out = await engines.stt.transcribe(pcm)
        else:
            out = await engines.stt.transcribe(pcm)
        log_event(log, "stt", model=out.get("model"), duration_ms=len(pcm) * 1000 // (STT_RATE * 2),
                  latency_ms=out.get("elapsed_ms"), count=len(out.get("dropped", [])))
        return out

    @app.post("/v1/tts", dependencies=[Depends(require_token)])
    async def tts(req: TTSRequest):
        if not engines.tts.up:
            return _err(503, "TTS_UNAVAILABLE")
        t0 = time.perf_counter()
        pcm, dur = await engines.tts.synthesize(req.text.strip())
        log_event(log, "tts", duration_ms=dur, latency_ms=int((time.perf_counter() - t0) * 1000))
        voice = engines.tts.describe()
        return Response(content=pcm, media_type="audio/L16; rate=48000; channels=1",
                        headers={"X-Duration-Ms": str(dur), "X-Voice-Model": f"{voice['model']}:{voice['voice']}"})

    @app.post("/v1/embed", dependencies=[Depends(require_token)])
    async def embed(req: EmbedRequest):
        try:
            vectors = await engines.backend.embed(req.texts)
        except LLMUnavailable:
            return _err(503, "EMBED_UNAVAILABLE")
        return {"vectors": vectors, "model": engines.embed_model, "dim": len(vectors[0]) if vectors else 0}

    return app


def default_engines() -> Engines:
    from app.aisvc.ollama import OllamaBackend
    from app.aisvc.speech import STTEngine, TTSEngine
    from app.core.config import get_settings

    s = get_settings()
    backend = OllamaBackend(s.ollama_url, s.llm_model, s.embed_model, s.llm_num_ctx)
    stt = STTEngine(model_name=s.stt_model, device=s.stt_device, compute_type=s.stt_compute_type,
                    download=s.model_auto_download)
    tts = TTSEngine(voice=s.tts_voice, steps=s.tts_steps, speed=s.tts_speed, download=s.model_auto_download)
    if s.tts_engine == "none":
        tts.load = _noop  # type: ignore[method-assign]
    return Engines(backend=backend, stt=stt, tts=tts, gateway=Gateway(chat=backend.chat), embed_model=s.embed_model,
                   gpu_policy=s.gpu_policy)


async def _noop() -> None:
    return None
