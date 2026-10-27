"""STT and TTS engines inside the `ai` process (ADR-0004, TRD 05 §2, TRD 06 §6).

STT: faster-whisper `large-v3-turbo` int8_float16 on the GPU, language ko, beam 1, no previous-text conditioning,
one VAD segment per call. If the GPU model cannot load, fall back to `small` on the CPU (degraded captions) and say
so in health. Segments that look like Whisper hallucinations are blanked (`hl-1`).
TTS: Supertonic-3 on the CPU, one fixed voice style, resampled to 48 kHz s16 mono for the WebRTC downlink track.
Model calls run in a worker thread; each engine serves one request at a time (CPU/GPU contention, TRD 05 §3).
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from app.core.logging import log_event

log = logging.getLogger("app.aisvc")

STT_RATE = 16000
TTS_RATE = 48000
MAX_STT_SECONDS = 30

# ── hl-1 (TRD 06 §6) ───────────────────────────────────────────────────────────
HALLUCINATION_RULE = "hl-1"
BLOCKLIST = ("시청해 주셔서 감사합니다", "시청해주셔서 감사합니다", "구독과 좋아요", "자막 제공", "자막 by", "MBC 뉴스",
             "다음 영상에서 만나요", "끝까지 시청")


def is_hallucination(text: str, no_speech_prob: float, avg_logprob: float, compression_ratio: float) -> str | None:
    """Reason the segment must be blanked, or None to keep it."""
    if no_speech_prob >= 0.6 and avg_logprob <= -1.0:
        return "NO_SPEECH"
    if compression_ratio > 2.4:
        return "REPETITION"
    compact = "".join(text.split())
    if any("".join(b.split()) in compact for b in BLOCKLIST):
        return "BLOCKLIST"
    return None


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def resample_to_s16(audio: np.ndarray, src_rate: int, dst_rate: int = TTS_RATE) -> bytes:
    """Float mono -> s16le mono at dst_rate (PyAV/FFmpeg resampler, already a dependency for aiortc)."""
    import av

    audio = np.ascontiguousarray(np.clip(audio.reshape(-1), -1.0, 1.0), dtype=np.float32)
    if audio.size == 0:
        return b""
    frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = src_rate
    rs = av.AudioResampler(format="s16", layout="mono", rate=dst_rate)
    out = [f.to_ndarray().reshape(-1) for f in rs.resample(frame)] + [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(out).astype("<i2").tobytes() if out else b""


# ── STT ────────────────────────────────────────────────────────────────────────
@dataclass
class STTEngine:
    model_name: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "int8_float16"
    download: bool = False
    fallback_model: str = "small"
    _model: object | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active: dict = field(default_factory=dict)  # {"model", "device", "compute"} actually loaded
    error: str | None = None

    def _load_sync(self) -> None:
        from faster_whisper import WhisperModel

        tries = [(self.model_name, self.device, self.compute_type)]
        if self.device != "cpu":
            tries += [(self.fallback_model, self.device, self.compute_type), (self.fallback_model, "cpu", "int8")]
        else:
            tries = [(self.fallback_model, "cpu", "int8")]
        last: Exception | None = None
        for name, dev, comp in tries:
            try:
                m = WhisperModel(name, device=dev, compute_type=comp, local_files_only=not self.download)
                m.transcribe(np.zeros(STT_RATE, dtype=np.float32), language="ko", beam_size=1)  # warm kernels
            except Exception as e:  # noqa: BLE001 - try the next fallback
                last = e
                log_event(log, "stt_load_failed", logging.WARNING, model=name, error_code=type(e).__name__)
                continue
            self._model, self.active, self.error = m, {"model": name, "device": dev, "compute": comp}, None
            if (name, dev) != (self.model_name, self.device):
                log_event(log, "stt_degraded", logging.WARNING, model=name, reason=dev)
            return
        self.error = type(last).__name__ if last else "unknown"

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    @property
    def up(self) -> bool:
        return self._model is not None

    @property
    def degraded(self) -> bool:
        return self.up and self.active.get("device") == "cpu" and self.device != "cpu"

    def _transcribe_sync(self, audio: np.ndarray) -> dict:
        segs, _ = self._model.transcribe(audio, language="ko", beam_size=1, condition_on_previous_text=False,
                                         vad_filter=False)
        kept, dropped = [], []
        for s in segs:
            text = s.text.strip()
            reason = is_hallucination(text, s.no_speech_prob, s.avg_logprob, s.compression_ratio)
            if reason:
                dropped.append(reason)
                continue
            if text:
                kept.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text,
                             "no_speech_prob": round(s.no_speech_prob, 3)})
        return {"text": " ".join(k["text"] for k in kept), "segments": kept, "dropped": dropped}

    async def transcribe(self, pcm16k: bytes) -> dict:
        if not self.up:
            raise RuntimeError("stt not loaded")
        audio = pcm16_to_float(pcm16k)
        t0 = time.perf_counter()
        async with self._lock:
            out = await asyncio.to_thread(self._transcribe_sync, audio)
        out.update(model=self.active["model"], device=self.active["device"],
                   elapsed_ms=int((time.perf_counter() - t0) * 1000))
        return out

    def describe(self) -> dict:
        return {"engine": "faster-whisper", "model": self.active.get("model", self.model_name),
                "device": self.active.get("device", self.device), "compute": self.active.get("compute", self.compute_type),
                "beam": 1, "vad": "energy-v1", "halluc": HALLUCINATION_RULE}


# ── TTS ────────────────────────────────────────────────────────────────────────
@dataclass
class TTSEngine:
    voice: str = ""
    steps: int = 8
    speed: float = 1.05
    download: bool = False
    _tts: object | None = None
    _style: object | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    voice_name: str = ""
    error: str | None = None

    def _load_sync(self) -> None:
        try:
            from supertonic import TTS

            tts = TTS(auto_download=self.download)
            name = self.voice or tts.voice_style_names[0]
            self._style, self._tts, self.voice_name, self.error = tts.get_voice_style(voice_name=name), tts, name, None
        except Exception as e:  # noqa: BLE001 - TTS down means text-only interviews (INT-04)
            self.error = type(e).__name__
            log_event(log, "tts_load_failed", logging.WARNING, error_code=self.error)

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    @property
    def up(self) -> bool:
        return self._tts is not None

    def _synth_sync(self, text: str) -> tuple[bytes, int]:
        wav, _ = self._tts.synthesize(text, voice_style=self._style, total_steps=self.steps, speed=self.speed, lang="ko")
        pcm = resample_to_s16(np.asarray(wav, dtype=np.float32), int(self._tts.sample_rate), TTS_RATE)
        return pcm, int(len(pcm) / 2 / TTS_RATE * 1000)

    async def synthesize(self, text: str) -> tuple[bytes, int]:
        if not self.up:
            raise RuntimeError("tts not loaded")
        async with self._lock:
            return await asyncio.to_thread(self._synth_sync, text)

    def describe(self) -> dict:
        return {"engine": "supertonic 1.3.1", "model": "supertonic-3", "voice": self.voice_name or self.voice,
                "steps": self.steps, "speed": self.speed}
