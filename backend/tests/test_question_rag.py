"""RAG for question generation (ai-architecture 6.1): the reference corpus is loaded and embedded by a job, T2/T3 get
the nearest examples of the same kind, copies of an example are dropped, and a missing index or embedding outage
never blocks composition."""
import json
from collections import Counter

import pytest
from sqlalchemy import text

from app.ai.client import AIUnavailable
from app.core.db import session_scope, sessionmaker
from app.questions import rag
from tests.test_handlers import drain, fake_ai, prepare_and_compose, q, setup_app  # noqa: F401  fixtures + helpers

MODEL = "qwen3-embedding:0.6b"  # settings.embed_model in tests
JOB_EXAMPLES = ["회귀 테스트 자동화 범위를 어떻게 정하나요?", "릴리스 품질 지표로 무엇을 보시나요?",
                "SQL 인덱스는 언제 쓰나요?", "파이썬의 GIL이 무엇인가요?"]
RESUME_EXAMPLES = ["Git으로 협업했던 경험을 말씀해 주세요.", "프로젝트에서 맡은 역할을 설명해 주시겠어요?"]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    rows = [{"kind": "JOB", "field": "QA", "text": t, "origin": "test"} for t in JOB_EXAMPLES]
    rows += [{"kind": "RESUME", "field": "기술", "text": t, "origin": "test"} for t in RESUME_EXAMPLES]
    path = tmp_path / "examples.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(rag, "DATA_FILE", path)
    return path


async def _sync(ai, model: str = MODEL):
    async with sessionmaker()() as db:
        return await rag.sync_examples(db, ai, model)


async def _no_design_seeds(on: bool) -> None:
    async with session_scope() as db:  # make the job slot go to T2 (DESIGN has seed questions otherwise)
        await db.execute(text("UPDATE question_bank SET is_active = :on WHERE source = 'SEED' AND job_family = 'DESIGN'"),
                         {"on": on})


async def test_sync_loads_embeds_and_resumes(fake_ai, corpus):
    fake_ai.down.add("embed")
    with pytest.raises(AIUnavailable):  # rows are loaded first; embedding waits for the AI service (job defers)
        await _sync(fake_ai)
    assert await q("SELECT count(*) FROM question_examples WHERE embedding IS NULL") == [(6,)]
    fake_ai.down.clear()
    assert await _sync(fake_ai) == {"inserted": 0, "embedded": 6, "pending": 0}
    assert await _sync(fake_ai) == {"inserted": 0, "embedded": 0, "pending": 0}  # idempotent
    # another embedding model -> everything is re-embedded, never compared across models
    assert (await _sync(fake_ai, "other-model"))["embedded"] == 6


async def test_periodic_sync_enqueues_the_index_job(fake_ai, corpus):
    from app.jobs.tasks import question_examples_sync

    await question_examples_sync()
    await question_examples_sync()  # deduped while queued
    assert await q("SELECT count(*) FROM jobs WHERE kind = 'index_question_examples'") == [(1,)]
    await drain()
    assert await q("SELECT count(*) FROM question_examples WHERE embedding_model = :m", m=MODEL) == [(6,)]
    await question_examples_sync()
    assert await q("SELECT count(*) FROM jobs WHERE kind = 'index_question_examples' AND state = 'QUEUED'") == [(0,)]


async def test_generation_gets_nearest_examples_of_the_same_kind(client, make_client, fake_ai, corpus):
    await _sync(fake_ai)
    await _no_design_seeds(False)
    try:
        _, app_id, cand = await setup_app(client, make_client, job_family="DESIGN", title="QA 엔지니어",
                                          duties="회귀 테스트 자동화 범위 설계. 릴리스 품질 지표 관리")
        await prepare_and_compose(cand, app_id)
    finally:
        await _no_design_seeds(True)
    gen = next(c for c in fake_ai.calls if c.task == "question_generate")
    assert len(gen.input["examples"]) == 3 and set(gen.input["examples"]) <= set(JOB_EXAMPLES)
    assert gen.input["examples"][0] in JOB_EXAMPLES[:2]  # nearest to the QA posting comes first
    prompt = gen.messages[0][-1]["content"]
    assert "참고 예시 질문" in prompt and gen.input["examples"][0] in prompt
    res = next(c for c in fake_ai.calls if c.task == "resume_question")
    assert set(res.input["examples"]) == set(RESUME_EXAMPLES)  # RESUME kind only, never JOB examples


async def test_copied_questions_are_dropped(client, make_client, fake_ai, corpus):
    await _sync(fake_ai)
    copy = JOB_EXAMPLES[0]

    def reply(inp, msgs):  # the model copies the first example (with a valid basis quote)
        return {"questions": [{"text": copy, "basis_quote": "회귀 테스트 자동화 범위 설계"}]}

    fake_ai.responses["question_generate"] = reply
    await _no_design_seeds(False)
    try:
        _, app_id, cand = await setup_app(client, make_client, job_family="DESIGN", title="QA 엔지니어",
                                          duties="회귀 테스트 자동화 범위 설계. 릴리스 품질 지표 관리")
        qs = await prepare_and_compose(cand, app_id)
    finally:
        await _no_design_seeds(True)
    assert copy not in [x.text for x in qs]
    gen = next(c for c in fake_ai.calls if c.task == "question_generate")
    assert len(gen.messages) == 2 and "베끼지 말고" in gen.messages[1][-1]["content"]  # one regeneration, then fallback
    async with session_scope() as db:  # the embedding check catches near copies the lexical check lets through
        ex = await rag.find_examples(db, fake_ai, MODEL, "JOB", copy, 1)
        assert await rag.copied(db, fake_ai, MODEL, [copy.replace("어떻게", "어떻게 "), "전혀 다른 질문인가요?"], ex, 0.9) == {0}


async def test_no_index_or_embed_outage_still_composes(client, make_client, fake_ai, corpus):
    _, app_id, cand = await setup_app(client, make_client)
    qs = await prepare_and_compose(cand, app_id)  # corpus not indexed yet
    assert len(qs) == 5
    assert next(c for c in fake_ai.calls if c.task == "resume_question").input["examples"] == []
    await _sync(fake_ai)
    fake_ai.down.add("embed")
    fake_ai.calls.clear()
    _, app2, cand2 = await setup_app(make_client(), make_client)
    assert len(await prepare_and_compose(cand2, app2)) == 5
    assert next(c for c in fake_ai.calls if c.task == "resume_question").input["examples"] == []


def test_shipped_corpus_is_clean():
    rows = rag.load_file(rag.DATA_FILE)
    kinds = Counter(r["kind"] for r in rows)
    assert set(kinds) == {"JOB", "RESUME", "PERSONALITY", "COLLABORATION", "COMPANY"} and kinds["JOB"] > 1000
    assert all(0 < len(r["text"]) <= 150 for r in rows)
    assert len({(r["kind"], "".join(r["text"].split())) for r in rows}) == len(rows)


def test_search_log_keeps_ids_and_scores_but_never_text():
    import json
    import logging

    from app.core.logging import JsonFormatter

    record = logging.LogRecord("app", logging.INFO, "", 0, "rag_search", None, None)
    record.fields = {"event": "rag_search", "kind": "JOB", "hits": 1, "top_similarity": 0.9, "example_ids": ["x"],
                     "query": "이력서 내용", "previews": ["질문 원문"]}
    out = json.loads(JsonFormatter().format(record))
    assert out["example_ids"] == ["x"] and out["top_similarity"] == 0.9
    assert "query" not in out and "previews" not in out
