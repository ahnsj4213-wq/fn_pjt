"""Evaluation consistency experiment with the real model (rubric-v5, 2026-11-09).

Runs T5 evaluate_answer on tests/data/eval_consistency.json the way the pipeline does (`eval_samples` judgements, median per
criterion) and reports, per answer, the scores of every sample, the kept score, the cited sentence and the comment.
Then: level order (GOOD > MID > POOR), same-level pairs (other words, same quality), sample spread, and comments that use
reference-case words the answer does not have. Synthetic answers only; nothing is written to any database.

Run inside the api image (it needs the model servers):
  docker compose -p aihire run --rm --no-deps -v <repo>/tools:/tools -v <repo>/backend/tests/data:/data api \
      python /tools/eval_consistency.py /data/eval_consistency.json > result.json
"""
import asyncio
import json
import statistics
import sys
import time

from app.ai import rubric
from app.ai.client import get_ai_client
from app.core.config import get_settings
from app.evaluation.pipeline import median_judgement

KEYS = {"EXPERTISE": "전문성", "LOGIC": "논리성", "ATTITUDE": "태도", "DELIVERY": "전달력"}
TERMS = getattr(rubric, "EXAMPLE_TERMS", ("인덱스", "풀 스캔", "실행 계획", "복합 인덱스", "주문 테이블", "주문 조회", "캐시",
                                          "응답 시간", "0.3초", "3초", "모니터링"))


def leak(comment: str, answer: str) -> list[str]:
    flat = answer.replace(" ", "")
    return [t for t in TERMS if t in (comment or "") and t.replace(" ", "") not in flat]


async def judge(ai, q: dict, a: dict) -> dict:
    inp = {"question": q["question"], "question_type": q["slot_type"], "job_brief": "백엔드 개발자. API 서버 개발과 운영, 데이터베이스 설계",
           "question_kind": rubric.question_kind(q["slot_type"], q["question"]), "answer": a["text"], "is_no_answer": False}
    if "LEVEL_VIEW" in vars(rubric):
        inp["level"] = q["level"]
    outs, t0, calls = [], time.monotonic(), 0
    for _ in range(max(1, get_settings().eval_samples)):
        r = await ai.llm("evaluate_answer", inp, lane="BATCH")
        calls += r.attempts.get("llm_calls", 0)
        if r.status == "OK":
            outs.append(r.output)
    if not outs:
        return {"id": a["id"], "failed": True}
    kept = median_judgement(outs)
    items = kept["items"]
    return {"id": a["id"], "band": a["band"], "pair": a["pair"], "kind": inp["question_kind"], "sec": round(time.monotonic() - t0, 1),
            "llm_calls": calls,
            "samples": {c: [o["items"][c]["score"] for o in outs] for c in KEYS},
            "score": {c: items[c]["score"] for c in KEYS},
            "evidence": {c: (items[c].get("quote") or {}).get("text") for c in KEYS},
            "status": {c: items[c]["evidence_status"] for c in KEYS},
            "comment": {c: items[c]["comment"] for c in KEYS},
            "leak": {c: leak(o["items"][c]["comment"], a["text"]) for o in outs for c in KEYS if leak(o["items"][c]["comment"], a["text"])}}


def summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("failed")]
    total = {r["id"]: sum(r["score"].values()) for r in ok}
    by = {r["id"]: r for r in ok}
    order = {}
    for qid in sorted({r["id"].split("-")[0] for r in ok}):
        band = {b: statistics.mean(total[i] for i in total if i.startswith(qid + "-" + b)) for b in ("GOOD", "MID", "POOR")
                if any(i.startswith(qid + "-" + b) for i in total)}
        order[qid] = {"mean_total": band, "ordered": band.get("GOOD", 0) > band.get("MID", 0) > band.get("POOR", 0)}
    pairs = {}
    for r in ok:
        pairs.setdefault(r["pair"], []).append(r)
    diffs = []
    pair_out = {}
    for p, rs in pairs.items():
        if len(rs) < 2:
            continue
        d = {c: abs(rs[0]["score"][c] - rs[1]["score"][c]) for c in KEYS}
        diffs += d.values()
        pair_out[p] = {"ids": [x["id"] for x in rs], "diff": d, "total_diff": abs(total[rs[0]["id"]] - total[rs[1]["id"]])}
    spread = [max(v) - min(v) for r in ok for v in r["samples"].values()]
    return {"rubric": rubric.RUBRIC_VERSION, "answers": len(rows), "failed": len(rows) - len(ok), "order": order,
            "pairs": pair_out,
            "pair_abs_diff_mean": round(statistics.mean(diffs), 2) if diffs else None,
            "pair_within_1": f"{sum(d <= 1 for d in diffs)}/{len(diffs)}",
            "sample_spread_mean": round(statistics.mean(spread), 2) if spread else None,
            "sample_spread_max": max(spread) if spread else None,
            "comments_with_example_words": sum(len(r["leak"]) for r in ok),
            "unlinked": sum(s == "UNLINKED" for r in ok for s in r["status"].values()),
            "sec_mean": round(statistics.mean(r["sec"] for r in ok), 1) if ok else None,
            "by_id": {i: {"score": by[i]["score"], "total": total[i]} for i in total}}


async def main(path: str) -> None:
    data = json.load(open(path, encoding="utf-8"))
    ai = get_ai_client()
    rows = []
    for q in data["questions"]:
        for a in q["answers"]:
            rows.append(await judge(ai, q, a))
            print(f"{a['id']} done", file=sys.stderr, flush=True)
    print(json.dumps({"summary": summary(rows), "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
