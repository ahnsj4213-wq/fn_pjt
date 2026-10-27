"""Spike: does whitespace-insensitive normalization fix the LLM quote mismatches seen in llm_bench?"""
import json, re, sys, unicodedata, httpx
sys.path.insert(0, "prototypes")
from llm_bench import ANSWER, QUESTION, EVAL_SCHEMA

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s​]+", "", s)
    s = re.sub(r"[\"'“”‘’`.,!?…·:;()\[\]{}~\-]", "", s)
    return s.lower()

sys_p = ("너는 채용 면접 답변 평가기다. 4개 항목(job_expertise, logic, specificity, delivery)을 각각 1~5점으로 판정한다. "
         "quote에는 답변 원문에서 글자 그대로 복사한 한 문장 이하의 연속 구절만 쓴다(띄어쓰기도 원문 그대로). 원문에 없는 말을 지어내지 않는다. "
         "합격·불합격을 판단하거나 추천하지 않는다. comment는 한국어 한두 문장.")
ok_raw = ok_norm = total = 0
for i in range(5):
    r = httpx.post("http://127.0.0.1:11434/api/chat", json={"model": "qwen3.5:4b", "stream": False, "think": False,
        "format": EVAL_SCHEMA, "options": {"temperature": 0.2, "num_ctx": 4096},
        "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": f"질문: {QUESTION}\n답변 원문: {ANSWER}"}]}, timeout=120)
    body = r.json(); d = json.loads(body["message"]["content"])
    for c in d["criteria"]:
        total += 1; ok_raw += c["quote"] in ANSWER; ok_norm += norm(c["quote"]) in norm(ANSWER)
    print(f"run {i}: {body['eval_count']} tok, {body['total_duration']/1e9:.1f}s, quotes:", [c['quote'][:40] for c in d['criteria']])
print(f"exact match {ok_raw}/{total}, normalized match {ok_norm}/{total}")
