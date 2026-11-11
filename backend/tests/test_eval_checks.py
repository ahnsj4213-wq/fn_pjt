"""The server's checks of an answer unit's judgement and the one re-judgement (app/evaluation/checks.py, pipeline.evaluate_unit):
reason codes, at most one re-judgement per evaluation of a unit, a hard cap on model calls, system side vs content side,
who decides what (model: score candidate, reason, sentence; code: checks, scores, total), coding correctness from the test
runs only, hidden test cases never in a prompt or a log line, and no transaction open while the model runs."""
import ast
import inspect
import json
import logging
from types import SimpleNamespace

import pytest

from app.ai import rubric
from app.ai.fake import FakeAIClient
from app.ai.tasks import TASKS
from app.ai.tasks.evaluate import EvalOut, EvalOutG
from app.ai.tasks.report import CodeReviewOut
from app.core.config import get_settings
from app.core.constants import Criterion
from app.evaluation import checks, pipeline, scoring
from app.evaluation import units as U
from app.evaluation.quotes import sentences
from tests.test_eval_graph import UNITS, answered, count, pre_evaluate_all, watch_scopes
from tests.test_handlers import (  # noqa: F401 - fake_ai is a fixture
    SUM_SOLUTION,
    answer_all,
    coding_app,
    drain,
    fake_ai,
    prepare_and_compose,
    q,
    submit_and_register,
    submit_code,
)

ANSWER = ("주문 API 응답이 3초까지 느려진 문제가 있었습니다. 실행 계획을 보니 인덱스를 타지 않아 복합 인덱스를 추가했습니다. "
          "배포 후 응답 시간이 0.4초로 줄어든 것을 모니터링으로 확인했습니다.")
KEYS = ("expertise", "logic", "attitude", "delivery")
GOOD = "원인과 조치가 이어진다"
_Q = SimpleNamespace(text="성능 문제를 해결했던 경험을 말씀해 주세요.", slot_type="PROJECT")
_POSTING = SimpleNamespace(title="백엔드 개발자", description="주문 API", requirements=None, preferred=None, duties=None, job_family="DEV")


def _a(text=ANSWER):
    return SimpleNamespace(eval_text=text, is_no_answer=False, code_text=None, code_language=None)


def _out(sent, score, comment=GOOD, **over):
    o = {k: {"sent": sent, "comment": comment, "score": score} for k in KEYS}
    o.update(over)
    return o | {"found": {"problem": 1, "cause": 2, "grounds": 2, "alternatives": 0, "action": 2, "result": 3, "knowledge": 2},
                "expertise_sub": {"a": 3, "b": 3, "c": 3}, "logic_sub": {"a": 3, "b": 3, "c": 3},
                "star": {"situation": 1, "task": 0, "action": 1, "result": 1}}


def _script(*replies):
    """One reply per T5 request (the last one repeats); the request index is the number of earlier requests."""
    state = {"n": 0}

    def reply(inp, msgs):
        if len(msgs) == 2:  # a new request starts with the system and the user message only
            state["n"] += 1
        return replies[min(state["n"] - 1, len(replies) - 1)]
    return reply


def _item(score=4, status="OK", sent=1, comment=GOOD, reason=None):
    s = sentences(ANSWER)[sent - 1]
    quote = {"start": s.start, "end": s.end, "text": s.text, "sent": sent} if status == "OK" else None
    return {"score": score, "evidence_status": status, "insufficient_reason": reason, "quote": quote, "comment": comment}


def _judged(**over):
    items = {c.value: _item() for c in Criterion}
    items.update(over)
    return {"items": items, "rubric_version": rubric.RUBRIC_VERSION, "llm": True}


# ── reason codes (code only) ───────────────────────────────────────────────────
def test_a_consistent_judgement_has_no_reason_code():
    assert checks.find_problems(_judged(), ANSWER) == {}


def test_each_inconsistency_has_its_reason_code():
    wrong_quote = {**_item(), "quote": {"start": 0, "end": 5, "text": "다른 문장", "sent": 1}}
    found = checks.find_problems(_judged(
        EXPERTISE=wrong_quote,
        LOGIC=_item(4, "UNLINKED"),                                   # a high score without a verified sentence
        ATTITUDE=_item(5, comment="구체적인 근거가 부족하다"),          # the reason says lacking, the score is high
        DELIVERY={**_item(), "score": 7}), ANSWER)                    # outside 1~5
    assert found == {"EXPERTISE": ["QUOTE_MISMATCH"], "LOGIC": ["NO_EVIDENCE"], "ATTITUDE": ["REASON_SCORE"],
                     "DELIVERY": ["SHAPE"]}
    missing = _judged()
    del missing["items"]["LOGIC"]
    assert checks.find_problems(missing, ANSWER) == {"LOGIC": ["SHAPE"]}


def test_samples_far_apart_are_unstable():
    samples = [{"items": {c.value: {"score": s} for c in Criterion}} for s in (1, 5, 4)]
    assert set(checks.find_problems(_judged(), ANSWER, samples)) == {c.value for c in Criterion}
    assert all(v == ["UNSTABLE"] for v in checks.find_problems(_judged(), ANSWER, samples).values())
    close = [{"items": {c.value: {"score": s} for c in Criterion}} for s in (3, 4, 5)]  # 2 apart: not unstable
    assert checks.find_problems(_judged(), ANSWER, close) == {}


@pytest.mark.parametrize(("comment", "lacking"), [
    ("구체적인 근거가 부족하다", True), ("결과가 드러나지 않는다", True), ("원인 설명이 모호하다", True), ("근거가 없다", True),
    ("장애 없이 배포했고 오류가 없다", False), ("원인과 조치가 이어진다", False), ("수치로 개선을 설명했다", False)])
def test_the_lacking_reason_is_narrow(comment, lacking):
    assert checks.lacking(comment) is lacking


def test_what_still_fails_is_settled_on_the_system_or_the_content_side():
    left = {"EXPERTISE": ["QUOTE_MISMATCH"], "LOGIC": ["NO_EVIDENCE"], "ATTITUDE": ["REASON_SCORE"], "DELIVERY": ["NO_EVIDENCE"]}
    judged = _judged(LOGIC=_item(4, "UNLINKED"), ATTITUDE=_item(5, comment="사례가 부족하다"),
                     DELIVERY=_item(3, "UNLINKED", comment="결과가 드러나지 않는다"))
    items, side = checks.settle(judged, left)
    assert (items["EXPERTISE"]["evidence_status"], items["EXPERTISE"]["insufficient_reason"], items["EXPERTISE"]["score"]) \
        == ("INSUFFICIENT", "QUOTE_MISMATCH", 1)
    assert (items["LOGIC"]["evidence_status"], items["LOGIC"]["score"]) == ("UNLINKED", 4)  # the model's score stays
    assert (items["ATTITUDE"]["insufficient_reason"], items["ATTITUDE"]["score"], items["ATTITUDE"]["quote"]) == ("LOW_CONTENT", 2, None)
    assert (items["DELIVERY"]["insufficient_reason"], items["DELIVERY"]["score"]) == ("LOW_CONTENT", 2)  # no sentence, lacking
    assert side == {"EXPERTISE": "SYSTEM", "LOGIC": "SYSTEM", "ATTITUDE": "CONTENT", "DELIVERY": "CONTENT"}
    with pytest.raises(ValueError):
        checks.settle(_judged(), {"LOGIC": ["SHAPE"]})
    # what settle leaves is exactly what the stored-result rules accept (and the DB constraint: LOW_CONTENT <= 2)
    payload = {**judged, "items": items, "check": {"version": checks.CHECK_VERSION}}
    assert U.reuse_problems(payload, _a()) == []


# ── the one re-judgement ───────────────────────────────────────────────────────
async def test_a_failed_unit_is_judged_again_once_with_the_reasons_and_never_more(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = _script(_out(2, 5, "구체적인 근거가 부족하다"))  # always inconsistent
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    assert len(ai.calls) == 3 + 1 and payload["llm_calls"] == 4
    assert [bool(c.input.get("review")) for c in ai.calls] == [False, False, False, True]
    review = ai.calls[-1].input["review"]
    assert len(review) == 4 and all("이유(comment)" in x for x in review)
    assert not any(s.text in x for x in review for s in sentences(ANSWER))  # code-written reasons, no answer text
    assert "다시 판정할 이유" in ai.calls[-1].messages[0][1]["content"] and "다시 판정할 이유" not in ai.calls[0].messages[0][1]["content"]
    c = payload["check"]
    assert c["rejudged"] is True and c["found"] == {k: ["REASON_SCORE"] for k in ("EXPERTISE", "LOGIC", "ATTITUDE", "DELIVERY")}
    assert c["side"] == {k: "CONTENT" for k in ("EXPERTISE", "LOGIC", "ATTITUDE", "DELIVERY")}
    assert {(v["evidence_status"], v["insufficient_reason"], v["score"]) for v in payload["items"].values()} == {
        ("INSUFFICIENT", "LOW_CONTENT", 2)}


async def test_a_consistent_unit_is_not_judged_again(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = _script(_out(2, 4))
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    assert len(ai.calls) == 3 and payload["check"] == {"version": checks.CHECK_VERSION, "found": {}, "rejudged": False,
                                                        "left": {}, "side": {}}


async def test_the_re_judgement_fixes_what_it_can_and_the_rest_is_system_side(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 1)
    ai = FakeAIClient()
    second = _out(2, 4, logic={"sent": 0, "comment": GOOD, "score": 4})  # finds a sentence for three, not for 논리성
    ai.responses["evaluate_answer"] = _script(_out(0, 4), second)
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    items = payload["items"]
    assert len(ai.calls) == 2 and payload["check"]["rejudged"]
    assert {c: items[c]["evidence_status"] for c in items} == {"EXPERTISE": "OK", "LOGIC": "UNLINKED", "ATTITUDE": "OK", "DELIVERY": "OK"}
    assert items["EXPERTISE"]["quote"]["text"] == sentences(ANSWER)[1].text and items["LOGIC"]["score"] == 4
    assert payload["check"]["left"] == {"LOGIC": ["NO_EVIDENCE"]} and payload["check"]["side"] == {"LOGIC": "SYSTEM"}


async def test_an_unstable_criterion_is_confirmed_or_stays_marked(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = _script(_out(2, 1), _out(2, 5), _out(2, 4), _out(2, 3))  # median 4, the 4th says 3
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    assert len(ai.calls) == 4 and payload["check"]["left"] == {}  # confirmed within 1 point
    assert {v["score"] for v in payload["items"].values()} == {4}  # the median stays: the extra judgement never moves it
    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = _script(_out(2, 1), _out(2, 5), _out(2, 4), _out(2, 1))  # the 4th disagrees
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    assert len(ai.calls) == 4 and all(v == ["UNSTABLE"] for v in payload["check"]["left"].values())
    assert {(v["evidence_status"], v["score"]) for v in payload["items"].values()} == {("OK", 4)}  # neither held nor capped
    assert U.check_counts([payload])["unstable_items"] == 4


async def test_a_rule_result_is_not_checked_or_judged_again(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a("잘 모르겠습니다"), _POSTING)
    assert len(ai.calls) == 1 and payload["llm_calls"] == 0 and payload["check"]["rule"] is True


@pytest.mark.parametrize("samples", [1, 3])
async def test_the_model_calls_of_a_unit_never_exceed_the_cap(monkeypatch, samples):
    """Worst case on every request: two format errors, a P-8 comment, a reference-case comment, then a judgement that still
    fails the checks. Every budget is spent and the total is exactly the cap, never more."""
    monkeypatch.setattr(get_settings(), "eval_samples", samples)
    p8 = _out(1, 4, "합격 추천할 만한 답변이다")
    leak = _out(1, 4, "캐시로 응답 시간을 줄였다")
    assert "캐시" in rubric.EXAMPLE_TERMS and "캐시" not in ANSWER

    def reply(inp, msgs):
        turn = (len(msgs) - 2) // 2  # earlier assistant replies in this request
        return ["not json", "not json", p8, leak][turn] if turn < 4 else _out(0, 5)

    ai = FakeAIClient()
    ai.responses["evaluate_answer"] = reply
    payload, _ = await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    cap = checks.unit_call_cap(samples)
    spec = TASKS["evaluate_answer"]
    assert cap == (samples + 1) * (1 + spec.format_retries + sum(spec.regen_limits.values()))
    assert payload["llm_calls"] == cap and len(ai.calls) == samples + checks.MAX_REJUDGE
    assert payload["check"]["rejudged"] and payload["check"]["left"]


async def test_no_usable_judgement_fails_the_unit_without_a_re_judgement(monkeypatch):
    monkeypatch.setattr(get_settings(), "eval_samples", 3)
    ai = FakeAIClient()
    ai.fail["evaluate_answer"] = "invalid"  # run_task already spent its format retries on each sample
    with pytest.raises(pipeline.EvaluationFailed):
        await pipeline.evaluate_unit(ai, _Q, _a(), _POSTING)
    assert len(ai.calls) == 3


# ── within the evaluation graph ────────────────────────────────────────────────
async def test_a_settled_pre_evaluation_is_reused_and_not_judged_again_in_the_final_run(client, make_client, fake_ai):
    app_id, ids = await answered(client, make_client)
    fake_ai.responses["evaluate_answer"] = lambda inp, msgs: _out(0, 4)  # UNLINKED every time
    await pre_evaluate_all(ids)
    judged = UNITS - 1  # seq 5 is a silent answer: a rule result
    assert count(fake_ai, "evaluate_answer") == UNITS + judged  # one re-judgement per judged unit, in pre_evaluate
    # system side only for 16 items of 16 judged: the run is held, and no unit is evaluated a second time
    await submit_and_register(app_id)
    await drain()
    assert count(fake_ai, "evaluate_answer") == UNITS + judged
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("PENDING",)]


async def test_content_side_is_scored_low_with_no_transaction_open_and_counted(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    fake_ai.responses["evaluate_answer"] = lambda inp, msgs: _out(1, 5, "구체적인 사례가 부족하다")
    seen = watch_scopes(fake_ai)
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    assert [x for x in seen if x[1] != 0] == [] and sum(t == "evaluate_answer" for t, _ in seen) == 2 * (UNITS - 1) + 1
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    ev = det["evidence"]
    assert (ev["system_items"], ev["content_items"], ev["rejudged_units"]) == (0, 16, UNITS - 1)
    assert ev["check_found"] == {"REASON_SCORE": 16} and ev["unstable_items"] == 0
    rows = await q("SELECT DISTINCT evidence_status, insufficient_reason, score FROM answer_evaluations WHERE evidence_status <> 'NO_ANSWER'")
    assert rows == [("INSUFFICIENT", "LOW_CONTENT", 2)]


# ── who decides what ───────────────────────────────────────────────────────────
def _imports(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


def test_the_model_gives_item_judgements_only_and_code_computes_the_rest():
    for model in (EvalOut, EvalOutG):  # per criterion: sentence, reason, score candidate. No total, no decision.
        fields = set(model.model_fields)
        assert {"expertise", "logic", "attitude", "delivery"} <= fields
        assert not fields & {"total", "total_score", "decision", "pass", "passed", "recommend", "recommendation", "result"}
    assert not set(CodeReviewOut.model_fields) & {"correctness", "passed", "total", "score", "pass_rate"}
    # the checks and the score computation never reach the model
    for module in (checks, scoring):
        assert not {m for m in _imports(module) if m.startswith(("app.ai.client", "app.aisvc", "httpx"))}
        assert "get_ai_client" not in inspect.getsource(module)
    qs = [scoring.QuestionScores(1, scoring.UnitScores({c: 3 for c in Criterion}))]
    with pytest.raises(ValueError, match="sum to 100"):
        scoring.compute(qs, {"expertise": 30, "logic": 30, "attitude": 20, "delivery": 10}, None)


# ── coding: correctness is what the test runs showed ───────────────────────────
@pytest.mark.parametrize(("code", "review_score", "all_passed"), [("print(0)", 5, False), (SUM_SOLUTION, 1, True)])
async def test_coding_correctness_comes_from_the_test_runs_not_the_code_review(client, make_client, fake_ai, fake_runner, caplog,
                                                                               code, review_score, all_passed):
    caplog.set_level(logging.DEBUG)
    app_id, cand = await coding_app(client, make_client)
    await answer_all(app_id, await prepare_and_compose(cand, app_id))
    default = FakeAIClient._reply_code_review

    def review(inp, msgs):  # T8 praises wrong code / condemns right code: neither changes correctness
        out = default(inp)
        for k in ("efficiency", "approach", "quality"):
            out[k]["score"] = review_score
        return out

    fake_ai.responses["code_review"] = review
    await submit_code(app_id, code)
    await submit_and_register(app_id)
    await drain()
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    (sub_score, passed, total), = await q("SELECT score, passed, total FROM coding_submissions WHERE application_id=:a", a=app_id)
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    correctness = next(i for i in det["coding"]["items"] if i["key"] == "correctness")
    assert correctness["score"] == float(sub_score) and correctness["evidence_status"] == "MEASURED"
    assert (passed == total) is all_passed
    assert await q("SELECT coding_all_passed FROM candidate_feedback WHERE application_id=:a", a=app_id) == [(all_passed,)]
    # hidden test cases: never in a prompt of any task, never in a log line
    hidden = [x for row in await q("SELECT input, expected_output FROM coding_testcases WHERE visibility='HIDDEN'") for x in row]
    hidden = [h.strip() for h in hidden if h and len(h.strip()) >= 8]
    assert hidden
    prompts = [m["content"] for c in fake_ai.calls for msgs in c.messages for m in msgs]
    inputs = [json.dumps(c.input, ensure_ascii=False) for c in fake_ai.calls]
    logs = [json.dumps({k: str(v) for k, v in r.__dict__.items()}, ensure_ascii=False) for r in caplog.records]
    assert prompts and logs
    assert not [h for h in hidden for text in prompts + inputs + logs if h in text]
