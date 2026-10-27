"""Spike for ADR-0008: compare quote-normalisation rules on real LLM quotes and on forged quotes.

Rules:
  exact      - raw substring
  ws         - NFKC + remove all whitespace (agent E)
  ws_edge    - ws + ignore leading/trailing punctuation and unify quote marks (integrator proposal)
  ws_punct   - ws + remove all punctuation/symbols (agent C qn-1 / first spike)
A good rule accepts genuine quotes and rejects every forged one.
"""
import json
import re
import sys
import unicodedata

import httpx

sys.path.insert(0, "prototypes")
from llm_bench import ANSWER, EVAL_SCHEMA, QUESTION  # noqa: E402

QUOTE_MARKS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "「": '"', "」": '"'})
EDGE_PUNCT = " \t\n.,!?;:…·\"'()[]{}-~"


def ws(s: str) -> str:
    return re.sub(r"[\s​‌‍﻿]+", "", unicodedata.normalize("NFKC", s))


def ws_edge(s: str) -> str:
    return ws(s.translate(QUOTE_MARKS)).strip(EDGE_PUNCT)


def ws_punct(s: str) -> str:
    return "".join(ch for ch in ws(s) if unicodedata.category(ch)[0] not in "PS")


RULES = {"exact": lambda s: s, "ws": ws, "ws_edge": ws_edge, "ws_punct": ws_punct}

FORGED = [  # (source, forged quote) pairs from agent E's review
    ("성능을 3.5배 개선했습니다", "35배 개선"),
    ("C 언어로 구현했습니다", "C++ 언어로 구현"),
    ("C 언어로 구현했습니다", "C# 언어로 구현"),
    ("팀원은 2~3명이었습니다", "23명이었습니다"),
    ("1:1 멘토링을 했습니다", "11 멘토링을 했습니다"),
    ("오류율을 10% 줄였습니다", "오류율을 10 줄였습니다"),
    ("2,3명이 함께 했습니다", "23명이 함께 했습니다"),
]

sys_p = ("너는 채용 면접 답변 평가기다. 4개 항목(job_expertise, logic, specificity, delivery)을 각각 1~5점으로 판정한다. "
         "quote에는 답변 원문에서 글자 그대로 복사한 한 문장 이하의 연속 구절만 쓴다(띄어쓰기도 원문 그대로). "
         "원문에 없는 말을 지어내지 않는다. 합격·불합격을 판단하거나 추천하지 않는다. comment는 한국어 한두 문장.")
answers = [ANSWER,
           "협업할 때 의견이 다르면 먼저 상대 근거를 듣습니다. 지난 프로젝트에서 캐시 TTL을 5분으로 할지 30초로 할지 갈렸는데, "
           "p95 지연과 DB 부하를 2주간 A/B로 측정해서 60초로 정했습니다. 결과적으로 DB QPS가 40% 줄었습니다.",
           "Python과 Go를 주로 씁니다. Kafka 컨슈머 지연이 3.5배 늘었을 때 파티션을 12개에서 24개로 늘리고 배치 크기를 조정했습니다."]
genuine = []
for ans in answers:
    for _ in range(4):
        r = httpx.post("http://127.0.0.1:11434/api/chat", json={
            "model": "qwen3.5:4b", "stream": False, "think": False, "format": EVAL_SCHEMA,
            "options": {"temperature": 0.7, "num_ctx": 8192},
            "messages": [{"role": "system", "content": sys_p},
                         {"role": "user", "content": f"질문: {QUESTION}\n답변 원문: {ans}"}]}, timeout=180).json()
        for c in json.loads(r["message"]["content"])["criteria"]:
            genuine.append((ans, c["quote"]))

print(f"genuine quotes: {len(genuine)}, forged: {len(FORGED)}")
for name, f in RULES.items():
    ok = sum(f(q) != "" and f(q) in f(src) for src, q in genuine)
    leaked = [q for src, q in FORGED if f(q) in f(src)]
    print(f"{name:9s} genuine accepted {ok}/{len(genuine)}  forged accepted {len(leaked)}/{len(FORGED)} {leaked}")
misses = [q for src, q in genuine if ws_edge(q) not in ws_edge(src)]
print("ws_edge misses:", json.dumps(misses, ensure_ascii=False)[:800])
