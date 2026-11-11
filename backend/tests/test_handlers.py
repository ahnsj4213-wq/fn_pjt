"""Job handlers end to end on PostgreSQL (handoff step 2): jobs are claimed and run through the real worker
(`claim` + `run_one`, so claim hooks, requeue hooks and fencing all run) with FakeAIClient and a fake runner.

Covers ADR-0005 composition (slot order, custom question, resume grounding, BANK_FALLBACK, company-scoped bank),
TTS pre-synthesis, T1 extraction, pre-evaluation reuse, the final evaluation (scores, report, candidate feedback,
EVL-06 ON_HOLD, AI outage deferral) and coding grading (C8 comparison outside the runner).
"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from app.ai.client import set_ai_client
from app.ai.fake import FakeAIClient
from app.core.constants import INTRO_QUESTION
from app.core.db import get_engine, session_scope
from app.jobs import handlers, worker  # noqa: F401  registers the handlers
from app.jobs.queue import enqueue
from app.models import InterviewQuestion, QuestionBank, QuestionTtsAsset
from tests.evalstate import to_submitted
from tests.fixtures import RESUME, apply, create_posting, text_pdf
from tests.helpers import candidate, recruiter

RESOURCES = ["LLM", "LLM_EVAL", "CPU", "RUNNER", "NONE"]
SUM_SOLUTION = """
import sys
data = sys.stdin.read().split()
n, q = int(data[0]), int(data[1])
a = list(map(int, data[2:2 + n]))
pre = [0]
for x in a:
    pre.append(pre[-1] + x)
idx = 2 + n
out = []
for _ in range(q):
    l, r = int(data[idx]), int(data[idx + 1]); idx += 2
    out.append(str(pre[r] - pre[l - 1]))
print("\\n".join(out))
"""


@pytest.fixture
def fake_ai():
    fake = FakeAIClient()
    set_ai_client(fake)
    yield fake
    set_ai_client(None)


async def drain(limit: int = 200) -> int:
    """Run every claimable job (run_after <= now) until none is left."""
    n = 0
    while n < limit:
        ctx = await worker.claim(RESOURCES, "test-worker")
        if ctx is None:
            return n
        await worker.run_one(ctx)
        n += 1
    raise AssertionError("job loop did not settle")


async def q(sql: str, **params):
    async with session_scope() as db:
        return (await db.execute(text(sql), params)).all()


FIVE_QUESTIONS = [{"area": "PERSONALITY", "text": INTRO_QUESTION}, {"area": "MOTIVATION", "text": None}, {"area": "JOB_SKILL", "text": None},
                  {"area": "PROJECT", "text": None}, {"area": "COLLABORATION", "text": None}]


async def setup_app(client, make_client, *, settings: dict | None = None, default_plan: bool = False, **posting
                    ) -> tuple[str, str, httpx.AsyncClient]:
    """`default_plan=True` keeps the product's default (ten questions); otherwise the posting gets the old five-question plan,
    which most tests are written around (they test behavior, not the number of questions)."""
    company_id, _ = await recruiter(client)
    p = await create_posting(client, **posting)
    url = f"/api/recruiter/postings/{p['id']}/evaluation-settings"
    if not default_plan and not (settings and "interview" in settings):
        cur = (await client.get(url)).json()
        iv = {"question_plan": FIVE_QUESTIONS, "think_sec": cur["interview"]["think_sec"], "answer_sec": cur["interview"]["answer_sec"],
              "followup_max": cur["interview"]["followup_max"]}
        settings = {"weights": cur["weights"], **(settings or {}), "interview": iv}
        if settings.get("custom_question"):  # a plan has no use for the old single custom question: it becomes the job slot's text
            plan = [dict(s) for s in FIVE_QUESTIONS]
            plan[2]["text"] = settings.pop("custom_question")
            settings["interview"] = {**iv, "question_plan": plan}
    if settings:
        r = await client.put(url, json=settings)
        assert r.status_code == 200, r.text
    cand = make_client()
    await candidate(cand)
    r = await apply(cand, p["id"])
    assert r.status_code == 201, r.text
    return company_id, r.json()["application_id"], cand


async def prepare_and_compose(cand, app_id: str) -> list[InterviewQuestion]:
    """What POST …/interview/prepare does (READY -> PREPARING + compose job). The interview router is not mounted
    yet (handoff step 4), so the transition is applied directly here."""
    async with session_scope() as db:
        await db.execute(text("UPDATE applications SET attempt_status='PREPARING' WHERE id=:a"), {"a": app_id})
        await db.execute(text("INSERT INTO interviews(application_id, company_id) SELECT id, company_id FROM applications "
                              "WHERE id=:a ON CONFLICT DO NOTHING"), {"a": app_id})
        sid = await db.scalar(text("SELECT p.current_settings_id FROM applications a JOIN job_postings p "
                                   "ON p.id=a.posting_id WHERE a.id=:a"), {"a": app_id})
        await enqueue(db, "compose_questions", {"application_id": app_id, "settings_id": str(sid)},
                      application_id=uuid.UUID(app_id), dedupe_key=f"compose:{app_id}:{sid}")
    await drain()
    async with session_scope() as db:
        return list((await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == app_id)
                                      .order_by(InterviewQuestion.seq))).scalars())


# ── composition (ADR-0005) ─────────────────────────────────────────────────────
async def test_compose_fills_fixed_slots_and_pregenerates_tts(client, make_client, fake_ai):
    custom = "우리 팀의 결제 장애 대응 절차를 개선한다면 무엇부터 하시겠습니까?"
    _, app_id, cand = await setup_app(client, make_client, settings={
        "weights": {"expertise": 35, "logic": 25, "attitude": 25, "delivery": 15}, "custom_question": custom})
    qs = await prepare_and_compose(cand, app_id)
    # Default plan (SC-A06): 자기소개, 지원 동기, 직무, 경험, 협업; the old custom question takes the job slot.
    assert [x.seq for x in qs] == list(range(1, 6)) and all(x.role == "MAIN" for x in qs)
    assert [x.slot_type for x in qs] == ["PERSONALITY", "MOTIVATION", "JOB_SKILL", "PROJECT", "COLLABORATION"]
    assert qs[0].source == "CUSTOM" and qs[0].text == INTRO_QUESTION
    assert qs[2].source == "CUSTOM" and qs[2].text == custom
    # The experience question is grounded in the snapshot, including the `career` section (mapped to T3 `experience`).
    for rq in qs[3:4]:
        assert rq.source == "AI" and rq.grounding["evidence"]
        snapshot = json.dumps(RESUME, ensure_ascii=False)
        assert all(e["text"] in snapshot for e in rq.grounding["evidence"])
    fields = {e["field"].split("[")[0] for rq in qs[3:4] for e in rq.grounding["evidence"]}
    assert fields <= {"education", "experience", "projects", "certifications", "skills"}
    assert len({x.text for x in qs}) == 5
    status = await q("SELECT status FROM question_sets WHERE application_id=:a", a=app_id)
    assert status == [("READY",)]
    # tts_pregen ran (CPU resource) and cached every main question.
    async with session_scope() as db:
        assets = (await db.execute(select(QuestionTtsAsset).where(
            QuestionTtsAsset.question_id.in_([x.id for x in qs])))).scalars().all()
    assert len(assets) == 5 and all(a.voice_model == "fake-tts" and a.duration_ms > 0 for a in assets)
    assert await q("SELECT state FROM jobs WHERE kind IN ('compose_questions','tts_pregen') AND state <> 'SUCCEEDED'") == []


async def test_compose_resume_fallback_and_company_scoped_generation(client, make_client, fake_ai):
    """No grounded resume question -> BANK_FALLBACK; a family without job seeds -> T2 questions stored for this
    company only (C2) and never offered to another company's applicant."""
    fake_ai.responses["resume_question"] = lambda inp, msgs: {"questions": []}
    canary = "카나리아결제엔진"
    async with session_scope() as db:  # every category now has seed questions: take DESIGN's away for this test
        await db.execute(text("UPDATE question_bank SET is_active = false WHERE source = 'SEED' AND job_family = 'DESIGN'"))
    try:
        await _compose_scoped(client, make_client, canary)
    finally:
        async with session_scope() as db:
            await db.execute(text("UPDATE question_bank SET is_active = true WHERE source = 'SEED' AND job_family = 'DESIGN'"))


async def _compose_scoped(client, make_client, canary: str) -> None:
    # One job slot in the default plan: T2 grounds it in the first line of the brief (the title).
    company_a, app_a, cand_a = await setup_app(client, make_client, job_family="DESIGN", title=f"{canary} QA 엔지니어",
                                               duties=f"{canary} 회귀 테스트 자동화. 릴리스 품질 지표 관리")
    qs = await prepare_and_compose(cand_a, app_a)
    assert qs[3].source == "TEMPLATE" and qs[3].slot_type == "PROJECT"
    assert qs[3].grounding["fallback"] == "EXPERIENCE" and qs[3].bank_question_id is None  # an experience question, not the job bank's
    ai_qs = [x for x in qs if x.source == "AI"]
    assert ai_qs and all(x.grounding["basis"]["text"] for x in ai_qs) and any(canary in x.text for x in ai_qs)
    async with session_scope() as db:
        stored = (await db.execute(select(QuestionBank).where(QuestionBank.source == "AI"))).scalars().all()
    assert stored and {str(s.company_id) for s in stored} == {company_a}

    other = make_client()
    _, app_b, cand_b = await setup_app(other, make_client, job_family="DESIGN", duties="모바일 앱 회귀 테스트")
    qs_b = await prepare_and_compose(cand_b, app_b)
    assert not any(canary in x.text for x in qs_b)


async def test_compose_is_idempotent_and_skips_finished_attempts(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client)
    first = await prepare_and_compose(cand, app_id)
    async with session_scope() as db:  # a duplicate job for the same settings does nothing
        sid = (await db.execute(text("SELECT settings_id FROM question_sets WHERE application_id=:a"), {"a": app_id})).scalar()
        await enqueue(db, "compose_questions", {"application_id": app_id, "settings_id": str(sid)}, dedupe_key="dup")
    await drain()
    async with session_scope() as db:
        n = (await db.execute(text("SELECT count(*) FROM interview_questions WHERE application_id=:a"), {"a": app_id})).scalar()
    assert n == 5 and len(first) == 5


# ── T1 extraction ──────────────────────────────────────────────────────────────
async def test_extract_resume_maps_to_form_keys_and_marks_failure(make_client, fake_ai):
    def reply(inp, msgs):
        return {"name": "김지원", "education": [], "projects": [], "skills": ["Python", "Rust"],
                "experience": [{"company": "커머스랩", "role": "백엔드 개발자", "period": "2020-2024", "description": None}],
                "certifications": []}

    fake_ai.responses["resume_extract"] = reply
    cand = make_client()
    await candidate(cand)
    pdf = text_pdf(["김지원", "경력: 커머스랩 백엔드 개발자 2020-2024", "주문 처리 API 응답 지연 개선", "기술: Python, PostgreSQL"])
    r = await cand.post("/api/candidate/resume-files", files={"file": ("cv.pdf", pdf, "application/pdf")})
    assert r.status_code == 201 and r.json()["extraction_status"] == "PENDING"
    fid = r.json()["file_id"]
    await drain()
    out = (await cand.get(f"/api/candidate/resume-files/{fid}")).json()
    assert out["extraction_status"] == "DONE"
    f = out["fields"]
    assert f["career"] == [{"company": "커머스랩", "role": "백엔드 개발자", "period": "2020-2024", "description": None}]
    assert f["skills"] == ["Python"]  # "Rust" is not in the PDF text: dropped (RES-03)
    assert "experience" not in f and "certifications" not in f

    seen = {}

    def reply_sections(inp, msgs):
        seen.update(inp)
        return {"name": None, "education": [], "skills": [], "certifications": [],
                "experience": [{"company": "커머스랩", "role": None, "period": None, "description": "짧게"}], "projects": []}

    fake_ai.responses["resume_extract"] = reply_sections
    pdf2 = text_pdf(["경력사항", "커머스랩", "주문 처리 API 응답 지연 개선", "결제 장애 재발 방지", "자기소개서", "데이터로 판단합니다"])
    r = await cand.post("/api/candidate/resume-files", files={"file": ("cv3.pdf", pdf2, "application/pdf")})
    await drain()
    f = (await cand.get(f"/api/candidate/resume-files/{r.json()['file_id']}")).json()["fields"]
    assert f["intro"] == "데이터로 판단합니다" and "데이터로" not in seen["source_text"]
    assert f["career"][0]["description"] == "주문 처리 API 응답 지연 개선 결제 장애 재발 방지"

    fake_ai.responses.pop("resume_extract")
    fake_ai.fail["resume_extract"] = "invalid"
    r = await cand.post("/api/candidate/resume-files", files={"file": ("cv2.pdf", pdf, "application/pdf")})
    await drain()
    out = (await cand.get(f"/api/candidate/resume-files/{r.json()['file_id']}")).json()
    assert out["extraction_status"] == "FAILED" and out["manual_input_required"] is True


# ── evaluation ─────────────────────────────────────────────────────────────────
ANSWERS = [
    "저는 팀 프로젝트에서 일정이 밀렸을 때 우선순위를 다시 정하고 매일 진행 상황을 공유해서 마감을 지켰습니다.",
    "동료와 의견이 달랐을 때 각자의 근거를 표로 정리해 비교했고 데이터를 기준으로 합의했습니다.",
    "주문 처리 API에서 복합 인덱스를 추가하고 N+1 쿼리를 배치 조회로 바꿔 응답 시간을 절반으로 줄였습니다.",
    "정산 배치를 파티션 단위로 병렬화해서 네 시간 걸리던 작업을 오십 분으로 줄였고 재처리 절차도 만들었습니다.",
    "결제 시스템의 안정성을 높이는 일에 관심이 있어서 지원했고 장애 대응 경험을 살리고 싶습니다.",
    "회사의 사내 플랫폼이 여러 팀의 생산성을 높인다는 점이 인상적이었고 그 방향에 기여하고 싶습니다.",
    "트래픽이 늘면 먼저 지표로 병목을 찾고 캐시와 쿼리 튜닝을 차례로 적용해서 지연을 줄이겠습니다.",
    "외부 결제 대행사 장애에 대비해 타임아웃과 서킷 브레이커를 두고 재시도는 멱등 키로 보호했습니다.",
    "API 하위 호환성은 필드 추가만 허용하고 제거는 버전을 올려서 공지한 뒤 단계적으로 진행했습니다.",
]


async def answer_all(app_id: str, qs: list[InterviewQuestion], *, no_answer_seq: int = 5) -> list[str]:
    """Confirmed answers for every main question (seq `no_answer_seq` is a silent answer)."""
    ids = []
    async with session_scope() as db:
        for qq in qs:
            turn = await db.scalar(text(
                "INSERT INTO interview_turns(application_id, question_id, presentation_no, state, opened_at, closed_at, "
                "end_reason) VALUES (:a, :q, 1, 'CLOSED', now(), now(), 'BUTTON') RETURNING id"), {"a": app_id, "q": qq.id})
            silent = qq.seq == no_answer_seq
            body = None if silent else ANSWERS[qq.seq - 1]
            ids.append(str(await db.scalar(text(
                "INSERT INTO interview_answers(question_id, application_id, company_id, input_mode, end_reason, "
                "is_no_answer, transcript, eval_text, turn_id, opened_at, confirmed_at) "
                "SELECT :q, a.id, a.company_id, 'TEXT', 'BUTTON', :na, :t, :t, :turn, now(), now() "
                "FROM applications a WHERE a.id=:a RETURNING id"),
                {"q": qq.id, "a": app_id, "na": silent, "t": body, "turn": turn})))
    return ids


async def submit_and_register(app_id: str) -> None:
    await to_submitted(app_id)
    async with session_scope() as db:
        await enqueue(db, "evaluate_application", {"application_id": app_id, "trigger": "SUBMIT"},
                      application_id=uuid.UUID(app_id), dedupe_key=f"eval:{app_id}")


async def test_evaluation_reuses_pre_evaluations_and_writes_report(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)
    answer_ids = await answer_all(app_id, qs)
    async with session_scope() as db:
        for aid in answer_ids:
            await enqueue(db, "pre_evaluate", {"answer_id": aid}, dedupe_key=f"pre:{aid}")
    await drain()
    assert len(await q("SELECT 1 FROM answer_pre_evaluations")) == 5
    calls_before = sum(c.task == "evaluate_answer" for c in fake_ai.calls)

    # Model calls run with no connection checked out: on the CPU they take minutes, and a transaction left open
    # across them is closed by the 30 s idle-in-transaction limit (every real evaluation went ON_HOLD).
    held: list[int] = []
    llm = fake_ai.llm

    async def llm_watching_pool(task, input, **kw):
        held.append(get_engine().pool.checkedout())
        return await llm(task, input, **kw)

    fake_ai.llm = llm_watching_pool
    await submit_and_register(app_id)
    await drain()
    assert held and set(held) == {0}
    assert sum(c.task == "evaluate_answer" for c in fake_ai.calls) == calls_before  # EVL-05: all units reused
    (eval_status,), = await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id)
    assert eval_status == "DONE"
    (run_status, trig, prompt), = await q("SELECT status, trigger, prompt_version FROM evaluation_runs WHERE application_id=:a",
                                          a=app_id)
    assert (run_status, trig, prompt) == ("SUCCEEDED", "SUBMIT", "sha256:fake")

    rep = (await client.get(f"/api/recruiter/applications/{app_id}/report")).json()
    # Fake judge: every answered unit scores 3 -> 4 answers x 3 + 1 silent answer x 1 -> (13 / 5) x 20 = 52.00.
    # Text answers carry one measured value, the negative-expression score (no such phrase -> 100), blended 70/30 into
    # 표현 및 태도: 52 x 0.7 + 100 x 0.3 = 66.40.
    assert [c["score"] for c in rep["criteria"]] == ["52.00", "52.00", "66.40", "52.00"]
    w = {c["key"].lower(): c["weight"] for c in rep["criteria"]}
    assert rep["total_score"] == f"{(52 * (w['expertise'] + w['logic'] + w['delivery']) + 66.4 * w['attitude']) / 100:.2f}"
    assert rep["no_answer_count"] == 1 and rep["insufficient_count"] == 0 and rep["answered_main_count"] == 4
    # Details: the fake judge gives logic 4/3/3, expertise 3/3/3 and STAR 1/0/1/1 on every judged answer.
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert [x["score"] for x in det["sub"]["logic"]] == [80, 60, 60] and [x["score"] for x in det["sub"]["expertise"]] == [60, 60, 60]
    assert {x["key"]: x["rate"] for x in det["sub"]["star"]["items"]} == {"situation": 100, "task": 0, "action": 100, "result": 100}
    assert det["blend"]["scoring"] == "score-v3" and det["blend"]["rule_scores"] == {"attitude": 100.0}
    assert det["rules"]["negative"]["count"] == 0 and "speed" not in det["rules"]  # text answers: no speech measurements
    rows = await q("SELECT criterion, detail FROM answer_evaluations WHERE detail IS NOT NULL")
    assert {c for c, _ in rows} == {"EXPERTISE", "LOGIC"}
    assert rep["strengths"] and rep["improvements"] and rep["summary"]
    answered = [a for x in rep["questions"] for a in x["answers"] if not a["no_answer"]]
    for a in answered:  # AC-16: every OK score has a quote that is a span of the stored eval_text
        for item in a["items"]:
            qt = item["quote"]
            assert item["evidence"] == "OK" and a["display_text"][qt["start"]:qt["end"]] == qt["text"]
    fb = await q("SELECT score_expertise, coding_all_passed, strengths FROM candidate_feedback WHERE application_id=:a",
                 a=app_id)
    assert str(fb[0][0]) == "52.00" and fb[0][1] is None and fb[0][2]


async def test_evaluation_failures_retry_once_then_on_hold(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client)
    await answer_all(app_id, await prepare_and_compose(cand, app_id))
    fake_ai.fail["report_summary"] = "invalid"
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("PENDING",)]  # retry in 5 s
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("ON_HOLD",)]
    runs = await q("SELECT run_no, trigger, status FROM evaluation_runs WHERE application_id=:a ORDER BY run_no", a=app_id)
    assert runs == [(1, "SUBMIT", "FAILED"), (2, "AUTO_RETRY", "FAILED")]  # EVL-06: one automatic regeneration
    assert await q("SELECT 1 FROM recruiter_reports WHERE application_id=:a", a=app_id) == []

    del fake_ai.fail["report_summary"]  # ADM-06: recruiter re-runs the evaluation
    r = await client.post(f"/api/recruiter/applications/{app_id}/re-evaluate")
    assert r.status_code in (200, 202), r.text
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    (last,), = await q("SELECT trigger FROM evaluation_runs WHERE application_id=:a ORDER BY run_no DESC LIMIT 1",
                       a=app_id)
    assert last == "RECRUITER"


async def test_ai_outage_defers_without_consuming_attempts(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client)
    await answer_all(app_id, await prepare_and_compose(cand, app_id))
    fake_ai.down.add("llm")
    await submit_and_register(app_id)
    await drain()
    (state, attempts, deferrals), = await q("SELECT state, attempts, infra_deferrals FROM jobs "
                                            "WHERE kind='evaluate_application'")
    assert (state, attempts, deferrals) == ("QUEUED", 0, 1)  # AC-18 / OQ-23
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("PENDING",)]
    assert await q("SELECT status FROM evaluation_runs WHERE application_id=:a", a=app_id) == [("ABORTED_INFRA",)]
    fake_ai.down.clear()
    await q("UPDATE jobs SET run_after=now() WHERE kind='evaluate_application' RETURNING id")
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]


# ── coding grading + evaluation with coding ────────────────────────────────────
async def coding_app(client, make_client) -> tuple[str, httpx.AsyncClient]:
    (pid,), = await q("SELECT id FROM coding_problems WHERE template_key = 'prefix_sum' AND variant_of IS NULL")
    _, app_id, cand = await setup_app(client, make_client, coding_enabled=True, coding_problem_id=str(pid),
                                      coding_time_limit_min=30)
    return app_id, cand


async def submit_code(app_id: str, code: str) -> None:
    async with session_scope() as db:
        sub = await db.scalar(text(
            "INSERT INTO coding_submissions(application_id, company_id, code, code_sha256, kind) "
            "SELECT id, company_id, :c, sha256(convert_to(:c, 'UTF8')), 'MANUAL' FROM applications WHERE id=:a "
            "RETURNING id"), {"a": app_id, "c": code})
        await enqueue(db, "grade_coding", {"submission_id": str(sub)}, application_id=uuid.UUID(app_id),
                      dedupe_key=f"grade:{app_id}")


@pytest.mark.parametrize(("code", "outcomes"), [
    (SUM_SOLUTION, {"PASS"}),
    ("print(0)", {"PASS", "WRONG_ANSWER"}),
    ("def broken(:\n", {"RUNTIME_ERROR"}),
    ("   \n", {"NOT_RUN"}),
])
async def test_grade_coding_compares_outside_runner(client, make_client, fake_runner, code, outcomes):
    app_id, _ = await coding_app(client, make_client)
    await submit_code(app_id, code)
    await drain()
    (status, p, total, sc, compile_ok), = await q(
        "SELECT grading_status, passed, total, score, compile_ok FROM coding_submissions WHERE application_id=:a", a=app_id)
    # The application's own variant: 2 public examples + the hidden cases generated for it.
    expected = [o for (o,) in await q("SELECT t.expected_output FROM coding_testcases t JOIN coding_problems v ON v.id = t.problem_id "
                                      "WHERE v.variant_for=:a ORDER BY t.ordinal", a=app_id)]
    passed = {SUM_SOLUTION: len(expected), "print(0)": sum(e == "0\n" for e in expected)}.get(code, 0)
    assert total == len(expected) >= 5 and 0 < passed or code not in (SUM_SOLUTION, "print(0)")
    assert (status, p, total) == ("DONE", passed, len(expected))
    assert str(sc) == f"{round(100 * passed / len(expected), 2):.2f}"
    assert compile_ok is (code == SUM_SOLUTION or code == "print(0)")
    assert {o for (o,) in await q("SELECT outcome FROM coding_test_results")} == outcomes
    if code.strip():
        assert len(fake_runner["requests"]) == 1 and len(fake_runner["requests"][0]["cases"]) == len(expected)
    else:
        assert fake_runner["requests"] == []  # empty code is not executed (6.5)


async def test_grading_defers_on_sandbox_error_and_evaluation_waits(client, make_client, fake_ai, fake_runner):
    app_id, cand = await coding_app(client, make_client)
    await answer_all(app_id, await prepare_and_compose(cand, app_id))
    fake_runner["sandbox_error"] = True
    await submit_code(app_id, SUM_SOLUTION)
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT state, attempts FROM jobs WHERE kind='grade_coding'") == [("QUEUED", 0)]
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("PENDING",)]
    fake_runner["sandbox_error"] = False
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()
    rep = await q("SELECT score_coding, total_score FROM recruiter_reports WHERE application_id=:a", a=app_id)
    # Coding score (ADR-0017): every test passed, the fake judges 4/4/4: 100 x .6 + 80 x .4 = 92.
    (sc, total), = rep
    (areas,) = await q("SELECT score_expertise, score_logic, score_attitude, score_delivery FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert str(sc) == "92.00" and [str(x) for x in areas] == ["52.00", "52.00", "66.40", "52.00"]
    assert float(total) == round(float(areas[0]) * .3 + float(areas[1]) * .2 + float(areas[2]) * .15 + float(areas[3]) * .15 + 92 * .2, 2)
    assert await q("SELECT coding_all_passed FROM candidate_feedback WHERE application_id=:a", a=app_id) == [(True,)]
    # T8 never sees hidden tests (COD-06, SC-79): the hidden inputs are absent from every code_review prompt.
    hidden = [i for (i,) in await q("SELECT input FROM coding_testcases WHERE visibility='HIDDEN'")]
    prompts = [m["content"] for c in fake_ai.calls if c.task == "code_review" for msgs in c.messages for m in msgs]
    assert prompts and not any(h.strip() in p for h in hidden for p in prompts)


async def test_company_items_are_graded_and_weighted_into_the_total(client, make_client, fake_ai):
    """SC-R07: the company's own item is graded once over the interview, scored by code (중 = 60) and weighted."""
    item = {"name": "책임감", "description": "맡은 일을 끝까지 하는가", "high": "끝까지 책임진 사례가 있다", "mid": "책임을 언급한다",
            "low": "책임에 대한 언급이 없다", "weight": 20}
    off = {**item, "name": "리더십", "weight": 0}  # 0% = not evaluated
    _, app_id, cand = await setup_app(client, make_client, settings={
        "weights": {"expertise": 25, "logic": 25, "attitude": 20, "delivery": 10}, "custom_items": [item, off]})
    qs = await prepare_and_compose(cand, app_id)
    await answer_all(app_id, qs)
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    calls = [c for c in fake_ai.calls if c.task == "custom_criteria"]
    assert len(calls) == 1 and [i["name"] for i in calls[0].input["items"]] == ["책임감"]  # the 0% item is left out
    (det, total), = await q("SELECT detail, total_score FROM recruiter_reports WHERE application_id=:a", a=app_id)
    (c,) = det["custom"]
    assert c["name"] == "책임감" and c["grade"] == "중" and c["score"] == 60 and c["weight"] == 20 and c["quote"]
    # 52 x (25+25+10)% + 66.4 x 20% (표현 및 태도, blended with the negative-expression score) + 60 x 20% = 56.48
    assert str(total) == "56.48"
    view = (await client.get(f"/api/recruiter/applications/{app_id}/report-view")).json()
    own = [x for x in view["criteria"] if x.get("custom")]
    assert [(x["label"], x["score"], x["weight"]) for x in own] == [("책임감", 60, 20)]
    assert any(a["label"] == "책임감" for a in view["areas"]) and len(view["criteria"]) == 5


async def test_an_interview_with_no_answers_gets_fixed_texts_not_invented_praise(client, make_client, fake_ai):
    """Nothing was answered: every area is the lowest score, and the summary and feedback are not written by the model."""
    _, app_id, cand = await setup_app(client, make_client)
    await prepare_and_compose(cand, app_id)
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    assert not [c for c in fake_ai.calls if c.task in ("report_summary", "candidate_feedback")]
    (summary, strengths, improvements, total), = await q(
        "SELECT summary, strengths, improvements, total_score FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert "답변한 내용이 없어" in summary and strengths == [] and len(improvements) == 1 and float(total) == 20.0
    (fb_strengths, fb_improvements), = await q("SELECT strengths, improvements FROM candidate_feedback WHERE application_id=:a", a=app_id)
    assert fb_strengths == [] and len(fb_improvements) == 1


# ── the ten-question default and resume questions that say what they are about ─────────────────────────────────
async def test_default_plan_is_ten_questions_with_fixed_counts_per_type(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client, default_plan=True)
    qs = await prepare_and_compose(cand, app_id)
    assert [x.seq for x in qs] == list(range(1, 11)) and all(x.role == "MAIN" for x in qs)
    from collections import Counter
    assert Counter(x.slot_type for x in qs) == {"PERSONALITY": 2, "MOTIVATION": 1, "JOB_SKILL": 3, "PROJECT": 3, "COLLABORATION": 1}
    assert qs[0].text == INTRO_QUESTION and qs[0].source == "CUSTOM"
    assert {x.answer_mode for x in qs} == {"SPEECH"}  # no live coding, whatever the job family
    assert len({x.text for x in qs}) == 10, "no question is asked twice"
    (main_count,) = (await q("SELECT main_count FROM question_sets WHERE application_id=:a", a=app_id))[0]
    assert main_count == 10


async def test_resume_questions_name_the_part_of_the_resume_they_are_about(client, make_client, fake_ai):
    from app.questions.compose import RESUME_TAILS
    _, app_id, cand = await setup_app(client, make_client, default_plan=True)
    qs = [x for x in await prepare_and_compose(cand, app_id) if x.slot_type == "PROJECT"]
    assert len(qs) == 3
    assert any(x.source == "AI" for x in qs)
    for x in qs:
        assert x.source in ("AI", "TEMPLATE")
        if x.source == "AI":
            quote = x.grounding["evidence"][0]["text"]
            # "커머스랩 백엔드 개발자 업무에서 …하셨는데, <question>": the resume is never named or quoted
            assert "이력서" not in x.text and "‘" not in x.text and "적으신" not in x.text
            assert quote[:12] in x.text and x.grounding["asked"] and any(x.text.endswith(t) for t in RESUME_TAILS)  # the model's own wording is kept only as grounding.asked
    assert any(x.text.startswith(("커머스랩 백엔드 개발자 업무에서", "정산 배치 개선 프로젝트에서")) for x in qs)


def test_the_single_custom_question_takes_only_the_first_job_slot():
    from app.models import PostingEvalSettings
    from app.questions.compose import plan_of
    s = PostingEvalSettings(question_plan=None, custom_question="우리 팀의 결제 장애 대응을 개선한다면 무엇부터 하시겠습니까?")
    plan = plan_of(s)
    jobs = [t for a, t in plan if a.value == "JOB_SKILL"]
    assert len(plan) == 10 and jobs == [s.custom_question, None, None]


async def test_a_yes_or_no_resume_question_fails_with_its_code_and_passes_on_the_last_request(fake_ai):
    from app.ai.tasks import TASKS
    from app.ai.tasks.questions import ResumeQuestion
    assert isinstance(TASKS["resume_question"], ResumeQuestion)
    resume = {"experience": [{"company": "커머스랩", "role": "백엔드 개발자", "period": "2020-2024",
                              "description": "주문 처리 API의 응답 지연을 줄이기 위해 복합 인덱스를 추가했다."}]}
    quote = "주문 처리 API의 응답 지연을 줄이기 위해 복합 인덱스를 추가했다"
    replies = iter([
        {"questions": [{"text": "주문 처리 API의 응답 지연을 줄이기 위해 복합 인덱스를 추가한 경험이 있나요?",
                        "evidence": [{"field": "experience[0].description", "quote": quote}]}]},
        {"questions": [{"text": "주문 처리 API의 응답 지연을 줄일 때 왜 복합 인덱스를 선택했고 결과는 어땠습니까?",
                        "evidence": [{"field": "experience[0].description", "quote": quote}]}]},
    ])
    fake_ai.responses["resume_question"] = lambda inp, messages: next(replies)
    r = await fake_ai.llm("resume_question", {"resume": resume, "job_brief": "백엔드", "n": 1}, lane="INTERACTIVE")
    # one answer per request: the yes/no question comes back with its code for the composer's second request (quality.fill)
    assert r.status == "OK" and len(fake_ai.calls[0].messages) == 1 and r.output["questions"] == []
    assert r.output["failed"][0]["codes"] == ["YES_NO"]
    r = await fake_ai.llm("resume_question", {"resume": resume, "job_brief": "백엔드", "n": 1, "feedback": r.output["failed"],
                                              "last_chance": True}, lane="INTERACTIVE")
    assert "[YES_NO]" in fake_ai.calls[1].messages[0][-1]["content"]  # the reason is in the second request
    assert [x["text"] for x in r.output["questions"]] == ["주문 처리 API의 응답 지연을 줄일 때 왜 복합 인덱스를 선택했고 결과는 어땠습니까?"]


def test_technical_failure_words_are_not_mistaken_for_questions_about_disability():
    from app.ai.textrules import is_forbidden_question as bad
    for ok in ("고객 배치잡 지연과 DB 장애가 겹쳤을 때 무엇을 근거로 어떻게 대응하셨습니까?", "서비스 장애 복구 절차를 어떻게 만드셨나요?", "장애 대응 훈련은 어떻게 진행했습니까?", "서비스에 장애가 있었을 때 무엇을 먼저 확인하셨습니까?"):
        assert not bad(ok), ok
    for no in ("장애인이십니까?", "신체적 장애가 있으신가요?", "장애 등급이 어떻게 되나요?", "건강 상태는 어떤가요?", "결혼 계획이 있으십니까?", "나이가 어떻게 되나요?"):
        assert bad(no), no


def test_the_lead_in_names_the_whole_sentence_of_the_resume_not_just_the_short_quote():
    from app.questions.compose import RESUME_TAILS, ground_question
    desc = "입사 첫해에 사내 도구를 만들었다. Greenplum 대용량 DB를 운영하며 고객 배치잡 지연이 발생했을 때 실행계획과 Grafana 지표를 비교해 원인을 찾았다. 이후 재발을 막았다."
    resume = {"experience": [{"company": "데이터웍스", "role": "DB 엔지니어", "period": "2019-2023", "description": desc}]}
    start = desc.index("Grafana 지표")
    text = ground_question(resume, {"text": "그때 무엇을 근거로 어떻게 해결하셨습니까?",
                                    "evidence": [{"field": "experience[0].description", "start": start, "end": start + 10, "text": "Grafana 지표"}]})
    assert text.startswith("데이터웍스 DB 엔지니어 업무에서 Greenplum 대용량 DB를 운영하며 고객 배치잡 지연")
    assert "원인을 찾으셨는데, " in text and "이력서" not in text and "2019" not in text
    assert "입사 첫해" not in text and "재발" not in text  # only the sentence the quote belongs to
    assert text.endswith(RESUME_TAILS[0]) and "그때 무엇을" not in text  # the model's sentence is not used


def test_three_resume_questions_use_different_how_or_why_questions_and_never_ask_yes_or_no():
    from app.ai.tasks.questions import is_yes_no
    from app.questions.compose import RESUME_TAILS, ground_question
    resume = {"experience": [{"company": "A", "role": "B", "period": "2020", "description": "정산 서비스의 DB 장애 대응 절차를 만들었다."}]}
    ev = {"field": "experience[0].description", "start": 0, "end": 20, "text": "정산 서비스의 DB 장애 대응 절차"}
    texts = [ground_question(resume, {"text": "만들었습니까?", "evidence": [ev]}, n) for n in range(3)]
    assert len({t.split("만드셨는데, ")[1] for t in texts}) == 3
    assert all(not is_yes_no(t) and t.endswith("?") and any(w in t for w in ("무엇", "어떻게")) for t in texts) and len(RESUME_TAILS) == 3


def test_resume_sentences_are_told_back_politely_whatever_their_ending():
    from app.ai.textrules import polite_did as _did
    cases = {"복합 인덱스를 추가했다.": "복합 인덱스를 추가하셨는데", "원인을 찾았다": "원인을 찾으셨는데", "사내 도구를 만들었다": "사내 도구를 만드셨는데",
             "응답 지연을 줄였다": "응답 지연을 줄이셨는데", "지표를 맞췄습니다.": "지표를 맞추셨는데", "응답 지연을 3.2초에서 1.4초로 단축": "응답 지연을 3.2초에서 1.4초로 단축하셨는데",
             "배포 자동화 담당함": "배포 자동화 담당하셨는데"}
    for given, want in cases.items():
        assert _did(given) == want, given
    assert _did("Python, SQL") is None


def test_yes_no_detection_ignores_questions_with_an_interrogative_word():
    from app.ai.tasks.questions import is_yes_no
    for yes_no in ("복합 인덱스를 추가한 경험이 있나요?", "장애를 해결하셨나요?", "Kafka를 해 보셨습니까?", "이 일을 직접 하셨습니까?"):
        assert is_yes_no(yes_no), yes_no
    for ok in ("어떻게 극복하셨습니까?", "무엇을 근거로 원인을 좁혔나요?", "왜 그 방법을 선택하셨습니까?", "결과는 어땠나요?"):
        assert not is_yes_no(ok), ok


async def test_job_questions_prefer_the_bank_questions_nearest_to_the_posting(client, make_client, fake_ai):
    """The posting's duties pick the job questions: the question about them is taken before an unrelated one."""
    from app.models import QuestionBank
    from app.questions.compose import _Ctx, _rank_by_relevance

    class Ai:  # a tiny embedding: one axis for "database", one for "screen"
        async def embed(self, texts):
            return [[1.0 if "데이터베이스" in t or "DB" in t else 0.0, 1.0 if "화면" in t else 0.0] + [0.0] * (1024 - 2) for t in texts]

    rows = [QuestionBank(text="화면 렌더링 성능 문제를 개선했던 방법을 말씀해 주세요."), QuestionBank(text="데이터베이스 트랜잭션 격리 수준을 고른 기준을 말씀해 주세요."),
            QuestionBank(text="웹 접근성을 높인 방법을 말씀해 주세요.")]
    c = _Ctx(None, None, None, None, Ai())
    ranked = await _rank_by_relevance(c, rows, "백엔드 개발자 업무: DB 설계와 성능 개선")
    assert ranked[0].text.startswith("데이터베이스")
    class Down:
        async def embed(self, texts):
            from app.ai.client import AIUnavailable
            raise AIUnavailable("down")
    assert await _rank_by_relevance(_Ctx(None, None, None, None, Down()), rows, "x") == rows  # embeddings down: unchanged order


async def test_common_bank_questions_are_fitted_to_the_resume_and_keep_their_text_when_a_rewrite_fails(client, make_client, fake_ai):
    from app.ai.tasks.questions import flatten_resume

    def adapt(inp, msgs):
        flat = flatten_resume(inp["resume"])[0]
        taken = tuple(a["entry"] for a in inp.get("avoid") or [])  # the job the resume question asks about is left to it
        line = next(ln.split(": ", 1)[1] for ln in flat.splitlines()
                    if ln.startswith(("experience[", "projects[")) and not ln.startswith(taken or ("\0",)) and len(ln.split(": ", 1)[-1]) >= 12)
        quote = line[:12]
        good, bad = inp["items"][0], inp["items"][1]
        return {"questions": [{"id": good["id"], "basis_quote": quote, "scene": "여러 팀과 일정을 맞추는 일에서"},
                              {"id": bad["id"], "basis_quote": "한국대학교 컴퓨터공학"}]}  # school: not work, so not used
    fake_ai.responses["question_adapt"] = adapt
    _, app_id, cand = await setup_app(client, make_client, default_plan=True)
    qs = await prepare_and_compose(cand, app_id)
    # 2026-11-10: no question comes from the question bank; 인성·협업 are made by code from the resume or the posting
    assert not [x for x in qs if x.source in ("BANK", "BANK_FALLBACK") or x.bank_question_id is not None and x.source != "AI"]
    common = [x for x in qs if x.slot_type in ("PERSONALITY", "COLLABORATION") and x.source != "CUSTOM"]
    assert common and all(x.source == "TEMPLATE" and x.grounding["sources"] and x.grounding["fallback"] == "COMMON" for x in common)

def test_the_adapted_sentence_is_built_by_code_around_the_bank_question():
    from app.ai.tasks.question_adapt import fit
    q = "피드백을 받고 일하는 방식을 바꿨던 경험을 말씀해 주세요."
    quote = "물류 수요 예측 모델 개선, 예측 오차 18% 감소"
    assert fit(q, quote, "RESUME", scene="예측 결과를 운영에 반영하는 일에서") == "예측 결과를 운영에 반영하는 일에서, " + q
    h = "협업 중에 맡은 일이 늦어지는 팀원이 있다면 어떻게 하시겠습니까?"
    assert fit(h, "x", "DOCUMENT", "공장 망 단절 대응") == "공장 망 단절 대응 같은 일을 맡았을 때 " + h
    assert fit(q, "x", "DOCUMENT", "공장 망 단절 대응") is None  # a document is not the candidate's past


def test_an_adapted_question_never_shows_the_resume_wording():
    """2026-11-09: "규칙 기반 경보와 점검 우선순위 수립 업무를 하실 때, ..." read as taken from the resume. The scene is general."""
    from app.ai.tasks.question_adapt import fit
    q = "일이 계획대로 되지 않아 크게 실망했을 때 어떻게 다시 추스르셨는지 말씀해 주세요."
    quote = "규칙 기반 경보와 점검 우선순위 수립"
    assert fit(q, quote, "RESUME", scene="규칙 기반 경보와 점검 우선순위 수립 업무에서") is None  # the resume's words
    assert fit(q, quote, "RESUME", scene="하루 300건을 처리하는 업무에서") is None  # a number from the resume
    assert fit(q, quote, "RESUME", scene="") is None  # nothing general to say: the bank text stays
    assert fit(q, quote, "RESUME", scene="여러 요청을 동시에 처리해야 하는 업무에서") == "여러 요청을 동시에 처리해야 하는 업무에서, " + q


async def test_a_later_resume_question_never_asks_about_a_part_of_the_resume_already_asked(client, make_client, fake_ai):
    """Live composition makes one resume question at a time: what earlier questions asked about is excluded (and the tail differs)."""
    from app.ai.client import get_ai_client
    from app.core.config import get_settings
    from app.models import Application, Company, JobPosting
    from app.questions import compose as C
    _, app_id, cand = await setup_app(client, make_client, default_plan=True)
    composed = await prepare_and_compose(cand, app_id)
    first = [x for x in composed if x.slot_type == "PROJECT" and x.source == "AI"][:1]
    assert first
    async with session_scope() as db:
        app = await db.get(Application, uuid.UUID(app_id))
        c = C._Ctx(app, await db.get(JobPosting, app.posting_id), await db.get(Company, app.company_id), None, get_ai_client(),
                   get_settings().embed_model, db)
        c.picked = [C.Picked(x.seq, x.slot_type, x.text, x.source, x.bank_question_id, x.grounding) for x in first]
        await C._fill_resume(db, c, [(9, "PROJECT")])
        new = c.picked[-1]
        resume = C.to_ai_resume(app.resume_snapshot)
        old_keys = {C._evidence_key(resume, ev) for ev in first[0].grounding["evidence"]}
        assert new.seq == 9 and new.text != first[0].text
        if new.source == "AI":
            assert not {C._evidence_key(resume, ev) for ev in new.grounding["evidence"]} & old_keys
        await db.rollback()
