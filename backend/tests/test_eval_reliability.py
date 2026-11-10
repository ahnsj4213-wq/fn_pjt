"""ADR-0023: rubric v4, evidence by sentence number, the evidence states, and the median of independent judgements.

The model decides every score; these tests pin what code does around it: the sentences it numbers, the kind of question it
names, the statuses it records, and that the kept score is always one the model gave.
"""
from types import SimpleNamespace

import pytest

from app.ai import rubric
from app.ai.fake import FakeAIClient
from app.core.config import get_settings
from app.evaluation import pipeline
from app.evaluation.quotes import LONG_SENTENCE, MAX_SENTENCES, numbered, sentences

ANSWER = ("주문 조회가 3초까지 느려진 문제를 맡았습니다. 실행 계획에서 풀 스캔을 확인했고, 캐시는 원인을 덮을 뿐이라 복합 인덱스를 추가했습니다. "
          "배포 후 응답 시간이 0.3초로 돌아온 것을 모니터링으로 확인했습니다.")


# ── rubric v4 ────────────────────────────────────────────────────────────────
def test_every_area_has_five_levels_with_an_example_and_logic_and_expertise_do_not_overlap():
    for it in rubric.ITEMS:
        assert set(it["levels"]) == set(it["examples"]) == {1, 2, 3, 4, 5}, it["key"]
    logic = next(i for i in rubric.ITEMS if i["key"] == "logic")
    expertise = next(i for i in rubric.ITEMS if i["key"] == "expertise")
    assert all(w in logic["looks_at"] for w in ("문제 상황", "원인 분석", "판단 근거", "대안 검토", "실행 과정", "결과 검증", "인과"))
    assert "판단 과정은 논리성에서" in expertise["looks_at"] and "지식이 맞는지는 전문성에서" in logic["looks_at"]
    assert "육하원칙은 필요한 맥락이 빠졌는지 확인할 때만 쓰고 개수를 세지 않는다" in logic["looks_at"]  # rubric-v5: a tool, not a checklist
    assert "길이로 판단하지 않는다" in rubric.COMMON and "서로 독립" in rubric.COMMON
    for kind in rubric.QUESTION_KINDS.values():
        assert kind["logic"] and kind["expertise"]


@pytest.mark.parametrize("area,question,kind", [
    ("PROJECT", "장애 원인을 파악할 때 늦게 도착한 데이터를 어떻게 처리하셨습니까?", "EXPERIENCE"),
    ("JOB_SKILL", "복구 시간을 1시간으로 줄여야 한다면 어떤 데이터를 먼저 처리하시겠습니까?", "SITUATION"),
    ("JOB_SKILL", "REST와 gRPC의 차이는 무엇입니까?", "TECHNICAL"),
    ("JOB_SKILL", "초당 10만 건의 로그를 수집하는 시스템을 설계한다면 어떻게 구성하시겠습니까?", "DESIGN"),
    ("COLLABORATION", "팀원과 의견이 다를 때 어떻게 하시나요?", "BEHAVIOR"),
    ("COLLABORATION", "갈등을 해결했던 경험을 말씀해 주세요.", "EXPERIENCE"),
    ("MOTIVATION", "지원한 동기는 무엇입니까?", "BEHAVIOR"),
    ("PERSONALITY", "1분 정도로 간단하게 자기소개를 해 주십시오.", "BEHAVIOR"),
])
def test_the_kind_of_question_comes_from_its_area_and_wording(area, question, kind):
    assert rubric.question_kind(area, question) == kind


def test_live_coding_is_a_knowledge_question():
    assert rubric.question_kind("JOB_SKILL", "중복 결제를 막는 함수를 작성해 주십시오.", has_code=True) == "TECHNICAL"


# ── sentences ────────────────────────────────────────────────────────────────
def test_sentences_are_spans_of_the_answer_in_order():
    ss = sentences(ANSWER)
    assert len(ss) == 3 and all(ANSWER[s.start:s.end] == s.text for s in ss)
    assert numbered(ss).splitlines()[1].startswith("[2] 실행 계획에서")


def test_a_run_on_without_full_stops_is_cut_at_commas_and_the_count_is_capped():
    run_on = ", ".join(["가" * 90] * 4)  # STT text with no full stop
    ss = sentences(run_on)
    assert len(ss) == 4 and all(len(s.text) <= LONG_SENTENCE for s in ss) and all(run_on[s.start:s.end] == s.text for s in ss)
    many = " ".join(f"문장 {i}입니다." for i in range(60))
    ss = sentences(many)
    assert len(ss) == MAX_SENTENCES and ss[-1].text.endswith("문장 59입니다.")  # the rest stays citable as the last piece


# ── evidence states ──────────────────────────────────────────────────────────
def _q(text="성능 문제를 해결했던 경험을 말씀해 주세요.", slot="PROJECT"):
    return SimpleNamespace(text=text, slot_type=slot)


def _a(text=ANSWER, no_answer=False):
    return SimpleNamespace(eval_text=text, is_no_answer=no_answer, code_text=None, code_language=None)


_POSTING = SimpleNamespace(title="백엔드 개발자", description="주문 API", requirements=None, preferred=None, duties=None, job_family="DEV")


def _judge(scores_by_call: list[dict], sent=1):
    calls = {"n": 0}

    def reply(inp, msgs):
        s = scores_by_call[calls["n"] % len(scores_by_call)]
        calls["n"] += 1
        return {k: {"sent": sent, "comment": f"{k} 판단 {calls['n']}", "score": s[k]} for k in ("expertise", "logic", "attitude", "delivery")} | {
            "found": {"problem": 1, "cause": 0, "grounds": 2, "alternatives": 2, "action": 2, "result": 3, "knowledge": 2},
            "expertise_sub": {"a": 3, "b": 3, "c": 3}, "logic_sub": {"a": s["logic"], "b": 3, "c": 3},
            "star": {"situation": 1, "task": 1, "action": 1, "result": 1}}
    return reply


async def test_a_real_answer_is_never_floored_because_its_evidence_could_not_be_linked(monkeypatch):
    """A failed citation keeps the model's score (UNLINKED); only a low score with nothing to point at is 내용 부족."""
    monkeypatch.setattr(get_settings(), "eval_samples", 1)
    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = lambda inp, msgs: {
        "expertise": {"sent": 0, "comment": "인덱스 적용이 구체적이다.", "score": 4},
        "logic": {"sent": 0, "comment": "원인과 대안이 이어진다.", "score": 5},
        "attitude": {"sent": 0, "comment": "입장이 드러나지 않는다.", "score": 1},
        "delivery": {"sent": 2, "comment": "결론이 앞에 있다.", "score": 4}}
    payload, _ = await pipeline.evaluate_unit(ai, _q(), _a(), _POSTING)
    items = payload["items"]
    assert (items["EXPERTISE"]["evidence_status"], items["EXPERTISE"]["score"]) == ("UNLINKED", 4)
    assert (items["LOGIC"]["evidence_status"], items["LOGIC"]["score"]) == ("UNLINKED", 5)
    assert (items["ATTITUDE"]["evidence_status"], items["ATTITUDE"]["insufficient_reason"], items["ATTITUDE"]["score"]) \
        == ("INSUFFICIENT", "LOW_CONTENT", 1)
    assert items["DELIVERY"]["quote"]["text"] == sentences(ANSWER)[1].text
    assert payload["llm_calls"] == 2  # asked once more for the missing sentences
    scores = pipeline.unit_scores(payload)
    assert scores.scores[pipeline.Criterion.LOGIC] == 5 and scores.insufficient == {pipeline.Criterion.ATTITUDE}


async def test_no_answer_and_too_short_stay_rules_with_one_point_and_no_model_call(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    none, _ = await pipeline.evaluate_unit(ai, _q(), _a("", no_answer=True), _POSTING)
    short, _ = await pipeline.evaluate_unit(ai, _q(), _a("잘 모르겠습니다"), _POSTING)
    assert {v["evidence_status"] for v in none["items"].values()} == {"NO_ANSWER"}
    assert {(v["evidence_status"], v["insufficient_reason"], v["score"]) for v in short["items"].values()} == {("INSUFFICIENT", "TOO_SHORT", 1)}
    assert none["llm_calls"] == short["llm_calls"] == 0  # no model call
    assert len(ai.calls) == 2 and none["samples"] == short["samples"] == 1  # one request each: a rule is not sampled again


# ── median of independent judgements ─────────────────────────────────────────
async def test_the_kept_score_is_the_median_of_the_models_own_judgements(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = _judge([
        {"expertise": 4, "logic": 2, "attitude": 5, "delivery": 3},
        {"expertise": 4, "logic": 5, "attitude": 2, "delivery": 3},
        {"expertise": 1, "logic": 3, "attitude": 3, "delivery": 3},
    ])
    payload, manifest = await pipeline.evaluate_unit(ai, _q(), _a(), _POSTING)
    items = payload["items"]
    assert len(ai.calls) == 3 and payload["samples"] == 3 and manifest
    assert {c: v["score"] for c, v in items.items()} == {"EXPERTISE": 4, "LOGIC": 3, "ATTITUDE": 3, "DELIVERY": 3}
    assert items["LOGIC"]["comment"] == "logic 판단 3"  # the kept judgement is whole: its own comment and sentence
    assert payload["detail"]["logic_sub"]["a"] == 3  # sub-items from the judgement whose 논리성 was kept
    assert payload["scores_sampled"]["LOGIC"] == [2, 5, 3]


async def test_a_failed_sample_is_left_out_and_all_failed_fails_the_unit(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    ai.fail["evaluate_answer"] = "invalid"
    with pytest.raises(pipeline.EvaluationFailed):
        await pipeline.evaluate_unit(ai, _q(), _a(), _POSTING)


def test_median_of_two_is_the_lower_judgement():
    a = {"items": {"LOGIC": {"score": 2}, "EXPERTISE": {"score": 5}}}
    b = {"items": {"LOGIC": {"score": 4}, "EXPERTISE": {"score": 3}}}
    out = pipeline.median_judgement([a, b])
    assert out["items"]["LOGIC"]["score"] == 2 and out["items"]["EXPERTISE"]["score"] == 3


# ── the company's own items (T10) ────────────────────────────────────────────
async def test_company_items_cite_a_numbered_sentence_and_a_failed_citation_keeps_the_grade():
    items = [{"id": f"i{n}", "name": f"항목{n}", "description": "", "high": "상", "mid": "중", "low": "하"} for n in range(1, 4)]
    answers = [{"no": 1, "question": "자기소개", "text": "끝까지 책임지는 편입니다. 마감을 지킨 사례가 있습니다."},
               {"no": 2, "question": "협업", "text": "팀과 일정을 맞췄습니다."}]
    ai = FakeAIClient()
    calls = {"n": 0}

    def reply(inp, msgs):
        calls["n"] += 1
        return {"results": [{"id": "i1", "sent": 3, "comment": "협업 근거가 있다.", "grade": "상"},
                            {"id": "i2", "sent": 0, "comment": "근거를 못 찾았다.", "grade": "중"},
                            {"id": "i3", "sent": 0, "comment": "언급이 없다.", "grade": "하"}]}

    ai.responses["custom_criteria"] = reply
    r = await ai.llm("custom_criteria", {"items": items, "answers": answers}, lane="BATCH")
    out = r.output["items"]
    assert "[3] 팀과 일정을 맞췄습니다." in ai.calls[0].messages[0][1]["content"]  # numbered across the interview
    assert out["i1"]["evidence_status"] == "OK" and out["i1"]["quote"] == {"no": 2, "text": "팀과 일정을 맞췄습니다."}
    assert (out["i2"]["evidence_status"], out["i2"]["grade"], out["i2"]["score"]) == ("UNLINKED", "중", 3)  # asked again, then kept
    assert (out["i3"]["evidence_status"], out["i3"]["score"]) == ("INSUFFICIENT", 1)
    assert calls["n"] == 2
