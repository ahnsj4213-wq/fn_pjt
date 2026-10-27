"""Deterministic in-process AIClient for tests and model-less development (handoff step 1).

`FakeAIClient.llm` runs the real TaskSpec pipeline (`run_task`: preflight, schema validation, quote checks, P-8
filters) against a scripted model, so tests exercise the same validators as production. Default replies are built
from the task input so they pass validation; override per task with `responses[task] = fn(input, messages)`
returning a dict (serialised as the model output) or a raw string (to test format errors).

Failure injection per task: `fail[task] = "unavailable" | "timeout" | "invalid"`. Every call is recorded in `calls`
(task, input, lane, messages) so tests can assert what a prompt did or did not contain (e.g. SC-79 hidden tests).
"""
import hashlib
import json
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.client import AIUnavailable, LLMResult, STTResult, TTSResult
from app.ai.tasks import TASKS, run_task
from app.ai.tasks.questions import QuestionGenerate, flatten_resume

FAKE_MANIFEST = "sha256:fake"
Responder = Callable[[dict, list[dict]], dict | str]


@dataclass
class FakeCall:
    task: str
    input: dict
    lane: str
    messages: list[list[dict]] = field(default_factory=list)  # one entry per model call (retries included)


@dataclass
class FakeAIClient:
    responses: dict[str, Responder] = field(default_factory=dict)
    fail: dict[str, str] = field(default_factory=dict)
    followup: str = "SKIP"  # default T4 decision: "SKIP" | "ASK"
    down: set[str] = field(default_factory=set)  # parts reported down by health(): llm, stt, tts, embed
    stt_texts: deque = field(default_factory=deque)  # queued STT results; empty -> ""
    calls: list[FakeCall] = field(default_factory=list)

    # ── LLM ──
    async def llm(self, task: str, input: dict, *, lane: str, deadline_ms: int | None = None) -> LLMResult:
        spec = TASKS[task]
        if lane != spec.lane:
            raise ValueError(f"{task} runs in {spec.lane}, not {lane}")
        rec = FakeCall(task, input, lane)
        self.calls.append(rec)
        mode = self.fail.get(task)
        if mode == "unavailable" or "llm" in self.down:
            raise AIUnavailable("fake: llm down")
        if mode == "timeout":
            return LLMResult(status="TIMEOUT", error="DEADLINE_EXCEEDED", manifest_hash=FAKE_MANIFEST)

        async def model(messages: list[dict], schema: dict, options: dict) -> tuple[str, dict]:
            rec.messages.append([dict(m) for m in messages])
            if mode == "invalid":
                return "not json", {}
            if task in self.responses:
                reply = self.responses[task](input, messages)
            else:
                reply = self._default(task, input)
            content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
            return content, {"prompt_tokens": sum(len(m["content"]) for m in messages) // 2,
                             "completion_tokens": len(content) // 2}

        out = await run_task(spec, input, model)
        return LLMResult(status=out.status, output=out.output, flags=out.flags, usage=out.usage,
                         manifest_hash=FAKE_MANIFEST, attempts=out.attempts, error=out.error)

    def _default(self, task: str, inp: dict) -> dict:
        return getattr(self, f"_reply_{task}")(inp)

    @staticmethod
    def _reply_resume_extract(inp: dict) -> dict:
        lines = [x.strip() for x in inp["source_text"].splitlines() if x.strip()]
        name = lines[0] if lines and len(lines[0]) <= 10 else None
        return {"name": name, "education": [], "experience": [], "projects": [], "skills": [], "certifications": []}

    @staticmethod
    def _reply_question_generate(inp: dict) -> dict:
        src = QuestionGenerate._source(inp)
        pieces = [x.strip() for x in src.replace(".", "\n").splitlines() if len(x.strip()) >= 4]
        qs = []
        for i, basis in enumerate(pieces[: int(inp.get("n", 3)) + 2]):
            qs.append({"text": f"'{basis[:40]}' 업무에 본인의 경험을 어떻게 활용할지 말씀해 주세요 ({i + 1})?",
                       "basis_quote": basis})
        return {"questions": qs}

    @staticmethod
    def _reply_resume_question(inp: dict) -> dict:
        flat, ranges = flatten_resume(inp.get("resume") or {})
        qs = []
        for label, start, end in ranges:
            value = flat[start:end]
            if len(value) < 8:
                continue
            qs.append({"text": f"{value[:30]}에서 본인이 맡은 역할과 결과를 말씀해 주세요?",
                       "evidence": [{"field": label, "quote": value[:60]}]})
            if len(qs) == 3:
                break
        return {"questions": qs}

    def _reply_followup_decide(self, inp: dict) -> dict:
        if self.followup != "ASK":
            return {"decision": "SKIP", "reason": "NONE", "anchor": None, "question": None}
        quote = inp["answer"].strip()[:12]
        return {"decision": "ASK", "reason": "VAGUE", "anchor": {"source": "answer", "quote": quote},
                "question": f"말씀하신 '{quote}' 부분을 구체적인 사례로 설명해 주시겠어요?"}

    @staticmethod
    def _reply_evaluate_answer(inp: dict) -> dict:
        quote = inp["answer"].strip()[:40]
        item = {"quote": quote, "comment": "답변에서 판단 근거가 되는 내용이 확인된다.", "score": 3}
        return {k: dict(item) for k in ("expertise", "logic", "specificity", "delivery")}

    @staticmethod
    def _reply_report_summary(inp: dict) -> dict:
        nos = [q["no"] for q in inp["questions"]] or [1]
        return {"overall": "질문 의도에 맞춰 경험을 중심으로 답변했다.",
                "strengths": [{"text": "경험을 근거로 설명했다.", "question_refs": nos[:1]}],
                "improvements": [{"text": "역할과 결과를 더 구체적으로 제시할 필요가 있다.", "question_refs": nos[-1:]}]}

    @staticmethod
    def _reply_candidate_feedback(inp: dict) -> dict:
        return {"strengths": ["질문의 핵심에 맞춰 차분하게 설명했습니다."],
                "improvements": ["본인의 역할과 결과를 조금 더 구체적으로 제시하면 좋겠습니다."]}

    @staticmethod
    def _reply_code_review(inp: dict) -> dict:
        return {"readability": "함수와 변수 이름이 역할을 잘 드러낸다.",
                "complexity": {"time": "unknown", "comment": "입력 크기에 따른 반복 구조를 확인해야 한다."},
                "improvements": ["예외 입력에 대한 처리를 추가하면 좋다."]}

    # ── speech / embeddings ──
    async def stt(self, pcm16k: bytes, *, purpose: str = "answer") -> STTResult:
        if "stt" in self.down:
            raise AIUnavailable("fake: stt down")
        text = self.stt_texts.popleft() if self.stt_texts else ""
        return STTResult(text=text, segments=[{"start": 0.0, "end": len(pcm16k) / 32000, "text": text}] if text else [],
                         model="fake-stt", device="cpu")

    async def tts(self, text: str) -> TTSResult:
        if "tts" in self.down:
            raise AIUnavailable("fake: tts down")
        ms = 150 * len(text)
        return TTSResult(pcm48k=bytes(ms * 96), duration_ms=ms, voice_model="fake-tts")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if "embed" in self.down:
            raise AIUnavailable("fake: embed down")
        return [_bigram_vector(t) for t in texts]

    async def health(self) -> dict:
        return {part: {"up": part not in self.down} for part in ("llm", "stt", "tts", "embed")} | {
            "ready": True, "manifest_hash": FAKE_MANIFEST}


def _bigram_vector(text: str, dim: int = 64) -> list[float]:
    """Character-bigram hashing: identical texts -> cosine 1, unrelated texts -> low cosine. Deterministic."""
    t = "".join(text.split())
    v = [0.0] * dim
    for i in range(max(1, len(t) - 1)):
        h = int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]
