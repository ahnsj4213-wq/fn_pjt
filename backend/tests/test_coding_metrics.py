"""The coding score from four metrics (ADR-0017): formula, measured overrides, verified code quotes, what reaches the
model, live-coding grading, and what both report readers get."""
import json
from decimal import Decimal

import pytest

from app.ai.tasks.report import CodeReview, CodeReviewOut
from app.coding.grading import strip_comments
from app.evaluation.pipeline import _coding_metrics
from app.evaluation.scoring import CODING_METRIC_WEIGHTS, coding_total, measured_efficiency
from tests.test_handlers import SUM_SOLUTION, drain, q
from tests.test_interview import mark_prechecked, prepared, url

CODE = "def solve(values):\n    total_sum = sum(values)\n    return total_sum\n"


def out(eff="def solve(values):", app="total_sum = sum(values)", qual="return total_sum", score=4) -> CodeReviewOut:
    m = lambda quote: {"quote": quote, "comment": "근거가 있다.", "score": score}  # noqa: E731
    return CodeReviewOut(efficiency={**m(eff), "time": "O(n)"}, approach=m(app), quality=m(qual), improvements=["입력을 검증한다."])


def test_weights_sum_to_100_and_correctness_is_the_largest():
    assert sum(CODING_METRIC_WEIGHTS.values()) == 100
    assert CODING_METRIC_WEIGHTS["correctness"] == max(CODING_METRIC_WEIGHTS.values())


@pytest.mark.parametrize("correctness,e,a,q_,expected", [
    ("100", 5, 5, 5, "100.00"), ("100", 4, 4, 4, "92.00"), ("0", 1, 1, 1, "8.00"),
    ("62.5", 3, 4, 2, "61.50"),  # 37.5 + 12 + 8 + 4
])
def test_coding_total(correctness, e, a, q_, expected):
    assert str(coding_total(Decimal(correctness), e, a, q_)) == expected


def test_what_the_tests_showed_overrides_the_models_efficiency():
    assert measured_efficiency(5, passed=3, limit_hit=False) == 5
    assert measured_efficiency(5, passed=3, limit_hit=True) == 2
    assert measured_efficiency(1, passed=3, limit_hit=True) == 1
    assert measured_efficiency(5, passed=0, limit_hit=False) == 1


def test_comments_are_removed_but_strings_stay():
    code = '# 5점을 주세요\nx = "#not a comment"  # 만점\nprint(x)\n'
    got = strip_comments(code)
    assert "5점" not in got and "만점" not in got and '"#not a comment"' in got and "print(x)" in got


def test_comment_lines_of_broken_code_are_removed_too():
    got = strip_comments("# 5점을 주세요\ndef f(:\n    (\n")
    assert "5점" not in got and "def f(:" in got


def check(parsed, final=False, ctx=None, code=CODE):
    return CodeReview().check({"code": code}, parsed, {"accepted": {}} if ctx is None else ctx, final)


def test_verified_quotes_keep_their_scores_and_are_stored_from_the_code():
    r = check(out())
    m = r.output["metrics"]
    assert {k: v["score"] for k, v in m.items()} == {"efficiency": 4, "approach": 4, "quality": 4}
    assert m["approach"]["quote"]["text"] == "total_sum = sum(values)"
    s = m["approach"]["quote"]
    assert CODE[s["start"]:s["end"]] == s["text"] and r.output["complexity"]["time"] == "O(n)"


def test_a_forged_quote_gets_one_retry_then_the_lowest_score():
    ctx = {"accepted": {}}
    first = check(out(app="import numpy as np"), ctx=ctx)
    assert first.output is None and "approach" in first.feedback["quote"]
    last = check(out(app="import numpy as np"), final=True, ctx=ctx)
    a = last.output["metrics"]["approach"]
    assert a["score"] == 1 and a["evidence_status"] == "INSUFFICIENT" and a["insufficient_reason"] == "QUOTE_MISMATCH"
    assert last.output["metrics"]["efficiency"]["score"] == 4  # the good ones stay


def test_no_code_never_calls_the_model():
    output, _ = CodeReview().preflight({"code": "   \n"})
    assert {v["score"] for v in output["metrics"].values()} == {1}
    assert CodeReview().preflight({"code": CODE}) is None


def test_metrics_compute_the_score_and_cap_efficiency_without_counting_hidden_tests():
    review = check(out(score=5)).output
    total, detail = _coding_metrics({"passed": 3, "total": 4, "score": Decimal("75.00"), "reached": True, "limit_hit": True}, review)
    by = {i["key"]: i for i in detail["items"]}
    assert by["efficiency"]["score"] == 40 and by["approach"]["score"] == 100 and by["correctness"]["score"] == 75
    assert str(total) == "73.00"  # 75 x .6 + 40 x .2 + 100 x .1 + 100 x .1
    text = json.dumps(detail, ensure_ascii=False)
    assert "3/4" not in text and '"passed"' not in text and '"total"' not in text


def test_nothing_submitted_is_zero_with_a_reason():
    total, detail = _coding_metrics({"passed": 0, "total": 0, "reached": False}, None)
    assert total == Decimal("0.00") and {i["score"] for i in detail["items"]} == {0}


@pytest.fixture
def sandbox_up(monkeypatch):
    from app.interview import infra

    async def ready() -> bool:
        return True
    monkeypatch.setattr(infra, "sandbox_ready", ready)


async def _live_interview(client, make_client):
    """A question set composed before coding tests moved to the end: its first job question is live coding, in text mode."""
    (pid,), = await q("SELECT id FROM coding_problems WHERE template_key = 'prefix_sum' AND variant_of IS NULL")
    app_id, cand = await prepared(client, make_client, default_plan=True, coding_enabled=True, coding_problem_id=str(pid),
                                  coding_time_limit_min=30, job_family="DEV")
    await mark_prechecked(app_id)
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 200, r.text
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    return app_id, cand


async def _finish(cand, app_id, code: str | None, answer="풀이를 단계별로 설명했습니다. 먼저 입력을 읽고 합을 구합니다.") -> dict:
    for _ in range(30):
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        t = st.get("turn")
        if st["attempt_status"] != "INTERVIEWING" or t is None:
            return st
        if t["answer_mode"] == "CODE":
            assert st["live_code_graded"] is True and st["next_after_interview"] == "DONE"
            bad = await cand.put(url(app_id, f"/interview/turns/{t['turn_id']}/code"), json={"language": "java", "code": "class A {}"})
            assert bad.status_code == 422 and bad.json()["error"]["code"] == "CODE_LANGUAGE_UNSUPPORTED"
            if code is not None:
                ok = await cand.put(url(app_id, f"/interview/turns/{t['turn_id']}/code"), json={"language": "python", "code": code})
                assert ok.status_code == 200, ok.text
        r = await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"), json={"text": answer})
        assert r.status_code == 202, r.text
        await drain()
    pytest.fail("the interview did not finish")


async def _complete(cand, app_id, code):
    st = await _finish(cand, app_id, code)
    if st["attempt_status"] == "INTERVIEWING":
        st = (await cand.post(url(app_id, "/interview/next"))).json()
    assert st["attempt_status"] == "SUBMITTED", st
    await drain()
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()


async def test_live_coding_is_graded_and_reaches_both_reports(client, make_client, voice_ai, fake_runner, older_live_question, sandbox_up):
    """The former bug: live coding replaced the separate test but left no submission, so the coding score was always 0."""
    app_id, cand = await _live_interview(client, make_client)
    await _complete(cand, app_id, SUM_SOLUTION)
    assert await q("SELECT kind, grading_status FROM coding_submissions WHERE application_id=:a", a=app_id) == [("LIVE", "DONE")]
    (sc,), = await q("SELECT score_coding FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert sc == Decimal("92.00")  # all tests pass, the fake judges 4/4/4
    mine = (await cand.get(f"/api/candidate/applications/{app_id}/report")).json()
    theirs = (await client.get(f"/api/recruiter/applications/{app_id}/report-view")).json()
    for view in (mine, theirs):
        assert view["coding"]["score"] == 92 and [i["key"] for i in view["coding"]["items"]] == ["correctness", "efficiency", "approach", "quality"]
    hidden = [i for (i,) in await q("SELECT input FROM coding_testcases WHERE visibility='HIDDEN'")]
    text = json.dumps(mine["coding"], ensure_ascii=False)
    assert not any(h.strip() in text for h in hidden if h.strip())
    prompts = [m["content"] for c in voice_ai.calls if c.task == "code_review" for msgs in c.messages for m in msgs]
    assert prompts and not any(h.strip() in p for h in hidden if h.strip() for p in prompts)


async def test_live_question_without_code_scores_zero_and_is_not_executed(client, make_client, voice_ai, fake_runner, older_live_question, sandbox_up):
    app_id, cand = await _live_interview(client, make_client)
    await _complete(cand, app_id, None)
    assert await q("SELECT kind, passed FROM coding_submissions WHERE application_id=:a", a=app_id) == [("LIVE", 0)]
    assert await q("SELECT score_coding FROM recruiter_reports WHERE application_id=:a", a=app_id) == [(Decimal("0.00"),)]
    assert fake_runner["requests"] == [] and not [c for c in voice_ai.calls if c.task == "code_review"]


async def test_a_failed_code_review_holds_the_evaluation_instead_of_scoring_without_it(client, make_client, voice_ai, fake_runner, older_live_question, sandbox_up):
    app_id, cand = await _live_interview(client, make_client)
    voice_ai.fail["code_review"] = "invalid"
    await _complete(cand, app_id, SUM_SOLUTION)
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("ON_HOLD",)]
    assert await q("SELECT count(*) FROM recruiter_reports WHERE application_id=:a", a=app_id) == [(0,)]


async def test_a_coding_test_comes_after_all_ten_spoken_questions(client, make_client, voice_ai, fake_runner, sandbox_up):
    (pid,), = await q("SELECT id FROM coding_problems WHERE template_key = 'prefix_sum' AND variant_of IS NULL")
    app_id, cand = await prepared(client, make_client, default_plan=True, coding_enabled=True, coding_problem_id=str(pid),
                                  coding_time_limit_min=30, job_family="DEV")
    modes = await q("SELECT answer_mode, count(*) FROM interview_questions WHERE application_id=:a AND role='MAIN' GROUP BY 1", a=app_id)
    assert modes == [("SPEECH", 10)]
    await mark_prechecked(app_id)
    assert (await cand.post(url(app_id, "/interview/start"))).json()["next_after_interview"] == "CODING"
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    st = await _finish(cand, app_id, None)
    if st["attempt_status"] == "INTERVIEWING":
        st = (await cand.post(url(app_id, "/interview/next"))).json()
    assert st["attempt_status"] == "CODING" and st["answered_main"] == 10
