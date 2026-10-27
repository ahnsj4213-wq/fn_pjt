"""Spike: aiortc audio + datachannel loopback on this machine (no browser).

Sender plays a Korean WAV over an Opus track; receiver decodes, resamples to 16 kHz mono s16
(what the STT stage consumes) and counts received audio. Verifies the server-side media path.
"""
import asyncio
import time
from pathlib import Path

import av
from aiortc import RTCConfiguration, RTCPeerConnection
from aiortc.contrib.media import MediaPlayer

WAV = Path(__file__).parent / "audio" / "ko_sample_0.wav"


async def main() -> None:
    cfg = RTCConfiguration(iceServers=[])  # no public STUN: on-prem + AC-22
    sender, receiver = RTCPeerConnection(cfg), RTCPeerConnection(cfg)
    player = MediaPlayer(str(WAV))
    sender.addTrack(player.audio)
    chan = sender.createDataChannel("control")
    got = {"samples": 0, "first_frame_at": None, "msgs": []}
    done = asyncio.Event()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

    @receiver.on("datachannel")
    def on_dc(dc):
        @dc.on("message")
        def on_msg(m):
            got["msgs"].append(m)

    @receiver.on("track")
    def on_track(track):
        async def consume():
            while True:
                try:
                    frame = await track.recv()
                except Exception:
                    break
                if got["first_frame_at"] is None:
                    got["first_frame_at"] = time.perf_counter()
                for f in resampler.resample(frame):
                    got["samples"] += f.samples
                if got["samples"] >= 16000 * 12:
                    done.set()
                    break
        asyncio.ensure_future(consume())

    t0 = time.perf_counter()
    await sender.setLocalDescription(await sender.createOffer())
    await receiver.setRemoteDescription(sender.localDescription)
    await receiver.setLocalDescription(await receiver.createAnswer())
    await sender.setRemoteDescription(receiver.localDescription)

    @chan.on("open")
    def on_open():
        chan.send("mic_open")

    try:
        await asyncio.wait_for(done.wait(), timeout=30)
    except asyncio.TimeoutError:
        pass
    print(f"connected+first audio frame after {got['first_frame_at'] - t0:.2f}s" if got["first_frame_at"] else "NO AUDIO")
    print(f"received {got['samples'] / 16000:.1f}s of 16k mono audio in {time.perf_counter() - t0:.1f}s wall")
    print("datachannel messages:", got["msgs"])
    await sender.close()
    await receiver.close()


if __name__ == "__main__":
    asyncio.run(main())
