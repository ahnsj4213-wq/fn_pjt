"""A question is of its slot's type whatever path filled the slot (2026-11-10 user report: "RAG 기반 문서 검색 서비스 개발에
임베딩과 벡터 검색에 대한 이해를 어떻게 적용하시겠습니까?" asked as 지원 동기, and "트래픽이 늘었을 때 ... 어떤 순서로 원인을
찾겠습니까?" asked as 경험). Both came from the question bank: the company bank held design questions the model once wrote, and an
경험 slot the resume questions could not fill took a job bank question (OQ-37, revised)."""
import uuid
from types import SimpleNamespace

from app.questions import compose as C
from app.questions.level import fits_type


def test_the_company_bank_offers_only_motivation_questions():
    for bad in ("RAG 기반 문서 검색 서비스 개발에 임베딩과 벡터 검색에 대한 이해를 어떻게 적용하시겠습니까?",
                "PostgreSQL 튜닝 경험을 바탕으로 최적화된 쿼리 실행 계획을 어떻게 설계하시겠습니까?",
                "대용량 데이터 처리 경험을 살리면서 데이터 수집·정제 파이프라인을 구축할 때 무엇을 먼저 고려하시겠습니까?",
                "회사의 업무 방식이 본인과 맞지 않을 때 어떻게 적응할지 말씀해 주세요."):
        assert not fits_type("COMPANY", bad), bad
    for good in ("우리 회사에 지원한 이유와 회사에서 이루고 싶은 목표를 말씀해 주세요.",
                 "이 직무에서 입사 후 1년 동안 기여할 수 있는 일을 구체적으로 말씀해 주세요.",
                 "RAG 기반 문서 검색 서비스 개발에 본인의 검색 품질 개선 경험을 어떻게 살릴 수 있다고 보십니까?",
                 C.MOTIVATION_QUESTION.format(title="결제 백엔드 개발자")):
        assert fits_type("COMPANY", good), good
    assert not fits_type("JOB", "우리 회사에 지원하게 된 동기는 무엇입니까?")
    assert fits_type("JOB", "트래픽이 늘었을 때 API 응답 지연을 줄이기 위해 어떤 순서로 원인을 찾겠습니까?")


def test_a_wrong_type_bank_row_is_skipped():
    c = SimpleNamespace(level="JUNIOR", picked=[], taken_ids=lambda: set(), texts=lambda: [])
    rows = [SimpleNamespace(id=uuid.uuid4(), kind="COMPANY", text=t) for t in (
        "RAG 기반 문서 검색 서비스 개발에 임베딩과 벡터 검색에 대한 이해를 어떻게 적용하시겠습니까?",
        "우리 회사가 하는 사업에서 관심 있는 부분과 그 이유를 말씀해 주세요.")]
    assert C._take_from_bank(c, rows, 1, [(3, "MOTIVATION")], "BANK") == 1
    assert [p.text for p in c.picked] == [rows[1].text]


def _ctx(picked=()):
    c = SimpleNamespace(app=SimpleNamespace(id=uuid.uuid4()), picked=list(picked))
    c.texts = lambda: [p.text for p in c.picked]
    return c


RESUME = {"experience": [{"company": "커머스랩", "role": "백엔드 개발자", "description": "주문 API 응답 지연 개선"}],
          "projects": [{"title": "사내 검색", "description": "검색 품질 개선"}]}


def test_experience_slots_left_by_the_resume_questions_stay_experience_questions():
    c = _ctx()
    C.experience_fallback(c, RESUME, [(8, "PROJECT"), (9, "PROJECT"), (10, "PROJECT")])
    texts = [p.text for p in c.picked]
    assert texts[0].startswith("커머스랩 백엔드 개발자 업무에서 ") and texts[1].startswith("사내 검색 프로젝트에서 ")
    assert texts[2].startswith(C.EXPERIENCE_GENERAL)  # every job and project asked about: a general experience question
    assert len(set(texts)) == 3 and not any("하시겠습니까" in t for t in texts)
    assert all(p.source == "TEMPLATE" and p.slot == "PROJECT" and p.grounding["fallback"] == "EXPERIENCE" for p in c.picked)
    assert c.picked[0].grounding["sources"] == [{"type": "RESUME_SNAPSHOT", "id": str(c.app.id), "field": "experience[0]"}]


def test_a_job_already_asked_about_is_not_asked_again():
    asked = C.Picked(4, "PROJECT", "커머스랩 ... 그 방법을 고른 이유는 무엇이었습니까?", "AI",
                     grounding={"evidence": [{"field": "experience[0].description", "text": "주문 API"}]})
    c = _ctx([asked])
    C.experience_fallback(c, RESUME, [(8, "PROJECT")])
    assert c.picked[-1].text.startswith("사내 검색 프로젝트에서 ")


def test_an_empty_resume_still_gets_different_experience_questions():
    c = _ctx()
    C.experience_fallback(c, {}, [(8, "PROJECT"), (9, "PROJECT"), (10, "PROJECT")])
    assert len({p.text for p in c.picked}) == 3 and all(p.text.startswith(C.EXPERIENCE_GENERAL) for p in c.picked)
