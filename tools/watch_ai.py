"""Live view of what the LLM and RAG do during an interview: python -X utf8 tools/watch_ai.py

Reads the logs of the api and worker containers and prints only the AI events. The logs hold metadata, never a resume, an
answer or a prompt; the text of a reference question is read from the database for this screen only (SEC-08).
Ctrl+C ends it; it reconnects by itself when a container restarts.
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
KST = timezone(timedelta(hours=9))
TASK_LABEL = {"question_generate": "T2 질문 생성", "resume_question": "T3 이력서 질문", "followup_decide": "T4 꼬리질문 판단",
              "resume_extract": "T1 이력서 추출", "evaluate_answer": "T5 답변 평가", "code_review": "T8 코드 리뷰"}


def example_texts(ids: list[str]) -> list[str]:
    """The reference questions behind a search, for this screen only."""
    ids = [i for i in ids if re.fullmatch(r"[0-9a-f-]{36}", i)]
    if not ids:
        return []
    sql = "SELECT left(text, 60) FROM question_examples WHERE id IN (" + ",".join(f"'{i}'" for i in ids) + ")"
    r = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "aihire", "-d", "aihire", "-At", "-c", sql],
                       cwd=DEPLOY, capture_output=True, encoding="utf-8", errors="replace", check=False)
    return r.stdout.splitlines()


def knowledge_titles(ids: list[str]) -> list[str]:
    """The company documents a search found, for this screen only."""
    ids = [i for i in ids if re.fullmatch(r"[0-9a-f-]{36}", i)]
    if not ids:
        return []
    sql = "SELECT title FROM company_knowledge WHERE id IN (" + ",".join(f"'{i}'" for i in ids) + ")"
    r = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "aihire", "-d", "aihire", "-At", "-c", sql],
                       cwd=DEPLOY, capture_output=True, encoding="utf-8", errors="replace", check=False)
    return r.stdout.splitlines()


def fmt(e: dict) -> str | None:
    kind = e.get("event")
    if kind == "knowledge_search":
        head = f"[회사자료] 검색 {e.get('kind', ''):<8} {e.get('hits')}건  최고 유사도 {e.get('top_similarity')}"
        return "\n".join([head, *[f"         - {t}" for t in knowledge_titles(e.get("example_ids", []))]])
    if kind == "company_info_missing":
        return "[회사자료] 자료 없음: 공고 기반 직무 질문으로 대체"
    if kind == "llm_task":
        task = TASK_LABEL.get(e.get("task"), e.get("task"))
        err = f" 오류={e['error_code']}" if e.get("error_code") else ""
        return (f"[LLM] {task:<16} {e.get('status'):<8} {e.get('latency_ms', 0) / 1000:6.1f}초  대기 {e.get('queue_ms', 0) / 1000:.1f}초  "
                f"토큰 입력 {e.get('prompt_tokens', 0)}/출력 {e.get('completion_tokens', 0)}  레인 {e.get('lane')}{err}")
    if kind == "rag_search":
        head = f"[RAG] 참고 질문 검색 {e.get('kind', ''):<8} {e.get('hits')}건  최고 유사도 {e.get('top_similarity')}"
        return "\n".join([head, *[f"         - {t}" for t in example_texts(e.get("example_ids", []))]])
    if kind == "rag_skipped":
        return f"[RAG] 건너뜀 {e.get('kind')}  사유 {e.get('reason')}"
    if kind == "rag_copy_dropped":
        return f"[RAG] 참고 질문과 너무 비슷한 생성 질문 {e.get('count')}개 제외"
    if kind == "question_generate_failed":
        return f"[LLM] 질문 생성 실패  {e.get('status')}  (재시도 또는 질문은행으로 대체)"
    if kind == "questions_composed":
        return f"[질문] 질문 세트 완성 {e.get('count')}개"
    if kind == "followup_decided":
        cause = f" 사유 {e['reason']}" if e.get("reason") else ""
        return f"[꼬리] 결정 {e.get('status')}{cause}  {e.get('latency_ms')}ms"
    return None


def follow() -> None:
    cmd = ["docker", "compose", "logs", "-f", "--since", "0s", "--no-color", "--no-log-prefix", "api", "worker"]
    with subprocess.Popen(cmd, cwd=DEPLOY, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace") as p:
        for line in p.stdout:
            try:
                e = json.loads(line)
                text = fmt(e)
            except (ValueError, KeyError, TypeError):
                continue
            if text:
                at = datetime.fromisoformat(e["ts"]).astimezone(KST).strftime("%H:%M:%S")
                print(f"{at} {text}", flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("면접 AI 로그 대기 중입니다 (LLM·RAG 호출이 일어나면 표시됩니다). 끝내려면 Ctrl+C\n", flush=True)
    while True:
        follow()
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
