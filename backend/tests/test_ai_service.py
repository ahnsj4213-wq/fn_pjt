"""ai service (TRD 05 §4-§5, ADR-0003/0004): gateway lanes, preemption and deadlines; HTTP contract and token;
the HTTP client's error mapping; the deterministic fake client; STT/TTS helpers. No models are needed."""
import asyncio
import json
import time

import httpx
import numpy as np
import pytest

from app.ai.client import AIUnavailable, HttpAIClient
from app.ai.fake import FakeAIClient
from app.ai.tasks import TASKS
from app.aisvc.gateway import CallStats, DeadlineExceeded, Gateway, LLMUnavailable
from app.aisvc.service import Engines, create_app
from app.aisvc.speech import TTS_RATE, is_hallucination, resample_to_s16

TOKEN = "test-ai-token"
ANSWER = ("저는 주문 처리 시스템에서 캐시를 도입해 응답 시간을 800밀리초에서 200밀리초로 줄였습니다. "
          "병목을 찾기 위해 APM으로 쿼리를 분석했고 인덱스를 추가했습니다.")


def now_ms(sec: float = 0.0) -> int:
    return int((time.time() + sec) * 1000)


# ── gateway ────────────────────────────────────────────────────────────────────
class ScriptedChat:
    """chat() that takes `delay` seconds and records start/finish/cancel order."""

    def __init__(self, delays: dict[str, float] | None = None) -> None:
        self.delays = delays or {}
        self.log: list[str] = []
        self.down = False

    async def __call__(self, messages, schema, options):
        tag = messages[0]["content"]
        if self.down:
            raise LLMUnavailable("refused")
        self.log.append(f"start:{tag}")
        try:
            await asyncio.sleep(self.delays.get(tag, 0.01))
        except asyncio.CancelledError:
            self.log.append(f"cut:{tag}")
            raise
        self.log.append(f"done:{tag}")
        return json.dumps({"tag": tag}), {"completion_tokens": 1}


def msg(tag: str) -> list[dict]:
    return [{"role": "user", "content": tag}]


async def test_gateway_rt_preempts_batch_and_batch_resumes_first_in_lane():
    chat = ScriptedChat({"batch1": 0.4, "batch2": 0.05, "rt": 0.05})
    gw = Gateway(chat=chat)
    s1, s2, s3 = CallStats(), CallStats(), CallStats()
    b1 = asyncio.create_task(gw.call("BATCH", msg("batch1"), {}, {}, deadline_ms=now_ms(10), stats=s1))
    await asyncio.sleep(0.05)
    b2 = asyncio.create_task(gw.call("BATCH", msg("batch2"), {}, {}, deadline_ms=now_ms(10), stats=s2))
    await asyncio.sleep(0.02)
    rt = await gw.call("RT", msg("rt"), {}, {}, deadline_ms=now_ms(5), stats=s3)
    assert json.loads(rt[0]) == {"tag": "rt"}
    await asyncio.gather(b1, b2)
    # batch1 was cut, rt ran, then batch1 went back to the FRONT of BATCH (before batch2)
    assert chat.log == ["start:batch1", "cut:batch1", "start:rt", "done:rt", "start:batch1", "done:batch1",
                        "start:batch2", "done:batch2"]
    assert s1.preempted == 1 and s2.preempted == 0 and s3.preempted == 0
    assert gw.snapshot() == {"running": None, "waiting": {"RT": 0, "INTERACTIVE": 0, "BATCH": 0}, "paused": []}


async def test_gateway_interactive_waits_behind_rt_but_preempts_batch():
    chat = ScriptedChat({"rt": 0.15, "batch": 0.3, "ia": 0.02})
    gw = Gateway(chat=chat)
    batch = asyncio.create_task(gw.call("BATCH", msg("batch"), {}, {}, deadline_ms=now_ms(10), stats=CallStats()))
    await asyncio.sleep(0.03)
    rt = asyncio.create_task(gw.call("RT", msg("rt"), {}, {}, deadline_ms=now_ms(5), stats=CallStats()))
    await asyncio.sleep(0.03)
    ia = asyncio.create_task(gw.call("INTERACTIVE", msg("ia"), {}, {}, deadline_ms=now_ms(10), stats=CallStats()))
    await asyncio.gather(batch, rt, ia)
    assert chat.log == ["start:batch", "cut:batch", "start:rt", "done:rt", "start:ia", "done:ia",
                        "start:batch", "done:batch"]


async def test_gateway_deadline_while_waiting_and_while_running():
    chat = ScriptedChat({"long": 0.5, "rt": 0.5})
    gw = Gateway(chat=chat)
    long = asyncio.create_task(gw.call("RT", msg("long"), {}, {}, deadline_ms=now_ms(5), stats=CallStats()))
    await asyncio.sleep(0.02)
    # Same lane: no preemption. 0.1 s budget cannot cover the wait -> DeadlineExceeded without running.
    with pytest.raises(DeadlineExceeded):
        await gw.call("RT", msg("rt"), {}, {}, deadline_ms=now_ms(0.1), stats=CallStats(), est_default_ms=10)
    await long
    assert "start:rt" not in chat.log
    # Running past the deadline is cut.
    with pytest.raises(DeadlineExceeded):
        await gw.call("RT", msg("rt"), {}, {}, deadline_ms=now_ms(0.1), stats=CallStats(), est_default_ms=10)
    assert chat.log[-2:] == ["start:rt", "cut:rt"]
    # Expected run time alone exceeds the budget -> refused immediately.
    with pytest.raises(DeadlineExceeded):
        await gw.call("RT", msg("x"), {}, {}, deadline_ms=now_ms(0.5), stats=CallStats(), est_default_ms=2000)


async def test_gateway_unavailable_fails_fast_then_recovers():
    chat = ScriptedChat()
    clock = [1000.0]
    gw = Gateway(chat=chat, clock=lambda: clock[0])
    chat.down = True
    with pytest.raises(LLMUnavailable):
        await gw.call("BATCH", msg("a"), {}, {}, deadline_ms=10**15, stats=CallStats())
    assert gw.is_down()
    chat.down = False
    with pytest.raises(LLMUnavailable):  # within 15 s: refused without touching the model (OQ-23)
        await gw.call("BATCH", msg("b"), {}, {}, deadline_ms=10**15, stats=CallStats())
    assert "start:b" not in chat.log
    clock[0] += 16
    await gw.call("BATCH", msg("c"), {}, {}, deadline_ms=10**15, stats=CallStats())
    assert not gw.is_down()


async def test_gateway_pause_lane_holds_batch_out():
    chat = ScriptedChat({"batch": 0.2})
    gw = Gateway(chat=chat)
    batch = asyncio.create_task(gw.call("BATCH", msg("batch"), {}, {}, deadline_ms=now_ms(10), stats=CallStats()))
    await asyncio.sleep(0.03)
    async with gw.pause_lane("BATCH"):  # STT on the shared GPU (gpu_policy=stt_preempts_batch)
        await asyncio.sleep(0.1)
        assert chat.log == ["start:batch", "cut:batch"]
    await batch
    assert chat.log[-2:] == ["start:batch", "done:batch"]


async def test_gateway_caller_cancel_frees_slot():
    chat = ScriptedChat({"slow": 1.0, "next": 0.01})
    gw = Gateway(chat=chat)
    slow = asyncio.create_task(gw.call("BATCH", msg("slow"), {}, {}, deadline_ms=now_ms(10), stats=CallStats()))
    await asyncio.sleep(0.03)
    slow.cancel()
    await asyncio.gather(slow, return_exceptions=True)
    await gw.call("BATCH", msg("next"), {}, {}, deadline_ms=now_ms(5), stats=CallStats())
    assert chat.log == ["start:slow", "cut:slow", "start:next", "done:next"]


# ── service over HTTP ──────────────────────────────────────────────────────────
class FakeBackend:
    """LLM backend that answers each task with schema-valid JSON (the same replies the fake client uses)."""

    def __init__(self) -> None:
        self.fake = FakeAIClient()
        self.task_for_next: str | None = None
        self.up = True
        self.delay = 0.0
        self.calls = 0
        self.inputs: dict = {}

    async def chat(self, messages, schema, options):
        if not self.up:
            raise LLMUnavailable("down")
        self.calls += 1
        await asyncio.sleep(self.delay)
        inp = self.inputs[self.task_for_next]
        return json.dumps(self.fake._default(self.task_for_next, inp), ensure_ascii=False), {
            "prompt_tokens": 100, "completion_tokens": 50}

    async def embed(self, texts):
        if not self.up:
            raise LLMUnavailable("down")
        return await self.fake.embed(texts)

    async def status(self):
        return {"up": self.up, "loaded": self.up, "full_gpu": True, "model": "qwen3.5:4b", "digest": "sha256:abc",
                "embed_available": True}

    async def warmup(self):
        return None


class FakeSTT:
    up = True
    degraded = False

    def __init__(self) -> None:
        self.active = {"model": "large-v3-turbo", "device": "cuda", "compute": "int8_float16"}
        self.seen: list[int] = []

    async def load(self):
        return None

    async def transcribe(self, pcm: bytes) -> dict:
        self.seen.append(len(pcm))
        return {"text": "안녕하세요", "segments": [{"start": 0.0, "end": 1.0, "text": "안녕하세요", "no_speech_prob": 0.01}],
                "dropped": [], "model": "large-v3-turbo", "device": "cuda", "elapsed_ms": 5}

    def describe(self):
        return {"engine": "faster-whisper", "model": "large-v3-turbo"}


class FakeTTS:
    up = True

    async def load(self):
        return None

    async def synthesize(self, text: str):
        return bytes(4800 * 2), 100

    def describe(self):
        return {"engine": "supertonic 1.3.1", "model": "supertonic-3", "voice": "F1", "steps": 8, "speed": 1.05}


@pytest.fixture
async def ai_app():
    backend = FakeBackend()
    engines = Engines(backend=backend, stt=FakeSTT(), tts=FakeTTS(), gateway=Gateway(chat=backend.chat),
                      embed_model="qwen3-embedding:0.6b")
    app = create_app(engines, token=TOKEN, warmup=False)
    async with app.router.lifespan_context(app):
        yield app, engines


def http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ai", headers={"X-AI-Token": TOKEN})


def env(task: str, lane: str, inp: dict, deadline: float = 30) -> dict:
    return {"task_version": task, "lane": lane, "deadline_ms": now_ms(deadline), "trace_id": "t1", "input": inp}


async def test_service_requires_token_on_v1(ai_app):
    app, _ = ai_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ai") as c:
        assert (await c.get("/healthz")).status_code == 200
        for method, path in (("GET", "/v1/health"), ("POST", "/v1/llm/evaluate_answer"), ("POST", "/v1/tts")):
            r = await c.request(method, path, headers={"X-AI-Token": "wrong"}, json={})
            assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_service_llm_runs_task_validators_and_reports_manifest(ai_app):
    app, engines = ai_app
    inp = {"question": "성능 문제를 해결한 경험을 말씀해 주세요.", "question_type": "JOB", "job_brief": "백엔드 개발",
           "answer": ANSWER}
    engines.backend.task_for_next, engines.backend.inputs = "evaluate_answer", {"evaluate_answer": inp}
    async with http(app) as c:
        r = await c.post("/v1/llm/evaluate_answer", json=env(TASKS["evaluate_answer"].version, "BATCH", inp))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OK" and body["manifest_hash"].startswith("sha256:")
    item = body["output"]["items"]["EXPERTISE"]
    # The stored quote is the eval_text span (ADR-0008), not the model's string.
    assert item["evidence_status"] == "OK" and ANSWER[item["quote"]["start"]:item["quote"]["end"]] == item["quote"]["text"]
    assert body["usage"]["completion_tokens"] == 50 and body["usage"]["preempted"] == 0


async def test_service_llm_errors(ai_app):
    app, engines = ai_app
    inp = {"question": "q", "answer": ANSWER, "followups_left": 4}
    async with http(app) as c:
        assert (await c.post("/v1/llm/nope", json=env("nope", "RT", inp))).status_code == 404
        r = await c.post("/v1/llm/followup_decide", json=env("followup_decide", "BATCH", inp))
        assert r.status_code == 422 and r.json()["error"]["code"] == "LANE_MISMATCH"
        r = await c.post("/v1/llm/followup_decide", json=env("followup_decide@0.9.0", "RT", inp))
        assert r.status_code == 409 and r.json()["error"]["code"] == "TASK_VERSION_MISMATCH"
        # Missing input key -> INVALID, not a 500.
        r = await c.post("/v1/llm/report_summary", json=env("report_summary", "BATCH", {"overall": "x"}))
        assert r.status_code == 422 and r.json()["error"].startswith("INPUT_INVALID")
        # Deadline already too close for the expected run time -> 504 TIMEOUT.
        engines.backend.task_for_next, engines.backend.inputs = "followup_decide", {"followup_decide": inp}
        r = await c.post("/v1/llm/followup_decide", json=env("followup_decide", "RT", inp, deadline=0.05))
        assert r.status_code == 504 and r.json()["status"] == "TIMEOUT"
        engines.backend.up = False
        r = await c.post("/v1/llm/followup_decide", json=env("followup_decide", "RT", inp))
        assert r.status_code == 503 and r.json()["status"] == "LLM_UNAVAILABLE"


async def test_service_stt_tts_embed_health(ai_app):
    app, engines = ai_app
    async with http(app) as c:
        pcm = bytes(16000 * 2)
        r = await c.post("/v1/stt", content=pcm, headers={"Content-Type": "audio/L16; rate=16000; channels=1"})
        assert r.status_code == 200 and r.json()["text"] == "안녕하세요"
        assert (await c.post("/v1/stt", content=pcm, headers={"Content-Type": "audio/wav"})).status_code == 415
        r = await c.post("/v1/stt", content=bytes(31 * 32000), headers={"Content-Type": "audio/L16"})
        assert r.status_code == 413
        r = await c.post("/v1/tts", json={"text": "질문을 읽어 드리겠습니다."})
        assert r.status_code == 200 and r.headers["X-Duration-Ms"] == "100" and len(r.content) == 9600
        assert r.headers["content-type"].startswith("audio/L16")
        assert (await c.post("/v1/tts", json={"text": ""})).status_code == 422
        r = await c.post("/v1/embed", json={"texts": ["a b", "c d"]})
        assert r.status_code == 200 and r.json()["dim"] == 64 and len(r.json()["vectors"]) == 2
        h = (await c.get("/v1/health")).json()
        assert h["llm"] == {"up": True, "loaded": True, "full_gpu": True, "model": "qwen3.5:4b", "digest": "sha256:abc"}
        assert h["stt"]["up"] and h["tts"]["up"] and h["embed"]["up"] and h["ready"]
        assert h["manifest_hash"].startswith("sha256:")
        engines.tts.up = False
        assert (await c.post("/v1/tts", json={"text": "질문"})).status_code == 503


async def test_http_client_maps_statuses(ai_app):
    app, engines = ai_app
    client = HttpAIClient(base_url="http://ai", token=TOKEN, transport=httpx.ASGITransport(app=app))
    inp = {"question": "성능 문제를 해결한 경험을 말씀해 주세요.", "question_type": "JOB", "answer": ANSWER,
           "followups_left": 4}
    engines.backend.task_for_next, engines.backend.inputs = "followup_decide", {"followup_decide": inp}
    r = await client.llm("followup_decide", inp, lane="RT", deadline_ms=now_ms(5))
    assert r.status == "OK" and r.output["decision"] == "SKIP" and r.manifest_hash.startswith("sha256:")
    engines.backend.delay = 0.3  # the model is slower than the 0.1 s left: cut at the deadline
    r = await client.llm("followup_decide", inp, lane="RT", deadline_ms=now_ms(0.1))
    assert r.status == "TIMEOUT"
    engines.backend.delay = 0.0
    assert (await client.health())["llm"]["up"]
    tts = await client.tts("안녕하세요")
    assert tts.duration_ms == 100 and len(tts.pcm48k) == 9600 and tts.voice_model == "supertonic-3:F1"
    stt = await client.stt(bytes(3200))
    assert stt.text == "안녕하세요" and stt.model == "large-v3-turbo"
    engines.backend.up = False
    with pytest.raises(AIUnavailable):
        await client.llm("followup_decide", inp, lane="RT", deadline_ms=now_ms(5))
    unreachable = HttpAIClient(base_url="http://127.0.0.1:9", token=TOKEN)
    with pytest.raises(AIUnavailable):
        await unreachable.llm("followup_decide", inp, lane="RT")
    with pytest.raises(AIUnavailable):
        await unreachable.health()


# ── fake client ────────────────────────────────────────────────────────────────
async def test_fake_client_runs_real_validators():
    fake = FakeAIClient()
    ev = await fake.llm("evaluate_answer", {"question": "q", "answer": ANSWER}, lane="BATCH")
    assert ev.status == "OK" and all(i["evidence_status"] == "OK" for i in ev.output["items"].values())
    short = await fake.llm("evaluate_answer", {"question": "q", "answer": "잘 모르겠습니다"}, lane="BATCH")
    assert {i["insufficient_reason"] for i in short.output["items"].values()} == {"TOO_SHORT"}
    assert short.attempts["llm_calls"] == 0  # rule-based, no model call (EVL-02)

    assert (await fake.llm("followup_decide", {"question": "q", "answer": ANSWER, "followups_left": 4},
                           lane="RT")).output["decision"] == "SKIP"
    fake.followup = "ASK"
    fu = await fake.llm("followup_decide", {"question": "성능 문제 해결 경험", "answer": ANSWER, "followups_left": 4},
                        lane="RT")
    assert fu.status == "OK" and fu.output["decision"] == "ASK" and fu.output["anchor"]["text"] in ANSWER

    resume = {"projects": [{"title": "주문 처리 시스템 개선", "description": "캐시 도입으로 주문 조회 응답 시간을 75% 단축"}],
              "experience": [{"company": "가나다 커머스", "role": "백엔드 개발자", "description": "결제 API 설계와 운영을 담당"}]}
    rq = await fake.llm("resume_question", {"resume": resume, "job_brief": "백엔드", "n": 2}, lane="INTERACTIVE")
    assert rq.status == "OK" and len(rq.output["questions"]) == 2
    qg = await fake.llm("question_generate", {"kind": "JOB", "job_family": "BACKEND", "n": 2,
                                              "posting": {"title": "백엔드 개발자", "duties": "주문 API 개발. 결제 시스템 운영",
                                                          "requirements": "Python 3년 이상"}}, lane="INTERACTIVE")
    assert qg.status == "OK" and len(qg.output["questions"]) == 2

    with pytest.raises(ValueError):
        await fake.llm("evaluate_answer", {"answer": ANSWER}, lane="RT")
    fake.fail["report_summary"] = "unavailable"
    with pytest.raises(AIUnavailable):
        await fake.llm("report_summary", {"questions": []}, lane="BATCH")
    fake.fail["code_review"] = "invalid"
    assert (await fake.llm("code_review", {"code": "print(1)"}, lane="BATCH")).status == "INVALID"
    # Every model call is captured for prompt assertions (SC-79).
    assert fake.calls[0].task == "evaluate_answer" and ANSWER in fake.calls[0].messages[0][1]["content"]


async def test_fake_client_speech_and_embed():
    fake = FakeAIClient()
    fake.stt_texts.append("첫 번째 답변")
    assert (await fake.stt(bytes(32000))).text == "첫 번째 답변"
    assert (await fake.stt(bytes(32000))).text == ""
    v = await fake.embed(["주문 처리 시스템", "주문 처리 시스템", "가족 관계"])
    assert sum(a * b for a, b in zip(v[0], v[1])) == pytest.approx(1.0)
    assert sum(a * b for a, b in zip(v[0], v[2])) < 0.5
    fake.down.add("tts")
    assert (await fake.health())["tts"]["up"] is False
    with pytest.raises(AIUnavailable):
        await fake.tts("질문")


# ── speech helpers ─────────────────────────────────────────────────────────────
def test_hallucination_rule_hl1():
    assert is_hallucination("시청해 주셔서 감사합니다", 0.1, -0.2, 1.1) == "BLOCKLIST"
    assert is_hallucination("네", 0.7, -1.2, 1.0) == "NO_SPEECH"
    assert is_hallucination("네", 0.7, -0.5, 1.0) is None  # both conditions are needed
    assert is_hallucination("아 아 아 아 아 아 아", 0.1, -0.3, 2.6) == "REPETITION"
    assert is_hallucination("캐시를 도입해 응답 시간을 줄였습니다", 0.05, -0.3, 1.3) is None


def test_resample_to_48k_s16():
    x = (0.5 * np.sin(np.arange(44100) * 2 * np.pi * 440 / 44100)).astype(np.float32)
    pcm = resample_to_s16(x, 44100, TTS_RATE)
    assert len(pcm) == TTS_RATE * 2  # 1 s of s16 mono
    assert resample_to_s16(np.zeros(0, dtype=np.float32), 44100) == b""


async def test_service_500_reports_exception_type_and_location(ai_app):
    """A crash names its type and raise site so it can be fixed from the response alone; no request data leaks."""
    app, engines = ai_app

    async def broken(pcm: bytes) -> dict:
        raise ValueError(f"secret-{len(pcm)}")

    engines.stt.transcribe = broken
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://ai", headers={"X-AI-Token": TOKEN}) as c:
        r = await c.post("/v1/stt", content=bytes(3200), headers={"Content-Type": "audio/L16"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "INTERNAL" and err["message"].startswith("ValueError at test_ai_service.py:")
    assert "in broken" in err["message"] and "secret" not in r.text


async def test_evaluate_empty_quote_gets_one_regeneration():
    """Score > 1 with an empty quote is asked again once; still empty -> NO_EVIDENCE 1 point (no invention)."""
    fake = FakeAIClient()
    calls = {"n": 0}
    good = ANSWER[:30]

    def reply(inp, msgs):
        calls["n"] += 1
        quote = "" if calls["n"] == 1 else good
        item = {"quote": good, "comment": "근거가 있다.", "score": 4}
        return {"expertise": item, "logic": {**item, "quote": quote}, "specificity": item,
                "delivery": {**item, "quote": ""}}

    fake.responses["evaluate_answer"] = reply
    r = await fake.llm("evaluate_answer", {"question": "q", "answer": ANSWER}, lane="BATCH")
    items = r.output["items"]
    assert r.status == "OK" and r.attempts["llm_calls"] == 2 and "비어 있거나" in fake.calls[0].messages[1][-1]["content"]
    assert items["LOGIC"]["evidence_status"] == "OK" and items["LOGIC"]["score"] == 4  # filled in on regeneration
    assert (items["DELIVERY"]["evidence_status"], items["DELIVERY"]["insufficient_reason"], items["DELIVERY"]["score"]) \
        == ("INSUFFICIENT", "NO_EVIDENCE", 1)


def test_stt_warmup_runs_encoder_and_falls_back(monkeypatch):
    """transcribe() is lazy: a GPU that cannot encode must fail at load (-> CPU small), not on the first answer."""
    import faster_whisper

    from app.aisvc.speech import STTEngine

    class FakeWhisper:
        def __init__(self, name, device, compute_type, local_files_only):
            self.device = device

        def transcribe(self, audio, **kw):
            def gen():
                if self.device == "cuda":
                    raise RuntimeError("cuDNN failed")  # raised only when the segments are consumed
                yield from ()
            return gen(), None

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWhisper)
    eng = STTEngine(model_name="large-v3-turbo", device="cuda")
    eng._load_sync()
    assert eng.up and eng.active == {"model": "small", "device": "cpu", "compute": "int8"} and eng.degraded


async def test_stt_hung_call_marks_engine_down(monkeypatch):
    import threading

    from app.aisvc import speech

    release = threading.Event()
    eng = speech.STTEngine()
    eng._model, eng.active = object(), {"model": "m", "device": "cuda"}
    monkeypatch.setattr(speech, "TRANSCRIBE_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(eng, "_transcribe_sync", lambda audio: release.wait(5) or {})
    with pytest.raises(TimeoutError):
        await eng.transcribe(bytes(3200))
    release.set()
    assert not eng.up and eng.error == "TRANSCRIBE_TIMEOUT"


async def test_ollama_status_matches_names_like_ollama():
    from app.aisvc.ollama import OllamaBackend

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "qwen3.5:4b", "model": "qwen3.5:4b", "digest": "d1"},
                {"name": "qwen3-embedding:latest", "model": "qwen3-embedding:latest", "digest": "d2"}]})
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b", "size": 10, "size_vram": 10}]})

    b = OllamaBackend("http://127.0.0.1:11434", "qwen3.5:4b", "qwen3-embedding", 8192,
                      transport=httpx.MockTransport(handle))
    s = await b.status()
    assert s["up"] and s["loaded"] and s["full_gpu"] and s["digest"] == "d1" and s["embed_available"]
    b.embed_model = "qwen3-embedding:0.6b"
    assert (await b.status())["embed_available"] is False  # a different tag is a different model
