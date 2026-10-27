"""Spike S-1: browser <-> aiortc. Uplink audio -> server gate -> energy VAD -> STT -> captions over datachannel.
Downlink: pre-generated TTS WAV streamed as a WebRTC audio track; the server announces tts_end and opens the gate.

Run: python prototypes/rtc_browser/server.py   then open http://localhost:8765
"""
import asyncio
import fractions
import json
import os
import time
from pathlib import Path

import av
import numpy as np
import soundfile as sf
import uvicorn
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).parent
AUDIO = HERE.parent / "audio"
DEVICE = os.environ.get("STT_DEVICE", "cuda")
if DEVICE == "cuda":
    nv = Path(__file__).resolve().parents[2] / ".venv/Lib/site-packages/nvidia"
    # ctranslate2 resolves cuBLAS/cuDNN through PATH; os.add_dll_directory alone is not enough.
    os.environ["PATH"] = os.pathsep.join(str(nv / s) for s in ("cublas/bin", "cudnn/bin")) + os.pathsep + os.environ["PATH"]
from faster_whisper import WhisperModel  # noqa: E402

stt = WhisperModel(os.environ.get("STT_MODEL", "large-v3-turbo"), device=DEVICE,
                   compute_type="int8_float16" if DEVICE == "cuda" else "int8")
app = FastAPI()
pcs: set[RTCPeerConnection] = set()


class TtsTrack(MediaStreamTrack):
    """48 kHz mono s16 track: plays queued PCM, silence otherwise; reports when a clip has been fully sent."""
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.buf = np.zeros(0, dtype=np.int16)
        self.on_clip_end = None
        self.pts = 0
        self.t0 = None

    def play(self, pcm48: np.ndarray, on_end):
        self.buf = pcm48
        self.on_clip_end = on_end

    async def recv(self):
        n = 960  # 20 ms
        if self.t0 is None:
            self.t0 = time.perf_counter()
        wait = self.t0 + self.pts / 48000 - time.perf_counter()
        if wait > 0:
            await asyncio.sleep(wait)
        chunk, self.buf = self.buf[:n], self.buf[n:]
        if len(chunk) and len(self.buf) == 0 and self.on_clip_end:
            cb, self.on_clip_end = self.on_clip_end, None
            asyncio.get_event_loop().call_soon(cb)
        if len(chunk) < n:
            chunk = np.concatenate([chunk, np.zeros(n - len(chunk), dtype=np.int16)])
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate, frame.pts, frame.time_base = 48000, self.pts, fractions.Fraction(1, 48000)
        self.pts += n
        return frame


def load_pcm48(path: Path) -> np.ndarray:
    a, sr = sf.read(path, dtype="float32")
    if a.ndim > 1:
        a = a.mean(axis=1)
    idx = np.linspace(0, len(a) - 1, int(len(a) * 48000 / sr))
    return (np.interp(idx, np.arange(len(a)), a) * 32767).astype(np.int16)


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/sample.wav")
def sample():
    return FileResponse(AUDIO / "ko_sample_0.wav")


@app.post("/offer")
async def offer(body: dict):
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pcs.add(pc)
    tts = TtsTrack()
    pc.addTrack(tts)
    state = {"gate_open": False, "seg": [], "silence": 0, "dc": None, "dropped_before_gate": 0, "t_last_voice": 0.0}
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

    def send(obj):
        if state["dc"] and state["dc"].readyState == "open":
            state["dc"].send(json.dumps(obj, ensure_ascii=False))

    async def transcribe(pcm: np.ndarray, t_pause: float):
        audio = pcm.astype(np.float32) / 32768
        segs = await asyncio.to_thread(lambda: list(stt.transcribe(audio, language="ko", beam_size=1,
                                                                     condition_on_previous_text=False)[0]))
        text = "".join(s.text for s in segs).strip()
        send({"type": "caption", "text": text, "latency_after_pause_s": round(time.perf_counter() - t_pause, 2)})

    def open_gate():
        state["gate_open"] = True
        send({"type": "mic_open", "server_t": time.time()})

    @pc.on("datachannel")
    def on_dc(dc):
        state["dc"] = dc

        @dc.on("message")
        def on_msg(m):
            msg = json.loads(m)
            if msg["type"] == "start_question":
                send({"type": "question", "text": "최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해 주세요."})
                tts.play(load_pcm48(AUDIO / "tts_supertonic_0.wav"),
                         lambda: (send({"type": "tts_end"}), asyncio.get_event_loop().call_later(0.3, open_gate)))

    @pc.on("track")
    def on_track(track):
        async def consume():
            while True:
                try:
                    frame = await track.recv()
                except Exception:
                    return
                for f in resampler.resample(frame):
                    pcm = f.to_ndarray().reshape(-1)
                    if not state["gate_open"]:
                        state["dropped_before_gate"] += len(pcm)
                        continue
                    rms = float(np.sqrt(np.mean((pcm.astype(np.float32) / 32768) ** 2)))
                    if rms > 0.01:
                        state["seg"].append(pcm)
                        state["silence"] = 0
                    elif state["seg"]:
                        state["seg"].append(pcm)
                        state["silence"] += len(pcm)
                        if state["silence"] >= 16000 * 0.5:  # 500 ms pause closes a caption segment
                            seg = np.concatenate(state["seg"])
                            state["seg"], state["silence"] = [], 0
                            send({"type": "debug", "dropped_before_gate_s": round(state["dropped_before_gate"] / 16000, 2)})
                            asyncio.ensure_future(transcribe(seg, time.perf_counter()))
        asyncio.ensure_future(consume())

    await pc.setRemoteDescription(RTCSessionDescription(sdp=body["sdp"], type=body["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    return JSONResponse({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
