"""SC-A06 live coding: the submitted code reaches the evaluation prompt, quotes stay tied to the spoken answer."""
from types import SimpleNamespace

from app.ai.tasks.evaluate import EvaluateAnswer
from app.evaluation.pipeline import CODE_MAX_CHARS, unit_input


def _q():
    return SimpleNamespace(text="중복 결제를 막는 함수를 작성해 주십시오.", slot_type="JOB_SKILL")


def _posting():
    return SimpleNamespace(title="백엔드 개발자", description="결제 API", requirements=None, preferred=None,
                           duties=None, job_family="DEV")


def test_code_is_added_to_unit_input_and_prompt(monkeypatch):
    monkeypatch.setattr("app.evaluation.pipeline.job_brief", lambda p: "결제 API")
    a = SimpleNamespace(eval_text="요청마다 고유 키를 두고 저장소에서 먼저 확인합니다.", is_no_answer=False,
                        code_text="def pay(k):\n    return k\n" + "x" * (CODE_MAX_CHARS + 50), code_language="python")
    inp = unit_input(_q(), a, _posting())
    assert inp["code_language"] == "python" and len(inp["code"]) == CODE_MAX_CHARS
    user, _ = EvaluateAnswer().build(inp, "n1")
    assert inp["question_kind"] == "TECHNICAL" and "질문 유형: 기술 지식" in user  # live coding is a knowledge question
    assert "<<<CODE_n1>>>" in user and "def pay(k):" in user and "sent는 답변 문장 번호에서만" in user


def test_speech_answer_has_no_code_block(monkeypatch):
    monkeypatch.setattr("app.evaluation.pipeline.job_brief", lambda p: "결제 API")
    a = SimpleNamespace(eval_text="팀에서 의견을 조율했습니다.", is_no_answer=False, code_text=None, code_language=None)
    inp = unit_input(_q(), a, _posting())
    assert "code" not in inp
    user, _ = EvaluateAnswer().build(inp, "n1")
    assert "CODE_n1" not in user
