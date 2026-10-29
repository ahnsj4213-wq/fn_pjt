"""Stand-in for the ai process with no models and no GPU (deploy/docker-compose.verify.yml, E2E checks).

Serves the same HTTP API as `python -m app.aisvc` (`/v1/llm/{task}`, `/v1/stt`, `/v1/tts`, `/v1/embed`, `/v1/health`,
`/healthz`, `/readyz`) backed by app.ai.fake.FakeAIClient, whose replies still pass the real task validators.
STT "hears" a fixed sentence for any audio (a fake microphone only beeps). Never use it for real interviews.

    python scripts/fake_ai_service.py [--host 0.0.0.0] [--port 8200]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from app.ai.client import STTResult, TTSResult
from app.ai.fake import FakeAIClient
from app.core.config import get_settings

PRECHECK_TEXT = "안녕하세요"
ANSWER_TEXT = "주문 API의 응답 지연을 복합 인덱스와 배치 조회로 줄였고 평균 응답 시간이 절반으로 줄었습니다."


def create_app() -> FastAPI:
    token = get_settings().ai_token.get_secret_value()
    fake = FakeAIClient()

    async def stt(pcm16k: bytes, *, purpose: str = "answer") -> STTResult:
        said = PRECHECK_TEXT if purpose == "precheck" else ANSWER_TEXT
        return STTResult(text=said, segments=[{"start": 0.0, "end": len(pcm16k) / 32000, "text": said}],
                         model="fake-stt", device="cpu")

    async def tts(_text: str) -> TTSResult:
        return TTSResult(pcm48k=bytes(19200 * 2), duration_ms=400, voice_model="fake-tts")

    fake.stt, fake.tts = stt, tts

    def require_token(x_ai_token: str = Header("")) -> None:
        if x_ai_token != token:
            raise PermissionError

    app = FastAPI(title="aihire-ai-fake", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(PermissionError)
    async def unauthorized(_r, _e):
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": ""}}, status_code=401)

    @app.get("/healthz")
    @app.get("/readyz")
    async def ready():
        return {"status": "ok", "fake": True}

    @app.get("/v1/health", dependencies=[Depends(require_token)])
    async def health():
        return await fake.health() | {"fake": True}

    @app.post("/v1/llm/{task}", dependencies=[Depends(require_token)])
    async def llm(task: str, request: Request):
        body = await request.json()
        r = await fake.llm(task, body["input"], lane=body["lane"], deadline_ms=body.get("deadline_ms"))
        return {"status": r.status, "output": r.output, "attempts": r.attempts, "flags": r.flags, "usage": r.usage,
                "manifest_hash": r.manifest_hash, "error": r.error}

    @app.post("/v1/stt", dependencies=[Depends(require_token)])
    async def stt_route(request: Request, purpose: str = "answer"):
        r = await fake.stt(await request.body(), purpose=purpose)
        return {"text": r.text, "segments": r.segments, "dropped": [], "model": r.model, "device": r.device,
                "elapsed_ms": 1}

    @app.post("/v1/tts", dependencies=[Depends(require_token)])
    async def tts_route(request: Request):
        r = await fake.tts((await request.json())["text"])
        return Response(r.pcm48k, media_type="audio/L16; rate=48000; channels=1",
                        headers={"X-Duration-Ms": str(r.duration_ms), "X-Voice-Model": r.voice_model})

    @app.post("/v1/embed", dependencies=[Depends(require_token)])
    async def embed(request: Request):
        vectors = await fake.embed((await request.json())["texts"])
        return {"vectors": vectors, "model": "fake-embed", "dim": len(vectors[0]) if vectors else 0}

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8200)
    a = ap.parse_args()
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")
