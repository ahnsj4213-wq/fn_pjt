"""The resume's long text comes from the file by code: the cover letter and whole descriptions (RES-03)."""
from app.ai.tasks.resume import ResumeExtract, ResumeOut
from app.resumes.schemas import AI_DESCRIPTION_MAX, ResumeContent, to_ai_resume
from app.resumes.sections import DESCRIPTION_MAX, apply_sections, read_sections

RESUME = """이력서
인적사항
성명
홍길동
학력사항
2011년 03월 ~ 2015년 02월
가상대학교 컴퓨터학과 (학사)
경력사항
2016년 12월 ~ 2020년 01월
가상테크(주) / 과장 / DBA — 대용량 DB 운영과 튜닝
테스트 꼬리말 - 1
경력직무 상세
회사명
근무기간
수행 직무
가상테크(주)
과장 / DBA
2016년 12월 ~
2020년 01월
■ 대용량 분산 DB 운영
- 실행계획 분석 기반 쿼리 튜닝
보유기술
Python
프로젝트 소개서 1
프로젝트주제
재고 예측 시스템
테스트 꼬리말 - 2
재고 데이터를 모아 예측했습니다.
프로젝트 소개서 2
프로젝트주제
품질 점검 자동화
검사 절차를 코드로 옮겼습니다.
자기소개서
지원동기
저는 데이터를 안전하게 옮기는 일을 해 왔습니다.
Greenplum 운영 중 성능 저하를 원인까지 좁혔습니다.
입사후포부
사내 문서를 활용하겠습니다.
"""


def fields(**kw) -> dict:
    return {"name": None, "education": [], "career": [], "projects": [], "skills": [], "certificates": [], **kw}


def test_cover_letter_is_cut_verbatim_and_never_reaches_the_model():
    s = read_sections(RESUME)
    assert s.intro == ("지원동기\n저는 데이터를 안전하게 옮기는 일을 해 왔습니다.\nGreenplum 운영 중 성능 저하를 원인까지 좁혔습니다.\n"
                       "입사후포부\n사내 문서를 활용하겠습니다.")
    assert "Greenplum" not in s.body and "자기소개서" not in s.body
    assert "가상테크(주)" in s.body and "Python" in s.body


def test_intro_ends_at_the_next_section():
    s = read_sections("자기소개\n열심히 일했습니다.\n자격증\n정보처리기사\n")
    assert s.intro == "열심히 일했습니다." and "정보처리기사" in s.body


def test_no_heading_means_no_intro_and_model_descriptions_stay():
    text = "홍길동\n가상테크 개발자로 일했습니다.\n"
    s = read_sections(text)
    assert s.intro is None and s.body.strip() == text.strip()
    row = {"company": "가상테크", "role": None, "period": None, "description": "개발자로 일했습니다."}
    assert apply_sections(fields(career=[row]), s)["career"][0]["description"] == "개발자로 일했습니다."


def test_the_paragraph_above_the_first_section_is_the_intro_word_for_word():
    """A resume that opens with a self-introduction and no heading (2026-11-09: the form kept the previous file's cover letter)."""
    lead = ("저는 약 8년간 대형 고객사 프로젝트를 수행하며 데이터베이스 운영, 성능 튜닝, 데이터 이\n관을 여러 번 경험해 왔습니다.\n"
            "최근까지 PostgreSQL 기반 대용량 분산처리 DB(Greenplum) 환경을 유지\n보수하며,\n쿼리 성능 개선을 수행하고 있습니다.")
    text = f"안 성 준\n+821000000000\nfake@example.com\n{lead}\n경력 8년 3개월\n시앤지원\n2017.12 - 2026.02 DB Engineer\n학력\n가상대학교\n"
    s = read_sections(text)
    assert s.intro == lead  # the lines as they are, nothing added or rewritten
    assert "8년간" not in s.body and "시앤지원" in s.body and "경력 8년 3개월" in s.body
    # one sentence at the top is not a self-introduction
    assert read_sections("홍길동\n대형 고객사 프로젝트에서 데이터베이스 운영을 맡아 일했습니다.\n경력\n가상테크\n").intro is None


def test_a_sentence_mentioning_a_section_is_not_a_heading():
    assert read_sections("자기소개서\n저의 경력은 다음과 같습니다.\n").intro == "저의 경력은 다음과 같습니다."


def test_whole_career_description_without_labels_footers_or_the_rows_own_heading():
    row = {"company": "가상테크(주)", "role": "과장 / DBA", "period": "2016년 12월 ~ 2020년 01월", "description": "짧은 요약"}
    d = apply_sections(fields(career=[row]), read_sections(RESUME))["career"][0]["description"]
    assert d.startswith("— ") is False and d.startswith("대용량 DB 운영과 튜닝")
    assert "■ 대용량 분산 DB 운영 - 실행계획 분석 기반 쿼리 튜닝" in d
    assert "\n" not in d  # the form keeps one row per line
    for noise in ("꼬리말", "회사명", "근무기간", "수행 직무", "2020년 01월", "Python"):
        assert noise not in d


def test_each_project_gets_the_text_up_to_the_next_one_whatever_order_the_model_used():
    rows = [{"title": "품질 점검 자동화", "role": None, "period": None, "description": None},
            {"title": "재고 예측 시스템", "role": None, "period": None, "description": None}]
    out = apply_sections(fields(projects=rows), read_sections(RESUME))["projects"]
    assert out[0]["description"] == "검사 절차를 코드로 옮겼습니다."
    assert out[1]["description"] == "재고 데이터를 모아 예측했습니다."


def test_a_row_the_section_does_not_name_keeps_the_models_description():
    row = {"company": "없는회사", "role": None, "period": None, "description": "모델 설명"}
    assert apply_sections(fields(career=[row]), read_sections(RESUME))["career"][0]["description"] == "모델 설명"


def test_description_and_intro_fit_the_form_limits():
    long = "가" * 200
    text = "경력\n가상테크\n" + "\n".join(f"{long}{i}" for i in range(40)) + "\n자기소개서\n" + "\n".join(f"{long}{i}" for i in range(80))
    s = read_sections(text)
    row = {"company": "가상테크", "role": None, "period": None, "description": None}
    out = apply_sections(fields(career=[row]), s)
    assert len(out["career"][0]["description"]) == DESCRIPTION_MAX
    ResumeContent.model_validate({**out, "name": "홍길동"})


def test_model_values_keep_their_closing_bracket():
    src = "시앤지원(주) / 과장"
    p = ResumeOut(experience=[{"company": "시앤지원(주)"}])
    assert ResumeExtract().check({"source_text": src}, p, {}, True).output["experience"][0]["company"] == "시앤지원(주)"


def test_t3_input_cuts_long_descriptions_only():
    content = {"name": "홍길동", "intro": "자기소개", "career": [{"company": "가상", "description": "가" * 1000}],
               "projects": [{"title": "짧은 프로젝트", "description": "짧다"}, {"title": "설명 없음", "description": None}]}
    out = to_ai_resume(content)
    assert len(out["experience"][0]["description"]) == AI_DESCRIPTION_MAX
    assert out["projects"][0]["description"] == "짧다" and out["projects"][1]["description"] is None
    assert "intro" not in out and "name" not in out


def test_shared_terms_puts_technology_names_first():
    from app.ai.textrules import shared_terms
    assert shared_terms("FastAPI 서버와 RAG 검색 개발", "RAG 검색 서비스를 FastAPI로 서빙")[:2] == ["fastapi", "rag"]


def test_resume_questions_about_what_posting_and_resume_share_come_first():
    from app.ai.tasks.questions import ResQOut, ResumeQuestion
    resume = {"projects": [{"title": "주식 예측 시스템 개발 내용", "description": "RAG 기반 사내 문서 검색 서비스 구축 경험"}]}

    def q(text, field, quote):
        return {"text": text, "evidence": [{"field": field, "quote": quote}]}

    p = ResQOut(questions=[
        q("주식 예측 시스템 개발 내용에서 무엇을 어떻게 했고 결과는 어땠습니까?", "projects[0].title", "주식 예측 시스템 개발 내용"),
        q("RAG 기반 사내 문서 검색 서비스 구축 때 무엇을 근거로 어떻게 하셨습니까?", "projects[0].description", "RAG 기반 사내 문서 검색 서비스 구축")])
    task = ResumeQuestion()
    inp = {"resume": resume, "n": 2, "shared_terms": ["rag"]}
    _, ctx = task.build(inp, "n")
    assert task.check(inp, p, ctx, True).output["questions"][0]["text"].startswith("RAG")


def test_followup_may_deepen_a_good_answer_and_sees_the_posting():
    from app.ai.tasks.followup import FollowupDecide, FollowupOut
    inp = {"question": "경험을 말씀해 주세요", "answer": "재순위화 모델로 정확도를 12% 높였습니다", "job_brief": "검색 개발",
           "shared_terms": ["rag"]}
    task = FollowupDecide()
    user, _ = task.build(inp, "n")
    assert "검색 개발" in user and "공통 항목: rag" in user
    p = FollowupOut(decision="ASK", reason="DEEPEN", anchor={"source": "answer", "quote": "재순위화 모델"},
                    question="재순위화 모델을 고른 이유는 무엇입니까?")
    assert task.check(inp, p, {}, True).output["reason"] == "DEEPEN"


def test_followup_anchor_outside_the_answer_is_asked_again_once_then_invalid():
    import pytest

    from app.ai.tasks.base import TaskInvalid
    from app.ai.tasks.followup import FollowupDecide, FollowupOut
    inp = {"question": "경험을 말씀해 주세요", "answer": "재순위화 모델로 정확도를 12% 높였습니다"}
    task = FollowupDecide()
    p = FollowupOut(decision="ASK", reason="DEEPEN", anchor={"source": "answer", "quote": "답변에 없는 표현입니다"},
                    question="재순위화 모델을 고른 이유는 무엇입니까?")
    assert "quote" in task.check(inp, p, {}, False).feedback and task.regen_limits == {"quote": 1}
    with pytest.raises(TaskInvalid):
        task.check(inp, p, {}, True)


def test_followup_time_limit_outlasts_a_real_answer():
    from app.core.constants import FOLLOWUP_DECISION_TIMEOUT_SEC
    assert FOLLOWUP_DECISION_TIMEOUT_SEC >= 15  # 5.1~6.6 s measured per call, and one anchor retry doubles it (ADR-0019)
