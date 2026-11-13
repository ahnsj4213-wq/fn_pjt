"""One source sentence, one question; one resume entry, one question; job questions from the company's own documents (2026-11-09 review
of session cc6a7915: three job questions from one posting line, four documents unused, one internship asked about three times)."""
from types import SimpleNamespace

from app.ai.tasks import TASKS
from app.questions.compose import used_entries

LINE = "가격 수집 실패율 감시와 원인 분석"
POSTING = {"title": "숙박 검색 데이터 엔지니어", "duties": f"{LINE}\n검색 로그 적재 파이프라인 운영"}
DOC = {"id": "d1", "title": "호텔 가격 수집기", "text": "제휴 호텔 가격을 30분마다 수집하는 수집기는 응답 지연이 잦다."}
DOC_QUOTE = "제휴 호텔 가격을 30분마다 수집하는 수집기는 응답 지연이 잦다"


def _t2(questions, **extra):
    t2 = TASKS["question_generate"]
    inp = {"kind": "JOB", "n": 2, "posting": POSTING, "posting_id": "p1", "company_id": "c1"} | extra
    return t2, inp, t2.out_model.model_validate({"questions": questions})


def test_job_questions_come_from_own_documents_when_there_are_some():
    posting_q = {"text": "가격 수집 실패율이 오르면 원인 분석을 무엇부터 어떻게 하시겠습니까?", "basis_quote": LINE}
    doc_q = {"text": "제휴 호텔 가격 수집기의 응답 지연이 잦다면 무엇부터 어떻게 확인하시겠습니까?", "basis_quote": DOC_QUOTE}
    t2, inp, out = _t2([posting_q, doc_q], knowledge=[DOC])
    first = t2.check(inp, out, {}, False).output
    assert [f["codes"] for f in first["failed"]] == [["NEEDS_DOCUMENT"]]  # the posting question is asked again from a document
    assert "회사 자료" in t2.build({**inp, "feedback": first["failed"]}, "n0")[0]
    # on the last request a posting question still beats a code-made one
    kept = t2.check({**inp, "last_chance": True}, out, {}, False).output["questions"]
    assert {q["source"]["type"] for q in kept} == {"POSTING", "KNOWLEDGE"}
    # no own documents: the posting line is fine
    t2, inp, out = _t2([posting_q], n=1)
    assert t2.check(inp, out, {}, False).output["questions"][0]["source"]["type"] == "POSTING"


def test_one_sentence_gives_one_question():
    a = {"text": "가격 수집 실패율이 오르면 원인 분석을 무엇부터 어떻게 하시겠습니까?", "basis_quote": LINE}
    b = {"text": "가격 수집 실패율 감시 기준을 정한다면 원인 분석은 어떻게 하시겠습니까?", "basis_quote": LINE}
    t2, inp, out = _t2([a, b])
    assert len(t2.check(inp, out, {}, True).output["questions"]) == 1
    # a sentence an earlier question of the set came from is not used again, and the prompt shows it
    t2, inp, out = _t2([a], n=1, used_basis=[LINE])
    assert t2.check(inp, out, {}, True).output["questions"] == []
    assert "이미 쓴 근거" in t2.build(inp, "n0")[0]


RESUME = {"experience": [
    {"company": "데이터웨이브", "title": "데이터 분석 인턴", "description": "물류 수요 예측 모델 개선으로 MAPE 18% 달성"},
    {"company": "스테이랩", "title": "데이터 엔지니어", "description": "주문 데이터 파이프라인 구축과 배치 모니터링"}]}
BANK = [{"id": "0", "kind": "PERSONALITY", "text": "피드백을 받고 일하는 방식을 바꿨던 경험을 말씀해 주세요."},
        {"id": "1", "kind": "COLLABORATION", "text": "의견이 다른 동료와 합의했던 경험을 말씀해 주세요."}]


def _t12(picks, avoid=()):
    t12 = TASKS["question_adapt"]
    inp = {"items": BANK, "resume": RESUME, "knowledge": [], "avoid": list(avoid)}
    user, ctx = t12.build(inp, "n0")
    return t12, inp, ctx, user, t12.out_model.model_validate({"questions": picks})


def test_adapted_questions_use_different_resume_entries():
    same = [{"id": "0", "basis_quote": "물류 수요 예측 모델 개선", "scene": "예측 결과를 운영에 반영하는 일에서"}, {"id": "1", "basis_quote": "MAPE 18% 달성", "scene": "성과를 수치로 확인하는 일에서"}]
    t12, inp, ctx, _, out = _t12(same)
    kept = t12.check(inp, out, ctx, True).output["questions"]
    assert [q["basis"]["field"] for q in kept] == ["experience[0].description"]  # the second pick is from the same internship
    assert "1" in t12.check(inp, out, ctx, False).feedback["gen"]


def test_adapt_skips_entries_other_questions_already_ask_about():
    picks = [{"id": "0", "basis_quote": "물류 수요 예측 모델 개선", "scene": "예측 결과를 운영에 반영하는 일에서"}, {"id": "1", "basis_quote": "주문 데이터 파이프라인 구축", "scene": "데이터 흐름을 새로 만드는 일에서"}]
    t12, inp, ctx, user, out = _t12(picks, avoid=[{"entry": "experience[0]", "label": "데이터웨이브 / 물류 수요 예측"}])
    assert "이미 다른 질문에 쓴" in user and "데이터웨이브 / 물류 수요 예측" in user
    kept = t12.check(inp, out, ctx, True).output["questions"]
    assert [q["basis"]["field"] for q in kept] == ["experience[1].description"]


def test_a_resume_with_one_job_still_fits_the_bank_question():
    t12 = TASKS["question_adapt"]
    inp = {"items": BANK[:1], "resume": {"experience": RESUME["experience"][:1]}, "knowledge": [],
           "avoid": [{"entry": "experience[0]", "label": "데이터웨이브"}]}
    _, ctx = t12.build(inp, "n0")
    out = t12.out_model.model_validate({"questions": [{"id": "0", "basis_quote": "물류 수요 예측 모델 개선", "scene": "예측 결과를 운영에 반영하는 일에서"}]})
    assert [q["basis"]["field"] for q in t12.check(inp, out, ctx, True).output["questions"]] == ["experience[0].description"]


def test_used_entries_collects_resume_evidence_and_adapted_bases():
    resume_q = SimpleNamespace(grounding={"evidence": [{"field": "experience[0].description", "text": "물류 수요 예측"}]})
    adapted = SimpleNamespace(grounding={"basis": {"field": "projects[1].title", "text": "x"}})
    job_q = SimpleNamespace(grounding={"basis": {"text": LINE}})
    c = SimpleNamespace(picked=[resume_q, adapted, job_q])
    resume = RESUME | {"projects": [{"title": "a"}, {"title": "추천 실험 플랫폼"}]}
    got = used_entries(c, resume)
    assert [x["entry"] for x in got] == ["experience[0]", "projects[1]"]
    assert "데이터웨이브" in got[0]["label"] and got[1]["label"] == "추천 실험 플랫폼"
    assert [x["entry"] for x in used_entries(c, resume, skip=[resume_q])] == ["projects[1]"]
