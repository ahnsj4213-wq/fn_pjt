"""Ollama HTTP backend (TRD 05 §2, §5.2). Request arguments only — the user's Ollama service settings are never
changed (T3). Every chat: stream=true (so the gateway can cut it), keep_alive=-1 (no 40 s cold reload), a fixed
num_ctx (a different value reloads the model, ~5 s), think=false, format = the task's JSON Schema.
"""
import json

import httpx

from app.aisvc.gateway import LLMUnavailable


def _canon(name: str) -> str:
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def _same_model(entry: dict, wanted: str) -> bool:
    """Ollama lists a model under `name` and `model`; an untagged name means `:latest`."""
    return any(_canon(entry.get(k) or "") == _canon(wanted) for k in ("name", "model") if entry.get(k))


class ThinkingLeak(Exception):
    """The model returned reasoning text although think=false: the output is treated as INVALID."""


class OllamaBackend:
    def __init__(self, base_url: str, model: str, embed_model: str, num_ctx: int,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model, self.embed_model, self.num_ctx = model, embed_model, num_ctx
        self._transport = transport

    def _client(self, timeout: float | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport)

    async def chat(self, messages: list[dict], schema: dict, options: dict) -> tuple[str, dict]:
        body = {"model": self.model, "messages": messages, "stream": True, "think": False, "keep_alive": -1,
                "format": schema, "options": {**options, "num_ctx": self.num_ctx}}
        parts: list[str] = []
        thinking = False
        final: dict = {}
        try:
            async with self._client(httpx.Timeout(None, connect=5.0)) as c, \
                    c.stream("POST", "/api/chat", json=body) as r:
                if r.status_code >= 500 or r.status_code == 404:
                    await r.aread()
                    raise LLMUnavailable(f"ollama chat HTTP {r.status_code}")
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise LLMUnavailable("ollama error")
                    msg = chunk.get("message") or {}
                    if msg.get("thinking"):
                        thinking = True
                    if msg.get("content"):
                        parts.append(msg["content"])
                    if chunk.get("done"):
                        final = chunk
        except httpx.TransportError as e:
            raise LLMUnavailable(type(e).__name__) from e
        content = "".join(parts)
        if thinking or "<think>" in content:
            raise ThinkingLeak()
        usage = {"prompt_tokens": final.get("prompt_eval_count", 0), "completion_tokens": final.get("eval_count", 0),
                 "prefill_ms": int(final.get("prompt_eval_duration", 0) / 1e6),
                 "gen_ms": int(final.get("eval_duration", 0) / 1e6)}
        return content, usage

    async def embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self.embed_model, "input": texts, "keep_alive": -1, "options": {"num_gpu": 0}}
        try:
            async with self._client(120.0) as c:
                r = await c.post("/api/embed", json=body)
        except httpx.TransportError as e:
            raise LLMUnavailable(type(e).__name__) from e
        if r.status_code >= 500 or r.status_code == 404:
            raise LLMUnavailable(f"ollama embed HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()["embeddings"]

    async def status(self) -> dict:
        """{up, loaded, full_gpu, model, digest} from /api/tags and /api/ps (size_vram == size means 100% on GPU)."""
        try:
            async with self._client(3.0) as c:
                tags = (await c.get("/api/tags")).json().get("models", [])
                ps = (await c.get("/api/ps")).json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return {"up": False, "loaded": False, "full_gpu": False, "model": self.model, "digest": None}
        digest = next((m.get("digest") for m in tags if _same_model(m, self.model)), None)
        run = next((m for m in ps if _same_model(m, self.model)), None)
        return {"up": digest is not None, "loaded": run is not None,
                "full_gpu": bool(run) and run.get("size_vram", 0) >= run.get("size", 1),
                "model": self.model, "digest": digest,
                "embed_available": any(_same_model(m, self.embed_model) for m in tags)}

    async def warmup(self) -> None:
        """Load the LLM with the fixed num_ctx before declaring ready (cold load ~40 s, TRD 05 §3)."""
        await self.chat([{"role": "user", "content": "ok"}], {"type": "object", "properties": {}},
                        {"num_predict": 1, "temperature": 0})
