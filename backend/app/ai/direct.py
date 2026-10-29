"""AI client for the split containers (deploy/docker-compose.yml): no central `ai` process.

    llm-rt    llama.cpp on the GPU   RT (follow-up decision during the interview) and INTERACTIVE (question set,
                                     resume extraction) — started with 2 slots so both can run at once
    llm-eval  llama.cpp on the CPU   BATCH (answer evaluation, report, feedback, code review)
    stt       faster-whisper (GPU)   /v1/stt      (app.aisvc.speech_server stt)
    tts       Supertonic (CPU)       /v1/tts      (app.aisvc.speech_server tts)
    embed     llama.cpp, BGE-M3 CPU  /v1/embeddings

The task validators, retries and quote checks (run_task) and the lane gateway run in the calling process, one
gateway per LLM server. Lanes on different servers no longer wait for each other, so the BATCH-cutting rule of
ADR-0003 only matters inside one process; RT/INTERACTIVE share llm-rt through its two slots (ADR-0014).
"""
import asyncio
import time
import uuid

from app.ai.client import AIUnavailable, HttpAIClient, LLMResult, STTResult, TTSResult
from app.ai.manifest import build_manifest, manifest_hash
from app.ai.tasks import TASKS, run_task
from app.aisvc.gateway import CallStats, DeadlineExceeded, Gateway, LLMUnavailable
from app.aisvc.llamacpp import LlamaCppBackend
from app.aisvc.ollama import ThinkingLeak
from app.core.config import get_settings

HEALTH_TTL_SEC = 5.0
LANE_SERVER = {"RT": "rt", "INTERACTIVE": "rt", "BATCH": "eval"}
DEFAULT_DEADLINE_SEC = {"RT": 5.0, "INTERACTIVE": 120.0, "BATCH": 600.0}


class DirectAIClient:
    def __init__(self, *, rt: LlamaCppBackend | None = None, eval_: LlamaCppBackend | None = None,
                 embedder: LlamaCppBackend | None = None, stt: HttpAIClient | None = None,
                 tts: HttpAIClient | None = None) -> None:
        s = get_settings()
        self.rt = rt or LlamaCppBackend(s.llm_rt_url, name=s.llm_model)
        self.eval = eval_ or LlamaCppBackend(s.llm_eval_url, name=s.llm_model)
        self.embedder = embedder or LlamaCppBackend(s.embed_url, name=s.embed_model)
        self.stt_client = stt or HttpAIClient(base_url=s.stt_url)
        self.tts_client = tts or HttpAIClient(base_url=s.tts_url)
        self.gateways = {"rt": Gateway(chat=self.rt.chat), "eval": Gateway(chat=self.eval.chat)}
        self._health: dict | None = None
        self._health_at = 0.0
        self._manifest_hash = ""

    async def llm(self, task: str, input: dict, *, lane: str, deadline_ms: int | None = None) -> LLMResult:
        spec = TASKS[task]
        if lane != spec.lane:
            raise ValueError(f"{task} runs in {spec.lane}, not {lane}")
        if deadline_ms is None:
            deadline_ms = int((time.time() + DEFAULT_DEADLINE_SEC[lane]) * 1000)
        gw = self.gateways[LANE_SERVER[lane]]
        if gw.is_down():
            raise AIUnavailable(f"{LANE_SERVER[lane]} down")
        stats = CallStats()

        async def call(messages: list[dict], schema: dict, options: dict) -> tuple[str, dict]:
            return await gw.call(lane, messages, schema, options, deadline_ms=deadline_ms, stats=stats,
                                 est_key=task, est_default_ms=300.0 + spec.num_predict * 5.0)

        if not self._manifest_hash:
            await self.health()
        try:
            out = await run_task(spec, input, call)
        except DeadlineExceeded:
            return LLMResult(status="TIMEOUT", error="DEADLINE_EXCEEDED", manifest_hash=self._manifest_hash)
        except LLMUnavailable as e:
            raise AIUnavailable(str(e)) from e
        except ThinkingLeak:
            return LLMResult(status="INVALID", error="THINKING_OUTPUT", manifest_hash=self._manifest_hash)
        usage = dict(out.usage) | {"queue_ms": stats.queue_ms, "preempted": stats.preempted,
                                   "trace_id": str(uuid.uuid4())}
        return LLMResult(status=out.status, output=out.output, flags=out.flags, usage=usage,
                         manifest_hash=self._manifest_hash, attempts=out.attempts, error=out.error)

    async def stt(self, pcm16k: bytes, *, purpose: str = "answer") -> STTResult:
        return await self.stt_client.stt(pcm16k, purpose=purpose)

    async def tts(self, text: str) -> TTSResult:
        return await self.tts_client.tts(text)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await self.embedder.embed(texts)
        except LLMUnavailable as e:
            raise AIUnavailable(str(e)) from e

    async def _speech(self, client: HttpAIClient) -> dict:
        try:
            return await client.health()
        except Exception:  # noqa: BLE001 - down or unreachable
            return {}

    async def health(self) -> dict:
        """Same shape as the ai service's /v1/health (llm, stt, tts, embed), plus llm_eval for the BATCH lane."""
        now = time.monotonic()
        if self._health is not None and now - self._health_at < HEALTH_TTL_SEC:
            return self._health
        rt, ev, em, stt, tts = await asyncio.gather(self.rt.status(), self.eval.status(), self.embedder.status(),
                                                    self._speech(self.stt_client), self._speech(self.tts_client))
        for key, st in (("rt", rt), ("eval", ev)):
            gw = self.gateways[key]
            gw.mark_up() if st["up"] else gw.mark_down()
        stt_part, tts_part = stt.get("stt", {}), tts.get("tts", {})
        manifest = build_manifest(llm={"runtime": "llama.cpp", "rt": rt.get("digest"), "eval": ev.get("digest"),
                                       "options": {"thinking": False}},
                                  stt={"model": stt_part.get("model"), "device": stt_part.get("device")},
                                  tts={"engine": tts.get("engine"), "voice": tts.get("voice")},
                                  embed={"model": em.get("digest") or em.get("model"), "device": "cpu"})
        self._manifest_hash = manifest_hash(manifest)
        self._health = {
            "llm": {"up": rt["up"], "model": rt.get("model"), "digest": rt.get("digest")},
            "llm_eval": {"up": ev["up"], "model": ev.get("model"), "digest": ev.get("digest")},
            "stt": {"up": bool(stt_part.get("up")), "model": stt_part.get("model"), "device": stt_part.get("device")},
            "tts": {"up": bool(tts_part.get("up"))},
            "embed": {"up": em["up"]},
            "ready": rt["up"] and bool(stt_part.get("up")),
            "manifest_hash": self._manifest_hash,
        }
        self._health_at = now
        return self._health
