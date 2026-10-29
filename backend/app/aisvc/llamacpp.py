"""llama.cpp `llama-server` backend (containers llm-rt / llm-eval / embed in deploy/docker-compose.yml).

Same surface as OllamaBackend (chat / embed / status / warmup), so the gateway and the task validators are unchanged.
- chat: OpenAI-compatible `/v1/chat/completions`, streamed so closing the connection stops generation (preemption,
  deadlines), the task's JSON Schema as `response_format` (grammar-constrained output), thinking switched off through
  the chat template (`enable_thinking: false`). Reasoning text that still appears is treated as INVALID.
- embed: `/v1/embeddings` (BGE-M3 server started with --embeddings).
- status: `/health` (200 = model loaded) and `/props` (model file, context size).
"""
import json

import httpx

from app.aisvc.gateway import LLMUnavailable
from app.aisvc.ollama import ThinkingLeak


class LlamaCppBackend:
    def __init__(self, base_url: str, *, name: str = "", transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name  # model label for the manifest (EVL-08); the server reports its file too
        self._transport = transport

    def _client(self, timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport)

    async def chat(self, messages: list[dict], schema: dict, options: dict) -> tuple[str, dict]:
        body = {"messages": messages, "stream": True, "temperature": options.get("temperature", 0.0),
                "max_tokens": options.get("num_predict", 512), "seed": options.get("seed", 42), "cache_prompt": True,
                "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}},
                "chat_template_kwargs": {"enable_thinking": False}, "stream_options": {"include_usage": True}}
        parts: list[str] = []
        thinking = False
        usage: dict = {}
        try:
            async with self._client(httpx.Timeout(None, connect=5.0)) as c, \
                    c.stream("POST", "/v1/chat/completions", json=body) as r:
                if r.status_code >= 500 or r.status_code == 404:
                    await r.aread()
                    raise LLMUnavailable(f"llama.cpp chat HTTP {r.status_code}")
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        raise LLMUnavailable("llama.cpp error")
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            thinking = True
                        if delta.get("content"):
                            parts.append(delta["content"])
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    if chunk.get("timings"):
                        usage = usage | {"timings": chunk["timings"]}
        except httpx.TransportError as e:
            raise LLMUnavailable(type(e).__name__) from e
        content = "".join(parts)
        if thinking or "<think>" in content:
            raise ThinkingLeak()
        t = usage.get("timings") or {}
        return content, {"prompt_tokens": usage.get("prompt_tokens", 0),
                         "completion_tokens": usage.get("completion_tokens", 0),
                         "prefill_ms": int(t.get("prompt_ms", 0)), "gen_ms": int(t.get("predicted_ms", 0))}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            async with self._client(120.0) as c:
                r = await c.post("/v1/embeddings", json={"input": texts})
        except httpx.TransportError as e:
            raise LLMUnavailable(type(e).__name__) from e
        if r.status_code >= 500 or r.status_code == 404:
            raise LLMUnavailable(f"llama.cpp embeddings HTTP {r.status_code}")
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]

    async def status(self) -> dict:
        """{up, loaded, model, digest}: the server answers /health 200 only once the model is loaded."""
        try:
            async with self._client(3.0) as c:
                h = await c.get("/health")
                props = (await c.get("/props")).json() if h.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return {"up": False, "loaded": False, "model": self.name, "digest": None}
        model_file = str(props.get("model_path", "")).rsplit("/", 1)[-1]
        ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
        return {"up": h.status_code == 200, "loaded": h.status_code == 200, "full_gpu": None,
                "model": self.name or model_file, "digest": f"{model_file}:ctx{ctx}" if model_file else None,
                "embed_available": True}

    async def warmup(self) -> None:
        await self.chat([{"role": "user", "content": "ok"}], {"type": "object", "properties": {}},
                        {"num_predict": 1, "temperature": 0})
