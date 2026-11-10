"""rubric-v5 / evaluate_answer@2.1.0 (2026-11-09 user directive): the model still decides 1~5 from the rubric, but by the
rubric's elements and what the answer means, not by how close it reads to a reference case."""
from types import SimpleNamespace

from app.ai import rubric
from app.ai.tasks.evaluate import EvalOut, EvaluateAnswer, example_leak
from app.evaluation.pipeline import candidate_level, unit_input


def test_examples_are_reference_cases_not_conditions():
    text = rubric.render()
    assert "참고 사례:" in text and " 예: " not in text
    assert "사례와 문장·표현·기술·길이가 비슷한지로 점수를 정하지 않고" in text
    assert "지원자가 말한 것으로 보지 않는다" in text
    assert "짧더라도 질문에 정확하고 충분하게 답했으면 5점도 줄 수 있다" in text
    assert "모든 답변이 갖춰야 할 목록이 아니다" in text  # 육하원칙 is a tool
    sys = EvaluateAnswer.system
    assert "같은 내용을 다른 말로 한 답변은 같은 점수다" in sys and "참고 사례에만 나오는" in sys


def test_logic_view_follows_the_kind_of_question_and_the_others_too():
    exp, tech, design = (rubric.render_kind(k) for k in ("EXPERIENCE", "TECHNICAL", "DESIGN"))
    assert all(w in exp for w in ("상황", "판단", "행동", "결과"))
    assert all(w in tech for w in ("개념", "이유", "일관성")) and "필수가 아니다" in tech
    assert all(w in design for w in ("요구사항", "대안", "트레이드오프"))
    for k in rubric.QUESTION_KINDS:
        assert "태도:" in rubric.render_kind(k) and "전달력:" in rubric.render_kind(k)


def test_level_reads_the_rubric_without_moving_scores():
    new = rubric.render_kind("EXPERIENCE", "NEW")
    assert "신입" in new and "학교·개인·팀 프로젝트" in new and "점수를 일괄로 올리거나 내리지 않는다" in new
    assert "지원자 수준" not in rubric.render_kind("EXPERIENCE", None)
    user, _ = EvaluateAnswer().build({"question": "q", "answer": "답변입니다. 두 번째 문장입니다.", "question_type": "JOB_SKILL",
                                      "level": "SENIOR"}, "n")
    assert "경력자(5년 이상)" in user


def test_level_is_the_only_thing_taken_from_the_resume():
    app = SimpleNamespace(resume_snapshot={"name": "김지원", "experience": [{"company": "A", "title": "백엔드 개발자",
                                                                            "period": "2015.01 ~ 현재"}]})
    posting = SimpleNamespace(experience_level=None, title="백엔드 개발자", requirements="", duties="", preferred="",
                              job_family="DEV")
    assert candidate_level(app, posting) == "SENIOR" and candidate_level(None, posting) is None
    q = SimpleNamespace(text="인덱스는 무엇입니까?", slot_type="JOB_SKILL")
    a = SimpleNamespace(eval_text="B트리입니다.", is_no_answer=False, code_text=None)
    inp = unit_input(q, a, SimpleNamespace(title="t", duties="", requirements="", preferred="", job_family="DEV",
                                           experience_level=None), "SENIOR")
    assert inp["level"] == "SENIOR" and "김지원" not in str(inp) and "resume" not in inp


def _out(comment: str, score: int = 4, sent: int = 1) -> EvalOut:
    item = {"sent": sent, "comment": comment, "score": score}
    plain = {"sent": 1, "comment": "원인과 조치가 이어짐", "score": 4}
    return EvalOut.model_validate({"expertise": item, "logic": plain, "attitude": plain, "delivery": plain})


def test_a_comment_that_takes_the_reference_case_for_the_answer_is_asked_again_then_dropped():
    answer = "사용자 요청이 몰리면 큐에 쌓아 순서대로 처리했습니다. 처리 지연은 대시보드로 확인했습니다."
    assert example_leak("인덱스를 추가해 개선함", answer) == ["인덱스"]
    assert example_leak("큐로 순서 처리", answer) == []
    spec, inp = EvaluateAnswer(), {"question": "q", "answer": answer}
    _, ctx = spec.build(inp, "n")
    first = spec.check(inp, _out("인덱스 추가로 속도 개선"), ctx, final=False)
    assert "example" in first.feedback and "expertise(인덱스)" in first.feedback["example"]
    _, ctx = spec.build(inp, "n")
    last = spec.check(inp, _out("인덱스 추가로 속도 개선", score=4), ctx, final=True)
    it = last.output["items"]["EXPERTISE"]
    assert it["score"] == 4 and it["comment"] == "" and last.flags["example_leak"]  # the score stays the model's
