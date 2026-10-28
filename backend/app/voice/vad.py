"""Energy VAD `energy-v1` (TRD 06 §6). Splits gated 16 kHz audio into caption segments; it never ends an answer
(silence does not finalise, X-02 / AC-10).

- a 20 ms frame is speech when its RMS > 0.01
- a segment closes after 0.5 s of silence following speech
- 15 s of continuous speech is cut at the quietest frame of its last 2 s (keeps Whisper inside its 30 s window)
- a segment with < 0.25 s of speech in total is dropped as noise (no STT call)
"""
from dataclasses import dataclass, field

import numpy as np

RATE = 16000
FRAME = 320  # 20 ms at 16 kHz
RMS_SPEECH = 0.01
CLOSE_SILENCE_FRAMES = 25  # 0.5 s
MAX_FRAMES = 750  # 15 s
SPLIT_SEARCH_FRAMES = 100  # last 2 s
MIN_SPEECH_FRAMES = 13  # ~0.25 s
VAD_VERSION = "energy-v1"


def frame_rms(frame: np.ndarray) -> float:
    x = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


@dataclass
class Segment:
    pcm: np.ndarray  # int16, 16 kHz mono
    speech_frames: int

    @property
    def seconds(self) -> float:
        return len(self.pcm) / RATE


@dataclass
class EnergyVAD:
    """Feed arbitrary-size int16 chunks; complete segments come back from `feed` and `flush`."""
    _pending: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))
    _frames: list = field(default_factory=list)  # (pcm, rms) of the open segment
    _silence: int = 0
    speech_frames_total: int = 0

    def feed(self, pcm: np.ndarray) -> list[Segment]:
        buf = np.concatenate([self._pending, pcm.astype(np.int16, copy=False)])
        n = len(buf) // FRAME * FRAME
        self._pending = buf[n:]
        out: list[Segment] = []
        for i in range(0, n, FRAME):
            seg = self._frame(buf[i:i + FRAME])
            if seg is not None:
                out.append(seg)
        return out

    def _frame(self, f: np.ndarray) -> Segment | None:
        rms = frame_rms(f)
        speech = rms > RMS_SPEECH
        if speech:
            self.speech_frames_total += 1
        if not self._frames and not speech:
            return None  # silence outside a segment is not kept
        self._frames.append((f, rms, speech))
        self._silence = 0 if speech else self._silence + 1
        if self._silence >= CLOSE_SILENCE_FRAMES:
            return self._close(len(self._frames))
        if len(self._frames) >= MAX_FRAMES:
            tail = self._frames[-SPLIT_SEARCH_FRAMES:]
            quiet = min(range(len(tail)), key=lambda k: tail[k][1])
            return self._close(len(self._frames) - len(tail) + quiet + 1)
        return None

    def _close(self, upto: int) -> Segment | None:
        head, self._frames = self._frames[:upto], self._frames[upto:]
        self._silence = 0
        speech = sum(1 for _, _, s in head if s)
        if speech < MIN_SPEECH_FRAMES:
            return None
        return Segment(np.concatenate([f for f, _, _ in head]), speech)

    def flush(self) -> Segment | None:
        """Answer closed: whatever is open becomes the last segment (if it holds enough speech)."""
        if self._frames:
            return self._close(len(self._frames))
        return None
