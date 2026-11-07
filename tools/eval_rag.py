"""Check the company knowledge RAG against the real models and the running stack (ADR-0020).

    docker compose exec -T api python - retrieval < ../tools/eval_rag.py      search accuracy of every virtual company, no model output
    docker compose exec -T api python - compose < ../tools/eval_rag.py        question sets of every virtual company
    docker compose exec -T api python - interview <company keys> < ../tools/eval_rag.py   text-mode interviews through the API
    docker compose exec -T api python - nodocs < ../tools/eval_rag.py         a company with no documents

`compose` and `interview` sign up a throwaway candidate per company (rag-eval-*@example.invalid) and leave those rows in the database.
Text mode: the voice path (WebRTC, STT, TTS) is not exercised. Output holds the questions; it holds no real person's data.
"""
import asyncio
import json
import re
import secrets
import sys
import time
import uuid

import httpx
from sqlalchemy import select, text

from app.ai.client import get_ai_client
from app.ai.tasks.questions import flatten_resume
from app.ai.textrules import content_tokens
from app.core.config import get_settings
from app.core.db import session_scope
from app.evaluation.quotes import find_span
from app.models import InterviewQuestion
from app.questions import knowledge, seed_knowledge
from app.resumes.schemas import to_ai_resume

API = "http://localhost:8000"
RESUME = {
    "name": "김테스트",
    "education": [{"school": "휴지대학교", "major": "컴퓨터공학", "degree": "학사", "period": "2020.03~2024.02"}],
    "career": [{"company": "(주)데이터웨이브", "role": "AI 엔지니어 인턴", "period": "2023.07~2023.12",
                    "description": "물류 수요 예측 모델을 개선해 MAPE를 18% 줄였습니다."}],
    "projects": [{"title": "RAG 사내 문서 검색", "role": "개발", "period": "2023.09~2023.12",
                  "description": "BM25와 임베딩, 재순위화를 결합해 검색 정확도를 12% 높였고 200문항 평가 세트로 MRR과 Recall@5를 측정했습니다."},
                 {"title": "LLM 고객 문의 요약 챗봇", "role": "개발", "period": "2024.03~2024.06",
                  "description": "FastAPI로 요약 API를 만들고 응답 시간을 3.2초에서 1.4초로 줄였습니다."}],
    "certificates": [{"name": "정보처리기사", "issuer": "한국산업인력공단", "date": "2023"}, {"name": "SQLD", "issuer": "한국데이터산업진흥원", "date": "2023"}],
    "skills": ["Python", "PyTorch", "FastAPI", "SQL", "Docker", "LangChain", "Hugging Face"],
}
ANSWERS = [
    "RAG 사내 문서 검색 프로젝트에서 BM25와 임베딩을 결합하고 재순위화를 붙였습니다. 200문항 평가 세트로 MRR과 Recall@5를 비교해 정확도를 12% 높였습니다.",
    "인턴 때 물류 수요 예측 모델을 맡아 특성 공학과 시계열 교차 검증을 적용했습니다. 평가 기간을 고정해 비교했고 MAPE를 18% 줄였습니다.",
    "고객 문의 요약 챗봇에서 FastAPI 비동기 처리와 캐시를 적용해 응답 시간을 3.2초에서 1.4초로 줄였습니다. 병목은 구간별 로그로 찾았습니다.",
    "먼저 재현 조건을 정리하고 지표를 정한 뒤 작은 단위로 실험했습니다. 팀원과 결과를 공유하면서 효과가 확인된 방법만 반영했습니다.",
]


def company_id(row: dict) -> uuid.UUID:
    return seed_knowledge._id("company", row["key"])


async def retrieval() -> None:
    """Hit@1 and Hit@3 of a query's document inside its own company, and how often an unscoped search would have crossed companies."""
    settings, ai = get_settings(), get_ai_client()
    rows = seed_knowledge.load_file()
    names = {company_id(r): r["key"] for r in rows}
    n = top1 = top3 = own_first = 0
    lows, misses = [], []
    async with session_scope() as db:
        for r in rows:
            posting = await db.scalar(text("SELECT id FROM job_postings WHERE company_id = :c LIMIT 1"), {"c": company_id(r)})
            for g in r["eval"]:
                found = await knowledge.search(db, ai, settings.embed_model, company_id=company_id(r), posting_id=posting, query=g["query"], k=3)
                titles = [c.title for c in found]
                n += 1
                top1 += titles[:1] == [g["gold"]]
                top3 += g["gold"] in titles
                if g["gold"] not in titles[:1]:
                    misses.append({"company": r["key"], "query": g["query"], "gold": g["gold"], "got": titles})
                if found:
                    lows.append(found[0].similarity)
                (vec,) = await ai.embed([g["query"]])
                lit = "[" + ",".join(f"{x:.7g}" for x in vec) + "]"
                near = await db.scalar(text("SELECT company_id FROM company_knowledge WHERE embedding IS NOT NULL "
                                            "ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"), {"v": lit})
                own_first += names.get(near) == r["key"]
        # what a company's search gives for another company's question: how low must the similarity floor be
        unrelated = []
        for r, other in zip(rows, rows[1:] + rows[:1]):
            posting = await db.scalar(text("SELECT id FROM job_postings WHERE company_id = :c LIMIT 1"), {"c": company_id(r)})
            for g in other["eval"]:
                (vec,) = await ai.embed([g["query"]])
                lit = "[" + ",".join(f"{x:.7g}" for x in vec) + "]"
                unrelated.append(float(await db.scalar(text(
                    "SELECT max(1 - (embedding <=> CAST(:v AS vector))) FROM company_knowledge WHERE company_id = :c"), {"v": lit, "c": company_id(r)})))
    print(json.dumps({"queries": n, "hit_at_1": round(top1 / n, 3), "hit_at_3": round(top3 / n, 3), "unscoped_top_is_own_company": round(own_first / n, 3),
                      "gold_similarity_min": round(min(lows), 3), "gold_similarity_median": round(sorted(lows)[len(lows) // 2], 3),
                      "other_company_query_best_similarity_median": round(sorted(unrelated)[len(unrelated) // 2], 3),
                      "other_company_query_best_similarity_max": round(max(unrelated), 3), "misses": misses}, ensure_ascii=False, indent=1))


async def _candidate(c: httpx.AsyncClient, key: str) -> None:
    email = f"rag-eval-{key}-{secrets.token_hex(3)}@example.invalid"
    password = "Str0ng-pass-" + secrets.token_hex(4)
    r = await c.post("/api/auth/signup", json={"name": "평가 지원자", "email": email, "password": password, "password_confirm": password,
                                               "phone": "010-0000-0000", "birth_date": "1998-03-14", "gender": "F", "terms_agreed": True})
    if r.status_code >= 300:
        raise SystemExit(f"signup {r.status_code} {r.text[:200]}")
    r = await c.post("/api/auth/login", json={"role": "CANDIDATE", "email": email, "password": password})
    if r.status_code >= 300:
        raise SystemExit(f"login {r.status_code} {r.text[:200]}")
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]


async def _apply(c: httpx.AsyncClient, posting_id: str) -> str:
    doc = (await c.get("/api/consent/current")).json()
    r = await c.post(f"/api/candidate/postings/{posting_id}/consents", json={"version": doc["version"], "answers": {i["code"]: True for i in doc["items"]}})
    if r.status_code >= 300:
        raise SystemExit(f"consent {r.status_code} {r.text[:200]}")
    r = await c.post(f"/api/candidate/postings/{posting_id}/applications", json={"consent_id": r.json()["consent_id"], "resume": RESUME, "share_result": True})
    if r.status_code >= 300:
        raise SystemExit(f"apply {r.status_code} {r.text[:300]}")
    return r.json()["application_id"]


async def _compose(c, posting_id: str) -> tuple[str, float]:
    app_id = await _apply(c, posting_id)
    t0 = time.monotonic()
    r = await c.post(f"/api/candidate/applications/{app_id}/interview/prepare")
    if r.status_code >= 300:
        raise SystemExit(f"prepare {r.status_code} {r.text[:300]}")
    for _ in range(240):
        await asyncio.sleep(3)
        async with session_scope() as db:
            n = await db.scalar(text("SELECT count(*) FROM interview_questions WHERE application_id = :a"), {"a": app_id})
        if n:
            return app_id, time.monotonic() - t0
    raise SystemExit("compose timed out")


async def _questions(app_id: str) -> list[InterviewQuestion]:
    async with session_scope() as db:
        return list((await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == uuid.UUID(app_id))
                                      .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars())


async def _check(row: dict, others: list[dict], q: InterviewQuestion, app_id: str, company: uuid.UUID, posting: uuid.UUID) -> list[str]:
    """Each stated source must exist and be allowed for this interview; its quote must be in that source's text."""
    bad = []
    g = q.grounding or {}
    if not g.get("sources"):
        bad.append("no source")
    async with session_scope() as db:
        snapshot = (await db.execute(text("SELECT resume_snapshot FROM applications WHERE id = :a"), {"a": app_id})).scalar_one()
        for s in g.get("sources", []):
            if s["type"] == "KNOWLEDGE":
                k = (await db.execute(text("SELECT company_id, posting_id, title, text FROM company_knowledge WHERE id = :i"), {"i": s["id"]})).first()
                if k is None or k[0] != company or (k[1] is not None and k[1] != posting):
                    bad.append(f"knowledge outside scope {s['id']}")
                elif g.get("basis") and find_span(g["basis"]["text"], f"{k[2]}: {k[3]}", min_chars=4) is None:
                    bad.append("quote not in knowledge")
            if s["type"] == "RESUME_SNAPSHOT":
                flat = flatten_resume(to_ai_resume(snapshot))[0]
                if s["id"] != app_id or not all(find_span(e["text"], flat, min_chars=4) for e in g.get("evidence", [])):
                    bad.append("resume evidence not in snapshot")
    for other in others:
        if other["name"].replace("(가상)", "") in q.text:
            bad.append(f"names another company {other['key']}")
    return bad


def _uses_company_terms(q: str, row: dict) -> bool:
    words = set().union(*(content_tokens(f"{d['title']} {d['text']}") for d in row["docs"]))
    return bool(re.search(r"[A-Za-z]{3,}", q) and any(t.lower() in words for t in re.findall(r"[A-Za-z]{3,}", q))) or any(d["title"] in q for d in row["docs"])


async def compose() -> None:
    rows = seed_knowledge.load_file()
    keys = sys.argv[2:] or [r["key"] for r in rows]
    summary = []
    async with httpx.AsyncClient(base_url=API, timeout=60) as c:
        for row in [r for r in rows if r["key"] in keys]:
            c.cookies.clear()
            await _candidate(c, row["key"])
            pid = await _posting(company_id(row))
            app_id, took = await _compose(c, str(pid))
            qs = await _questions(app_id)
            bad, by_source = [], {}
            print(f"\n== {row['name']} ({took:.0f}초)")
            for q in qs:
                types = "+".join(s["type"] for s in (q.grounding or {}).get("sources", []))
                by_source[types] = by_source.get(types, 0) + 1
                problems = await _check(row, [r for r in rows if r is not row], q, app_id, company_id(row), pid)
                bad += problems
                print(f"{q.seq:>2} {q.slot_type:<13} {q.source:<13} {types:<16} {q.text}" + (f"  !! {problems}" if problems else ""))
            summary.append({"company": row["key"], "seconds": round(took), "sources": by_source, "problems": bad})
    print("\nSUMMARY " + json.dumps(summary, ensure_ascii=False))


async def nodocs() -> None:
    """A company without documents: its motivation question must come from the posting, and name no company fact."""
    row = {"key": "eval-nodocs", "name": "자료없음기업(가상)", "intro": "이 소개 문장은 확인되지 않았습니다.", "docs": [],
           "posting": {"title": "백엔드 개발자", "job_family": "DEV", "duties": "주문 API 개발과 운영.", "requirements": "Python 서버 개발 경험.", "preferred": "PostgreSQL 튜닝 경험."}}
    async with session_scope() as db:
        pid = await seed_knowledge.seed(db, row)
    async with httpx.AsyncClient(base_url=API, timeout=60) as c:
        await _candidate(c, row["key"])
        app_id, took = await _compose(c, str(pid))
    print(f"== {row['name']} ({took:.0f}초)")
    for q in await _questions(app_id):
        g = q.grounding or {}
        print(f"{q.seq:>2} {q.slot_type:<13} {q.source:<13} {'+'.join(s['type'] for s in g.get('sources', [])):<16} {g.get('replaced') or '-':<22} {q.text}")


async def _posting(cid: uuid.UUID) -> uuid.UUID:
    async with session_scope() as db:
        return await db.scalar(text("SELECT id FROM job_postings WHERE company_id = :c AND status = 'OPEN' LIMIT 1"), {"c": cid})


async def interview() -> None:
    rows = seed_knowledge.load_file()
    report = []
    async with httpx.AsyncClient(base_url=API, timeout=90) as c:
        for row in [r for r in rows if r["key"] in sys.argv[2:]]:
            c.cookies.clear()
            await _candidate(c, row["key"])
            pid = await _posting(company_id(row))
            app_id, _ = await _compose(c, str(pid))
            base = f"/api/candidate/applications/{app_id}"
            async with session_scope() as db:
                await db.execute(text("UPDATE interviews SET precheck_media_ok_at = now(), precheck_webrtc_ok_at = now() WHERE application_id = :a"), {"a": app_id})
            for path in ("/interview/start", "/interview/text-mode"):
                r = await c.post(base + path)
                if r.status_code >= 300:
                    raise SystemExit(f"{path} {r.status_code} {r.text[:300]}")
            print(f"\n== {row['name']}")
            n = 0
            while True:
                st = (await c.post(base + "/interview/next")).json()
                t = st.get("turn")
                if not t:
                    break
                answer = ANSWERS[n % len(ANSWERS)]
                n += 1
                tag = "  꼬리" if t.get("is_followup") else f"{t['main_no']:>2}번"
                print(f"{tag} Q: {t['question_text']}", flush=True)
                r = await c.post(f"{base}/interview/turns/{t['turn_id']}/text-answer", json={"text": answer})
                if r.status_code >= 300:
                    raise SystemExit(f"answer {r.status_code} {r.text[:300]}")
            qs = await _questions(app_id)
            fus = [q for q in qs if q.role == "FOLLOWUP"]
            async with session_scope() as db:
                decisions = (await db.execute(text("SELECT decision, reason, skip_cause, latency_ms FROM followup_decisions WHERE application_id = :a"), {"a": app_id})).all()
            problems = []
            for q in qs:
                problems += await _check(row, [r for r in rows if r is not row], q, app_id, company_id(row), pid)
            report.append({"company": row["key"], "turns": n, "followups": [{"q": q.text, "sources": [s["type"] for s in q.grounding.get("sources", [])],
                                                                               "anchor": (q.grounding.get("anchor") or {}).get("text")} for q in fus],
                           "decisions": [list(d) for d in decisions], "problems": problems})
    print("\nSUMMARY " + json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run({"retrieval": retrieval, "compose": compose, "interview": interview, "nodocs": nodocs}[sys.argv[1]]())
