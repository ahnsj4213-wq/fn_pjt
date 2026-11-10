"""The question generation graph after the 2026-11-10 refactoring: limited regeneration with failure codes, the clarity rules, no
database transaction held across a model call (upfront and live), idempotent re-runs and one composition per live slot."""
import asyncio
import contextvars
import uuid

import pytest

from app.ai.tasks.questions import CHECK_TEXT
from app.ai.textrules import clarity_problems
from app.core.db import open_scopes
from app.core.config import get_settings
from app.questions import compose as C
from app.questions import graph as Q
from app.questions import live
from app.questions import rag_graph
from tests.test_company_knowledge import DOC, _add, _embed, _posting_of
from tests.test_handlers import fake_ai, q, setup_app  # noqa: F401 - the fixture
from tests.test_interview import started, url

HAN = "".join(map(chr, (0x7528, 0x4EC0, 0x4E48)))  # the Chinese words a real answer of the 4B model had in a Korean question

POSTING = {"title": "결제 백엔드 개발자", "duties": "결제 승인 API 운영과 장애 대응\n정산 배치 처리 성능 개선"}


def _gen_input(n: int = 2, **kw) -> dict:
    return {"kind": "JOB", "job_family": "BACKEND", "n": n, "existing": [], "posting_id": "p", "company_id": "c",
            "posting": {"title": POSTING["title"], "duties": POSTING["duties"], "requirements": "", "preferred": ""},
            "company_intro": "", "knowledge": [], "used_basis": [], "level": "JUNIOR", "difficulties": [], **kw}


GOOD1 = {"text": "결제 승인 API 운영에서 가장 먼저 확인할 지표는 무엇입니까?", "basis_quote": "결제 승인 API 운영과 장애 대응"}
GOOD2 = {"text": "정산 배치 처리 성능 개선을 맡는다면 무엇부터 보시겠습니까?", "basis_quote": "정산 배치 처리 성능 개선"}
FOREIGN = {"text": f"정산 배치 처리 성능 개선에서 {HAN} 기준을 쓰시겠습니까?", "basis_quote": "정산 배치 처리 성능 개선"}
MISQUOTED = {"text": "결제 승인 API 장애 대응은 어떻게 하시겠습니까?", "basis_quote": "공고에 없는 문장입니다"}


# ── rules ──────────────────────────────────────────────────────────────────────
def test_clarity_rules_catch_foreign_script_and_stacked_conditions():
    assert clarity_problems(f"어떤 기준을 {HAN} 하시겠습니까?") == ["FOREIGN_SCRIPT"]
    assert clarity_problems("트래픽이 몰릴 때 캐시가 없다면 어떻게 하시겠습니까?") == ["NESTED"]
    assert clarity_problems("무엇을 보셨습니까? 왜 그랬습니까?") == ["NESTED"]
    for fine in ("Kafka 장애가 난 경우라면 무엇부터 확인하시겠습니까?", "배포할 때 가장 먼저 확인할 것은 무엇입니까?",
                 "때때로 어떤 일을 하십니까?"):
        assert clarity_problems(fine) == [], fine
    assert {"FOREIGN_SCRIPT", "NESTED", "QUOTE_MISMATCH", "DUPLICATE", "SAME_BASIS"} <= set(CHECK_TEXT)


# ── T2: only the failed questions are asked again ──────────────────────────────
async def test_regeneration_keeps_the_passed_question_and_asks_only_for_the_missing_one(fake_ai):
    answers = iter([{"questions": [GOOD1, FOREIGN, MISQUOTED]}, {"questions": [GOOD1, GOOD2]}])
    fake_ai.responses["question_generate"] = lambda i, m: next(answers)
    r = await fake_ai.llm("question_generate", _gen_input(2), lane="INTERACTIVE")
    assert r.status == "OK" and [x["text"] for x in r.output["questions"]] == [GOOD1["text"], GOOD2["text"]]
    call = fake_ai.calls[-1]
    assert len(call.messages) == 2  # one regeneration
    regen = call.messages[1][-1]["content"]
    assert "2번 질문: [FOREIGN_SCRIPT]" in regen and "3번 질문: [QUOTE_MISMATCH]" in regen and "새 질문 1개만" in regen
    assert "1번" in regen and "그대로 쓴다" in regen  # the passed one is kept, not made again
    # counts per code over both answers (the repeated GOOD1 repeats the kept one and its basis), never the text
    assert r.flags["rejects"] == {"FOREIGN_SCRIPT": 1, "QUOTE_MISMATCH": 1, "DUPLICATE": 1, "SAME_BASIS": 1} and r.flags["kept"] == 1


async def test_the_regeneration_budget_stays_one_and_the_kept_question_survives_a_bad_second_answer(fake_ai):
    answers = iter([{"questions": [GOOD1, FOREIGN]}, {"questions": [FOREIGN]}])
    fake_ai.responses["question_generate"] = lambda i, m: next(answers)
    r = await fake_ai.llm("question_generate", _gen_input(2), lane="INTERACTIVE")
    assert len(fake_ai.calls[-1].messages) == 2 and [x["text"] for x in r.output["questions"]] == [GOOD1["text"]]


async def test_last_chance_asks_once(fake_ai):
    fake_ai.responses["question_generate"] = lambda i, m: {"questions": [GOOD1, FOREIGN]}
    r = await fake_ai.llm("question_generate", _gen_input(2, last_chance=True), lane="INTERACTIVE")
    assert len(fake_ai.calls[-1].messages) == 1 and len(r.output["questions"]) == 1


async def test_the_resume_task_also_keeps_what_passed(fake_ai):
    from app.ai.tasks.questions import flatten_resume
    resume = {"experience": [{"company": "데이터웍스", "role": "백엔드", "description": "결제 승인 서버의 응답 지연을 캐시로 줄였다"},
                             {"company": "페이랩", "role": "백엔드", "description": "정산 배치를 파티션 단위로 병렬화했다"}]}
    flat = flatten_resume(resume)[0]
    assert "응답 지연" in flat
    good = {"text": "결제 승인 서버의 응답 지연을 줄일 때 무엇을 근거로 캐시를 고르셨습니까?",
            "evidence": [{"field": "experience[0].description", "quote": "결제 승인 서버의 응답 지연을 캐시로 줄였다"}]}
    bad = {"text": f"정산 배치를 {HAN} 병렬화하셨습니까?", "evidence": [{"field": "experience[1].description", "quote": "정산 배치를 파티션 단위로 병렬화했다"}]}
    good2 = {"text": "정산 배치를 파티션 단위로 나눈 이유는 무엇이었습니까?",
             "evidence": [{"field": "experience[1].description", "quote": "정산 배치를 파티션 단위로 병렬화했다"}]}
    answers = iter([{"questions": [good, bad]}, {"questions": [good2]}])
    fake_ai.responses["resume_question"] = lambda i, m: next(answers)
    r = await fake_ai.llm("resume_question", {"resume": resume, "job_brief": "결제 백엔드", "n": 2, "level": "JUNIOR"}, lane="INTERACTIVE")
    assert {x["text"] for x in r.output["questions"]} == {good["text"], good2["text"]}
    assert "2번 질문: [FOREIGN_SCRIPT]" in fake_ai.calls[-1].messages[1][-1]["content"]


# ── corrective RAG: bounded model calls ────────────────────────────────────────
async def test_rag_asks_the_model_at_most_twice_and_not_again_without_new_documents(client, make_client, fake_ai):
    _, app_id, _cand = await setup_app(client, make_client, title="결제 백엔드 개발자", duties=DOC)
    company, _ = await _posting_of(app_id)
    await _add(company, "정산 배치 재설계", DOC)
    await _embed(fake_ai)
    never = {"text": f"정산 배치 재설계에서 {HAN} 멱등 키를 어떻게 쓰시겠습니까?", "basis_quote": "멱등 키로 중복 정산을 막습니다"}
    fake_ai.responses["question_generate"] = lambda i, m: {"questions": [never]}  # never passes
    c, _slots = await Q._load({"application_id": app_id, "settings_id": await _settings(app_id), "picked": []})
    out = await rag_graph.run(c, "JOB", 2)
    gen = [x for x in fake_ai.calls if x.task == "question_generate"]
    # round 1: one regeneration; round 2 only if the search found a document not shown yet, and then without regeneration
    assert sum(len(x.messages) for x in gen) <= 3 and len(gen) <= 2
    assert out["accepted"] == [] and out["rejects"].get("FOREIGN_SCRIPT", 0) >= 2


async def _settings(app_id: str) -> str:
    (sid,) = (await q("SELECT p.current_settings_id FROM applications a JOIN job_postings p ON p.id=a.posting_id WHERE a.id=:a", a=app_id))[0]
    return str(sid)


# ── no transaction across a model call ─────────────────────────────────────────
_composing: contextvars.ContextVar[bool] = contextvars.ContextVar("composing", default=False)


@pytest.fixture
def guard(monkeypatch):
    """Wraps the active FakeAIClient: every LLM or embedding call made while composing records the open session_scopes."""
    seen: list[tuple[str, int]] = []

    def wrap(ai):
        real_llm, real_embed = ai.llm, ai.embed

        async def llm(task, inp, **kw):
            if _composing.get():
                seen.append((task, open_scopes()))
            return await real_llm(task, inp, **kw)

        async def embed(texts):
            if _composing.get():
                seen.append(("embed", open_scopes()))
            return await real_embed(texts)
        monkeypatch.setattr(ai, "llm", llm)
        monkeypatch.setattr(ai, "embed", embed)
    return wrap, seen


async def _composed(fn, *a, **k):
    token = _composing.set(True)
    try:
        return await fn(*a, **k)
    finally:
        _composing.reset(token)


async def test_upfront_composition_calls_no_model_inside_a_transaction(client, make_client, fake_ai, guard):
    wrap, seen = guard
    wrap(fake_ai)
    _, app_id, cand = await setup_app(client, make_client, default_plan=True, title="결제 백엔드 개발자", duties=DOC)
    company, _ = await _posting_of(app_id)
    await _add(company, "정산 배치 재설계", DOC)
    await _embed(fake_ai)
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200
    qset = await _composed(Q.run, uuid.UUID(app_id), uuid.UUID(await _settings(app_id)))
    tasks = {t for t, _ in seen}
    assert {"question_generate", "resume_question", "embed"} <= tasks
    assert all(n == 0 for _, n in seen), [x for x in seen if x[1]]
    assert (await q("SELECT count(*) FROM interview_questions WHERE question_set_id=:s", s=str(qset)))[0][0] == 10


async def test_a_crash_after_the_write_committed_reruns_write_without_a_second_set(client, make_client, fake_ai, monkeypatch):
    _, app_id, cand = await setup_app(client, make_client)
    assert (await cand.post(url(app_id, "/interview/prepare"))).status_code == 200
    aid, sid = uuid.UUID(app_id), uuid.UUID(await _settings(app_id))
    real_log, calls = Q.log_event, {"n": 0, "llm": 0}

    def crash_after_write(logger, event, *a, **fields):  # the write transaction has committed; the checkpoint is not saved yet
        if event == "compose_stage" and fields.get("stage") == "write" and not calls["n"]:
            calls["n"] += 1
            raise RuntimeError("worker killed")
        return real_log(logger, event, *a, **fields)
    monkeypatch.setattr(Q, "log_event", crash_after_write)
    with pytest.raises(RuntimeError):
        await Q.run(aid, sid)
    graph = await Q.get_graph()
    snap = await graph.aget_state({"configurable": {"thread_id": Q.thread_id(aid, sid)}})
    assert snap.next == ("write",)  # the checkpoint says write has not run; the database says it has
    before = len(fake_ai.calls)
    qset = await Q.run(aid, sid)
    assert len(fake_ai.calls) == before  # the model nodes are not run again
    assert await q("SELECT count(*) FROM question_sets WHERE application_id=:a", a=app_id) == [(1,)]
    assert await q("SELECT count(*) FROM interview_questions WHERE question_set_id=:s", s=str(qset)) == [(5,)]
    assert await q("SELECT count(*) FROM jobs WHERE kind='tts_pregen' AND application_id=:a", a=app_id) == [(1,)]


async def test_a_model_node_run_twice_fills_nothing_twice(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client)
    state = {"application_id": app_id, "settings_id": await _settings(app_id), "picked": []}
    state.update(await Q.plan(state))
    node = Q._model_node(C.Area.MOTIVATION, Q._fill_motivation)
    state.update(await node(state))
    n = len(fake_ai.calls)
    assert await node(state) == {} and len(fake_ai.calls) == n  # the slot is filled: no model call, no change
    assert sorted(p["seq"] for p in state["picked"]) == [1, 2, 5]  # custom 자기소개, 동기, 협업 (the five-question plan)


# ── live composition ───────────────────────────────────────────────────────────
async def test_live_composition_holds_no_transaction_and_composes_a_slot_once(client, make_client, voice_ai, monkeypatch, guard):
    monkeypatch.setattr(get_settings(), "question_mode", "live")
    app_id, _cand = await started(client, make_client)
    wrap, seen = guard
    wrap(voice_ai)
    fills = {"n": 0}
    real_fill = live._fill

    async def counted(db, c, sl):
        fills["n"] += 1
        await asyncio.sleep(0.05)  # the second caller arrives while the first composes
        return await real_fill(db, c, sl)
    monkeypatch.setattr(live, "_fill", counted)
    aid = uuid.UUID(app_id)
    # the prefetch and a second call for the same application at once: one composition, one row, one voice job
    results = await asyncio.gather(_composed(live.ensure_next, aid, ahead=True), _composed(live.ensure_next, aid, ahead=True))
    assert sorted(results) == [False, True] and fills["n"] == 1
    assert await q("SELECT seq FROM interview_questions WHERE application_id=:a AND role='MAIN' ORDER BY seq", a=app_id) == [(1,), (2,)]
    assert await q("SELECT count(*) FROM jobs WHERE application_id=:a AND kind='tts_pregen' AND dedupe_key LIKE :k", a=app_id, k="tts:%:2") == [(1,)]
    assert seen and all(n == 0 for _, n in seen), [x for x in seen if x[1]]
    assert not live._locks  # the per-application lock is forgotten when nobody waits


async def test_live_write_refuses_a_slot_written_meanwhile(client, make_client, voice_ai, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_mode", "live")
    app_id, _cand = await started(client, make_client)
    aid = uuid.UUID(app_id)
    found = await live._read(aid, True)
    assert found is not None
    c, qset, sl = found
    C.posting_fallback(c, "JOB", [(sl.seq, sl.area.value)])  # code-made: no model needed for this check
    pick = c.picked[-1]
    assert await live._write(aid, qset.id, sl, pick) is True
    assert await live._write(aid, qset.id, sl, pick) is False  # another process got there first: no second row
    assert await q("SELECT count(*) FROM interview_questions WHERE application_id=:a AND seq=:s AND role='MAIN'", a=app_id, s=sl.seq) == [(1,)]
