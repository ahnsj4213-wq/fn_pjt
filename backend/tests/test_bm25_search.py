"""BM25 beside pgvector (ADR-0030): real BM25 scores, tokens for Korean and technology names, an independent BM25 list that finds
what the vector search misses, weighted RRF, the company/posting scope of both lists, and one method answering when the other fails."""
import math
import uuid
from collections import Counter

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from app.questions import bm25, knowledge
from tests.test_company_knowledge import MODEL, _add, _embed, _posting_of
from tests.test_handlers import fake_ai, q, setup_app  # noqa: F401  fixtures + helpers


@pytest.fixture(autouse=True)
def _fresh_index():
    bm25.clear_cache()
    yield
    bm25.clear_cache()


def _doc(words: str) -> bm25.Doc:
    tf = Counter(bm25.tokenize(words))
    return bm25.Doc(uuid.uuid4(), uuid.uuid4(), None, uuid.uuid4(), "TECH", "", words, tf, sum(tf.values()))


# ── tokens ─────────────────────────────────────────────────────────────────────
def test_tokens_keep_technology_names_and_strip_korean_particles():
    toks = bm25.tokenize("PostgreSQL과 Spring Boot, Kafka를 쓰는 Node.js 서비스")
    assert {"postgresql", "spring", "boot", "kafka", "node.js", "node", "js", "서비스"} <= set(toks)
    hangul = bm25.tokenize("카프카를 도입했습니다")
    assert "카프카" in hangul and "kafka" in hangul and "카프카를" not in hangul  # the Hangul spelling also names the technology
    assert "스트리" in "".join(bm25.tokenize("스트리밍처리"))  # bigrams of a compound: 스트, 트리, 리밍, 밍처, 처리
    assert {"스트", "처리"} <= set(bm25.tokenize("스트리밍처리"))
    assert bm25.tokenize("a 의 을") == ["a"]  # a lone particle is no term


# ── the score is BM25 ──────────────────────────────────────────────────────────
def test_score_is_bm25_with_idf_tf_saturation_and_length_normalisation():
    docs = [_doc("kafka 오프셋 kafka"), _doc("kafka 컨슈머 그룹 재처리 운영 기록 정리"), _doc("redis 캐시")]
    index = bm25.Index(docs)
    n, avgdl = 3, sum(d.length for d in docs) / 3
    idf = math.log(1 + (n - 2 + 0.5) / (2 + 0.5))  # "kafka" is in 2 of 3 chunks

    def expected(d):
        f = d.tf["kafka"]
        return idf * f * (bm25.K1 + 1) / (f + bm25.K1 * (1 - bm25.B + bm25.B * d.length / avgdl))

    scored = dict((d.id, s) for d, s in index.score("kafka", lambda d: True))
    assert set(scored) == {docs[0].id, docs[1].id}  # no shared term -> no score, not a zero entry
    assert scored[docs[0].id] == pytest.approx(expected(docs[0])) and scored[docs[1].id] == pytest.approx(expected(docs[1]))
    assert scored[docs[0].id] > scored[docs[1].id]  # more occurrences in a shorter chunk
    # a rare term counts more than a common one
    rare = dict((d.id, s) for d, s in index.score("redis", lambda d: True))
    assert rare[docs[2].id] > idf
    # TF saturates: ten occurrences are worth far less than ten times one
    one, ten = bm25.Index([_doc("x 정산"), _doc(" ".join(["x"] * 10) + " 정산")]).score("x", lambda d: True)[::-1]
    assert ten[1] < 3 * one[1]


def test_the_coverage_floor_keeps_out_a_chunk_that_shares_only_a_common_word():
    docs = [_doc("Kafka 오프셋 이동 재처리"), _doc("Kafka 소개"), _doc("정산 배치"), _doc("주문 배치")]
    index = bm25.Index(docs)
    everything = {d.id for d, _ in index.score("Kafka 오프셋 재처리", lambda d: True)}
    strict = {d.id for d, _ in index.score("Kafka 오프셋 재처리", lambda d: True, min_coverage=0.5)}
    assert everything == {docs[0].id, docs[1].id} and strict == {docs[0].id}


def test_weighted_rrf_uses_ranks_from_one_and_drops_what_no_weighted_list_holds(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rag_rrf_k", 60)
    a, b, c = (knowledge.Chunk(uuid.uuid4(), t, t, sim) for t, sim in (("a", 0.9), ("b", 0.8), ("c", 0.0)))
    fused = knowledge.fuse([(0.6, [a, b]), (0.4, [c, a])])
    assert [x.title for x in fused] == ["a", "b", "c"]  # a: .6/61+.4/62; b: .6/62; c: .4/61
    assert fused[0].similarity == 0.9  # the vector similarity is kept for the log
    assert [x.title for x in knowledge.fuse([(1.0, [a, b]), (0.0, [c])])] == ["a", "b"]  # weight 0 adds nothing


# ── against the database ───────────────────────────────────────────────────────
async def _hybrid(company, posting, query, k=5, **kw):
    async with session_scope() as db:
        return await knowledge.search_many(db, kw.pop("ai"), MODEL, company_id=company, posting_id=posting, queries=[query], k=k,
                                           floor=get_settings().rag_knowledge_min_similarity, **kw)


async def test_bm25_finds_a_chunk_the_vector_search_misses(client, make_client, fake_ai, monkeypatch):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    target = await _add(company, "윈도우 집계 도입기", "Kafka Streams 윈도우 집계로 주문 이벤트를 5분 단위로 모았습니다. " * 3)
    await _add(company, "배치 정산", "정산 배치를 파티션 단위로 나눠 새벽 처리 시간을 줄였습니다. " * 3)
    await _embed(fake_ai)
    query = "Kafka Streams 경험이 있으신가요"
    # the vector of the query points away from every chunk: the vector search alone finds nothing above the floor
    real_embed = fake_ai.embed

    async def far_from_documents(texts):
        return [[0.0] * 1023 + [1.0] for _ in texts]
    fake_ai.embed = far_from_documents
    s = get_settings()
    monkeypatch.setattr(s, "rag_weight_bm25", 0.0)
    assert await _hybrid(company, posting, query, ai=fake_ai) == []
    monkeypatch.setattr(s, "rag_weight_bm25", 0.3)
    found = await _hybrid(company, posting, query, ai=fake_ai)
    assert found and found[0].id == target  # BM25 found it on its own, by the technology name
    fake_ai.embed = real_embed


async def test_both_lists_stay_in_the_company_and_the_posting_and_blogs_are_never_lent(client, make_client, fake_ai):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    other = uuid.uuid4()
    async with session_scope() as db:
        await db.execute(text("INSERT INTO companies (id, name) VALUES (:i, '다른 회사')"), {"i": other})
    other_posting = (await client.post("/api/recruiter/postings", json={"title": "같은 회사의 다른 공고", "job_family": "DEV"})).json()["id"]
    body = "OpenSearch 색인 설계와 형태소 분석기 교체로 후기 검색 정확도를 높였습니다."
    mine = await _add(company, "검색 개선", body)
    theirs = await _add(other, "검색 개선", body)
    sibling = await _add(company, "다른 공고 전용", body + " 다른 공고 전용.", other_posting)
    await _embed(fake_ai)
    s = get_settings()
    saved = (s.rag_weight_vector, s.rag_weight_bm25)
    for weights in ((0.0, 1.0), (1.0, 0.0), (0.7, 0.3)):  # BM25 alone, vector alone, both
        s.rag_weight_vector, s.rag_weight_bm25 = weights
        try:
            found = {c.id for c in await _hybrid(company, posting, "OpenSearch 색인 설계", ai=fake_ai)}
        finally:
            s.rag_weight_vector, s.rag_weight_bm25 = saved
        assert found == {mine}, weights
        assert theirs not in found and sibling not in found
    # the other company borrows (it has no blog), but never a TECH_BLOG chunk
    async with session_scope() as db:
        await db.execute(text("UPDATE company_knowledge SET origin = 'TECH_BLOG', source_url = 'https://blog.example/x' WHERE id = :i"),
                         {"i": mine})
    lent = await _hybrid(other, uuid.uuid4(), "OpenSearch 색인 설계", ai=fake_ai, shared=True)
    assert mine not in {c.id for c in lent}
    # and the blog company borrows nothing
    assert await _hybrid(company, posting, "OpenSearch 색인 설계", ai=fake_ai, shared=True) == []


async def test_one_method_down_leaves_the_other_and_both_down_leave_nothing(client, make_client, fake_ai, monkeypatch):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    target = await _add(company, "GPU 클러스터", "쿠버네티스 위 GPU 클러스터 스케줄링을 개선했습니다. " * 3)
    await _embed(fake_ai)
    query = "쿠버네티스 GPU 클러스터 스케줄링"

    fake_ai.down.add("embed")  # the embedding model is down: BM25 alone
    assert [c.id for c in await _hybrid(company, posting, query, ai=fake_ai)] == [target]
    fake_ai.down.discard("embed")

    async def broken(*a, **k):
        raise RuntimeError("bm25 index broken")
    monkeypatch.setattr(bm25, "search", broken)  # BM25 fails: the vector search alone
    assert [c.id for c in await _hybrid(company, posting, query, ai=fake_ai)] == [target]

    fake_ai.down.add("embed")  # both: nothing, and the caller takes its usual fallback
    assert await _hybrid(company, posting, query, ai=fake_ai) == []
    fake_ai.down.discard("embed")


async def test_the_index_follows_new_chunks(client, make_client, fake_ai):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    await _add(company, "정산", "정산 배치를 나눴습니다.")
    fake_ai.down.add("embed")
    assert await _hybrid(company, posting, "Airflow DAG 재처리", ai=fake_ai) == []
    added = await _add(company, "Airflow 운영", "Airflow DAG 재처리 절차를 문서로 남겼습니다.")
    assert [c.id for c in await _hybrid(company, posting, "Airflow DAG 재처리", ai=fake_ai)] == [added]
    fake_ai.down.discard("embed")
    assert (await q("SELECT count(*) FROM company_knowledge WHERE company_id = :c", c=company))[0][0] == 2
