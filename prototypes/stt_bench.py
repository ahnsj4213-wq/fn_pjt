"""Spike: faster-whisper Korean STT latency/accuracy on CPU (int8) for the partial-caption budget (<2s after a pause).

Usage: python prototypes/stt_bench.py --models small large-v3-turbo --threads 6
"""
import argparse
import re
import time
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

AUDIO = Path(__file__).parent / "audio"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def cer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(len(r), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["small"])
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--compute", default="int8")
    args = ap.parse_args()

    refs = (AUDIO / "ko_samples.txt").read_text(encoding="utf-8-sig").splitlines()
    files = sorted(AUDIO.glob("ko_sample_*.wav"))
    for name in args.models:
        t0 = time.perf_counter()
        model = WhisperModel(name, device=args.device, compute_type=args.compute, cpu_threads=args.threads)
        print(f"== {name} ({args.device}/{args.compute}, threads={args.threads}) load={time.perf_counter() - t0:.1f}s")
        # warm-up so the first measured file is not penalised by lazy init
        model.transcribe(np.zeros(16000, dtype=np.float32), language="ko", beam_size=1)
        cers = []
        for f, ref in zip(files, refs):
            audio, sr = sf.read(f, dtype="float32")
            dur = len(audio) / sr
            for beam in (1, 5):
                t1 = time.perf_counter()
                segs, _ = model.transcribe(audio, language="ko", beam_size=beam, vad_filter=False,
                                           condition_on_previous_text=False, without_timestamps=True)
                text = "".join(s.text for s in segs).strip()
                el = time.perf_counter() - t1
                c = cer(ref, text)
                if beam == 1:
                    cers.append(c)
                print(f"  {f.name} dur={dur:.1f}s beam={beam} stt={el:.2f}s rtf={el / dur:.2f} cer={c:.3f} :: {text[:70]}")
        print(f"  mean CER(beam1)={sum(cers) / len(cers):.3f}")


if __name__ == "__main__":
    main()
