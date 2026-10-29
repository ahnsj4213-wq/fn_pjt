"""The `stt` and `tts` containers of the split stack (deploy/docker-compose.yml, ADR-0014).

    python -m app.aisvc.speech_server stt     faster-whisper (GPU, CPU `small` fallback)   :8201
    python -m app.aisvc.speech_server tts     Supertonic (CPU)                             :8202

Same request/response contract as the single ai service (`/v1/stt`, `/v1/tts`, X-AI-Token), so HttpAIClient talks to
either. `/readyz` answers 503 until the model is loaded; `/v1/health` reports the part for DirectAIClient.health().
"""
import argparse
import asyncio
import hmac
import logging
import os
import time
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.aisvc.offline import offline_env
from app.aisvc.speech import MAX_STT_SECONDS, STT_RATE
from app.core.logging import log_event

log = logging.getLogger("app.aisvc")
MAX_TTS_CHARS = 500


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    voice: str = "default"


def _err(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_speech_app(kind: str, engine, *, token: str, load: bool = True) -> FastAPI:
    """`engine`: STTEngine / TTSEngine, or a test double with the same surface (load, up, transcribe/synthesize,
    describe, active for STT)."""
    assert kind in ("stt", "tts")

    def require_token(x_ai_token: str = Header("")) -> None:
        if not hmac.compare_digest(x_ai_token.encode(), token.encode()):
            raise PermissionError

    async def lifespan(app: FastAPI):
        task = asyncio.create_task(engine.load()) if load else None
        yield
        if task is not None and not task.done():
            task.cancel()

    app = FastAPI(title=f"aihire-{kind}", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(PermissionError)
    async def unauthorized(_r, _e):
        return _err(401, "UNAUTHORIZED")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz():
        return {"ready": True} if engine.up else _err(503, "NOT_READY")

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    async def health():
        d = engine.describe()
        if kind == "stt":
            return {"stt": {"up": engine.up, "model": engine.active.get("model"), "device": engine.active.get("device"),
                            "degraded": getattr(engine, "degraded", False)}}
        return {"tts": {"up": engine.up}, "engine": d.get("engine"), "voice": d.get("voice")}

    if kind == "stt":
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
            if not engine.up:
                return _err(503, "STT_UNAVAILABLE")
            out = await engine.transcribe(pcm)
            log_event(log, "stt", model=out.get("model"), duration_ms=len(pcm) * 1000 // (STT_RATE * 2),
                      latency_ms=out.get("elapsed_ms"), count=len(out.get("dropped", [])))
            return out
    else:
        @app.post("/v1/tts", dependencies=[Depends(require_token)])
        async def tts(req: TTSRequest):
            if not engine.up:
                return _err(503, "TTS_UNAVAILABLE")
            t0 = time.perf_counter()
            pcm, dur = await engine.synthesize(req.text.strip())
            log_event(log, "tts", duration_ms=dur, latency_ms=int((time.perf_counter() - t0) * 1000))
            voice = engine.describe()
            return Response(content=pcm, media_type="audio/L16; rate=48000; channels=1",
                            headers={"X-Duration-Ms": str(dur), "X-Voice-Model": f"{voice['model']}:{voice['voice']}"})

    return app


def main() -> None:
    from app.core.config import get_settings

    ap = argparse.ArgumentParser(prog="python -m app.aisvc.speech_server")
    ap.add_argument("kind", choices=["stt", "tts"])
    kind = ap.parse_args().kind
    s = get_settings()
    os.environ.update(offline_env(s.model_auto_download))  # before the model libraries load

    import uvicorn

    from app.aisvc.speech import STTEngine, TTSEngine
    from app.core.logging import configure_logging

    configure_logging()
    if kind == "stt":
        engine = STTEngine(model_name=s.stt_model, device=s.stt_device, compute_type=s.stt_compute_type,
                           download=s.model_auto_download)
        url = s.stt_url
    else:
        engine = TTSEngine(voice=s.tts_voice, steps=s.tts_steps, speed=s.tts_speed, download=s.model_auto_download)
        url = s.tts_url
    u = urlparse(url)
    uvicorn.run(create_speech_app(kind, engine, token=s.ai_token.get_secret_value()),
                host=os.environ.get("AIHIRE_BIND_HOST", "0.0.0.0"), port=u.port or (8201 if kind == "stt" else 8202),
                log_config=None, access_log=False)
    os._exit(0)  # a native call stuck in CUDA/ONNX must not keep the process alive after shutdown


if __name__ == "__main__":
    main()
