"""The evaluation graph (app/evaluation/graph.py): shape, retries that continue instead of starting over (no model call twice),
no transaction open during a model call, the reuse rules of stored unit results, system-side vs content-side outcomes, the
lease guard, what the checkpoint may hold, and its cleanup."""
import json
import uuid

import pytest
from sqlalchemy import text

from app.ai import rubric
from app.ai.fake import FAKE_MANIFEST
from app.core.db import open_scopes, session_scope
from app.evaluation import graph as G
from app.evaluation import units as U
from app.evaluation.pipeline import EvaluationFailed
from app.interview.checkpoint import get_checkpointer, purge_finished
from app.jobs import handlers
from app.jobs.queue import enqueue
from tests.evalstate import to_submitted
from tests.test_handlers import (  # noqa: F401 - fake_ai is a fixture
    ANSWERS,
    SUM_SOLUTION,
    answer_all,
    coding_app,
    drain,
    fake_ai,
    prepare_and_compose,
    q,
    setup_app,
    submit_and_register,
    submit_code,
)

UNITS = 5  # five main questions, no follow-ups (the fake skips them); seq 5 is a silent answer (a rule result, no model call)


def count(fake, task: str) -> int:
    return sum(c.task == task for c in fake.calls)


def watch_scopes(fake) -> list[tuple[str, int]]:
    """Every model / embedding call with the number of open session_scope transactions at that moment."""
    seen: list[tuple[str, int]] = []
    llm, embed = fake.llm, fake.embed

    async def llm_watched(task, input, **kw):
        seen.append((task, open_scopes()))
        return await llm(task, input, **kw)

    async def embed_watched(texts):
        seen.append(("embed", open_scopes()))
        return await embed(texts)

    fake.llm, fake.embed = llm_watched, embed_watched
    return seen


async def answered(client, make_client, **kw) -> tuple[str, list[str]]:
    _, app_id, cand = await setup_app(client, make_client, **kw)
    ids = await answer_all(app_id, await prepare_and_compose(cand, app_id))
    return app_id, ids


async def pre_evaluate_all(ids: list[str]) -> None:
    async with session_scope() as db:
        for aid in ids:
            await enqueue(db, "pre_evaluate", {"answer_id": aid}, dedupe_key=f"pre:{aid}")
    await drain()


async def retry_now() -> None:
    await q("UPDATE jobs SET run_after=now() WHERE state='QUEUED' RETURNING id")
    await drain()


async def threads() -> list[str]:
    if not (await q("SELECT to_regclass('public.checkpoints') IS NOT NULL"))[0][0]:
        return []
    return [t for (t,) in await q("SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'eval:%'")]


# ── shape ──────────────────────────────────────────────────────────────────────
def test_graph_is_units_in_a_loop_then_the_fixed_chain():
    g = G.build().compile().get_graph()
    assert {n for n in g.nodes} - {"__start__", "__end__"} == {"prepare", "unit", "verify", "code_review", "custom", "score",
                                                               "summary", "feedback"}
    edges = {(e.source, e.target) for e in g.edges}
    assert {("__start__", "prepare"), ("prepare", "unit"), ("prepare", "verify"), ("unit", "unit"), ("unit", "verify"),
            ("verify", "code_review"), ("code_review", "custom"), ("custom", "score"), ("score", "summary"),
            ("summary", "feedback"), ("feedback", "__end__")} <= edges


# ── retries continue at the step that stopped ──────────────────────────────────
async def test_a_failed_attempt_continues_at_the_failed_step_and_finished_steps_are_not_called_again(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    fake_ai.fail["candidate_feedback"] = "invalid"  # T7 fails on the first attempt: T5 x5, T6 have run
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("PENDING",)]
    assert (count(fake_ai, "evaluate_answer"), count(fake_ai, "report_summary"), count(fake_ai, "candidate_feedback")) == (UNITS, 1, 1)
    (job_id,), = await q("SELECT id FROM jobs WHERE kind='evaluate_application'")
    assert await threads() == [G.thread_id(app_id, job_id)]

    del fake_ai.fail["candidate_feedback"]
    await retry_now()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    # the retry called only T7 again
    assert (count(fake_ai, "evaluate_answer"), count(fake_ai, "report_summary"), count(fake_ai, "candidate_feedback")) == (UNITS, 1, 2)
    runs = await q("SELECT run_no, trigger, status FROM evaluation_runs WHERE application_id=:a ORDER BY run_no", a=app_id)
    assert runs == [(1, "SUBMIT", "FAILED"), (2, "AUTO_RETRY", "SUCCEEDED")]
    assert await threads() == []  # written: the checkpoint is gone
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert det["evidence"]["evaluated"] == UNITS and det["evidence"]["reused"] == 0 and det["evidence"]["system_items"] == 0


async def test_on_hold_after_two_failed_attempts_costs_the_units_only_once(client, make_client, fake_ai):
    """EVL-06 stays (two attempts, then ON_HOLD, no score shown); the second attempt no longer evaluates every answer again."""
    app_id, _ = await answered(client, make_client)
    fake_ai.fail["report_summary"] = "invalid"
    await submit_and_register(app_id)
    await drain()
    await retry_now()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("ON_HOLD",)]
    assert await q("SELECT 1 FROM recruiter_reports WHERE application_id=:a", a=app_id) == []
    assert count(fake_ai, "evaluate_answer") == UNITS and count(fake_ai, "report_summary") == 2
    # the recruiter's re-evaluation is a new job: a new thread, and the units stored by the held run are reused
    del fake_ai.fail["report_summary"]
    r = await client.post(f"/api/recruiter/applications/{app_id}/re-evaluate")
    assert r.status_code in (200, 202), r.text
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    assert count(fake_ai, "evaluate_answer") == UNITS
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert det["evidence"]["reused"] == UNITS and det["evidence"]["evaluated"] == 0


async def test_a_crash_between_storing_a_unit_and_its_checkpoint_does_not_call_the_model_again(client, make_client, fake_ai,
                                                                                               monkeypatch):
    app_id, _ = await answered(client, make_client)
    await to_submitted(app_id)
    real_store, crashed = U.store, []

    async def store_then_crash(db, answer_id, manifest, payload, *, replace):
        await real_store(db, answer_id, manifest, payload, replace=replace)
        if not crashed:
            crashed.append(answer_id)
            raise RuntimeError("worker died")  # rolls the store back too: the transaction never committed

    monkeypatch.setattr(U, "store", store_then_crash)
    with pytest.raises(RuntimeError):
        await G.run(uuid.UUID(app_id), FAKE_MANIFEST)
    assert count(fake_ai, "evaluate_answer") == 1

    # Committed store, then the process dies before the checkpoint: the marker finds it.
    async def store_and_die_after_commit(db, answer_id, manifest, payload, *, replace):
        await real_store(db, answer_id, manifest, payload, replace=replace)

    monkeypatch.setattr(U, "store", store_and_die_after_commit)
    graph = await G.get_graph()
    cfg = {"configurable": {"thread_id": G.thread_id(app_id, None)}}
    real_unit = G.unit
    died = []

    async def unit_dies_after_store(state, config):
        out = await real_unit(state, config)
        if not died:
            died.append(1)
            raise RuntimeError("worker died after the commit")
        return out

    monkeypatch.setattr(G, "unit", unit_dies_after_store)
    G._compiled = None  # rebuild with the patched node
    try:
        with pytest.raises(RuntimeError):
            await G.run(uuid.UUID(app_id), FAKE_MANIFEST)
        calls = count(fake_ai, "evaluate_answer")
        assert calls == 2  # the one rolled back is evaluated again (nothing was kept), once
        values = await G.run(uuid.UUID(app_id), FAKE_MANIFEST)
    finally:
        G._compiled = None
    assert values["finished"] and count(fake_ai, "evaluate_answer") == UNITS + 1  # +1: only the rolled-back one twice
    assert (await graph.aget_state(cfg)).values["finished"]


# ── no transaction across a model call ─────────────────────────────────────────
async def test_no_model_call_is_made_inside_a_transaction(client, make_client, fake_ai, fake_runner):
    app_id, cand = await coding_app(client, make_client)
    ids = await answer_all(app_id, await prepare_and_compose(cand, app_id))
    seen = watch_scopes(fake_ai)  # from here on: pre_evaluate, grading, the evaluation graph
    await pre_evaluate_all(ids[:3])
    await submit_code(app_id, SUM_SOLUTION)
    await submit_and_register(app_id)
    await drain()
    await retry_now()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    tasks = {t for t, _ in seen}
    assert {"evaluate_answer", "code_review", "report_summary", "candidate_feedback"} <= tasks
    assert [x for x in seen if x[1] != 0] == []


async def test_company_items_are_graded_with_no_transaction_open_and_their_quote_stays_out_of_the_checkpoint(
        client, make_client, fake_ai):
    item = {"name": "책임감", "description": "맡은 일을 끝까지 하는가", "high": "끝까지 책임진 사례가 있다", "mid": "책임을 언급한다",
            "low": "책임에 대한 언급이 없다", "weight": 20}
    app_id, _ = await answered(client, make_client, settings={
        "weights": {"expertise": 25, "logic": 25, "attitude": 20, "delivery": 10}, "custom_items": [item]})
    seen = watch_scopes(fake_ai)
    fake_ai.fail["candidate_feedback"] = "invalid"
    await submit_and_register(app_id)
    await drain()
    assert ("custom_criteria", 0) in seen and [x for x in seen if x[1] != 0] == []
    await assert_no_answer_text_in_checkpoints()
    del fake_ai.fail["candidate_feedback"]
    await retry_now()
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    (c,) = det["custom"]
    assert c["quote"] and any(c["quote"] in a for a in ANSWERS)  # read back from the answer for the report
    assert count(fake_ai, "custom_criteria") == 1


async def assert_no_answer_text_in_checkpoints() -> None:
    """Neither an answer nor any sentence of one is in the checkpoint tables (state, blobs, pending writes)."""
    pieces = [s.strip() for a in ANSWERS for s in a.replace(",", ".").split(".") if len(s.strip()) >= 8]
    assert (await q("SELECT count(*) FROM checkpoints WHERE thread_id LIKE 'eval:%'"))[0][0] > 0
    for p in pieces:
        for table, col in (("checkpoints", "convert_to(checkpoint::text, 'UTF8')"), ("checkpoint_blobs", "blob"),
                           ("checkpoint_writes", "blob")):
            hits = (await q(f"SELECT count(*) FROM {table} WHERE thread_id LIKE 'eval:%' "
                            f"AND position(convert_to(:p, 'UTF8') IN {col}) > 0", p=p))[0][0]
            assert hits == 0, (table, p)


async def test_the_checkpoint_holds_ids_and_counts_not_the_answers(client, make_client, fake_ai, fake_runner):
    app_id, cand = await coding_app(client, make_client)
    await answer_all(app_id, await prepare_and_compose(cand, app_id))
    await submit_code(app_id, SUM_SOLUTION)
    fake_ai.fail["report_summary"] = "invalid"
    await submit_and_register(app_id)
    await drain()
    await assert_no_answer_text_in_checkpoints()
    (job_id,), = await q("SELECT id FROM jobs WHERE kind='evaluate_application'")
    values = (await (await G.get_graph()).aget_state({"configurable": {"thread_id": G.thread_id(app_id, job_id)}})).values
    dumped = json.dumps(values, ensure_ascii=False)
    assert all(a[:20] not in dumped for a in ANSWERS)
    assert values["review"] and all("text" not in (m.get("quote") or {}) for m in values["review"]["metrics"].values())
    assert "import sys" not in dumped  # the submitted code stays in the database too


# ── reuse rules of stored unit results ─────────────────────────────────────────
def _answer(text="정산 배치를 병렬화했습니다. 네 시간이 오십 분이 되었습니다.", silent=False):
    from types import SimpleNamespace
    return SimpleNamespace(eval_text=None if silent else text, is_no_answer=silent)


def _payload(answer, **over):
    t = answer.eval_text
    end = t.index(".") + 1
    item = {"score": 4, "evidence_status": "OK", "insufficient_reason": None, "comment": "",
            "quote": {"start": 0, "end": end, "text": t[:end], "sent": 1}}
    items = {c: dict(item) for c in ("EXPERTISE", "LOGIC", "ATTITUDE", "DELIVERY")}
    items.update(over)
    return {"items": items, "rubric_version": rubric.RUBRIC_VERSION, "llm": True}


def test_a_stored_unit_is_reused_only_when_nothing_is_wrong_with_it():
    a = _answer()
    assert U.reuse_problems(_payload(a), a) == []
    unlinked = {"score": 4, "evidence_status": "UNLINKED", "insufficient_reason": None, "quote": None, "comment": ""}
    assert U.reuse_problems(_payload(a, LOGIC=unlinked), a) == ["UNLINKED"]  # score 3+ without a sentence
    no_quote = {"score": 3, "evidence_status": "OK", "insufficient_reason": None, "quote": None, "comment": ""}
    assert U.reuse_problems(_payload(a, LOGIC=no_quote), a) == ["UNLINKED"]
    edited = _answer("정산 배치를 고쳤습니다. 네 시간이 오십 분이 되었습니다.")  # the text changed after the pre-evaluation
    assert U.reuse_problems(_payload(a), edited) == ["QUOTE_MISMATCH"]
    old = {**_payload(a), "rubric_version": "rubric-v1"}
    assert U.reuse_problems(old, a) == ["RUBRIC_VERSION"]
    assert U.reuse_problems(_payload(a), _answer(silent=True))[0] == "STALE"
    bad = {"score": 4, "evidence_status": "INSUFFICIENT", "insufficient_reason": "LOW_CONTENT", "quote": None, "comment": ""}
    assert U.reuse_problems(_payload(a, ATTITUDE=bad), a) == ["SHAPE"]
    assert U.reuse_problems({"items": {"EXPERTISE": {}}}, a) == ["SHAPE"]
    low = {"score": 2, "evidence_status": "INSUFFICIENT", "insufficient_reason": "LOW_CONTENT", "quote": None, "comment": ""}
    assert U.reuse_problems(_payload(a, ATTITUDE=low), a) == []


def test_a_fresh_result_settles_what_still_fails_and_counts_system_and_content_apart():
    a = _answer()
    p = _payload(a)
    p["items"]["LOGIC"]["quote"] = {"start": 0, "end": 5, "text": "다른 문장", "sent": 1}  # not a span of the answer
    sealed = U.seal_fresh(p, a)
    assert sealed["items"]["LOGIC"] == {**p["items"]["LOGIC"], "score": 1, "evidence_status": "INSUFFICIENT",
                                        "insufficient_reason": "QUOTE_MISMATCH", "quote": None}
    with pytest.raises(EvaluationFailed):
        U.seal_fresh({**_payload(a), "rubric_version": "rubric-v1"}, a)
    low = {"score": 2, "evidence_status": "INSUFFICIENT", "insufficient_reason": "LOW_CONTENT", "quote": None, "comment": ""}
    unlinked = {"score": 4, "evidence_status": "UNLINKED", "insufficient_reason": None, "quote": None, "comment": ""}
    counts = U.outcome_counts([U.seal_fresh(_payload(a, ATTITUDE=low, DELIVERY=unlinked), a), sealed])
    assert counts == {"judged_items": 8, "system_items": 2, "content_items": 1, "no_answer_items": 0}


async def test_stored_units_that_fail_the_rules_are_evaluated_again_once_and_replaced(client, make_client, fake_ai):
    app_id, ids = await answered(client, make_client)
    await pre_evaluate_all(ids)
    assert count(fake_ai, "evaluate_answer") == UNITS
    async with session_scope() as db:  # one UNLINKED high score made before the checks, one older rubric version, one other manifest
        await db.execute(text("UPDATE answer_pre_evaluations SET payload = jsonb_set(payload - 'check', '{items,LOGIC}', "
                              "'{\"score\": 4, \"evidence_status\": \"UNLINKED\", \"insufficient_reason\": null, \"quote\": null, "
                              "\"comment\": \"\"}') WHERE answer_id=:a"), {"a": ids[0]})
        await db.execute(text("UPDATE answer_pre_evaluations SET payload = jsonb_set(payload, '{rubric_version}', '\"rubric-v1\"') "
                              "WHERE answer_id=:a"), {"a": ids[1]})
        await db.execute(text("UPDATE answer_pre_evaluations SET manifest_hash='sha256:older' WHERE answer_id=:a"), {"a": ids[2]})
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    assert count(fake_ai, "evaluate_answer") == UNITS + 3
    (det,), = await q("SELECT detail FROM recruiter_reports WHERE application_id=:a", a=app_id)
    assert det["evidence"]["reused"] == UNITS - 3 and det["evidence"]["evaluated"] == 3
    assert det["evidence"]["reasons"] == {"UNLINKED": 1, "RUBRIC_VERSION": 1, "MISSING": 1}
    rows = await q("SELECT answer_id::text, payload->'items'->'LOGIC'->>'evidence_status', payload->>'rubric_version' "
                   "FROM answer_pre_evaluations WHERE manifest_hash=:m ORDER BY answer_id", m=FAKE_MANIFEST)
    assert {r[0] for r in rows} == set(ids) and {(r[1], r[2]) for r in rows if r[0] in ids[:3]} == {("OK", rubric.RUBRIC_VERSION)}


# ── system side vs content side ────────────────────────────────────────────────
def _judge_all(sent: int, score: int):
    def reply(inp, msgs):
        return {k: {"sent": sent, "comment": "판단 이유", "score": score} for k in ("expertise", "logic", "attitude", "delivery")} | {
            "expertise_sub": {"a": 3, "b": 3, "c": 3}, "logic_sub": {"a": 3, "b": 3, "c": 3},
            "star": {"situation": 0, "task": 0, "action": 0, "result": 0}}
    return reply


async def test_evidence_that_cannot_be_linked_holds_the_evaluation_without_asking_the_model_again(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    fake_ai.responses["evaluate_answer"] = _judge_all(sent=0, score=4)  # high scores, never a sentence: UNLINKED after one more ask
    await submit_and_register(app_id)
    await drain()
    first = count(fake_ai, "evaluate_answer")
    assert first == UNITS + (UNITS - 1) and not [c for c in fake_ai.calls if c.task == "report_summary"]
    await retry_now()  # the retry continues at verify: the same verdict, no model call
    assert count(fake_ai, "evaluate_answer") == first
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("ON_HOLD",)]
    assert [e for (e,) in await q("SELECT error_code FROM evaluation_runs WHERE application_id=:a ORDER BY run_no", a=app_id)] \
        == ["EvidenceUnverifiable", "EvidenceUnverifiable"]
    assert await q("SELECT 1 FROM recruiter_reports WHERE application_id=:a", a=app_id) == []


async def test_answers_with_little_content_are_scored_low_not_held(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    fake_ai.responses["evaluate_answer"] = _judge_all(sent=0, score=1)  # nothing to point at and a low score: 내용 부족
    await submit_and_register(app_id)
    await drain()
    assert await q("SELECT eval_status FROM applications WHERE id=:a", a=app_id) == [("DONE",)]
    (det, n), = await q("SELECT detail, insufficient_count FROM recruiter_reports WHERE application_id=:a", a=app_id)
    ev = det["evidence"]
    assert (ev["system_items"], ev["content_items"], ev["judged_items"]) == (0, 16, 16) and n == 16
    reasons = {r for (r,) in await q("SELECT DISTINCT insufficient_reason FROM answer_evaluations WHERE evidence_status='INSUFFICIENT'")}
    assert reasons == {"LOW_CONTENT"}


# ── versions, leases, cleanup ──────────────────────────────────────────────────
async def test_a_changed_manifest_starts_the_run_again_and_never_mixes_versions(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    await to_submitted(app_id)
    fake_ai.fail["report_summary"] = "invalid"
    with pytest.raises(EvaluationFailed):
        await G.run(uuid.UUID(app_id), FAKE_MANIFEST)
    assert count(fake_ai, "evaluate_answer") == UNITS
    # The model server came back with another manifest: the old thread is dropped; the first unit made under the new one
    # reports the fake manifest again, which is a change during the run -> deferral (ManifestChanged), not a mixed report.
    with pytest.raises(G.ManifestChanged):
        await G.run(uuid.UUID(app_id), "sha256:newer")
    snap = await (await G.get_graph()).aget_state({"configurable": {"thread_id": G.thread_id(app_id, None)}})
    assert snap.values["manifest"] == "sha256:newer" and snap.next == ("unit",)
    with pytest.raises(G.ManifestChanged):
        await G.run(uuid.UUID(app_id), "")  # unknown manifest: no run at all


async def test_a_worker_that_lost_its_lease_makes_no_model_call(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    await to_submitted(app_id)
    before = len(fake_ai.calls)  # the question composition's calls
    with pytest.raises(G.LeaseLost):
        await G.run(uuid.UUID(app_id), FAKE_MANIFEST, job_id=987654, lease_token=1)  # no such running job
    assert len(fake_ai.calls) == before


async def test_evaluation_checkpoints_are_kept_while_the_job_can_run_and_purged_after(client, make_client, fake_ai):
    app_id, _ = await answered(client, make_client)
    fake_ai.fail["report_summary"] = "invalid"
    await submit_and_register(app_id)
    await drain()
    (job_id,), = await q("SELECT id FROM jobs WHERE kind='evaluate_application'")
    thread = G.thread_id(app_id, job_id)
    async with session_scope() as db:
        await purge_finished(db)
    assert await threads() == [thread]  # QUEUED for its retry
    await retry_now()  # fails again: FAILED, ON_HOLD
    async with session_scope() as db:
        await purge_finished(db)
    assert await threads() == []
    await (await get_checkpointer()).adelete_thread(thread)  # no error once gone


async def test_pre_evaluate_uses_the_same_unit_evaluation_as_the_graph(client, make_client, fake_ai, monkeypatch):
    app_id, ids = await answered(client, make_client)
    used = []
    real = U.judge

    async def counted(ai, answer_id):
        used.append(answer_id)
        return await real(ai, answer_id)

    monkeypatch.setattr(handlers.eval_units, "judge", counted)
    await pre_evaluate_all(ids[:2])
    assert [str(x) for x in used] == ids[:2]
    await submit_and_register(app_id)
    await drain()
    assert len(used) == UNITS  # the graph's unit node evaluated the other three through the same function
