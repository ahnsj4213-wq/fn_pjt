"""The composition stage of the interview graph (app/interview/graph.py COMPOSE entry, app/questions/steps.py; ADR-0016 rev.
2026-11-11): shape, resume after a failed attempt, idempotent write, and the cleanup rules of its checkpoints."""
import asyncio
import contextlib
import uuid

import pytest

from app.core.db import session_scope
from app.interview import graph as G
from app.interview.checkpoint import get_checkpointer, purge_finished
from app.questions import compose as C
from tests.test_handlers import FIVE_QUESTIONS, drain, fake_ai, q, setup_app  # noqa: F401 - the fixture
from tests.test_interview import mark_prechecked, prepared, started, url
from tests.test_interview_graph import RecordingIO


def test_the_composition_is_a_chain_inside_the_one_graph():
    g = G.build().compile().get_graph()
    nodes = {n for n in g.nodes} - {"__start__", "__end__"}
    assert {"start", "plan", "project", "motivation", "job_skill", "write"} <= nodes  # code-only steps run inside plan and write
    chain = ["__start__", "start", "plan", "project", "motivation", "job_skill", "write", "__end__"]
    edges = {(e.source, e.target) for e in g.edges}
    assert all((a, b) in edges for a, b in zip(chain, chain[1:]))


async def _prepared(client, make_client):
    _, app_id, cand = await setup_app(client, make_client)
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200  # PREPARING; the queued job is not drained
    (sid,) = (await q("SELECT p.current_settings_id FROM applications a JOIN job_postings p ON p.id=a.posting_id WHERE a.id=:a", a=app_id))[0]
    return uuid.UUID(app_id), sid


async def _rows(thread: str) -> int:
    return (await q("SELECT count(*) FROM checkpoints WHERE thread_id=:t", t=thread))[0][0]


async def test_a_failed_attempt_resumes_at_the_node_that_stopped_and_the_write_is_idempotent(client, make_client, fake_ai, monkeypatch):
    app_id, settings_id = await _prepared(client, make_client)
    calls = {"resume": 0, "motivation": 0}
    real_resume, real_gen = C._fill_resume, C._fill_generated_kind

    async def counted_resume(db, c, slots):
        calls["resume"] += 1
        return await real_resume(db, c, slots)

    async def flaky_generated(db, c, kind, slots, embed_model):
        if kind == "COMPANY" and calls["motivation"] == 0:
            calls["motivation"] += 1
            raise RuntimeError("model down")
        return await real_gen(db, c, kind, slots, embed_model)

    monkeypatch.setattr(C, "_fill_resume", counted_resume)
    monkeypatch.setattr(C, "_fill_generated_kind", flaky_generated)

    with pytest.raises(RuntimeError):
        await G.compose(app_id, settings_id)
    thread = G.thread_id(app_id, G.COMPOSE, settings_id)
    # one graph, one thread per entry (PR #2 review): the composition never shares the interview's thread (the application id)
    assert thread == f"compose:{app_id}:{settings_id}" and G.thread_id(app_id) == str(app_id)
    assert await _rows(str(app_id)) == 0
    graph = await G.get_graph()
    snap = await graph.aget_state({"configurable": {"thread_id": thread}})
    assert snap.next == ("motivation",) and await _rows(thread) > 0 and calls["resume"] == 1
    assert (await q("SELECT count(*) FROM question_sets WHERE application_id=:a", a=str(app_id)))[0][0] == 0  # nothing written yet

    qset_id = await G.compose(app_id, settings_id)  # the retry: continues at motivation
    assert calls["resume"] == 1, "the nodes that had finished were not run again"
    assert await _rows(thread) == 0  # the run is over: its checkpoints are deleted
    texts = await q("SELECT seq, text, slot_type FROM interview_questions WHERE question_set_id=:s ORDER BY seq", s=str(qset_id))
    assert [t[0] for t in texts] == [1, 2, 3, 4, 5] and len({t[1] for t in texts}) == 5
    assert await q("SELECT count(*) FROM jobs WHERE kind='tts_pregen' AND application_id=:a", a=str(app_id)) == [(1,)]

    again = await G.compose(app_id, settings_id)  # a second run for the same version: `start` finds the set, nothing new
    assert again == qset_id
    assert (await q("SELECT count(*) FROM question_sets WHERE application_id=:a AND status='READY'", a=str(app_id)))[0][0] == 1


async def test_a_run_left_for_another_settings_version_is_not_continued(client, make_client, fake_ai, monkeypatch):
    app_id, settings_id = await _prepared(client, make_client)
    real_gen = C._fill_generated_kind
    boom = {"on": True}

    async def flaky(db, c, kind, slots, embed_model):
        if boom["on"]:
            raise RuntimeError("model down")
        return await real_gen(db, c, kind, slots, embed_model)
    monkeypatch.setattr(C, "_fill_generated_kind", flaky)
    other = uuid.uuid4()
    graph = await G.get_graph()
    cfg = {"configurable": {"thread_id": G.thread_id(app_id, G.COMPOSE, settings_id)}}
    with pytest.raises(RuntimeError):
        await G.compose(app_id, settings_id)
    await graph.aupdate_state(cfg, {"settings_id": str(other)}, as_node="project")  # as if the stopped run belonged to an older version
    boom["on"] = False
    qset_id = await G.compose(app_id, settings_id)  # starts over for this version instead of carrying the other one on
    assert await q("SELECT settings_id FROM question_sets WHERE id=:s", s=str(qset_id)) == [(settings_id,)]


async def test_checkpoint_cleanup_follows_the_application_state(client, make_client, fake_ai, monkeypatch):
    app_id, settings_id = await _prepared(client, make_client)

    async def boom(db, c, kind, slots, embed_model):
        raise RuntimeError("model down")

    monkeypatch.setattr(C, "_fill_generated_kind", boom)
    with pytest.raises(RuntimeError):
        await G.compose(app_id, settings_id)
    thread = G.thread_id(app_id, G.COMPOSE, settings_id)
    assert await _rows(thread) > 0
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(thread) > 0  # still PREPARING: a retry needs it
    await q("UPDATE applications SET attempt_status='READY' WHERE id=:a RETURNING 1", a=str(app_id))
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(thread) > 0  # READY can still be composed again
    await (await get_checkpointer()).adelete_thread("unrelated")  # no error for an unknown thread
    graph = await G.get_graph()
    # orphan threads of an application that does not exist any more (interview, composition and submission entries)
    gone = uuid.uuid4()
    ghost, old = str(gone), f"compose:{gone}:{settings_id}"
    for t in (ghost, old, f"submit:{gone}"):
        await graph.aupdate_state({"configurable": {"thread_id": t}}, {"application_id": "x", "settings_id": "y", "picked": []},
                                  as_node="plan")
        assert await _rows(t) > 0
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(ghost) == 0 and await _rows(old) == 0 and await _rows(f"submit:{gone}") == 0 and await _rows(thread) > 0
    await q("UPDATE applications SET attempt_status='EXPIRED' WHERE id=:a RETURNING 1", a=str(app_id))
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(thread) == 0  # the attempt is over


# ── one graph, one checkpoint thread per entry (PR #2 review) ──────────────────
async def _interview_snap(aid: uuid.UUID) -> tuple:
    snap = await (await G.get_graph()).aget_state(G._cfg(aid))
    return snap.next, (snap.values or {}).get("turn"), snap.config["configurable"].get("checkpoint_id")


async def _waiting(client, make_client):
    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    await q("UPDATE interviews SET text_mode_since=now() WHERE application_id=:a RETURNING 1", a=app_id)  # text transport
    out = await G.begin(aid, "TEXT", RecordingIO())
    assert out.waiting
    (sid,), = await q("SELECT eval_settings_id FROM applications WHERE id=:a", a=app_id)
    return app_id, aid, sid, out


async def test_a_late_compose_retry_never_changes_a_waiting_interview_thread(client, make_client, voice_ai):
    app_id, aid, sid, out = await _waiting(client, make_client)
    before = await _interview_snap(aid)
    assert before[0] == ("wait_answer",) and before[1]["turn_id"] == str(out.view.turn_id)
    (in_use,), = await q("SELECT question_set_id FROM interviews WHERE application_id=:a", a=app_id)
    assert await G.compose(aid, sid) is None  # a late retry: the interview began, nothing is composed or written
    old = await q("SELECT s.id FROM posting_eval_settings s JOIN applications a ON a.posting_id=s.posting_id "
                  "WHERE a.id=:a AND s.id<>:s", a=app_id, s=sid)
    assert old  # the posting's earlier settings version
    with contextlib.suppress(Exception):  # whatever the late run of another version does, it is on its own thread
        await G.compose(aid, old[0][0])
    assert await _interview_snap(aid) == before  # same next, same turn, same checkpoint
    assert await q("SELECT count(*) FROM checkpoints WHERE thread_id LIKE :t", t=f"compose:{app_id}:{sid}") == [(0,)]
    # the interview goes on from its checkpoint
    res = await G.resume(aid, "BUTTON", RecordingIO("주문 API의 지연을 인덱스로 줄였습니다."))
    assert res.result is None
    assert await q("SELECT count(*) FROM interview_answers WHERE application_id=:a", a=app_id) == [(1,)]


async def test_a_composition_that_ends_after_the_interview_began_writes_nothing(client, make_client, voice_ai, monkeypatch):
    """The race the start check cannot see: a composition of an older settings version passed `start` before the interview
    began and reaches `write` while the interview runs. The set in use stays READY, no unused set or voice job appears."""
    app_id, aid, sid, _out = await _waiting(client, make_client)
    (in_use,), = await q("SELECT question_set_id FROM interviews WHERE application_id=:a", a=app_id)
    old = await q("SELECT s.id FROM posting_eval_settings s JOIN applications a ON a.posting_id=s.posting_id "
                  "WHERE a.id=:a AND s.id<>:s", a=app_id, s=sid)
    assert old
    sets_before = await q("SELECT id, status FROM question_sets WHERE application_id=:a ORDER BY created_at", a=app_id)
    tts_before = await q("SELECT count(*) FROM jobs WHERE kind='tts_pregen' AND application_id=:a", a=app_id)

    async def start_as_before_the_interview(state):  # what `start` saw when the attempt was still READY
        return {"stage": G.COMPOSE}
    monkeypatch.setattr(G, "start", start_as_before_the_interview)
    G.reset_graph()
    try:
        assert await G.compose(aid, old[0][0]) is None
    finally:
        monkeypatch.undo()
        G.reset_graph()
    assert await q("SELECT id, status FROM question_sets WHERE application_id=:a ORDER BY created_at", a=app_id) == sets_before
    assert (in_use, "READY") in sets_before
    assert await q("SELECT count(*) FROM jobs WHERE kind='tts_pregen' AND application_id=:a", a=app_id) == tts_before


async def test_a_compose_ending_while_the_interview_begins_keeps_the_interview_thread(client, make_client, voice_ai, monkeypatch):
    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    await q("UPDATE interviews SET text_mode_since=now() WHERE application_id=:a RETURNING 1", a=app_id)
    (sid,), = await q("SELECT eval_settings_id FROM applications WHERE id=:a", a=app_id)
    begun, real_forget = asyncio.Event(), G._forget
    forgotten = []

    async def late_forget(tid):  # the composition deletes its thread only after the interview has checkpointed
        await asyncio.wait_for(begun.wait(), 10)
        forgotten.append(tid)
        await real_forget(tid)
    monkeypatch.setattr(G, "_forget", late_forget)

    async def interview():
        try:
            return await G.begin(aid, "TEXT", RecordingIO())
        finally:
            begun.set()
    _qset, out = await asyncio.gather(G.compose(aid, sid), interview())
    assert forgotten == [f"compose:{app_id}:{sid}"]
    nxt, turn, _cp = await _interview_snap(aid)
    assert out.waiting and nxt == ("wait_answer",) and turn["turn_id"] == str(out.view.turn_id)
    assert await _rows(str(aid)) > 0


async def test_compose_runs_of_two_settings_versions_keep_their_own_threads(client, make_client, fake_ai, monkeypatch):
    app_id, sid1 = await _prepared(client, make_client)
    boom = {"on": True}
    real_gen = C._fill_generated_kind

    async def flaky(db, c, kind, slots, embed_model):
        if boom["on"] and kind == "COMPANY":
            raise RuntimeError("model down")
        return await real_gen(db, c, kind, slots, embed_model)
    monkeypatch.setattr(C, "_fill_generated_kind", flaky)
    with pytest.raises(RuntimeError):
        await G.compose(app_id, sid1)
    # the recruiter saves the settings again before the interview: a new version, its own composition
    (pid,), = await q("SELECT posting_id FROM applications WHERE id=:a", a=str(app_id))
    surl = f"/api/recruiter/postings/{pid}/evaluation-settings"
    cur = (await client.get(surl)).json()
    iv = {"question_plan": FIVE_QUESTIONS, **{k: cur["interview"][k] for k in ("think_sec", "answer_sec", "followup_max")}}
    assert (await client.put(surl, json={"weights": cur["weights"], "interview": iv})).status_code == 200
    (sid2,), = await q("SELECT current_settings_id FROM job_postings WHERE id=:p", p=pid)
    assert sid2 != sid1
    with pytest.raises(RuntimeError):
        await G.compose(app_id, sid2)
    graph = await G.get_graph()
    t1, t2 = G.thread_id(app_id, G.COMPOSE, sid1), G.thread_id(app_id, G.COMPOSE, sid2)
    s1 = await graph.aget_state({"configurable": {"thread_id": t1}})
    s2 = await graph.aget_state({"configurable": {"thread_id": t2}})
    assert (s1.next, s1.values["settings_id"]) == (("motivation",), str(sid1))
    assert (s2.next, s2.values["settings_id"]) == (("motivation",), str(sid2))
    boom["on"] = False
    qset2 = await G.compose(app_id, sid2)
    assert await _rows(t2) == 0 and await _rows(t1) > 0  # only its own thread is deleted
    s1_after = await graph.aget_state({"configurable": {"thread_id": t1}})
    assert s1_after.config["configurable"]["checkpoint_id"] == s1.config["configurable"]["checkpoint_id"]
    assert await q("SELECT settings_id FROM question_sets WHERE id=:s", s=str(qset2)) == [(sid2,)]
    assert await _rows(str(app_id)) == 0  # the interview thread was never written by either


async def test_purge_keeps_running_threads_of_each_entry_and_removes_finished_ones(client, make_client, voice_ai):
    app_id, aid, sid, _out = await _waiting(client, make_client)  # INTERVIEWING, the interview thread waits
    graph = await G.get_graph()
    compose_t, submit_t = G.thread_id(aid, G.COMPOSE, sid), G.thread_id(aid, G.SUBMIT)

    async def put(t):
        await graph.aupdate_state({"configurable": {"thread_id": t}}, {"application_id": app_id, "picked": []}, as_node="plan")
        assert await _rows(t) > 0

    async def purge():
        async with session_scope() as db:
            await purge_finished(db)
    # a second application, still PREPARING: its composition thread is in progress
    prep_id, prep_sid = await _prepared(client, make_client)
    prep_compose, prep_submit = G.thread_id(prep_id, G.COMPOSE, prep_sid), G.thread_id(prep_id, G.SUBMIT)
    for t in (compose_t, submit_t, prep_compose, prep_submit):
        await put(t)
    await purge()
    assert await _rows(app_id) > 0  # interviewing: the interview thread stays
    assert await _rows(compose_t) == 0 and await _rows(submit_t) == 0  # composition over, not coding
    assert await _rows(prep_compose) > 0 and await _rows(prep_submit) == 0  # PREPARING: composition kept, no submission
    # the coding test: a submission in progress keeps its thread
    await q("UPDATE applications SET attempt_status='CODING' WHERE id=:a RETURNING 1", a=app_id)
    await put(submit_t)
    await purge()
    assert await _rows(submit_t) > 0 and await _rows(app_id) > 0
    # over: everything of it goes
    await q("UPDATE applications SET attempt_status='SUBMITTED', eval_status='PENDING', submitted_at=now() WHERE id=:a RETURNING 1", a=app_id)
    await purge()
    assert await _rows(submit_t) == 0 and await _rows(app_id) == 0 and await _rows(prep_compose) > 0


# ── a COMPOSING set (not written by the current code) is not a composed set ─────
async def test_compose_does_not_take_a_composing_set_as_done(client, make_client, fake_ai):
    from app.jobs.queue import enqueue

    app_id, sid = await _prepared(client, make_client)
    first = await G.compose(app_id, sid)
    await q("UPDATE question_sets SET status='COMPOSING' WHERE id=:s RETURNING 1", s=str(first))
    again = await G.compose(app_id, sid)  # the graph: a new READY set, the COMPOSING one superseded (no ux_qset_live clash)
    assert again != first
    rows = dict(await q("SELECT id, status FROM question_sets WHERE application_id=:a", a=str(app_id)))
    assert rows == {first: "SUPERSEDED", again: "READY"}
    # the job path as well: a COMPOSING set of the same version is not "already composed"
    await q("UPDATE question_sets SET status='COMPOSING' WHERE id=:s RETURNING 1", s=str(again))
    async with session_scope() as db:
        await enqueue(db, "compose_questions", {"application_id": str(app_id), "settings_id": str(sid)}, application_id=app_id,
                      dedupe_key=f"compose:{app_id}:{sid}")
    await drain()
    assert await q("SELECT state FROM jobs WHERE kind='compose_questions'") == [("SUCCEEDED",)]
    statuses = [s for (s,) in await q("SELECT status FROM question_sets WHERE application_id=:a", a=str(app_id))]
    assert sorted(statuses) == ["READY", "SUPERSEDED", "SUPERSEDED"]


async def test_starting_with_a_composing_set_supersedes_it_and_composes_again(client, make_client, voice_ai):
    app_id, cand = await prepared(client, make_client)
    await mark_prechecked(app_id)
    (first,), = await q("SELECT id FROM question_sets WHERE application_id=:a AND status='READY'", a=app_id)
    await q("UPDATE question_sets SET status='COMPOSING' WHERE id=:s RETURNING 1", s=str(first))
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUESTIONS_NOT_READY", r.text
    assert await q("SELECT status FROM question_sets WHERE id=:s", s=str(first)) == [("SUPERSEDED",)]
    assert await q("SELECT count(*) FROM jobs WHERE kind='compose_questions' AND state='QUEUED' AND application_id=:a",
                   a=app_id) == [(1,)]
    await drain()
    r = await cand.post(url(app_id, "/interview/start"))
    assert r.status_code == 200 and r.json()["attempt_status"] == "INTERVIEWING", r.text
    (used,), = await q("SELECT question_set_id FROM interviews WHERE application_id=:a", a=app_id)
    assert used != first and await q("SELECT status FROM question_sets WHERE id=:s", s=str(used)) == [("READY",)]
