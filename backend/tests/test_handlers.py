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
from app.core.db import session_scope
from app.jobs import handlers, worker  # noqa: F401  registers the handlers
from app.jobs.queue import enqueue
from app.models import InterviewQuestion, QuestionBank, QuestionTtsAsset
from tests.evalstate import to_submitted
from tests.fixtures import RESUME, apply, create_posting, text_pdf
from tests.helpers import candidate, recruiter

RESOURCES = ["LLM", "CPU", "RUNNER", "NONE"]
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


async def setup_app(client, make_client, *, settings: dict | None = None, **posting) -> tuple[str, str, httpx.AsyncClient]:
    company_id, _ = await recruiter(client)
    p = await create_posting(client, **posting)
    if settings:
        r = await client.put(f"/api/recruiter/postings/{p['id']}/evaluation-settings", json=settings)
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
        "weights": {"expertise": 35, "logic": 25, "specificity": 25, "delivery": 15}, "custom_question": custom})
    qs = await prepare_and_compose(cand, app_id)
    assert [x.seq for x in qs] == list(range(1, 11)) and all(x.role == "MAIN" for x in qs)
    assert [x.slot_type for x in qs] == ["PERSONALITY"] * 2 + ["RESUME"] * 2 + ["COMPANY"] * 2 + ["JOB"] * 4
    assert qs[6].source == "CUSTOM" and qs[6].text == custom  # OQ-36: seq 7
    # Resume questions are grounded in the snapshot, including the `career` section (mapped to T3 `experience`).
    for rq in qs[2:4]:
        assert rq.source == "AI" and rq.grounding["evidence"]
        snapshot = json.dumps(RESUME, ensure_ascii=False)
        assert all(e["text"] in snapshot for e in rq.grounding["evidence"])
    fields = {e["field"].split("[")[0] for rq in qs[2:4] for e in rq.grounding["evidence"]}
    assert fields <= {"education", "experience", "projects", "certifications", "skills"}
    assert len({x.text for x in qs}) == 10
    status = await q("SELECT status FROM question_sets WHERE application_id=:a", a=app_id)
    assert status == [("READY",)]
    # tts_pregen ran (CPU resource) and cached all 10 main questions.
    async with session_scope() as db:
        assets = (await db.execute(select(QuestionTtsAsset).where(
            QuestionTtsAsset.question_id.in_([x.id for x in qs])))).scalars().all()
    assert len(assets) == 10 and all(a.voice_model == "fake-tts" and a.duration_ms > 0 for a in assets)
    assert await q("SELECT state FROM jobs WHERE kind IN ('compose_questions','tts_pregen') AND state <> 'SUCCEEDED'") == []


async def test_compose_resume_fallback_and_company_scoped_generation(client, make_client, fake_ai):
    """No grounded resume question -> BANK_FALLBACK; a family without job seeds -> T2 questions stored for this
    company only (C2) and never offered to another company's applicant."""
    fake_ai.responses["resume_question"] = lambda inp, msgs: {"questions": []}
    canary = "카나리아결제엔진"
    company_a, app_a, cand_a = await setup_app(client, make_client, job_family="QA",
                                               duties=f"{canary} 회귀 테스트 자동화. 릴리스 품질 지표 관리")
    qs = await prepare_and_compose(cand_a, app_a)
    assert [x.source for x in qs[2:4]] == ["BANK_FALLBACK", "BANK_FALLBACK"]
    assert all(x.slot_type == "RESUME" for x in qs[2:4])
    ai_qs = [x for x in qs if x.source == "AI"]
    assert ai_qs and all(x.grounding["basis"]["text"] for x in ai_qs) and any(canary in x.text for x in ai_qs)
    async with session_scope() as db:
        stored = (await db.execute(select(QuestionBank).where(QuestionBank.source == "AI"))).scalars().all()
    assert stored and {str(s.company_id) for s in stored} == {company_a}

    other = make_client()
    _, app_b, cand_b = await setup_app(other, make_client, job_family="QA", duties="모바일 앱 회귀 테스트")
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
    assert n == 10 and len(first) == 10


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


async def answer_all(app_id: str, qs: list[InterviewQuestion], *, no_answer_seq: int = 10) -> list[str]:
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
    assert len(await q("SELECT 1 FROM answer_pre_evaluations")) == 10
    calls_before = sum(c.task == "evaluate_answer" for c in fake_ai.calls)

    await submit_and_register(app_id)
    await drain()
    assert sum(c.task == "evaluate_answer" for c in fake_ai.calls) == calls_before  # EVL-05: all units reused
    (eval_status,), = await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id)
    assert eval_status == "DONE"
    (run_status, trig, prompt), = await q("SELECT status, trigger, prompt_version FROM evaluation_runs WHERE application_id=:a",
                                          a=app_id)
    assert (run_status, trig, prompt) == ("SUCCEEDED", "SUBMIT", "sha256:fake")

    rep = (await client.get(f"/api/recruiter/applications/{app_id}/report")).json()
    # Fake judge: every answered unit scores 3 -> 9 answers x 3 + 1 silent answer x 1 -> (28 / 10) x 20 = 56.00.
    assert [c["score"] for c in rep["criteria"]] == ["56.00"] * 4 and rep["total_score"] == "56.00"
    assert rep["no_answer_count"] == 1 and rep["insufficient_count"] == 0 and rep["answered_main_count"] == 9
    assert rep["strengths"] and rep["improvements"] and rep["summary"]
    answered = [a for x in rep["questions"] for a in x["answers"] if not a["no_answer"]]
    for a in answered:  # AC-16: every OK score has a quote that is a span of the stored eval_text
        for item in a["items"]:
            qt = item["quote"]
            assert item["evidence"] == "OK" and a["display_text"][qt["start"]:qt["end"]] == qt["text"]
    fb = await q("SELECT score_expertise, coding_all_passed, strengths FROM candidate_feedback WHERE application_id=:a",
                 a=app_id)
    assert str(fb[0][0]) == "56.00" and fb[0][1] is None and fb[0][2]


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
    (pid,), = await q("SELECT id FROM coding_problems LIMIT 1")
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


@pytest.mark.parametrize(("code", "passed", "score", "outcomes"), [
    (SUM_SOLUTION, 5, "100.00", {"PASS"}),
    ("print(0)", 1, "20.00", {"PASS", "WRONG_ANSWER"}),
    ("def broken(:\n", 0, "0.00", {"RUNTIME_ERROR"}),
    ("   \n", 0, "0.00", {"NOT_RUN"}),
])
async def test_grade_coding_compares_outside_runner(client, make_client, fake_runner, code, passed, score, outcomes):
    app_id, _ = await coding_app(client, make_client)
    await submit_code(app_id, code)
    await drain()
    (status, p, total, sc, compile_ok), = await q(
        "SELECT grading_status, passed, total, score, compile_ok FROM coding_submissions WHERE application_id=:a", a=app_id)
    assert (status, p, total, str(sc)) == ("DONE", passed, 5, score)
    assert compile_ok is (code == SUM_SOLUTION or code == "print(0)")
    assert {o for (o,) in await q("SELECT outcome FROM coding_test_results")} == outcomes
    if code.strip():
        assert len(fake_runner["requests"]) == 1 and len(fake_runner["requests"][0]["cases"]) == 5
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
    # weights 30/20/20/10 + coding 20: 56 x 0.8 + 100 x 0.2 = 64.80
    assert [(str(a), str(b)) for a, b in rep] == [("100.00", "64.80")]
    assert await q("SELECT coding_all_passed FROM candidate_feedback WHERE application_id=:a", a=app_id) == [(True,)]
    # T8 never sees hidden tests (COD-06, SC-79): the hidden inputs are absent from every code_review prompt.
    hidden = [i for (i,) in await q("SELECT input FROM coding_testcases WHERE visibility='HIDDEN'")]
    prompts = [m["content"] for c in fake_ai.calls if c.task == "code_review" for msgs in c.messages for m in msgs]
    assert prompts and not any(h.strip() in p for h in hidden for p in prompts)
