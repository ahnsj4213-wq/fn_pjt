"""The composition stage of the interview graph (app/interview/graph.py COMPOSE entry, app/questions/steps.py; ADR-0016 rev.
2026-11-11): shape, resume after a failed attempt, idempotent write, and the cleanup rules of its checkpoints."""
import uuid

import pytest

from app.core.db import session_scope
from app.interview import graph as G
from app.interview.checkpoint import get_checkpointer, purge_finished
from app.questions import compose as C
from tests.test_handlers import fake_ai, q, setup_app  # noqa: F401 - the fixture
from tests.test_interview import url


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
    thread = G.thread_id(app_id)
    assert thread == str(app_id)  # one thread per application: the composition and the interview share it
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
    cfg = {"configurable": {"thread_id": str(app_id)}}
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
    thread = G.thread_id(app_id)
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
    # an orphan thread of an application that does not exist any more, and a thread of the earlier design (compose:<app>:<v>)
    ghost, old = str(uuid.uuid4()), f"compose:{app_id}:{settings_id}"
    for t in (ghost, old):
        await graph.aupdate_state({"configurable": {"thread_id": t}}, {"application_id": "x", "settings_id": "y", "picked": []},
                                  as_node="plan")
        assert await _rows(t) > 0
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(ghost) == 0 and await _rows(old) == 0 and await _rows(thread) > 0
    await q("UPDATE applications SET attempt_status='EXPIRED' WHERE id=:a RETURNING 1", a=str(app_id))
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(thread) == 0  # the attempt is over
