"""ADR-0029: old test postings archived after a backup, and virtual test postings built from companies' public tech blogs.

The blog postings are marked virtual everywhere, have no invented deadline, name their sources, and a company's interview search
never returns another company's blog post."""
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.postings import archive, tech_blog
from app.questions import knowledge, seed_knowledge
from tests.fixtures import apply, create_posting
from tests.helpers import candidate, login, new_company, new_recruiter, operator, recruiter
from tests.test_handlers import fake_ai, q  # noqa: F401  fixture + helper

MODEL = "qwen3-embedding:0.6b"
BODY = "Kafka 컨슈머 오프셋을 옮겨 메시지를 다시 처리했습니다. 분산 서버의 Redis 리스너가 이동 요청을 받아 각 컨슈머에 전달합니다. " * 12


def _post(company: str, url: str, title: str, body: str = BODY, date: str | None = "2026-01-02") -> dict:
    return {"company_id": company, "document_type": "tech_blog", "is_synthetic": False, "source_url": url, "title": title,
            "published_at": date, "content": body}


def _config() -> dict:
    """Two companies, one posting each: a small copy of data/tech_blog_postings.json."""
    full = tech_blog.load_config()
    companies = [c for c in full["companies"] if c["key"] in ("kurly", "toss")]
    postings = [
        {"key": "kurly-x", "company": "kurly", "title": "백엔드 엔지니어 (테스트)", "job_family": "DEV", "experience_level": "경력 3년 이상",
         "duties": "주문 API 개발", "requirements": "Spring Boot", "preferred": "Kafka", "coding_enabled": False,
         "sources": ["https://helloworld.kurly.com/blog/kafka/"]},
        {"key": "toss-x", "company": "toss", "title": "플랫폼 엔지니어 (테스트)", "job_family": "DEV", "experience_level": "신입 가능",
         "duties": "쿠버네티스 운영", "requirements": "Kubernetes", "preferred": "GPU", "coding_enabled": False,
         "sources": ["https://toss.tech/article/gpu"]},
    ]
    return {"notice": full["notice"], "companies": companies, "postings": postings}


def _raw(tmp_path: Path, kurly: list[dict] | None = None, toss: list[dict] | None = None) -> Path:
    files = {"kurly": kurly or [_post("kurly", "https://helloworld.kurly.com/blog/kafka/", "Kafka 오프셋 이동")],
             "toss": toss or [_post("toss", "https://toss.tech/article/gpu", "GPU 클러스터", body=BODY.replace("Kafka", "GPU"), date=None)]}
    for key, posts in files.items():
        (tmp_path / f"{key}.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in posts) + "\n", encoding="utf-8")
    return tmp_path


async def test_blog_postings_are_virtual_listed_without_a_deadline_and_name_their_sources(client, fake_ai, tmp_path):
    report = await tech_blog.load(_raw(tmp_path), _config())
    assert report["companies"]["kurly"]["docs_added"] == 1 and report["embedded_now"] > 0
    assert {p["state"] for p in report["postings"].values()} == {"created"}

    listed = (await client.get("/api/postings")).json()
    assert {(p["company_name"], p["title"]) for p in listed} == {("컬리", "백엔드 엔지니어 (테스트)"), ("토스", "플랫폼 엔지니어 (테스트)")}
    kurly = next(p for p in listed if p["company_name"] == "컬리")
    assert kurly["is_virtual"] and kurly["is_open"] and kurly["deadline_date"] is None and kurly["deadline_at"] is None
    assert "테스트용 가상 공고" in kurly["company_intro"] and kurly["employment_type"].startswith("테스트용")
    detail = (await client.get(f"/api/postings/{kurly['id']}")).json()
    assert detail["sources"] == [{"title": "Kafka 오프셋 이동", "url": "https://helloworld.kurly.com/blog/kafka/", "published_date": "2026-01-02"}]
    toss = next(p for p in listed if p["company_name"] == "토스")
    assert (await client.get(f"/api/postings/{toss['id']}")).json()["sources"][0]["published_date"] is None  # not invented

    rows = await q("SELECT origin, source_url, company_id::text FROM company_knowledge")
    assert {r[0] for r in rows} == {"TECH_BLOG"} and all(r[1].startswith("https://") for r in rows)
    again = await tech_blog.load(_raw(tmp_path), _config())  # idempotent
    assert again["companies"]["kurly"]["docs_added"] == 0 and {p["state"] for p in again["postings"].values()} == {"unchanged"}
    assert (await q("SELECT count(*) FROM job_postings"))[0][0] == 2


async def test_a_candidate_applies_to_a_blog_posting(client, make_client, fake_ai, tmp_path):
    await tech_blog.load(_raw(tmp_path), _config())
    posting = (await client.get("/api/postings")).json()[0]
    cc = make_client()
    await candidate(cc)
    r = await apply(cc, posting["id"])
    assert r.status_code == 201, r.text


async def test_a_post_from_another_blog_stops_the_load(fake_ai, tmp_path):
    stray = [_post("kurly", "https://toss.tech/article/gpu", "남의 글")]  # a Toss URL in Kurly's file
    with pytest.raises(ValueError, match="is not on"):
        await tech_blog.load(_raw(tmp_path, kurly=stray), _config())
    assert (await q("SELECT count(*) FROM company_knowledge"))[0][0] == 0


async def test_a_posting_source_must_be_a_post_of_its_company(fake_ai, tmp_path):
    cfg = _config()
    cfg["postings"][0]["sources"] = ["https://toss.tech/article/gpu"]
    with pytest.raises(ValueError, match="sources not among kurly"):
        await tech_blog.load(_raw(tmp_path), cfg)


async def test_blog_companies_never_see_another_companys_documents(client, make_client, fake_ai, tmp_path):
    """A Kurly posting gets Kurly's posts only; the borrow-from-others path is closed for blog companies, and no company borrows
    a blog post either."""
    report = await tech_blog.load(_raw(tmp_path), _config())
    kurly, toss = (uuid.UUID(report["companies"][k]["company_id"]) for k in ("kurly", "toss"))
    kurly_posting = uuid.UUID(report["postings"]["kurly-x"]["id"])
    query = [BODY.replace("Kafka", "GPU")]  # Toss's words: only the scope keeps them out
    async with session_scope() as db:
        own = await knowledge.search_many(db, fake_ai, MODEL, company_id=kurly, posting_id=kurly_posting, queries=query, k=10, floor=-1)
        borrowed = await knowledge.search_many(db, fake_ai, MODEL, company_id=kurly, posting_id=kurly_posting, queries=query, k=10,
                                               floor=-1, shared=True)
    assert own and {c.doc_id for c in own} == {tech_blog.doc_id("https://helloworld.kurly.com/blog/kafka/")}
    assert borrowed == []

    other_company, _ = await recruiter(client)  # an ordinary company without documents
    p = await create_posting(client)
    async with session_scope() as db:
        cases = await knowledge.search_many(db, fake_ai, MODEL, company_id=uuid.UUID(other_company), posting_id=uuid.UUID(p["id"]),
                                            queries=query, k=10, floor=-1, shared=True)
    assert cases == []  # Kurly's and Toss's posts are never lent out
    assert {r[0] for r in await q("SELECT DISTINCT company_id FROM company_knowledge")} == {kurly, toss}


async def test_old_test_postings_are_backed_up_archived_and_restorable(client, make_client, fake_ai, tmp_path):
    company_id, _ = await recruiter(client)
    old = await create_posting(client, title="AI 백엔드 개발자 (질문 테스트)")
    unclear = await create_posting(client, title="백엔드 개발자")
    cc = make_client()
    await candidate(cc)
    assert (await apply(cc, old["id"])).status_code == 201
    await tech_blog.load(_raw(tmp_path), _config())

    async with session_scope() as db:
        rows = await archive.survey(db)
    by_title = {r["title"]: r for r in rows}
    assert by_title["AI 백엔드 개발자 (질문 테스트)"]["class"] == "TEST"
    assert by_title["백엔드 개발자"]["class"] == "TEST"  # same company has a test-titled posting: evidence, not a guess
    assert by_title["백엔드 엔지니어 (테스트)"]["class"] == "VIRTUAL_TECH_BLOG"

    async with session_scope() as db:
        backup, done = await archive.apply(db, await archive.survey(db), set(), tmp_path / "backups")
    assert {r["title"] for r in done} == {"AI 백엔드 개발자 (질문 테스트)", "백엔드 개발자"}
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert {r["id"] for r in saved["rows"]} == {old["id"], unclear["id"]} and all(r["status"] == "OPEN" for r in saved["rows"])

    listed = {p["id"] for p in (await client.get("/api/postings")).json()}
    assert old["id"] not in listed and unclear["id"] not in listed and len(listed) == 2  # only the blog postings
    assert (await client.get(f"/api/postings/{unclear['id']}")).status_code == 404
    applied = await cc.get(f"/api/postings/{old['id']}")
    assert applied.status_code == 200 and not applied.json()["is_open"]  # the applicant keeps the history, cannot start anew
    assert (await q("SELECT count(*) FROM applications"))[0][0] == 1
    state = await q("SELECT status, close_reason, hidden_by FROM job_postings WHERE id = :i", i=old["id"])
    assert state == [("CLOSED", "ARCHIVED", "ARCHIVE")]

    oc = make_client()
    await operator(oc)
    r = await oc.post(f"/api/operator/postings/{old['id']}/show")
    assert r.status_code == 409 and r.json()["error"]["code"] == "POSTING_ARCHIVED"

    async with session_scope() as db:
        assert await archive.restore(db, backup) == 2
    assert old["id"] in {p["id"] for p in (await client.get("/api/postings")).json()}
    assert (await q("SELECT status, close_reason, hidden_by FROM job_postings WHERE id = :i", i=old["id"])) == [("OPEN", None, None)]


async def test_a_posting_without_evidence_is_left_alone(client, fake_ai, tmp_path):
    company_id = await new_company()
    await login(client, await new_recruiter(company_id, email="hr@company-a.co.kr"), role="RECRUITER")  # not a test-only domain
    p = await create_posting(client, title="백엔드 개발자")
    async with session_scope() as db:
        rows = await archive.survey(db)
        assert [r["class"] for r in rows] == ["UNCLEAR"]
        _, done = await archive.apply(db, rows, set(), tmp_path)
    assert done == [] and p["id"] in {x["id"] for x in (await client.get("/api/postings")).json()}


async def test_old_seeds_refuse_to_run_after_archiving(client, fake_ai, tmp_path):
    await recruiter(client)
    await create_posting(client, title="화면 확인용 임시 공고")
    async with session_scope() as db:
        await archive.apply(db, await archive.survey(db), set(), tmp_path)
    with pytest.raises(SystemExit, match="보관 처리"):
        await seed_knowledge.main()
    from scripts.seed_demo import seed_demo
    from app.main import create_app
    with pytest.raises(SystemExit, match="보관 처리"):
        await seed_demo(create_app(), title="백엔드 개발자")
    assert (await q("SELECT count(*) FROM companies WHERE name LIKE '%(가상)' OR name = '이투이테크'"))[0][0] == 0


async def test_only_a_virtual_posting_may_be_open_without_a_deadline(client, fake_ai):
    await recruiter(client)
    p = await create_posting(client)
    with pytest.raises(Exception, match="job_postings_check"):
        async with session_scope() as db:
            await db.execute(text("UPDATE job_postings SET deadline_at = NULL WHERE id = :i"), {"i": p["id"]})


async def test_an_embedding_timeout_is_retried_and_what_is_left_is_reported(fake_ai, tmp_path, monkeypatch):
    from app.ai.client import AIUnavailable

    calls = {"n": 0}
    real = fake_ai.embed

    async def flaky(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AIUnavailable("ReadTimeout")
        return await real(texts)

    monkeypatch.setattr(fake_ai, "embed", flaky)
    monkeypatch.setattr(tech_blog, "EMBED_RETRY_SEC", 0)
    report = await tech_blog.load(_raw(tmp_path), _config())
    assert report["embedding_pending"] == 0 and report["embedded_now"] > 0 and calls["n"] >= 2

    async def down(texts):
        raise AIUnavailable("down")

    monkeypatch.setattr(fake_ai, "embed", down)
    async with session_scope() as db:
        await db.execute(text("UPDATE company_knowledge SET embedding = NULL"))
    total = (await q("SELECT count(*) FROM company_knowledge"))[0][0]
    assert total > 0 and (await tech_blog.load(_raw(tmp_path), _config()))["embedding_pending"] == total
