"""Resume parsing and question personalisation, measured (ADR-0032). Read-only: nothing is written to the database.

    docker compose -p aihire -f deploy/docker-compose.yml run --rm --no-deps -v "<repo>/backend:/srv/backend" \
        -v "<repo>/tools:/srv/tools" api python /srv/tools/eval_personalization.py [postings] [repetitions]

Part 1 (no model): every uploaded resume file in the database is read again by the new parser, the model's stored rows standing
in for T1 (their descriptions cut to T1's 150 characters). Printed per file: the extracted text's length, the old and the new
snapshot text's length (flatten), the number of blocks kept, and how many of them belong to a row (by name / by resemblance).
Lengths and counts only: no resume text is printed.

Part 2 (real models): for each of the first N virtual postings, the composer's own job (3) and motivation (1) slots, through its
quality loop and fallback, once without a resume (the questions every candidate used to get) and once per made-up resume below.
One JSON line per run ("ROW ..."), then a summary ("SUMMARY ..."): the share of questions the model wrote (AI) vs code (TEMPLATE),
the share connected to the resume (verified link), the share whose basis is found in the posting or the documents, the share
passing every quality rule again (quality.judge, the naturalness rules), duplicates inside a set and across the resumes of one
posting, the reject codes, and p50/p95 of a composition's time. The questions printed are made from the postings and the
made-up resumes only.
"""
import asyncio
import json
import statistics
import sys
import time
import uuid
from types import SimpleNamespace

from sqlalchemy import text

from app.ai.client import get_ai_client
from app.ai.tasks.questions import similar_question
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Company, JobPosting
from app.questions import compose as C
from app.questions import quality
from app.questions.dbio import ShortReads
from app.resumes.schemas import ResumeContent, flatten
from app.resumes.sections import apply_sections, read_sections

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
POSTINGS = int(ARGS[0]) if ARGS else 3
REPS = int(ARGS[1]) if len(ARGS) > 1 else 1
PROBE = "--probe" in sys.argv  # also print every T2 question with its quotes and failure codes ("CAND ..."), for the reject analysis
WHO = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--who=")), None)

if PROBE:  # the check runs in this process (AI_MODE=direct): wrap it to see what each question quoted
    from app.ai.tasks.questions import QuestionGenerate
    _check = QuestionGenerate.check

    def _probe(self, inp, p, ctx, final):
        out = _check(self, inp, p, ctx, final)
        from app.evaluation.quotes import find_span
        failed = {f["text"]: f["codes"] for f in out.output.get("failed") or []}
        parts = [t for _, t in QuestionGenerate._parts(inp)]
        for q in p.questions:
            print("CAND " + json.dumps({"kind": inp["kind"], "resume": bool(inp.get("resume")), "text": q.text,
                                        "basis_quote": q.basis_quote, "resume_quote": q.resume_quote,
                                        "last_chance": bool(inp.get("last_chance")), "docs_given": len(inp.get("knowledge") or []),
                                        "in_given": any(find_span(q.basis_quote, t, min_chars=4) for t in parts),
                                        "in_used": any(find_span(q.basis_quote, u, min_chars=4) for u in inp.get("used_basis") or []),
                                        "in_resume": any(find_span(q.basis_quote, r["text"], min_chars=4) for r in inp.get("resume") or []),
                                        "codes": next((v for k, v in failed.items() if k.startswith(q.text.strip()[:30])), [])},
                                       ensure_ascii=False), flush=True)
        return out
    QuestionGenerate.check = _probe

# Made-up candidates with different backgrounds (no real person, company or school).
RESUMES = {
    "backend_cache": {
        "name": "가상A", "education": [{"school": "가상대학교", "major": "컴퓨터공학"}], "skills": ["Java", "Spring Boot", "Redis", "MySQL"],
        "career": [{"company": "가상커머스", "role": "백엔드 개발자", "period": "2022.03~2025.10",
                    "description": "주문 API 응답 지연을 Redis 캐시와 복합 인덱스로 450ms에서 120ms로 줄였습니다. 주문 이벤트 처리 실패를 재처리 큐로 옮겨 유실을 막았습니다."}],
        "projects": [{"title": "정산 배치 개선", "description": "하루 정산 배치를 청크 단위로 나누고 실패 구간만 다시 돌리도록 바꿔 처리 시간을 절반으로 줄였습니다."}]},
    "data_ml": {
        "name": "가상B", "education": [{"school": "가상대학교", "major": "통계학"}], "skills": ["Python", "PyTorch", "SQL", "Airflow"],
        "career": [{"company": "가상데이터랩", "role": "데이터 엔지니어", "period": "2023.01~2025.06",
                    "description": "사용자 로그를 Airflow로 매일 집계하는 파이프라인을 만들고, 지연 데이터가 들어와도 다시 계산되도록 했습니다."}],
        "projects": [{"title": "추천 모델 실험", "description": "클릭 로그로 추천 모델을 학습하고 오프라인 지표와 A/B 테스트 결과를 비교했습니다."}]},
    "agent_newgrad": {
        "name": "가상C", "education": [{"school": "가상대학교", "major": "소프트웨어학"}], "skills": ["Python", "FastAPI", "LangGraph", "PostgreSQL"],
        "projects": [{"title": "고객지원 업무 처리 AI 에이전트", "period": "2026.03~08",
                      "description": "FAQ 검색, 주문 상태 조회, 정책 확인을 도구로 나누고 LangGraph로 흐름을 설계했습니다. 도구 호출 전 파라미터를 검증해 잘못된 조회를 줄였습니다."},
                     {"title": "서버 운영 점검 도구", "period": "2025.09~12",
                      "description": "PostgreSQL 로그와 시스템 지표를 읽기 전용으로 조회하는 점검 도구를 만들고, 시간 기준을 UTC로 맞췄습니다."}]},
}


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1) if xs else 0.0


async def part1() -> None:
    async with session_scope() as db:
        rows = (await db.execute(text("SELECT id, extracted_text, extracted_fields FROM resume_files WHERE purpose = 'UPLOAD' "
                                      "AND extracted_text IS NOT NULL AND extracted_fields IS NOT NULL"))).all()
    for fid, source, fields in rows:
        model = {k: fields.get(k) or [] for k in ("education", "career", "projects", "skills", "certificates")}
        model["name"] = "x"
        for sec in ("career", "projects"):
            model[sec] = [{**r, "description": (r.get("description") or "")[:150] or None} for r in model[sec]]
        try:
            old = len(flatten(ResumeContent.model_validate({**{k: v for k, v in fields.items() if k != "details"}, "name": "x"})))
            new_fields = apply_sections(model, read_sections(source))
            new = len(flatten(ResumeContent.model_validate(new_fields)))
            d = new_fields["details"]
            print("FILE " + json.dumps({"file": str(fid)[:8], "source_chars": len(source), "old_snapshot": old, "new_snapshot": new,
                                        "details": len(d), "by_name": sum(x["how"] == "name" for x in d),
                                        "by_resemblance": sum(x["how"] == "similar" for x in d)}), flush=True)
        except Exception as e:  # noqa: BLE001 - report and go on
            print("FILE " + json.dumps({"file": str(fid)[:8], "error": type(e).__name__}), flush=True)


async def one(posting_id, who: str) -> dict:
    ai, model = get_ai_client(), get_settings().embed_model
    async with session_scope() as db:
        posting = await db.get(JobPosting, posting_id)
        company = await db.get(Company, posting.company_id)
        db.expunge_all()
    snapshot = ResumeContent.model_validate(RESUMES[who]).model_dump() if who != "none" else None
    c = C._Ctx(SimpleNamespace(id=uuid.uuid4(), resume_snapshot=snapshot), posting, company, None, ai, model, ShortReads())
    t = time.time()
    await C._fill_generated_kind(c.db, c, "JOB", [(4, "JOB_SKILL"), (5, "JOB_SKILL"), (6, "JOB_SKILL")], model)
    await C._fill_generated_kind(c.db, c, "COMPANY", [(3, "MOTIVATION")], model)
    seconds = round(time.time() - t, 1)
    qs = []
    for p in c.picked:
        g = p.grounding or {}
        link = (g.get("resume") or {}).get("link")
        qs.append({"slot": p.slot, "source": p.source, "text": p.text, "resume_link": (link or {}).get("ref"),
                   "link_how": (link or {}).get("how"), "basis_ok": p.source != "AI" or not quality._grounding(c, p),
                   "quality": quality.judge(c, p, others=[o for o in c.picked if o is not p])})
    inside = sum(similar_question(a["text"], b["text"]) for i, a in enumerate(qs) for b in qs[i + 1:])
    return {"posting": posting.title, "company": company.name, "who": who, "seconds": seconds, "requests": c.requests,
            "rejects": c.rejects, "failed": [{"codes": f.get("codes"), "text": f.get("text")} for f in c.failed][:12],
            "dup_inside": inside, "questions": qs}


async def part2() -> None:
    async with session_scope() as db:
        ids = [i for (i,) in (await db.execute(text("SELECT id FROM job_postings WHERE is_virtual ORDER BY company_id, title"))).all()]
    rows = []
    for rep in range(REPS):
        for pid in ids[:POSTINGS]:
            for who in WHO or ("none", *RESUMES):
                try:
                    r = await one(pid, who)
                except Exception as e:  # noqa: BLE001 - report and go on
                    r = {"posting_id": str(pid), "who": who, "error": f"{type(e).__name__}: {e}"[:300]}
                r["rep"] = rep
                rows.append(r)
                print("ROW " + json.dumps(r, ensure_ascii=False), flush=True)
    ok = [r for r in rows if "questions" in r]

    def share(sel, pred):
        qs = [q for r in ok if sel(r) for q in r["questions"]]
        return round(sum(map(pred, qs)) / len(qs), 3) if qs else None

    out = {}
    for name, sel in (("no_resume", lambda r: r["who"] == "none"), ("with_resume", lambda r: r["who"] != "none")):
        times = [r["seconds"] for r in ok if sel(r)]
        rej: dict = {}
        for r in ok:
            if sel(r):
                for k, v in (r["rejects"] or {}).items():
                    rej[k] = rej.get(k, 0) + v
        out[name] = {"runs": len(times), "ai_share": share(sel, lambda q: q["source"] == "AI"),
                     "personalised": share(sel, lambda q: bool(q["resume_link"])),
                     "basis_found": share(sel, lambda q: q["basis_ok"]),
                     "passes_quality": share(sel, lambda q: not q["quality"]),
                     "dup_inside_sets": sum(r["dup_inside"] for r in ok if sel(r)),
                     "p50_s": pct(times, 0.5), "p95_s": pct(times, 0.95), "rejects": rej}
    # the same posting, different resumes: how many questions are (nearly) the same text
    cross, pairs = 0, 0
    for rep in range(REPS):
        by_posting: dict = {}
        for r in ok:
            if r["rep"] == rep and r["who"] != "none":
                by_posting.setdefault(r["posting"], []).append([q["text"] for q in r["questions"]])
        for sets in by_posting.values():
            for i, a in enumerate(sets):
                for b in sets[i + 1:]:
                    pairs += len(a)
                    cross += sum(any(similar_question(x, y) for y in b) for x in a)
    out["cross_resume_same_question"] = round(cross / pairs, 3) if pairs else None
    print("SUMMARY " + json.dumps(out, ensure_ascii=False), flush=True)


async def main() -> None:
    if not PROBE:
        await part1()
    await part2()


asyncio.run(main())
