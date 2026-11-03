"""Builds app/questions/data/question_examples.jsonl, the RAG example corpus (ai-architecture 6.1), from the team's
question spreadsheets. Run once when the spreadsheets change (needs openpyxl, a dev-only tool):

    python -I -X utf8 scripts/build_question_examples.py <IT 질의응답.xlsx> <인성질문.xlsx>

The examples are only shown to the question generator as tone/level references; they are never asked as they are.
Dropped on purpose: coding problems (spoken interview), questions naming a specific company, LLM-generated sheets
("생성_*", low quality), very long questions. Kinds:
- JOB: IT questions; RESUME: IT questions marked as experience questions (경험담여부=예)
- COLLABORATION / COMPANY / PERSONALITY: the personality sheet by category
"""
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

from openpyxl import load_workbook

OUT = Path(__file__).resolve().parents[1] / "app" / "questions" / "data" / "question_examples.jsonl"
MAX_CHARS = 150
COLLABORATION = {"협업/팀워크", "팀 협업", "갈등/의견 불일치", "갈등 해결", "커뮤니케이션"}
COMPANY = {"지원동기", "동기/지원 이유", "커리어 목표", "조직 문화 적합성"}


def norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def rows(path: str):
    wb = load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        head = next(it, None)
        if head:
            for r in it:
                yield ws.title, dict(zip(head, r))


def it_examples(path: str):
    for sheet, r in rows(path):
        text = (r.get("질문(한글)") or "").strip()
        company = str(r.get("기업명") or "").strip()
        if not sheet.startswith("기업_") or not text or len(text) > MAX_CHARS or r.get("코딩문제여부") == "예":
            continue
        if company and company.lower() in text.lower():
            continue
        kind = "RESUME" if r.get("경험담여부") == "예" else "JOB"
        yield {"kind": kind, "field": r.get("분야") or "", "text": text, "origin": sheet}


def personality_examples(path: str):
    for sheet, r in rows(path):
        text = (r.get("질문(한글)") or "").strip()
        cat = (r.get("카테고리") or "").strip()
        if not text or len(text) > MAX_CHARS or re.search(r"[A-Za-z]{2,}", text):  # Latin here = a company name
            continue
        kind = "COLLABORATION" if cat in COLLABORATION else "COMPANY" if cat in COMPANY else "PERSONALITY"
        yield {"kind": kind, "field": cat, "text": text, "origin": sheet}


def main(it_path: str, personality_path: str) -> None:
    seen: set[tuple[str, str]] = set()
    out = []
    for ex in [*it_examples(it_path), *personality_examples(personality_path)]:
        key = (ex["kind"], hashlib.sha256(norm(ex["text"]).encode()).hexdigest())
        if key not in seen:
            seen.add(key)
            out.append(ex)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in out), encoding="utf-8")
    counts: dict[str, int] = {}
    for e in out:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    print(OUT, len(out), counts)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
