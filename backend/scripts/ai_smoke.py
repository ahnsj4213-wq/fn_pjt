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

import httpx
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

    async def step(name: str, fn) -> None:
        """Run one check; an exception fails only this step and shows the server's error detail."""
        try:
            await fn()
        except httpx.HTTPStatusError as e:
            report(name, False, f"HTTP {e.response.status_code} {e.response.text[:300]}")
        except Exception as e:  # noqa: BLE001 - keep going so every capability is reported
            report(name, False, f"{type(e).__name__}: {e}")

    async def health() -> None:
        t = time.perf_counter()
        h = await ai.health()
        parts = {k: bool(h.get(k, {}).get("up")) for k in ("llm", "stt", "tts", "embed")}
        report("health", all(parts.values()),
               " ".join(f"{k}={'up' if v else 'DOWN'}" for k, v in parts.items())
               + f" | llm={h.get('llm')} stt={h.get('stt')} ({time.perf_counter() - t:.2f}s)")

    async def followups() -> None:
        for label, answer in (("clear answer", ANSWER), ("vague answer", VAGUE)):
            t = time.perf_counter()
            r = await ai.llm("followup_decide", {"question": QUESTION, "question_type": "JOB", "answer": answer,
                                                 "followups_left": 4}, lane="RT", deadline_ms=now_ms(5.0))
            el = time.perf_counter() - t
            detail = f"status={r.status} decision={r.output.get('decision')} reason={r.output.get('reason')} {el:.2f}s"
            if r.error:
                detail += f" error={r.error}"
            if r.output.get("question"):
                detail += f"\n         question: {r.output['question']}"
            report(f"T4 follow-up ({label}, budget 5s)", r.status in ("OK", "TIMEOUT") and el < 5.5, detail)

    async def evaluate() -> None:
        t = time.perf_counter()
        r = await ai.llm("evaluate_answer", {"question": QUESTION, "question_type": "JOB", "job_brief": "백엔드 개발자",
                                             "answer": ANSWER}, lane="BATCH")
        el = time.perf_counter() - t
        items = r.output.get("items", {})
        quotes_ok = all(v["quote"] is None or ANSWER[v["quote"]["start"]:v["quote"]["end"]] == v["quote"]["text"]
                        for v in items.values())
        lines = [f"status={r.status} model_calls={r.attempts.get('llm_calls')} regen={r.attempts.get('regen')} "
                 f"format_retries={r.attempts.get('format')} {el:.1f}s" + (f" error={r.error}" if r.error else "")]
        for k, v in items.items():
            why = f" reason={v['insufficient_reason']}" if v.get("insufficient_reason") else ""
            quote = f" quote=\"{v['quote']['text'][:40]}\"" if v.get("quote") else ""
            lines.append(f"{k}: {v['score']}/{v['evidence_status']}{why}{quote} comment=\"{v.get('comment', '')}\"")
        report("T5 evaluate_answer", r.status == "OK" and quotes_ok, "\n         ".join(lines))

    async def roundtrip() -> None:
        t = time.perf_counter()
        tts = await ai.tts(TTS_TEXT)
        t_tts = time.perf_counter() - t
        report("TTS", bool(tts.pcm48k), f"audio={tts.duration_ms}ms voice={tts.voice_model} {t_tts:.2f}s")
        t = time.perf_counter()
        stt = await ai.stt(to_16k(tts.pcm48k))
        t_stt = time.perf_counter() - t
        c = cer(TTS_TEXT, stt.text)
        report("STT (TTS round trip)", c <= 0.15,
               f"CER={c:.3f} model={stt.model}/{stt.device} dropped={stt.dropped} {t_stt:.2f}s\n         heard: {stt.text}")

    async def embeddings() -> None:
        t = time.perf_counter()
        v = await ai.embed(["팀 프로젝트 갈등 해결 경험", "협업 중 의견 충돌을 해결한 경험", "좋아하는 음식"])

        def sim(a, b) -> float:
            return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

        report("embeddings", sim(v[0], v[1]) > sim(v[0], v[2]),
               f"dim={len(v[0])} similar={sim(v[0], v[1]):.2f} unrelated={sim(v[0], v[2]):.2f} "
               f"{time.perf_counter() - t:.2f}s")

    for name, fn in (("health", health), ("T4 follow-up", followups), ("T5 evaluate_answer", evaluate),
                     ("TTS/STT", roundtrip), ("embeddings", embeddings)):
        await step(name, fn)
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
