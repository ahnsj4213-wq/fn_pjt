"""The question composition graph (app/questions/graph.py, ADR-0016): shape, resume after a failed attempt, idempotent write,
and the cleanup rules of its checkpoints."""
import uuid

import pytest

from app.interview.checkpoint import get_checkpointer, purge_finished
from app.core.db import session_scope
from app.questions import compose as C
from app.questions import graph as Q
from tests.test_handlers import fake_ai, q, setup_app  # noqa: F401 - the fixture
from tests.test_interview import url


def test_graph_is_the_designed_chain():
    g = Q.build().compile().get_graph()
    nodes = {n for n in g.nodes} - {"__start__", "__end__"}
    assert nodes == {"plan", "personality", "collaboration", "project", "motivation", "job_skill", "verify", "write"}
    chain = ["__start__", "plan", "personality", "collaboration", "project", "motivation", "job_skill", "verify", "write", "__end__"]
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
        await Q.run(app_id, settings_id)
    thread = Q.thread_id(app_id, settings_id)
    graph = await Q.get_graph()
    snap = await graph.aget_state({"configurable": {"thread_id": thread}})
    assert snap.next == ("motivation",) and await _rows(thread) > 0 and calls["resume"] == 1
    assert (await q("SELECT count(*) FROM question_sets WHERE application_id=:a", a=str(app_id)))[0][0] == 0  # nothing written yet

    qset_id = await Q.run(app_id, settings_id)  # the retry: continues at motivation
    assert calls["resume"] == 1, "the nodes that had finished were not run again"
    assert await _rows(thread) == 0  # the run is over: its checkpoints are deleted
    texts = await q("SELECT seq, text, slot_type FROM interview_questions WHERE question_set_id=:s ORDER BY seq", s=str(qset_id))
    assert [t[0] for t in texts] == [1, 2, 3, 4, 5] and len({t[1] for t in texts}) == 5
    assert await q("SELECT count(*) FROM jobs WHERE kind='tts_pregen' AND application_id=:a", a=str(app_id)) == [(1,)]

    again = await Q.run(app_id, settings_id)  # a second run for the same version writes nothing new
    assert again == qset_id
    assert (await q("SELECT count(*) FROM question_sets WHERE application_id=:a AND status='READY'", a=str(app_id)))[0][0] == 1


async def test_checkpoint_cleanup_follows_the_application_state(client, make_client, fake_ai, monkeypatch):
    app_id, settings_id = await _prepared(client, make_client)

    async def boom(db, c, kind, slots, embed_model):
        raise RuntimeError("model down")

    monkeypatch.setattr(C, "_fill_generated_kind", boom)
    with pytest.raises(RuntimeError):
        await Q.run(app_id, settings_id)
    thread = Q.thread_id(app_id, settings_id)
    assert await _rows(thread) > 0
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(thread) > 0  # still PREPARING: a retry needs it
    await q("UPDATE applications SET attempt_status='READY' WHERE id=:a RETURNING 1", a=str(app_id))
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(thread) > 0  # READY can still be composed again
    await q("UPDATE applications SET attempt_status='PREPARING' WHERE id=:a RETURNING 1", a=str(app_id))
    await (await get_checkpointer()).adelete_thread("unrelated")  # no error for an unknown thread
    # an orphan thread of an application that does not exist any more
    ghost = f"compose:{uuid.uuid4()}:{uuid.uuid4()}"
    graph = await Q.get_graph()
    await graph.aupdate_state({"configurable": {"thread_id": ghost}}, {"application_id": "x", "settings_id": "y", "picked": []}, as_node="plan")
    assert await _rows(ghost) > 0
    async with session_scope() as db:
        await purge_finished(db)
    assert await _rows(ghost) == 0 and await _rows(thread) > 0
