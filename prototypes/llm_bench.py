"""Spike: measure local LLM latency, JSON-schema adherence and VRAM on the 6GB box.

Usage: python prototypes/llm_bench.py --model qwen3.5:4b --runs 3
Talks only to the local Ollama server (default http://127.0.0.1:11434).
"""
import argparse
import json
import statistics
import subprocess
import time

import httpx

ANSWER = (
    "저는 이전 회사에서 주문 처리 API의 응답 시간이 길어지는 문제를 맡았습니다. "
    "먼저 APM으로 느린 쿼리를 찾았고, 주문 목록 조회에 인덱스가 없다는 걸 확인했습니다. "
    "복합 인덱스를 추가하고 N+1 쿼리를 배치 조회로 바꿔서 평균 응답 시간을 800밀리초에서 120밀리초로 줄였습니다. "
    "그 과정에서 팀원들과 코드 리뷰 기준도 새로 정리했습니다."
)
QUESTION = "최근 프로젝트에서 성능 문제를 해결한 경험을 말씀해 주세요."

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "need_followup": {"type": "boolean"},
        "reason": {"type": "string", "enum": ["vague", "no_example", "inconsistent", "resume_mismatch", "none"]},
        "anchor_quote": {"type": "string"},
        "question": {"type": "string"},
    },
    "required": ["need_followup", "reason", "anchor_quote", "question"],
}

EVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string", "enum": ["job_expertise", "logic", "specificity", "delivery"]},
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "quote": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["criterion", "score", "quote", "comment"],
            },
        }
    },
    "required": ["criteria"],
}


def gpu_mem_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout
    return int(out.strip().splitlines()[0])


def chat(client: httpx.Client, base: str, model: str, system: str, user: str, schema: dict) -> tuple[dict, float, dict]:
    t0 = time.perf_counter()
    r = client.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_ctx": 4096},
        },
        timeout=300,
    )
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    body = r.json()
    content = body["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"_invalid": content}
    stats = {
        "eval_count": body.get("eval_count"),
        "eval_tps": round(body.get("eval_count", 0) / (body.get("eval_duration", 1) / 1e9), 1),
        "prompt_tps": round(body.get("prompt_eval_count", 0) / (max(body.get("prompt_eval_duration", 1), 1) / 1e9), 1),
        "load_s": round(body.get("load_duration", 0) / 1e9, 2),
    }
    return parsed, elapsed, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--base", default="http://127.0.0.1:11434")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    base_mem = gpu_mem_mib()
    client = httpx.Client()
    fu_sys = (
        "너는 채용 면접의 꼬리질문 판단기다. 지원자 답변이 모호하거나 사례가 없거나 앞뒤가 맞지 않을 때만 꼬리질문을 만든다. "
        "꼬리질문은 지원자가 실제로 말한 표현을 anchor_quote에 그대로 복사하고 그 표현을 짚어서 묻는다. 새 주제를 꺼내지 않는다. "
        "합격·불합격을 판단하거나 추천하지 않는다. 필요 없으면 need_followup=false, reason='none', 빈 문자열을 쓴다."
    )
    fu_user = f"질문: {QUESTION}\n답변: {ANSWER}"
    ev_sys = (
        "너는 채용 면접 답변 평가기다. 4개 항목(job_expertise, logic, specificity, delivery)을 각각 1~5점으로 판정한다. "
        "quote에는 답변 원문에서 그대로 복사한 연속된 구절만 쓴다. 원문에 없는 말을 지어내지 않는다. "
        "합격·불합격을 판단하거나 추천하지 않는다. comment는 한국어 한두 문장."
    )
    ev_user = f"질문: {QUESTION}\n답변 원문: {ANSWER}"

    results = {"followup": [], "eval": []}
    peak = base_mem
    for kind, sys_p, user_p, schema in (
        ("followup", fu_sys, fu_user, FOLLOWUP_SCHEMA),
        ("eval", ev_sys, ev_user, EVAL_SCHEMA),
    ):
        for i in range(args.runs):
            parsed, elapsed, stats = chat(client, args.base, args.model, sys_p, user_p, schema)
            peak = max(peak, gpu_mem_mib())
            quotes_ok = None
            if kind == "eval" and "criteria" in parsed:
                quotes_ok = [c.get("quote", "") in ANSWER for c in parsed["criteria"]]
            if kind == "followup" and parsed.get("need_followup"):
                quotes_ok = [parsed.get("anchor_quote", "") in ANSWER]
            results[kind].append(elapsed)
            print(json.dumps({"kind": kind, "run": i, "elapsed_s": round(elapsed, 2), **stats,
                              "valid_json": "_invalid" not in parsed, "quotes_in_source": quotes_ok}, ensure_ascii=False))
            if i == 0:
                print("  sample:", json.dumps(parsed, ensure_ascii=False)[:600])

    ps = client.get(f"{args.base}/api/ps").json()
    for m in ps.get("models", []):
        print("ollama ps:", m["name"], "size_vram_MiB=", m.get("size_vram", 0) // 2**20, "size_MiB=", m.get("size", 0) // 2**20,
              "context=", m.get("context_length"))
    print(f"GPU MiB baseline={base_mem} peak_observed={peak}")
    for k, v in results.items():
        warm = v[1:] or v
        print(f"{k}: first={v[0]:.2f}s warm_median={statistics.median(warm):.2f}s")


if __name__ == "__main__":
    main()
