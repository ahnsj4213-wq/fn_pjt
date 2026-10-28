"""Downlink question audio as a WebRTC track (TRD 06 §4, ADR-0009 §4): 48 kHz mono s16 in 20 ms frames, paced in
real time, silence between clips. The server learns when the last frame of a clip has been sent and only then
starts the 0.3 s playout guard before opening the mic — the client cannot open the gate earlier (AC-09)."""
import asyncio
import fractions
import time
from collections.abc import Callable

import av
import numpy as np
from aiortc import MediaStreamTrack

RATE = 48000
FRAME = 960  # 20 ms


class TtsTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._buf = np.zeros(0, dtype=np.int16)
        self._on_end: Callable[[], None] | None = None
        self._pts = 0
        self._t0: float | None = None

    def play(self, pcm48k: bytes, on_end: Callable[[], None]) -> None:
        """Replace whatever is queued with this clip; `on_end` runs right after its last frame is sent."""
        self._buf = np.frombuffer(pcm48k, dtype="<i2").copy()
        self._on_end = on_end
        if len(self._buf) == 0:
            self._finish()

    def stop(self) -> None:
        self._buf, self._on_end = np.zeros(0, dtype=np.int16), None

    def _finish(self) -> None:
        cb, self._on_end = self._on_end, None
        if cb is not None:
            asyncio.get_running_loop().call_soon(cb)

    async def recv(self) -> av.AudioFrame:
        if self._t0 is None:
            self._t0 = time.perf_counter()
        wait = self._t0 + self._pts / RATE - time.perf_counter()
        if wait > 0:
            await asyncio.sleep(wait)
        chunk, self._buf = self._buf[:FRAME], self._buf[FRAME:]
        if len(chunk) and len(self._buf) == 0:
            self._finish()
        if len(chunk) < FRAME:
            chunk = np.concatenate([chunk, np.zeros(FRAME - len(chunk), dtype=np.int16)])
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate, frame.pts, frame.time_base = RATE, self._pts, fractions.Fraction(1, RATE)
        self._pts += FRAME
        return frame
