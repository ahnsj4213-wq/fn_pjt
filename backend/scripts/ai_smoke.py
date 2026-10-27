"""Smoke test against a running ai service (`python -m app.aisvc`): one real call per capability, with timings.

    cd backend
    ..\\.venv\\Scripts\\python scripts\\ai_smoke.py          (Windows)   /   ../.venv/bin/python scripts/ai_smoke.py
    ... scripts/ai_smoke.py --fake                          (no service: checks the script itself with FakeAIClient)

Checks: T4 follow-up decision (RT lane, 5 s budget), T5 answer evaluation with quote verification, TTS -> STT
round trip (Korean), embeddings. Prints PASS/FAIL per step; exit code 1 if anything failed. No data is stored.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.client import AIClient, HttpAIClient

QUESTION = "최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해 주세요."
ANSWER = ("저는 주문 처리 API의 응답 지연 문제를 맡았습니다. 먼저 APM으로 느린 쿼리를 찾았고, 복합 인덱스를 추가하고 "
          "N+1 쿼리를 배치 조회로 바꿨습니다. 그 결과 평균 응답 시간이 800밀리초에서 200밀리초로 줄었습니다.")
VAGUE = "그냥 여러 가지를 열심히 해서 성능을 좀 개선했던 것 같습니다. 팀원들과 같이 잘 해결했습니다."
TTS_TEXT = "팀원과 의견이 달랐을 때 어떻게 해결했는지 말씀해 주세요."


def now_ms(sec: float) -> int:
    return int((time.time() + sec) * 1000)


def to_16k(pcm48k: bytes) -> bytes:
    """48 kHz s16 -> 16 kHz s16 by averaging groups of 3 samples (good enough for a round-trip check)."""
    x = np.frombuffer(pcm48k, dtype="<i2").astype(np.float32)
    x = x[: len(x) // 3 * 3].reshape(-1, 3).mean(axis=1)
    return x.astype("<i2").tobytes()


def cer(ref: str, hyp: str) -> float:
    a, b = "".join(ref.split()).strip("."), "".join(hyp.split()).strip(".")
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ca != cb))
    return d[len(b)] / max(1, len(a))


async def run(ai: AIClient) -> bool:
    ok_all = True

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal ok_all
        ok_all &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    t = time.perf_counter()
    h = await ai.health()
    report("health", all(h.get(k, {}).get("up") for k in ("llm", "stt", "tts", "embed")),
           f"llm={h.get('llm')} stt={h.get('stt')} ({time.perf_counter() - t:.2f}s)")

    for label, answer in (("clear answer", ANSWER), ("vague answer", VAGUE)):
        t = time.perf_counter()
        r = await ai.llm("followup_decide", {"question": QUESTION, "question_type": "JOB", "answer": answer,
                                             "followups_left": 4}, lane="RT", deadline_ms=now_ms(5.0))
        el = time.perf_counter() - t
        detail = f"status={r.status} decision={r.output.get('decision')} reason={r.output.get('reason')} {el:.2f}s"
        if r.output.get("question"):
            detail += f"\n         question: {r.output['question']}"
        report(f"T4 follow-up ({label}, budget 5s)", r.status in ("OK", "TIMEOUT") and el < 5.5, detail)

    t = time.perf_counter()
    r = await ai.llm("evaluate_answer", {"question": QUESTION, "question_type": "JOB", "job_brief": "백엔드 개발자",
                                         "answer": ANSWER}, lane="BATCH")
    el = time.perf_counter() - t
    items = r.output.get("items", {})
    quotes_ok = all(v["quote"] is None or ANSWER[v["quote"]["start"]:v["quote"]["end"]] == v["quote"]["text"]
                    for v in items.values())
    summary = ", ".join(f"{k}={v['score']}/{v['evidence_status']}" for k, v in items.items())
    report("T5 evaluate_answer", r.status == "OK" and quotes_ok,
           f"status={r.status} {summary} model_calls={r.attempts.get('llm_calls')} {el:.1f}s")

    t = time.perf_counter()
    tts = await ai.tts(TTS_TEXT)
    t_tts = time.perf_counter() - t
    t = time.perf_counter()
    stt = await ai.stt(to_16k(tts.pcm48k)) if tts.pcm48k else None
    t_stt = time.perf_counter() - t
    text = stt.text if stt else ""
    c = cer(TTS_TEXT, text)
    report("TTS -> STT round trip", bool(tts.pcm48k) and c <= 0.15,
           f"audio={tts.duration_ms}ms tts={t_tts:.2f}s stt={t_stt:.2f}s CER={c:.3f}\n         heard: {text}")

    t = time.perf_counter()
    v = await ai.embed(["팀 프로젝트 갈등 해결 경험", "협업 중 의견 충돌을 해결한 경험", "좋아하는 음식"])
    sim = lambda a, b: float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    report("embeddings", sim(v[0], v[1]) > sim(v[0], v[2]),
           f"dim={len(v[0])} similar={sim(v[0], v[1]):.2f} unrelated={sim(v[0], v[2]):.2f} "
           f"{time.perf_counter() - t:.2f}s")
    return ok_all


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="use FakeAIClient (no running service needed)")
    args = ap.parse_args()
    if args.fake:
        from app.ai.fake import FakeAIClient

        fake = FakeAIClient()
        fake.stt_texts.append(TTS_TEXT)
        ai: AIClient = fake
    else:
        ai = HttpAIClient()
    ok = asyncio.run(run(ai))
    print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
