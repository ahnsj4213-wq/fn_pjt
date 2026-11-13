"""The interview progress graph (app/interview/graph.py, ADR-0016): its shape, the interrupt/resume cycle, the
checkpoints in PostgreSQL, and the rule that no answer text is ever written into them."""
import uuid

import pytest
from langgraph.graph import END

from app.interview import graph as G
from app.interview.checkpoint import get_checkpointer
from tests.test_handlers import q
from tests.test_interview import started, url

SECRET = "비밀답변절대체크포인트에남지않아야함"


def test_graph_nodes_and_edges_match_the_design():
    g = G.build().compile().get_graph()
    nodes = {n for n in g.nodes} - {"__start__", "__end__"}
    assert nodes == {"start", "plan", "project", "motivation", "job_skill", "write",  # composition
                     "compose_next", "present", "ask", "wait_answer", "transcribe", "decide_followup", "finish",  # interview
                     "coding_submit"}  # the end of the coding test
    edges = {(e.source, e.target) for e in g.edges}
    assert ("__start__", "start") in edges
    assert {("start", "plan"), ("start", "compose_next"), ("start", "coding_submit"), ("start", "__end__")} <= edges
    assert ("write", "__end__") in edges and ("ask", "wait_answer") in edges and ("transcribe", "decide_followup") in edges
    assert ("decide_followup", "compose_next") in edges
    assert {("compose_next", "present"), ("compose_next", "finish"), ("compose_next", "__end__")} <= edges
    assert {("present", "ask"), ("present", "finish"), ("present", "__end__")} <= edges
    assert {("wait_answer", "transcribe"), ("wait_answer", "present")} <= edges
    assert ("finish", "__end__") in edges and ("coding_submit", "__end__") in edges


def test_forks_are_decided_by_code_from_plain_state():
    assert G.after_start({"stage": "COMPOSE"}) == "plan" and G.after_start({"stage": "INTERVIEW"}) == "compose_next"
    assert G.after_start({"stage": "SUBMIT"}) == "coding_submit" and G.after_start({"stage": END}) == END
    assert G.after_present({"result": "ENDED"}) == END and G.after_present({"result": "TEXT_MODE_REQUIRED"}) == END
    assert G.after_present({"turn": {"turn_id": "x"}}) == "ask" and G.after_present({"route": "DONE"}) == "finish"
    assert G.after_wait({"signal": "ENDED"}) == "transcribe" and G.after_wait({"signal": "ABANDONED"}) == "present"
    assert G.after_compose_next({"mode": "TEXT", "route": "PRESENT"}) == "present"  # a (re)start always shows the next question
    assert G.after_compose_next({"mode": "VOICE", "route": "NEXT"}) == "present"
    assert G.after_compose_next({"mode": "VOICE", "route": "FOLLOWUP"}) == "present"
    assert G.after_compose_next({"mode": "VOICE", "route": "DONE"}) == "finish"
    assert G.after_compose_next({"mode": "TEXT", "route": "DONE"}) == END  # text mode hands control back to the client


class RecordingIO(G.TextIO):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.asked: list[str] = []

    async def ask(self, view) -> None:
        self.asked.append(f"{view.main_no}{'-1' if view.is_followup else ''}")


async def _checkpoint_has(app_id: str, needle: str) -> bool:
    """Is `needle` anywhere in the thread's stored state? Blobs are msgpack (UTF-8 strings inside), so compare bytes."""
    for table in ("checkpoint_blobs", "checkpoint_writes"):
        col = "blob"
        n = await q(f"SELECT count(*) FROM {table} WHERE thread_id=:t AND {col} IS NOT NULL AND position(convert_to(:s, 'UTF8') in {col}) > 0",
                    t=app_id, s=needle)
        if n[0][0]:
            return True
    return bool((await q("SELECT count(*) FROM checkpoints WHERE thread_id=:t AND position(:s in metadata::text) > 0", t=app_id, s=needle))[0][0])


async def _checkpoint_rows(app_id: str) -> int:
    return (await q("SELECT count(*) FROM checkpoints WHERE thread_id=:t", t=app_id))[0][0]


async def test_wait_resume_cycle_with_checkpoints_and_a_dropped_connection(client, make_client, voice_ai):
    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    await q("UPDATE interviews SET text_mode_since=now() WHERE application_id=:a RETURNING 1", a=app_id)  # text transport, no audio
    voice_ai.followup = "ASK"
    io = RecordingIO()

    # 1. begin: present -> ask -> stops at wait_answer; the checkpoint says so
    out = await G.begin(aid, "TEXT", io)
    assert out.waiting and out.view.main_no == 1 and out.view.presentation_no == 1 and io.asked == ["1"]
    graph = await G.get_graph()
    snap = await graph.aget_state(G._cfg(aid, io))
    assert snap.next == ("wait_answer",) and snap.values["turn"]["turn_id"] == str(out.view.turn_id)

    # 2. the connection drops and a new session starts: the same question is shown again as a new presentation
    again = await G.begin(aid, "TEXT", io)
    assert again.waiting and again.view.main_no == 1 and again.view.presentation_no == 2 and again.view.turn_id != out.view.turn_id
    states = {s: n for s, n in await q("SELECT state, count(*) FROM interview_turns WHERE application_id=:a GROUP BY state", a=app_id)}
    assert states == {"ABANDONED": 1, "ANSWERING": 1}

    # 3. the answer window ended: transcribe -> decide_followup (the model asks) -> text mode hands control back
    io = RecordingIO(f"결제 API의 지연을 캐시로 줄였습니다. {SECRET}")
    out = await G.resume(aid, "BUTTON", io)
    assert not out.waiting and out.result is None
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT decision FROM followup_decisions WHERE application_id=:a", a=app_id) == [("ASK",)]

    # 4. nothing the candidate said is in the checkpoints (state holds ids and flags only)
    assert await _checkpoint_rows(app_id) > 0 and await _checkpoint_has(app_id, "wait_answer") in (True, False)
    assert not await _checkpoint_has(app_id, SECRET) and not await _checkpoint_has(app_id, "캐시")
    assert await _checkpoint_has(app_id, str(again.view.turn_id))  # the control works: ids are there

    # 5. the next presentation is the follow-up (route FOLLOWUP), then the following main question
    out = await G.begin(aid, "TEXT", io)
    assert out.waiting and out.view.is_followup and out.view.main_no == 1


async def test_text_answer_without_a_checkpoint_is_put_in_place_and_finishing_cleans_up(client, make_client, voice_ai):
    app_id, cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    st = (await cand.post(url(app_id, "/interview/next"))).json()
    turn = st["turn"]
    assert await _checkpoint_rows(app_id) > 0
    # An interview that began before the graph existed has a live turn but no checkpoint: it is resynced, not rejected.
    await (await get_checkpointer()).adelete_thread(app_id)
    assert await _checkpoint_rows(app_id) == 0
    r = await cand.post(url(app_id, f"/interview/turns/{turn['turn_id']}/text-answer"), json={"text": "경험이 있습니다."})
    assert r.status_code == 202 and r.json()["state"] == "CLOSED"
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(1,)]
    # A second submit of the same turn is a no-op, not a second run.
    r = await cand.post(url(app_id, f"/interview/turns/{turn['turn_id']}/text-answer"), json={"text": "다시"})
    assert r.status_code == 409 or r.json()["state"] == "CLOSED"
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(1,)]

    # Answer everything: the run ends with the coding test or the submission, and its checkpoints are deleted.
    for _ in range(20):
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        if st["attempt_status"] != "INTERVIEWING":
            break
        t = st["turn"]
        assert (await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"), json={"text": "답변입니다."})).status_code == 202
    assert st["attempt_status"] in ("SUBMITTED", "CODING")
    assert await _checkpoint_rows(app_id) == 0
    assert aid


async def test_an_attempt_that_is_not_interviewing_ends_the_run_instead_of_asking(client, make_client, voice_ai):
    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    # any attempt that is no longer INTERVIEWING (here: the connection dropped and has not been resumed) ends the run
    await q("UPDATE applications SET attempt_status='INTERRUPTED', interrupted_from='INTERVIEWING' WHERE id=:a RETURNING 1", a=app_id)
    out = await G.begin(aid, "VOICE", RecordingIO())
    assert not out.waiting and out.result == "ENDED"
    assert await q("SELECT count(*) FROM interview_turns WHERE application_id=:a", a=app_id) == [(0,)]


@pytest.mark.parametrize("secret_key", ["transcript", "text", "answer"])
def test_state_schema_has_no_place_for_an_answer(secret_key):
    assert secret_key not in G.State.__annotations__


async def test_the_sweeper_purges_checkpoints_of_attempts_that_are_over(client, make_client, voice_ai):
    from app.jobs import tasks

    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    out = await G.begin(aid, "VOICE", RecordingIO())
    assert out.waiting and await _checkpoint_rows(app_id) > 0
    await tasks.attempt_sweeper()
    assert await _checkpoint_rows(app_id) > 0  # still interviewing: kept
    await q("UPDATE applications SET attempt_status='INTERRUPTED', interrupted_from='INTERVIEWING' WHERE id=:a RETURNING 1", a=app_id)
    await tasks.attempt_sweeper()  # ADR-0024: an interrupted attempt is over (nothing resumes): ended and purged
    assert await q("SELECT attempt_status FROM applications WHERE id=:a", a=app_id) == [("EXPIRED",)]
    assert await _checkpoint_rows(app_id) == 0


def test_langchain_tracing_is_forced_off_so_nothing_is_sent_out():
    import os

    import app.interview.checkpoint  # noqa: F401 - importing it sets the switches

    assert os.environ["LANGSMITH_TRACING"] == "false" and os.environ["LANGCHAIN_TRACING_V2"] == "false"


async def test_live_coding_turn_shows_its_problem_and_nothing_hidden(client, make_client, voice_ai, older_live_question):
    """The question says "화면의 문제": the screen must have it. Statement and public examples (with their explanations) come
    from this application's own copy; hidden cases never; other turns and other candidates get nothing."""
    from tests.helpers import candidate

    app_id, cand = await started(client, make_client)
    assert (await cand.post(url(app_id, "/interview/text-mode"))).status_code == 200
    seen_plain = None
    for _ in range(8):
        st = (await cand.post(url(app_id, "/interview/next"))).json()
        t = st["turn"]
        if t["answer_mode"] == "CODE":
            break
        seen_plain = t
        assert (await cand.post(url(app_id, f"/interview/turns/{t['turn_id']}/text-answer"), json={"text": "답변입니다."})).status_code == 202
    else:
        pytest.fail("no live-coding question in the default plan")
    r = await cand.get(url(app_id, f"/interview/turns/{t['turn_id']}/problem"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "### 입력" in body["statement_md"] and "### 출력" in body["statement_md"]
    assert len(body["examples"]) >= 1 and all(e["note"] and e["input"] and e["output"] for e in body["examples"])
    hidden = [i for (i,) in await q("SELECT input FROM coding_testcases WHERE visibility='HIDDEN' AND problem_id IN "
                                    "(SELECT id FROM coding_problems WHERE variant_for=:a)", a=app_id)]
    assert hidden and not any(h in r.text for h in hidden)  # the application's own hidden cases exist and stay on the server
    assert seen_plain is not None
    plain = await cand.get(url(app_id, f"/interview/turns/{seen_plain['turn_id']}/problem"))
    assert plain.status_code == 409 and plain.json()["error"]["code"] == "NOT_CODING_TURN"
    other = make_client()
    await candidate(other)
    assert (await other.get(url(app_id, f"/interview/turns/{t['turn_id']}/problem"))).status_code == 404
