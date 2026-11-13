"""Coding test API (handoff step 5; TRD 04 §5.3, COD-01~06, AC-15) over HTTP. The candidate reaches CODING through
the real interview (text mode), the runner is the local fake that executes the submitted Python, AI is fake."""
import asyncio

import pytest

from app.interview import infra
from app.jobs import tasks
from tests.test_handlers import SUM_SOLUTION, drain, q, setup_app
from tests.test_interview import mark_prechecked, url


@pytest.fixture
def sandbox_up(monkeypatch):
    state = {"ready": True}

    async def ready() -> bool:
        return state["ready"]

    monkeypatch.setattr(infra, "sandbox_ready", ready)
    return state


async def to_coding(client, make_client, voice_ai) -> tuple[str, object]:
    (pid,), = await q("SELECT id FROM coding_problems WHERE template_key = 'prefix_sum' AND variant_of IS NULL")
    # A non-coding family keeps the separate coding test (in 개발·데이터·AI the problem is a live-coding question).
    _, app_id, cand = await setup_app(client, make_client, coding_enabled=True, coding_problem_id=str(pid),
                                      coding_time_limit_min=30, job_family="MGMT")
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200
    await drain()
    await mark_prechecked(app_id)
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 200 and r.json()["next_after_interview"] == "CODING", r.text
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    while True:
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        if st["attempt_status"] != "INTERVIEWING":
            break
        r = await cand.post(url(app_id, f"/interview/turns/{st['turn']['turn_id']}/text-answer"),
                            json={"text": "주문 API의 지연을 캐시와 인덱스로 줄인 경험이 있습니다."})
        assert r.status_code == 202
    assert st["attempt_status"] == "CODING" and st["coding_deadline_at"]  # COD-01: right after the interview
    assert await q("SELECT 1 FROM interview_slots WHERE application_id=:a", a=app_id) == []  # slot returned (T10)
    return app_id, cand


async def test_ac15_run_shows_normal_or_abnormal_only_and_submit_is_graded(client, make_client, voice_ai,
                                                                          fake_runner, sandbox_up):
    app_id, cand = await to_coding(client, make_client, voice_ai)
    hidden = [i for (i,) in await q("SELECT input FROM coding_testcases WHERE visibility='HIDDEN'")]

    view = await cand.get(url(app_id, "/coding"))
    body = view.json()
    assert view.status_code == 200 and body["language"] == "python" and body["run_available"] is True
    assert len(body["problem"]["examples"]) == 2 and not any(h in view.text for h in hidden)  # PUBLIC only

    r = await cand.put(url(app_id, "/coding/draft"), json={"code": "print(1)", "revision": 2})
    assert r.json()["applied"] is True
    r = await cand.put(url(app_id, "/coding/draft"), json={"code": "stale", "revision": 1})
    assert r.json()["applied"] is False and r.json()["revision"] == 2  # a late older autosave never wins (D-11)

    r = await cand.post(url(app_id, "/coding/runs"), json={"code": SUM_SOLUTION})
    run = r.json()
    assert r.status_code == 200 and run["verdict"] == "NORMAL" and run["outcome"] == "OK"
    assert [e["stdout"] for e in run["examples"]] == ["6\n9\n5\n", "0\n"]
    assert not {"passed", "correct", "expected_output", "score"} & set(run) and not any(h in r.text for h in hidden)
    assert len(fake_runner["requests"][-1]["cases"]) == 2  # [실행] sends the public examples only
    bad = (await cand.post(url(app_id, "/coding/runs"), json={"code": "raise SystemExit(3)"})).json()
    assert bad["verdict"] == "ABNORMAL" and bad["outcome"] == "RUNTIME_ERROR"
    again = (await cand.get(url(app_id, f"/coding/runs/{bad['run_id']}"))).json()
    assert again["verdict"] == "ABNORMAL"  # same result after a refresh

    r = await cand.post(url(app_id, "/coding/submit"), json={"code": SUM_SOLUTION, "revision": 3, "confirmed": False})
    assert r.status_code == 422
    r = await cand.post(url(app_id, "/coding/submit"), json={"code": SUM_SOLUTION, "revision": 3, "confirmed": True})
    assert r.status_code == 200 and r.json() == {"attempt_status": "SUBMITTED"}
    r = await cand.post(url(app_id, "/coding/submit"), json={"code": "x", "revision": 4, "confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_SUBMITTED" and r.json()["error"]["kind"] == "MANUAL"
    (dep,), = await q("SELECT depends_on IS NOT NULL FROM jobs WHERE kind='evaluate_application'")
    assert dep  # the evaluation waits for grading (TRD 03 A7)

    await drain()
    (cases,), = await q("SELECT count(*) FROM coding_testcases t JOIN coding_attempts a ON a.problem_id = t.problem_id "
                        "WHERE a.application_id=:a", a=app_id)  # the application's own variant: 2 public + generated hidden
    assert cases >= 5
    assert await q("SELECT passed, total FROM coding_submissions WHERE application_id=:a", a=app_id) == [(cases, cases)]
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    rec = (await client.get(f"/api/recruiter/applications/{app_id}/coding-result")).json()
    assert {t["visibility"] for t in rec["tests"]} == {"PUBLIC", "HIDDEN"}  # hidden tests are for the recruiter only
    assert await q("SELECT coding_all_passed FROM candidate_feedback WHERE application_id=:a", a=app_id) == [(True,)]


async def test_ac15_deadline_autosubmits_last_draft_even_when_disconnected(client, make_client, voice_ai,
                                                                          fake_runner, sandbox_up):
    app_id, cand = await to_coding(client, make_client, voice_ai)
    assert (await cand.put(url(app_id, "/coding/draft"), json={"code": SUM_SOLUTION, "revision": 1})).status_code == 200
    # The browser goes away: no coding calls for 120 s -> the attempt ends (C7, ADR-0024) and the last draft is submitted
    # as it is (COD-05): nothing waits for the deadline any more.
    await q("UPDATE interviews SET last_heartbeat_at = now() - interval '3 minutes' WHERE application_id=:a RETURNING 1",
            a=app_id)
    await tasks.attempt_sweeper()
    assert await q("SELECT attempt_status, interrupted_from FROM applications WHERE id=:a", a=app_id) == [("EXPIRED", None)]
    assert await q("SELECT kind, code FROM coding_submissions WHERE application_id=:a", a=app_id) ==         [("AUTO_EXPIRED", SUM_SOLUTION)]
    await tasks.coding_autosubmit()  # idempotent
    assert len(await q("SELECT 1 FROM coding_submissions WHERE application_id=:a", a=app_id)) == 1
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    r = await cand.put(url(app_id, "/coding/draft"), json={"code": "late", "revision": 9})
    assert r.status_code == 409


async def test_two_final_submissions_at_once_make_one_submission_and_one_evaluation(client, make_client, voice_ai,
                                                                                   fake_runner, sandbox_up):
    """A double-clicked [최종 제출] (two requests at once): the application lock and the submission's unique key give one
    submission, one grading job and one evaluation job; the other request is ALREADY_SUBMITTED. The submission's own graph
    thread (submit:<application>) is deleted and the interview thread is not recreated by it."""
    app_id, cand = await to_coding(client, make_client, voice_ai)
    body = {"code": SUM_SOLUTION, "revision": 2, "confirmed": True}
    r1, r2 = await asyncio.gather(cand.post(url(app_id, "/coding/submit"), json=body),
                                  cand.post(url(app_id, "/coding/submit"), json=body))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (r1.text, r2.text)
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["error"]["code"] == "ALREADY_SUBMITTED"
    assert await q("SELECT count(*) FROM coding_submissions WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM jobs WHERE kind='grade_coding' AND application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM jobs WHERE kind='evaluate_application' AND application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id) == [("SUBMITTED",)]
    assert await q("SELECT count(*) FROM checkpoints WHERE thread_id IN (:s, :i)", s=f"submit:{app_id}", i=app_id) == [(0,)]


async def test_coding_time_over_and_sandbox_down(client, make_client, voice_ai, fake_runner, sandbox_up):
    app_id, cand = await to_coding(client, make_client, voice_ai)
    sandbox_up["ready"] = False
    assert (await cand.get(url(app_id, "/coding"))).json()["run_available"] is False
    r = await cand.post(url(app_id, "/coding/runs"), json={"code": "print(1)"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "CODE_EXECUTION_UNAVAILABLE"
    sandbox_up["ready"] = True
    await q("UPDATE coding_attempts SET deadline_at = now() - interval '5 seconds' WHERE application_id=:a RETURNING 1",
            a=app_id)
    r = await cand.post(url(app_id, "/coding/runs"), json={"code": "print(1)"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "TIME_OVER"
    r = await cand.post(url(app_id, "/coding/submit"), json={"code": "print(1)", "revision": 1, "confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "TIME_OVER"  # past deadline + grace


async def test_sweeper_returns_idle_preparing_to_ready(client, make_client, voice_ai):
    _, app_id, cand = await setup_app(client, make_client)
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200
    await tasks.attempt_sweeper()
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id) == [("PREPARING",)]
    await q("UPDATE interviews SET last_heartbeat_at = now() - interval '31 minutes' WHERE application_id=:a RETURNING 1",
            a=app_id)
    await tasks.attempt_sweeper()
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id) == [("READY",)]
