"""Graph 1 end to end (app/interview/graph.py, ADR-0016 rev. 2026-11-11): one thread per application from the composition
through the interview (follow-up, next question) to the coding test's entry and final submission; a restart continues where it
stopped; no database transaction is open while a model runs; nothing the candidate wrote or said reaches the checkpoints."""
import uuid

import pytest

from app.core.db import open_scopes
from app.interview import graph as G
from tests.test_coding import sandbox_up, to_coding  # noqa: F401 - the fixture
from tests.test_handlers import SUM_SOLUTION, q
from tests.test_interview_graph import _checkpoint_has, _checkpoint_rows
from tests.test_interview import url

SECRET = "체크포인트에남으면안되는코드주석"


def _ask(inp, msgs):
    quote = inp["answer"].strip()[:12]
    return {"decision": "ASK", "reason": "VAGUE", "anchor": {"source": "answer", "quote": quote},
            "question": f"말씀하신 '{quote}' 부분을 구체적인 사례로 설명해 주시겠어요?"}


@pytest.fixture
def scopes(voice_ai, monkeypatch):
    """Every model or embedding call records how many database transactions are open at that moment."""
    seen: list[tuple[str, int]] = []
    real_llm, real_embed = voice_ai.llm, voice_ai.embed

    async def llm(task, inp, **kw):
        seen.append((task, open_scopes()))
        return await real_llm(task, inp, **kw)

    async def embed(texts):
        seen.append(("embed", open_scopes()))
        return await real_embed(texts)
    monkeypatch.setattr(voice_ai, "llm", llm)
    monkeypatch.setattr(voice_ai, "embed", embed)
    return seen


async def test_compose_interview_followup_next_coding_and_submission_in_one_graph(client, make_client, voice_ai, fake_runner,
                                                                                  sandbox_up, scopes):
    voice_ai.responses["followup_decide"] = _ask
    app_id, cand = await to_coding(client, make_client, voice_ai)  # compose (COMPOSE entry) -> interview (text mode) -> CODING
    aid = uuid.UUID(app_id)
    tasks = {t for t, _ in scopes}
    assert {"question_generate", "resume_question", "followup_decide"} <= tasks
    assert all(n == 0 for _, n in scopes), [x for x in scopes if x[1]]  # no model call inside a transaction, in any stage

    # the follow-up was asked right after the first answer, then the next main question; every main question once
    rows = await q("SELECT t.presented_at, q.role FROM interview_turns t JOIN interview_questions q ON q.id=t.question_id "
                   "WHERE t.application_id=:a AND t.state='CLOSED' ORDER BY t.presented_at", a=app_id)
    roles = [r for _s, r in rows]
    assert roles[:3] == ["MAIN", "FOLLOWUP", "MAIN"]
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(len(rows),)]
    assert await q("SELECT count(DISTINCT question_id) = count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(True,)]
    assert await _checkpoint_rows(app_id) == 0  # the interview run ended at CODING

    # the coding test ends through the graph's SUBMIT entry: snapshot, grading job and the evaluation that waits for it
    r = await cand.post(url(app_id, "/coding/submit"), json={"code": SUM_SOLUTION + f"\n# {SECRET}\n", "revision": 3, "confirmed": True})
    assert r.status_code == 200 and r.json() == {"attempt_status": "SUBMITTED"}, r.text
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id) == [("SUBMITTED",)]
    assert await q("SELECT kind FROM coding_submissions WHERE application_id=:a", a=app_id) == [("MANUAL",)]
    assert await q("SELECT count(*) FROM jobs WHERE application_id=:a AND kind='grade_coding'", a=app_id) == [(1,)]
    assert (await q("SELECT count(*) FROM jobs WHERE application_id=:a AND kind='evaluate_application'", a=app_id))[0][0] >= 1
    assert await _checkpoint_rows(app_id) == 0 and not await _checkpoint_has(app_id, SECRET)
    again = await cand.post(url(app_id, "/coding/submit"), json={"code": "print(2)", "revision": 4, "confirmed": True})
    assert again.status_code == 409  # one submission per application
    assert aid


async def test_the_submission_works_without_the_graph(client, make_client, voice_ai, fake_runner, sandbox_up, monkeypatch):
    """An attempt whose checkpoint store is unavailable (or an older attempt) is submitted by the direct path, once."""
    app_id, cand = await to_coding(client, make_client, voice_ai)

    async def down(*a, **k):
        raise ConnectionError("checkpoint store down")
    monkeypatch.setattr(G, "submit_coding", down)
    r = await cand.post(url(app_id, "/coding/submit"), json={"code": SUM_SOLUTION, "revision": 3, "confirmed": True})
    assert r.status_code == 200 and r.json() == {"attempt_status": "SUBMITTED"}
    assert await q("SELECT count(*) FROM coding_submissions WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM jobs WHERE application_id=:a AND kind='grade_coding'", a=app_id) == [(1,)]


async def test_a_restart_continues_at_the_same_question_and_writes_nothing_twice(client, make_client, voice_ai):
    from tests.test_interview import started
    from tests.test_interview_graph import RecordingIO

    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    await q("UPDATE interviews SET text_mode_since=now() WHERE application_id=:a RETURNING 1", a=app_id)
    voice_ai.followup = "SKIP"
    out = await G.begin(aid, "TEXT", RecordingIO())
    assert out.waiting and out.view.main_no == 1
    io = RecordingIO("결제 API의 지연을 캐시로 줄였습니다.")
    await G.resume(aid, "BUTTON", io)  # answer 1 confirmed, the follow-up decided (skip)
    # the process restarts: the checkpoint is gone; the database says what is done
    from app.interview.checkpoint import get_checkpointer
    await (await get_checkpointer()).adelete_thread(app_id)
    out = await G.begin(aid, "TEXT", RecordingIO())
    assert out.waiting and out.view.main_no == 2 and not out.view.is_followup  # continues with the next question, not the first
    again = await G.begin(aid, "TEXT", RecordingIO())  # a second reconnect: the same question shown again, not a third
    assert again.waiting and again.view.main_no == 2
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM followup_decisions WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM interview_questions WHERE application_id=:a AND role='MAIN'", a=app_id) == [(5,)]


async def test_a_submit_entry_outside_the_coding_test_does_nothing(client, make_client, voice_ai):
    from tests.test_interview import started

    app_id, _cand = await started(client, make_client)
    from app.core.db import session_scope
    async with session_scope() as db:
        assert await G.submit_coding(db, uuid.UUID(app_id), "print(1)", "MANUAL") == "NOT_CODING"
    assert await q("SELECT count(*) FROM coding_submissions WHERE application_id=:a", a=app_id) == [(0,)]
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id) == [("INTERVIEWING",)]
