"""Career level, difficulty mix and the code checks of a question (ADR-0025, 2026-11-09 user report: a new graduate got a
Kafka + Redis + Prometheus design question, the motivation slot asked about architecture, sentences carried numbers and stacked
conditions)."""
from datetime import date

from app.ai.tasks import TASKS
from app.questions.level import (career_level, complexity, difficulty_plan, fits_level, level_block, problems,
                                 work_months)

TODAY = date(2026, 11, 9)


def test_career_level_counts_work_but_not_internships():
    intern_only = {"experience": [{"company": "데이터웨이브", "title": "데이터 분석 인턴", "period": "2025.07 ~ 2025.12"}]}
    assert work_months(intern_only, TODAY) == 0
    assert career_level(intern_only, today=TODAY) == "NEW"
    two_years = {"experience": [{"company": "스테이랩", "title": "백엔드 개발자", "period": "2023.03 ~ 2025.03"}]}
    assert career_level(two_years, today=TODAY) == "JUNIOR"
    long = {"experience": [{"company": "A", "title": "개발자", "period": "2015.01 ~ 현재"}]}
    assert career_level(long, today=TODAY) == "SENIOR"
    assert career_level(long, "신입 채용", today=TODAY) == "JUNIOR"  # a new-graduate posting: no senior design questions
    assert career_level({}, today=TODAY) == "NEW"


def test_difficulty_mix_follows_the_level_easiest_first():
    assert difficulty_plan("NEW", 10) == ["BASIC"] * 5 + ["APPLIED"] * 4 + ["ADVANCED"]
    assert difficulty_plan("NEW", 6) == ["BASIC"] * 3 + ["APPLIED"] * 2 + ["ADVANCED"]
    assert difficulty_plan("SENIOR", 10).count("ADVANCED") == 4
    assert difficulty_plan("JUNIOR", 0) == []


def test_complexity_reads_what_a_question_asks_for():
    assert complexity("Redis의 기본 자료구조를 설명해 주시겠습니까?") == "BASIC"
    assert complexity("Redis 캐시가 자주 만료되는 상황이라면 무엇부터 확인하시겠습니까?") == "APPLIED"
    assert complexity("Kafka와 Redis로 대용량 처리 구조를 어떻게 설계하시겠습니까?") == "ADVANCED"


def test_problems_catch_the_reported_questions():
    many = "Kafka, Redis, Prometheus를 함께 써서 장애를 감지하는 구조를 어떻게 설계하시겠습니까?"
    assert "MANY_TOPICS" in problems(many, kind="JOB", level="NEW")
    arch = "저희 회사의 결제 시스템 아키텍처를 어떻게 설계하시겠습니까?"
    assert "NOT_MOTIVATION" in problems(arch, kind="COMPANY", level="NEW")
    assert problems("저희 회사의 숙박 검색 서비스에 관심을 갖게 된 이유는 무엇입니까?", kind="COMPANY", level="NEW") == []
    presume = "결제 모듈 리팩터링을 진행하셨을 때 어떤 점이 어려웠습니까?"
    assert "PRESUMES_EXPERIENCE" in problems(presume, kind="JOB", level="NEW")
    assert "PRESUMES_EXPERIENCE" not in problems("결제 모듈을 리팩터링해 본 적이 있다면 어떤 점이 어려웠습니까?", kind="JOB")
    assert "PRESUMES_EXPERIENCE" not in problems(presume, kind="RESUME", from_resume=True)  # the resume says so
    assert "NUMBER" in problems("응답 시간을 200ms 아래로 줄이려면 무엇부터 보시겠습니까?", kind="JOB", basis="응답 시간 개선")
    assert "NUMBER" not in problems("30분마다 수집하는 작업이 늦어지면 무엇부터 보시겠습니까?", kind="JOB",
                                    basis="가격을 30분마다 수집한다")
    assert "TOO_LONG" in problems("가" * 90 + "?", kind="JOB", level="NEW")
    assert "NOT_JOB" in problems("이 직무에 지원하게 된 계기는 무엇입니까?", kind="JOB")


def test_bank_fallback_prefers_questions_within_reach():
    assert fits_level("SQL 조인의 종류를 설명해 주시겠습니까?", "NEW")
    assert not fits_level("Kafka와 Redis로 대용량 처리 구조를 어떻게 설계하시겠습니까?", "NEW")
    assert fits_level("Kafka와 Redis로 대용량 처리 구조를 어떻게 설계하시겠습니까?", "SENIOR")


def test_level_and_difficulty_lead_the_prompt():
    block = level_block({"level": "NEW", "difficulties": ["BASIC", "APPLIED"]})
    assert block.startswith("지원자 수준: 신입") and "기초 / 실무 적용" in block and "참고 자료는" in block
    assert level_block({}) == ""


POSTING = {"title": "백엔드 개발자", "duties": "주문 API 개발과 운영\n결제 데이터 정합성 점검"}


def _t2(questions, **extra):
    t2 = TASKS["question_generate"]
    inp = {"kind": "JOB", "n": 1, "posting": POSTING, "posting_id": "p1", "company_id": "c1"} | extra
    return t2, inp, t2.out_model.model_validate({"questions": questions})


def test_generation_check_rejects_a_question_above_the_new_graduates_mix():
    hard = {"text": "주문 API 개발과 운영을 대용량 처리 구조로 어떻게 설계하시겠습니까?", "basis_quote": "주문 API 개발과 운영"}
    easy = {"text": "주문 API 개발과 운영에서 가장 먼저 점검할 것은 무엇입니까?", "basis_quote": "주문 API 개발과 운영"}
    t2, inp, out = _t2([hard], level="NEW", difficulties=["BASIC"])
    first = t2.check(inp, out, {}, False).output
    assert first["questions"] == [] and "TOO_HARD" in first["failed"][0]["codes"]
    assert "어렵다" in t2.build({**inp, "feedback": first["failed"]}, "n0")[0]  # the reason goes back to the model, once
    t2, inp, out = _t2([hard, easy], level="NEW", difficulties=["BASIC"])
    kept = t2.check(inp, out, {}, False).output["questions"]
    assert [q["text"] for q in kept] == [easy["text"]] and kept[0]["difficulty"] == "BASIC"
    assert "지원자 수준: 신입" in t2.build(inp, "n0")[0]


def test_generation_check_rejects_a_motivation_question_about_design():
    arch = {"text": "결제 데이터 정합성 점검 구조를 어떻게 설계하시겠습니까?", "basis_quote": "결제 데이터 정합성 점검"}
    t2, inp, out = _t2([arch], kind="COMPANY", level="NEW")
    assert t2.check(inp, out, {}, True).output["questions"] == []


def test_followup_check_keeps_one_point_and_no_invented_numbers():
    fu = TASKS["followup_decide"]
    inp = {"question": "주문 API를 개발한 경험을 말씀해 주세요.", "answer": "주문 API에서 Redis 캐시를 붙여 조회를 빠르게 했습니다.",
           "level": "NEW", "followups_left": 1}
    out = fu.out_model.model_validate({"decision": "ASK", "reason": "DEEPEN", "anchor": {"source": "answer", "quote": "Redis 캐시"},
                                       "question": "Redis 캐시로 조회 시간을 3초 줄인 방법은 무엇입니까?"})
    first = fu.check(inp, out, {}, False)
    assert first.feedback and "숫자" in first.feedback["quote"]
    ok = fu.out_model.model_validate({"decision": "ASK", "reason": "DEEPEN", "anchor": {"source": "answer", "quote": "Redis 캐시"},
                                      "question": "Redis 캐시를 고른 이유는 무엇입니까?"})
    assert fu.check(inp, ok, {}, False).output["question"] == "Redis 캐시를 고른 이유는 무엇입니까?"
    assert "지원자 수준: 신입" in fu.build(inp, "n0")[0]


def test_problems_catch_a_copied_fact_and_a_new_graduates_figure():
    # 2026-11-09 interview: the company document's sentence set down as the premise, the purpose left unsaid
    basis = "맞힐 확률이 60~80퍼센트인 문제를 후보로 고릅니다"
    bad = problems("맞힐 확률이 60~80 퍼센트인 문제를 후보로 고르시면 어떻게 판단하시겠습니까?", kind="JOB", level="NEW", basis=basis)
    assert "COPIES_FACT" in bad and "NUMBER" in bad
    good = problems("학습자에게 너무 쉽거나 어렵지 않은 문제를 추천하려면 어떤 기준을 쓰시겠습니까?", kind="JOB", level="NEW", basis=basis)
    assert good == []
    # a junior may get the company's figure when the source has it
    assert "NUMBER" not in problems("정답률 60~80% 구간을 추천 기준으로 쓴다면 무엇을 확인하시겠습니까?", kind="JOB", level="JUNIOR",
                                    basis="정답률 60~80% 구간 문제를 추천합니다")


