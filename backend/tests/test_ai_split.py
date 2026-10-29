"""ADR-0014 split AI stack: llama.cpp backend, lane routing (RT/INTERACTIVE -> llm-rt, BATCH -> llm-eval), the
stt / tts speech servers and the aggregated health. No models: HTTP doubles stand in for the servers."""
import json

import httpx
import pytest

from app.ai.client import AIUnavailable, HttpAIClient
from app.ai.direct import DirectAIClient
from app.ai.fake import FakeAIClient
from app.aisvc.gateway import LLMUnavailable
from app.aisvc.llamacpp import LlamaCppBackend
from app.aisvc.ollama import ThinkingLeak
from app.aisvc.speech_server import create_speech_app

TOKEN = "t0ken"


def sse(*chunks: dict) -> bytes:
    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


def llama(handler) -> LlamaCppBackend:
    return LlamaCppBackend("http://llm", name="qwen", transport=httpx.MockTransport(handler))


async def test_llamacpp_chat_streams_with_schema_and_no_thinking():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": '{"a":'}}]},
                                               {"choices": [{"delta": {"content": " 1}"}}]},
                                               {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}))

    content, usage = await llama(handler).chat([{"role": "user", "content": "hi"}], {"type": "object"},
                                               {"temperature": 0.2, "num_predict": 40})
    assert content == '{"a": 1}' and usage["prompt_tokens"] == 7 and usage["completion_tokens"] == 3
    assert seen["stream"] is True and seen["max_tokens"] == 40
    assert seen["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}


async def test_llamacpp_reasoning_and_outage():
    think = llama(lambda r: httpx.Response(200, content=sse({"choices": [{"delta": {"reasoning_content": "hmm"}}]})))
    with pytest.raises(ThinkingLeak):
        await think.chat([], {}, {})
    with pytest.raises(LLMUnavailable):
        await llama(lambda r: httpx.Response(503, json={"error": "loading"})).chat([], {}, {})


async def test_llamacpp_embeddings_and_status():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.0, 1.0]},
                                                      {"index": 0, "embedding": [1.0, 0.0]}]})
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"model_path": "/models/bge-m3-q8_0.gguf",
                                         "default_generation_settings": {"n_ctx": 8192}})

    b = llama(handler)
    assert await b.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    st = await b.status()
    assert st["up"] and st["digest"] == "bge-m3-q8_0.gguf:ctx8192"
    down = await llama(lambda r: httpx.Response(503, json={"error": "loading"})).status()
    assert down["up"] is False


class TaskBackend:
    """Answers with the fake client's schema-valid reply for the task named in `task` (set by the test)."""

    def __init__(self, label: str, calls: list) -> None:
        self.label, self.calls, self.task, self.inp, self.up = label, calls, None, None, True
        self.fake = FakeAIClient()

    async def chat(self, messages, schema, options):
        if not self.up:
            raise LLMUnavailable("down")
        self.calls.append(self.label)
        return json.dumps(self.fake._default(self.task, self.inp), ensure_ascii=False), {"prompt_tokens": 1}

    async def embed(self, texts):
        return [[1.0] * 1024 for _ in texts]

    async def status(self):
        return {"up": self.up, "loaded": self.up, "model": self.label, "digest": f"{self.label}.gguf"}


def speech_client(kind: str, engine) -> HttpAIClient:
    app = create_speech_app(kind, engine, token=TOKEN, load=False)
    return HttpAIClient(base_url="http://speech", token=TOKEN, transport=httpx.ASGITransport(app=app))


class Stt:
    up, degraded = True, False
    active = {"model": "large-v3-turbo", "device": "cuda"}

    async def transcribe(self, pcm):
        return {"text": "안녕하세요", "segments": [], "dropped": [], "model": "large-v3-turbo", "device": "cuda",
                "elapsed_ms": 3}

    def describe(self):
        return {"engine": "faster-whisper", "model": "large-v3-turbo"}


class Tts:
    up = True

    async def synthesize(self, text):
        return bytes(960), 10

    def describe(self):
        return {"engine": "supertonic", "model": "supertonic-3", "voice": "F1"}


def direct(calls: list) -> DirectAIClient:
    return DirectAIClient(rt=TaskBackend("rt", calls), eval_=TaskBackend("eval", calls),
                          embedder=TaskBackend("embed", calls), stt=speech_client("stt", Stt()),
                          tts=speech_client("tts", Tts()))


async def test_lanes_go_to_their_server_and_health_has_every_part():
    calls: list = []
    ai = direct(calls)
    h = await ai.health()
    assert {k: v["up"] for k, v in h.items() if isinstance(v, dict) and "up" in v} == \
        {"llm": True, "llm_eval": True, "stt": True, "tts": True, "embed": True}
    assert h["ready"] and h["manifest_hash"]

    inp = {"source_text": "김지원\n커머스랩 백엔드 개발자 2020-2024 주문 API 개발"}
    ai.rt.task, ai.rt.inp = "resume_extract", inp
    r = await ai.llm("resume_extract", inp, lane="INTERACTIVE")
    assert r.status == "OK" and calls == ["rt"] and r.manifest_hash == h["manifest_hash"]

    ev_inp = {"question": "성능 문제를 해결한 경험을 말씀해 주세요.", "question_type": "JOB", "job_brief": "백엔드",
              "answer": "주문 API 응답 지연을 인덱스 추가와 배치 조회로 줄여 평균 응답 시간을 절반으로 낮췄습니다."}
    ai.eval.task, ai.eval.inp = "evaluate_answer", ev_inp
    r = await ai.llm("evaluate_answer", ev_inp, lane="BATCH")
    assert r.status == "OK" and calls[-1] == "eval"

    assert (await ai.stt(bytes(3200))).text == "안녕하세요"
    assert (await ai.tts("질문")).duration_ms == 10


async def test_a_down_server_defers_its_lane_only():
    calls: list = []
    ai = direct(calls)
    ai.eval.up = False
    h = await ai.health()
    assert h["llm"]["up"] and not h["llm_eval"]["up"]
    with pytest.raises(AIUnavailable):
        await ai.llm("evaluate_answer", {"question": "q", "question_type": "JOB", "job_brief": "",
                                         "answer": "충분히 긴 답변 내용이 여기에 들어 있습니다. 성능 개선 경험입니다."}, lane="BATCH")


async def test_speech_server_rejects_missing_token():
    app = create_speech_app("stt", Stt(), token=TOKEN, load=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as c:
        assert (await c.get("/readyz")).status_code == 200
        r = await c.post("/v1/stt", content=bytes(320), headers={"Content-Type": "audio/L16; rate=16000"})
        assert r.status_code == 401
