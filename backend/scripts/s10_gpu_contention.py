"""S-10: STT latency while the LLM is generating on the same GPU (TRD 08 §8, TRD 05 §3). Run on the GPU machine.

    cd backend
    ..\\.venv\\Scripts\\python scripts\\s10_gpu_contention.py --label shared              (Windows)
    ../.venv/bin/python scripts/s10_gpu_contention.py --fake                         (checks the script only)

Needs a running ai service (`python -m app.aisvc`) with the real models. Measure once per GPU policy: restart the
service with AIHIRE_GPU_POLICY=shared, then with AIHIRE_GPU_POLICY=stt_preempts_batch, and pass the same --label.

What it does (no data is stored):
  1. Synthesizes ~13.5 s of Korean speech with the service's TTS (the longest caption segment in TRD 08 §8).
  2. Idle: `--runs` STT calls with nothing else on the GPU.
  3. Contended: BATCH `evaluate_answer` generations run back to back (several hundred tokens each, the evaluation
     load of EVL-05) while STT calls land at random points inside them.
Pass criterion (8.2): contended caption latency p95 <= 2.0 s. It also prints LLM generations finished/cut and the
GPU memory in use (nvidia-smi), which TRD 05 §2 still lists as unmeasured for turbo.
"""
import argparse
import asyncio
import random
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.client import AIClient, HttpAIClient

SEGMENT_SEC = 13.5
RATE = 16_000
SPEECH = ("저는 주문 처리 시스템의 성능 개선을 맡았습니다. 먼저 느린 쿼리를 찾아 복합 인덱스를 추가했고, 반복 조회를 "
          "한 번의 배치 조회로 바꿨습니다. 그 결과 평균 응답 시간이 팔백 밀리초에서 이백 밀리초로 줄었고, 장애 건수도 "
          "절반 이하로 줄었습니다. 이 경험으로 측정부터 하고 고치는 습관을 갖게 되었습니다.")
EVAL_INPUT = {"question": "최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해 주세요.", "question_type": "JOB",
              "job_brief": "백엔드 개발자", "answer": SPEECH}
P95_LIMIT = 2.0


def to_16k(pcm48k: bytes) -> np.ndarray:
    x = np.frombuffer(pcm48k, dtype="<i2").astype(np.float32)
    return x[: len(x) // 3 * 3].reshape(-1, 3).mean(axis=1)


def fit(samples: np.ndarray, seconds: float) -> bytes:
    """Loops or cuts the speech to exactly `seconds`."""
    n = int(seconds * RATE)
    if len(samples) == 0:
        samples = np.zeros(1, dtype=np.float32)
    reps = int(np.ceil(n / len(samples)))
    return np.tile(samples, reps)[:n].astype("<i2").tobytes()


def gpu_mem_mib() -> str:
    if not shutil.which("nvidia-smi"):
        return "n/a"
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=False).stdout.strip().splitlines()
        used, total = out[0].split(",")
        return f"{used.strip()}/{total.strip()} MiB"
    except Exception:  # noqa: BLE001 - informational only
        return "n/a"


def summary(xs: list[float]) -> str:
    if not xs:
        return "no samples"
    return f"n={len(xs)} p50={statistics.median(xs):.2f}s p95={p95(xs):.2f}s max={max(xs):.2f}s"


def p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))] if s else float("inf")


async def timed_stt(ai: AIClient, pcm: bytes) -> tuple[float, str]:
    t = time.perf_counter()
    out = await ai.stt(pcm)
    return time.perf_counter() - t, out.text


async def run(ai: AIClient, runs: int, label: str) -> bool:
    print(f"S-10 GPU contention  label={label}  segment={SEGMENT_SEC}s  runs={runs}")
    health = await ai.health()
    print("health:", {k: v.get("up") if isinstance(v, dict) else v for k, v in health.items() if k != "manifest_hash"})
    tts = await ai.tts(SPEECH)
    pcm = fit(to_16k(tts.pcm48k), SEGMENT_SEC)
    print(f"speech: tts {tts.duration_ms} ms -> fitted to {SEGMENT_SEC}s; GPU memory idle {gpu_mem_mib()}")

    await timed_stt(ai, pcm)  # warm-up (first call loads kernels)
    idle = []
    for _ in range(runs):
        el, text = await timed_stt(ai, pcm)
        idle.append(el)
    print(f"idle STT:       {summary(idle)}   heard: {text[:40]}…")

    stop = asyncio.Event()
    llm = {"ok": 0, "other": {}, "busy_mem": "n/a"}

    async def generate() -> None:
        while not stop.is_set():
            r = await ai.llm("evaluate_answer", EVAL_INPUT, lane="BATCH")
            if r.status == "OK":
                llm["ok"] += 1
            else:
                llm["other"][r.status] = llm["other"].get(r.status, 0) + 1
            await asyncio.sleep(0)  # a fake client never suspends; keep the loop fair

    gen = asyncio.create_task(generate())
    await asyncio.sleep(3.0)  # let the first generation get going
    llm["busy_mem"] = gpu_mem_mib()
    busy = []
    t0 = time.perf_counter()
    for _ in range(runs):
        await asyncio.sleep(random.uniform(0.5, 3.0))  # land at different points of a generation
        el, _ = await timed_stt(ai, pcm)
        busy.append(el)
    stop.set()
    await gen
    wall = time.perf_counter() - t0
    print(f"contended STT:  {summary(busy)}   GPU memory busy {llm['busy_mem']}")
    print(f"LLM during the contended phase ({wall:.0f}s): evaluate_answer OK={llm['ok']} other={llm['other']}")
    ok = p95(busy) <= P95_LIMIT
    print(f"\nS-10 {'PASS' if ok else 'FAIL'}: contended p95 {p95(busy):.2f}s (limit {P95_LIMIT}s, 8.2)")
    if not ok:
        print("next: rerun with AIHIRE_GPU_POLICY=stt_preempts_batch (or check the STT model/device), TRD 02 §5")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--label", default="", help="e.g. the AIHIRE_GPU_POLICY the service was started with")
    ap.add_argument("--fake", action="store_true", help="FakeAIClient: checks the script, measures nothing real")
    args = ap.parse_args()
    if args.fake:
        from app.ai.fake import FakeAIClient

        ai: AIClient = FakeAIClient()
        args.runs = min(args.runs, 3)
    else:
        ai = HttpAIClient()
    ok = asyncio.run(run(ai, args.runs, args.label or ("fake" if args.fake else "unlabelled")))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
