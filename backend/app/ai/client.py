"""Client for the `ai` service (TRD 05 §4). app and worker never call Ollama/Whisper/Supertonic directly (ADR-0003).

    AIClient.llm(task, input, *, lane, deadline_ms=None) -> LLMResult
    AIClient.stt(pcm16k, *, purpose)                      -> STTResult
    AIClient.tts(text)                                    -> TTSResult
    AIClient.embed(texts)                                 -> list[list[float]]
    AIClient.health()                                     -> dict

`AIUnavailable` means the infrastructure is down (connection refused, 503): the worker defers the job without
consuming an attempt (OQ-23, AC-18) and the interview skips the follow-up (cause INFRA). A model that answered but
produced nothing usable comes back as an LLMResult with status INVALID / TIMEOUT instead.
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import get_settings

TOKEN_HEADER = "X-AI-Token"
# Default per-call deadline when the caller gives none (TRD 05 §5.2-5.3).
LANE_DEFAULT_DEADLINE_SEC = {"RT": 5.0, "INTERACTIVE": 120.0, "BATCH": 600.0}


class AIUnavailable(Exception):
    """The ai service or the model behind it is down. Retry later; never counts as a failed attempt."""


@dataclass
class LLMResult:
    status: str  # OK | INVALID | TIMEOUT | LLM_UNAVAILABLE
    output: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    manifest_hash: str = ""
    attempts: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class STTResult:
    text: str
    segments: list[dict]
    model: str
    device: str = ""
    dropped: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


@dataclass
class TTSResult:
    pcm48k: bytes  # signed 16-bit little-endian mono, 48 kHz (WebRTC downlink track)
    duration_ms: int
    voice_model: str


class AIClient(Protocol):
    async def llm(self, task: str, input: dict, *, lane: str, deadline_ms: int | None = None) -> LLMResult: ...
    async def stt(self, pcm16k: bytes, *, purpose: str = "answer") -> STTResult: ...
    async def tts(self, text: str) -> TTSResult: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def health(self) -> dict: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


class HttpAIClient:
    """Talks to `python -m app.aisvc` over the internal network with the shared token (SEC-06)."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.ai_service_url).rstrip("/")
        self._headers = {TOKEN_HEADER: token if token is not None else s.ai_token.get_secret_value()}
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self._headers, timeout=timeout,
                                 transport=self._transport)

    async def _post(self, path: str, timeout: float, **kw) -> httpx.Response:
        try:
            async with self._client(timeout) as c:
                return await c.post(path, **kw)
        except httpx.TimeoutException as e:
            raise TimeoutError(path) from e
        except httpx.TransportError as e:
            raise AIUnavailable(f"{path}: {type(e).__name__}") from e

    async def llm(self, task: str, input: dict, *, lane: str, deadline_ms: int | None = None) -> LLMResult:
        if deadline_ms is None:
            deadline_ms = _now_ms() + int(LANE_DEFAULT_DEADLINE_SEC[lane] * 1000)
        body = {"task_version": task, "lane": lane, "deadline_ms": deadline_ms, "trace_id": str(uuid.uuid4()),
                "input": input}
        while True:
            remaining = (deadline_ms - _now_ms()) / 1000
            if remaining <= 0:
                return LLMResult(status="TIMEOUT", error="DEADLINE_EXCEEDED")
            try:
                r = await self._post(f"/v1/llm/{task}", remaining + 2.0, json=body)
            except TimeoutError:
                return LLMResult(status="TIMEOUT", error="DEADLINE_EXCEEDED")
            if r.status_code == 503:
                raise AIUnavailable(_code(r))
            if r.status_code == 409 and _code(r) == "PREEMPTED":
                continue  # the gateway normally requeues by itself; resubmitting never consumes an attempt
            data = _json(r)
            if r.status_code in (200, 422, 504) and "status" in data:
                return LLMResult(status=data["status"], output=data.get("output") or {}, flags=data.get("flags") or {},
                                 usage=data.get("usage") or {}, manifest_hash=data.get("manifest_hash", ""),
                                 attempts=data.get("attempts") or {}, error=data.get("error"))
            raise RuntimeError(f"ai /v1/llm/{task}: HTTP {r.status_code} {_code(r)}")

    async def stt(self, pcm16k: bytes, *, purpose: str = "answer") -> STTResult:
        r = await self._post("/v1/stt", 60.0, params={"purpose": purpose}, content=pcm16k,
                             headers={"Content-Type": "audio/L16; rate=16000; channels=1"})
        if r.status_code == 503:
            raise AIUnavailable(_code(r))
        r.raise_for_status()
        d = r.json()
        return STTResult(text=d["text"], segments=d.get("segments", []), model=d.get("model", ""),
                         device=d.get("device", ""), dropped=d.get("dropped", []), elapsed_ms=d.get("elapsed_ms", 0))

    async def tts(self, text: str) -> TTSResult:
        r = await self._post("/v1/tts", 60.0, json={"text": text, "voice": "default"})
        if r.status_code == 503:
            raise AIUnavailable(_code(r))
        r.raise_for_status()
        return TTSResult(pcm48k=r.content, duration_ms=int(r.headers.get("X-Duration-Ms", "0")),
                         voice_model=r.headers.get("X-Voice-Model", ""))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        r = await self._post("/v1/embed", 120.0, json={"texts": texts})
        if r.status_code == 503:
            raise AIUnavailable(_code(r))
        r.raise_for_status()
        return r.json()["vectors"]

    async def health(self) -> dict:
        try:
            async with self._client(5.0) as c:
                r = await c.get("/v1/health")
        except httpx.HTTPError as e:
            raise AIUnavailable(f"health: {type(e).__name__}") from e
        r.raise_for_status()
        return r.json()


def _json(r: httpx.Response) -> dict:
    try:
        d = r.json()
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _code(r: httpx.Response) -> str:
    d = _json(r)
    err = d.get("error")
    if isinstance(err, dict):
        return str(err.get("code", ""))
    return str(err or d.get("detail") or "")


_client: AIClient | None = None


def get_ai_client() -> AIClient:
    global _client
    if _client is None:
        _client = HttpAIClient()
    return _client


def set_ai_client(client: AIClient | None) -> None:
    """Tests and the worker swap in a FakeAIClient (app.ai.fake). None resets to the HTTP client."""
    global _client
    _client = client
