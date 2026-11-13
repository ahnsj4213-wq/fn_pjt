"""Measurement of question-set composition with a scripted noisy model (fake client, no GPU; 2026-11-11): model requests and
calls, the code-made (TEMPLATE) share per slot, reject counts per reason, the questions of the final set the quality gate would
refuse, and time, over four fixed candidates. `-s` prints the table. The asserts keep the composer within what it promises: the
call bound per area, ten questions, and no generated question in the set that fails the gate."""
import logging
import time

import pytest
from collections import Counter

from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Application, Company, JobPosting
from app.questions import compose as C
from app.questions import quality
from tests.fixtures import RESUME, apply, create_posting
from tests.helpers import candidate, recruiter
from tests.test_company_knowledge import DOC, _add, _embed, _posting_of
from tests.test_handlers import drain, fake_ai, q  # noqa: F401 - the fixture
from tests.test_interview import url

HAN = "".join(map(chr, (0x7528, 0x4EC0, 0x4E48)))
FEEDBACK_MARKS = ("[FOREIGN_SCRIPT]", "[NESTED]")

NEW_GRAD = {"name": "이신입", "education": [{"school": "한국대학교", "major": "컴퓨터공학", "degree": "학사", "period": "2021-2025"}],
            "projects": [{"title": "캠퍼스 중고거래 앱", "role": "백엔드", "period": "2024",
                          "description": "게시글 검색 API를 만들고 자주 찾는 검색어를 캐시에 저장해 응답을 빠르게 했다."}],
            "skills": ["Python", "FastAPI"]}
SENIOR = {"name": "박경력", "career": [{"company": "페이코어", "role": "백엔드 리드", "period": "2012-2024",
                                        "description": "결제 승인 서버를 이중화하고 장애 전환 절차를 만들어 무중단 배포를 운영했다."},
                                       {"company": "로지스텍", "role": "백엔드 개발자", "period": "2008-2012",
                                        "description": "배송 추적 API의 조회 쿼리를 튜닝해 응답 시간을 절반으로 줄였다."}],
          "skills": ["Java", "PostgreSQL"]}
DOC2 = "주문 이벤트 파이프라인은 주문 상태 변경을 메시지 큐로 전달해 재고와 정산이 같은 순서로 반영되게 합니다."

PROFILES = [  # (name, resume, documents)
    ("기본·자료없음", RESUME, []),
    ("기본·자료2", RESUME, [("정산 배치 재설계", DOC), ("주문 이벤트 파이프라인", DOC2)]),
    ("신입·자료1", NEW_GRAD, [("정산 배치 재설계", DOC)]),
    ("경력·자료없음", SENIOR, []),
]


def noisy(default, stubborn: bool = False):
    """The first answer of a request: question 1 with foreign script, question 2 with stacked conditions; an answer to a request
    that names failure codes (a regeneration) is clean, or (`stubborn`) still has question 1 in foreign script."""
    def reply(inp, messages):
        out = default(inp)
        regen = any(m in msg["content"] for msg in messages for m in FEEDBACK_MARKS)
        if regen and not stubborn:
            return out
        if regen:
            qs = out["questions"]
            if qs:
                qs[0] = {**qs[0], "text": qs[0]["text"].replace("본인", f"{HAN} 본인", 1).replace("맡은", f"{HAN} 맡은", 1)}
            return out
        qs = out["questions"]
        if qs:
            qs[0] = {**qs[0], "text": qs[0]["text"].replace("본인", f"{HAN} 본인", 1).replace("맡은", f"{HAN} 맡은", 1)}
        if len(qs) > 1:
            qs[1] = {**qs[1], "text": "트래픽이 몰릴 때 캐시가 없다면 " + qs[1]["text"]}
        return out
    return reply


class _Rejects(logging.Handler):
    def __init__(self):
        super().__init__()
        self.counts: Counter = Counter()

    def emit(self, record):
        f = getattr(record, "fields", None) or {}
        if f.get("event") == "question_reject":
            self.counts[f.get("reason")] += int(f.get("count") or 0)


async def _one(client, make_client, fake, resume, docs):
    company_id, _ = await recruiter(client)
    p = await create_posting(client, title="결제 백엔드 개발자", duties="주문·결제 API 개발과 운영\n정산 배치 처리 성능 개선")
    cand = make_client()
    await candidate(cand)
    r = await apply(cand, p["id"], resume=resume)
    app_id = r.json()["application_id"]
    company, _ = await _posting_of(app_id)
    for title, body in docs:
        await _add(company, title, body)
    if docs:
        await _embed(fake)
    before = len(fake.calls)
    t0 = time.monotonic()
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200
    await drain()
    ms = int((time.monotonic() - t0) * 1000)
    calls = [c for c in fake.calls[before:] if c.task in ("question_generate", "resume_question", "question_adapt")]
    rows = await q("SELECT slot_type, source FROM interview_questions WHERE application_id=:a AND role='MAIN' ORDER BY seq", a=app_id)
    return {"requests": len(calls), "model_calls": sum(len(c.messages) for c in calls), "rows": rows, "ms": ms,
            "gate_failures": await _gate_failures(app_id)}


async def _gate_failures(app_id: str) -> list[list[str]]:
    """The generated questions of the written set that quality.judge refuses (each against the ones before it)."""
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        posting, company = await db.get(JobPosting, app.posting_id), await db.get(Company, app.company_id)
    rows = await q("SELECT seq, slot_type, text, source, grounding FROM interview_questions WHERE application_id=:a "
                   "AND role='MAIN' ORDER BY seq", a=app_id)
    c = C._Ctx(app, posting, company, None, None)
    c.level = C.career_level(C.to_ai_resume(app.resume_snapshot), " ".join(filter(None, (posting.title, posting.requirements))))
    picks = [C.Picked(s, a, t, src, grounding=g, fixed=src == "AI" and a == "MOTIVATION" and t.startswith("이 회사의 ‘"))
             for s, a, t, src, g in rows]
    return [r for p in picks if p.source == "AI" and (r := quality.judge(c, p, others=[o for o in picks if o.seq < p.seq]))]


@pytest.mark.parametrize("stubborn", [False, True], ids=["fixed-on-regen", "stubborn"])
async def test_measure_composition(client, make_client, fake_ai, monkeypatch, stubborn):
    monkeypatch.setattr(get_settings(), "question_mode", "upfront")
    fake_ai.responses["question_generate"] = noisy(fake_ai._reply_question_generate, stubborn)
    fake_ai.responses["resume_question"] = noisy(fake_ai._reply_resume_question, stubborn)
    h = _Rejects()
    lg = logging.getLogger("app.questions")
    lg.addHandler(h)
    old = lg.level
    lg.setLevel(logging.INFO)
    try:
        results = []
        for name, resume, docs in PROFILES:
            results.append((name, await _one(client, make_client, fake_ai, resume, docs)))
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)
    total_slots = Counter()
    template = Counter()
    print(f"\n[{'stubborn' if stubborn else 'fixed-on-regen'}]")
    print("| 지원자 | 요청 수 | 모델 호출 수 | TEMPLATE/생성 슬롯 | 게이트 위반 | 소요 ms |")
    for name, r in results:
        gen = [(a, s) for a, s in r["rows"] if s != "CUSTOM"]
        for a, s in gen:
            total_slots[a] += 1
            template[a] += s == "TEMPLATE"
        print(f"| {name} | {r['requests']} | {r['model_calls']} | {sum(s == 'TEMPLATE' for _, s in gen)}/{len(gen)} | "
              f"{len(r['gate_failures'])} | {r['ms']} |")
        assert len(r["rows"]) == 10 and r["gate_failures"] == []
        assert r["requests"] <= 3 * quality.MAX_REQUESTS and r["model_calls"] <= 3 * quality.max_model_calls()  # 경험, 동기, 직무
    print("| 슬롯 | TEMPLATE 비율 |")
    for a in total_slots:
        print(f"| {a} | {template[a]}/{total_slots[a]} |")
    print("탈락 사유:", dict(sorted(h.counts.items())))
