"""The company knowledge base behind interview questions (ADR-0020): search never leaves the company and the posting, questions name
their sources, a company without documents gets job questions and no made-up facts, and follow-ups see earlier answers."""
import uuid

import pytest
from sqlalchemy import text

from app.ai.tasks.questions import unsupported_terms
from app.ai.tasks.followup import FollowupDecide
from app.core.db import session_scope, sessionmaker
from app.models import CompanyKnowledge
from app.questions import knowledge, seed_knowledge
from tests.helpers import login
from tests.test_handlers import drain, fake_ai, prepare_and_compose, q, setup_app  # noqa: F401  fixtures + helpers

MODEL = "qwen3-embedding:0.6b"
DOC = "정산 배치 재설계 프로젝트는 가맹점별 파티션 처리로 새벽 정산을 앞당기고 멱등 키로 중복 정산을 막습니다."


async def _add(company_id, title, body, posting_id=None) -> uuid.UUID:
    async with session_scope() as db:
        row = CompanyKnowledge(company_id=company_id, posting_id=posting_id, doc_id=uuid.uuid4(), doc_type="PROJECT", title=title, text=body, origin="SYNTHETIC")
        db.add(row)
        await db.flush()
        return row.id


async def _embed(ai) -> None:
    async with sessionmaker()() as db:
        await knowledge.embed_pending(db, ai, MODEL)


async def _posting_of(app_id: str) -> tuple:
    return (await q("SELECT company_id, posting_id FROM applications WHERE id = :a", a=app_id))[0]


async def _search(ai, company_id, posting_id, query=DOC, k=5):
    async with session_scope() as db:
        return await knowledge.search(db, ai, MODEL, company_id=company_id, posting_id=posting_id, query=query, k=k)


async def test_search_never_leaves_the_company_or_the_posting(client, make_client, fake_ai):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    other = uuid.uuid4()
    async with session_scope() as db:
        await db.execute(text("INSERT INTO companies (id, name) VALUES (:i, '다른 회사')"), {"i": other})
    other_posting = (await client.post("/api/recruiter/postings", json={"title": "같은 회사의 다른 공고", "job_family": "DEV"})).json()["id"]
    mine = await _add(company, "정산 배치 재설계", DOC)
    theirs = await _add(other, "정산 배치 재설계", DOC)  # the same words: only the scope can tell them apart
    own_posting = await _add(company, "이 공고 전용", DOC + " 이 공고 전용.", posting)
    sibling = await _add(company, "다른 공고 전용", DOC + " 다른 공고 전용.", other_posting)
    await _embed(fake_ai)

    found = {c.id for c in await _search(fake_ai, company, posting)}
    assert found == {mine, own_posting}
    assert theirs not in found and sibling not in found
    assert {c.id for c in await _search(fake_ai, other, posting)} == {theirs}
    with pytest.raises(ValueError):
        await _search(fake_ai, None, posting)


async def test_a_chunk_cannot_name_another_companys_posting(client, make_client, fake_ai):
    _, app_id, _ = await setup_app(client, make_client)
    _, posting = await _posting_of(app_id)
    other = uuid.uuid4()
    async with session_scope() as db:
        await db.execute(text("INSERT INTO companies (id, name) VALUES (:i, '다른 회사')"), {"i": other})
    with pytest.raises(Exception, match="company_knowledge"):
        await _add(other, "남의 공고", DOC, posting)


async def test_questions_are_made_from_the_companys_documents_and_name_their_sources(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client, title="결제 백엔드 개발자", duties=DOC)
    company, _ = await _posting_of(app_id)
    chunk = await _add(company, "정산 배치 재설계", DOC)
    await _embed(fake_ai)
    qs = await prepare_and_compose(cand, app_id)

    gen = [c for c in fake_ai.calls if c.task == "question_generate"]
    assert all(str(chunk) in [k["id"] for k in c.input["knowledge"]] for c in gen)
    assert "회사 자료" in gen[0].messages[0][-1]["content"]
    motivation = next(x for x in qs if x.slot_type == "MOTIVATION")
    assert motivation.source == "AI" and motivation.grounding["sources"] == [{"type": "KNOWLEDGE", "id": str(chunk)}]
    assert "‘정산 배치 재설계’" in motivation.text and "경험" in motivation.text  # the sentence is fixed, the document is the model's pick
    assert motivation.bank_question_id is None  # a question from a document does not reach other postings through the bank
    resume_q = next(x for x in qs if x.source == "AI" and x.slot_type == "PROJECT")
    assert resume_q.grounding["sources"][0]["type"] == "RESUME_SNAPSHOT" and resume_q.grounding["sources"][0]["id"] == app_id
    assert "retrieved" in resume_q.grounding
    assert all(x.grounding and x.grounding["sources"] for x in qs)


async def test_no_company_documents_means_a_job_question_from_the_posting(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client, title="결제 백엔드 개발자", duties="가맹점 정산 API 개발")
    async with session_scope() as db:
        await db.execute(text("UPDATE companies SET intro = '확인되지 않은 회사 소개 문장입니다.' "
                              "WHERE id = (SELECT company_id FROM applications WHERE id = :a)"), {"a": app_id})
    qs = await prepare_and_compose(cand, app_id)
    motivation = next(x for x in qs if x.slot_type == "MOTIVATION")
    assert motivation.source == "AI" and motivation.grounding["replaced"] == "NO_COMPANY_KNOWLEDGE"
    assert motivation.grounding["sources"][0]["type"] == "POSTING"
    gen = next(c for c in fake_ai.calls if c.task == "question_generate" and c.input.get("company_intro") == "")
    assert gen.input["kind"] == "JOB" and gen.input["knowledge"] == []
    assert "확인되지 않은" not in motivation.text


async def test_generated_questions_may_not_state_what_the_sources_do_not_have(fake_ai):
    assert unsupported_terms("Kafka 파티션을 300개로 늘린 이유는 무엇입니까?", "Kafka로 정산 시스템에 전달합니다.") == ["300"]
    assert unsupported_terms("Redis 키 보관 기간은?", "redis에 요청 키를 보관합니다.") == []
    inp = {"kind": "COMPANY", "n": 1, "posting": {"title": "결제 백엔드"}, "posting_id": "p", "company_id": "c",
           "knowledge": [{"id": "k1", "title": "정산 배치 재설계", "text": DOC}]}
    fake_ai.responses["question_generate"] = lambda i, m: {"questions": [
        {"text": "정산 배치에 Kafka를 쓰신다면 어떻게 하시겠습니까?", "basis_quote": "가맹점별 파티션 처리"},  # invented technology
        {"text": "결제 백엔드 지원 이유는 무엇입니까?", "basis_quote": "결제 백엔드"},  # not from a document
        {"text": "가맹점별 파티션 처리에서 중복 정산은 어떻게 막으시겠습니까?", "basis_quote": "가맹점별 파티션 처리"}]}
    r = await fake_ai.llm("question_generate", inp, lane="INTERACTIVE")
    assert [x["source"]["id"] for x in r.output["questions"]] == ["k1"]
    assert "중복 정산" in r.output["questions"][0]["text"]


def test_document_questions_ask_the_candidate_not_about_the_company():
    from app.ai.tasks.questions import asks_the_candidate

    assert asks_the_candidate("가맹점별 파티션 처리에서 중복 정산은 어떻게 막으시겠습니까?")
    assert asks_the_candidate("정산 배치에 본인의 어떤 경험을 살릴 수 있다고 보십니까?")
    assert not asks_the_candidate("이중화 방식에서 대사 작업은 어떻게 작동하나요?")  # only the company knows
    assert not asks_the_candidate("무중단 배포 방식을 운영하시겠습니까?")  # yes or no
    assert not asks_the_candidate("두 개로 이중화하신 시스템에서 어떻게 대응하시겠습니까?")  # their work, not the candidate's


async def test_followup_sees_earlier_answers_and_records_its_sources(fake_ai):
    previous = "캐시 무효화 전략을 직접 설계해 지연을 줄였습니다."
    inp = {"question": "최근 프로젝트를 말씀해 주세요.", "question_type": "PROJECT", "answer": "정산 작업의 멱등 처리를 맡았습니다. 재처리 때 중복을 막았습니다.",
           "answer_id": "a2", "application_id": "app", "followups_left": 2, "resume_evidence": [], "asked": [],
           "previous_answers": [{"id": "a1", "text": previous}],
           "company_context": [{"id": "k1", "title": "정산 배치 재설계", "text": DOC}]}
    fake_ai.responses["followup_decide"] = lambda i, m: {"decision": "ASK", "reason": "DEEPEN", "anchor": {"source": "previous", "quote": "캐시 무효화 전략"},
                                                         "question": "캐시 무효화 전략을 정산 배치 재설계에 어떻게 적용하시겠습니까?"}
    r = await fake_ai.llm("followup_decide", inp, lane="RT")
    assert r.status == "OK"
    assert r.output["sources"] == [{"type": "ANSWER", "id": "a1"}, {"type": "KNOWLEDGE", "id": "k1"}]
    assert "앞선 답변" in fake_ai.calls[-1].messages[0][-1]["content"] and "회사 자료" in fake_ai.calls[-1].messages[0][-1]["content"]

    # a word that merely occurs in the document ("처리") is not use of the document
    assert FollowupDecide._knowledge_used(inp, "멱등 처리의 효과와 앞당긴 방식은 무엇입니까?") == []

    # the same question again is asked to be replaced, then skipped; a technology nobody mentioned is rejected
    again = {**inp, "asked": ["캐시 무효화 전략을 정산 배치 재설계에 어떻게 적용하시겠습니까?"]}
    assert (await fake_ai.llm("followup_decide", again, lane="RT")).status == "INVALID"
    fake_ai.responses["followup_decide"] = lambda i, m: {"decision": "ASK", "reason": "DEEPEN", "anchor": {"source": "answer", "quote": "멱등 처리"},
                                                         "question": "멱등 처리에 Kafka를 어떻게 쓰셨습니까?"}
    assert (await fake_ai.llm("followup_decide", inp, lane="RT")).status == "INVALID"


async def test_recruiter_manages_only_the_companys_own_documents(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client, title="공고")
    _, posting = await _posting_of(app_id)
    r = await client.post("/api/recruiter/knowledge", json={"doc_type": "TECH", "title": "기술 스택", "text": DOC + "\n" + "두 번째 문단입니다. " * 60,
                                                            "posting_id": str(posting)})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["chunks"] == 2 and doc["indexed"] is False
    await drain()
    assert (await client.get("/api/recruiter/knowledge")).json()[0]["indexed"] is True

    from tests.helpers import recruiter

    outsider = make_client()
    await recruiter(outsider)
    assert (await outsider.get("/api/recruiter/knowledge")).json() == []
    assert (await outsider.delete(f"/api/recruiter/knowledge/{doc['doc_id']}")).status_code == 404
    assert (await outsider.post("/api/recruiter/knowledge", json={"doc_type": "TECH", "title": "남의 공고", "text": DOC, "posting_id": str(posting)})).status_code == 404
    assert (await cand.get("/api/recruiter/knowledge")).status_code == 403
    assert (await client.delete(f"/api/recruiter/knowledge/{doc['doc_id']}")).status_code == 204
    assert await q("SELECT count(*) FROM company_knowledge") == [(0,)]
    assert await q("SELECT count(*) FROM audit_logs WHERE action LIKE 'KNOWLEDGE_%'") == [(2,)]


async def test_search_log_has_ids_and_scores_but_no_text(client, make_client, fake_ai, caplog):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    await _add(company, "정산 배치 재설계", DOC)
    await _embed(fake_ai)
    with caplog.at_level("INFO", logger="app.questions"):
        await _search(fake_ai, company, posting, query=DOC + " 이력서에 적힌 비밀 문장")
    rec = next(r for r in caplog.records if r.getMessage() == "knowledge_search" or getattr(r, "fields", {}).get("event") == "knowledge_search")
    assert "비밀" not in str(rec.fields) and DOC not in str(rec.fields) and rec.fields["hits"] == 1


async def test_virtual_companies_load_once_and_are_marked(fake_ai):
    rows = seed_knowledge.load_file()
    assert len(rows) >= 10 and all("(가상)" in r["name"] for r in rows)
    assert all(len(r["docs"]) >= 5 and {d["type"] for d in r["docs"]} == {"PROJECT", "TECH"} for r in rows)
    assert all(g["gold"] in {d["title"] for d in r["docs"]} for r in rows for g in r["eval"])
    async with session_scope() as db:
        first = await seed_knowledge.seed(db, rows[0])
    async with session_scope() as db:
        assert await seed_knowledge.seed(db, rows[0]) == first  # again: nothing new
    n = len(rows[0]["docs"]) + sum(len(knowledge.split_document(d["text"])) for d in seed_knowledge.profile_docs(rows[0]))
    assert all(len(seed_knowledge.profile_docs(r)) == 7 for r in rows)
    assert await q("SELECT count(*), min(origin), max(origin) FROM company_knowledge") == [(n, "SYNTHETIC", "SYNTHETIC")]
    login = await q("SELECT status FROM users WHERE email LIKE 'synthetic-%'")
    assert login == [("DISABLED",)]


async def _login_virtual(client, rows):
    async with session_scope() as db:
        for r in rows[:2]:
            await seed_knowledge.seed(db, r)
    async with session_scope() as db:
        email, password = await seed_knowledge.demo_login(db, rows[0]["key"])
    assert await q("SELECT email FROM users WHERE email LIKE 'synthetic-%' AND status = 'ACTIVE'") == [(email,)]
    await login(client, email, role="RECRUITER", password=password)


async def test_posting_draft_comes_from_own_company_documents_and_saves_nothing(client, fake_ai):
    rows = seed_knowledge.load_file()
    await _login_virtual(client, rows)
    await _embed(fake_ai)
    postings = await q("SELECT count(*) FROM job_postings")
    fake_ai.calls.clear()
    res = await client.post("/api/recruiter/postings/ai-draft", json={"title": rows[0]["docs"][0]["text"][:190], "job_family": "DEV"})
    assert res.status_code == 200, res.text
    assert res.json()["duties"] and res.json()["sources"]
    sent = next(c for c in fake_ai.calls if c.task == "posting_draft").input["knowledge"]
    owners = await q("SELECT DISTINCT company_id FROM company_knowledge WHERE id = ANY(:i)", i=[uuid.UUID(x["id"]) for x in sent])
    assert [str(o[0]) for o in owners] == [str(seed_knowledge._id("company", rows[0]["key"]))]
    assert await q("SELECT count(*) FROM job_postings") == postings
    assert await q("SELECT count(*) FROM audit_logs WHERE action = 'POSTING_AI_DRAFT'") == [(1,)]


async def test_posting_draft_needs_documents_and_a_recruiter(client, make_client, fake_ai):
    from tests.helpers import candidate, recruiter

    await recruiter(client)
    res = await client.post("/api/recruiter/postings/ai-draft", json={"title": "백엔드 개발자"})
    assert res.status_code == 409, res.text
    cand = make_client()
    await candidate(cand)
    assert (await cand.post("/api/recruiter/postings/ai-draft", json={"title": "백엔드 개발자"})).status_code == 403
    fake_ai.down.add("embed")
    assert (await client.post("/api/recruiter/postings/ai-draft", json={"title": "백엔드 개발자"})).status_code == 503


def test_posting_draft_drops_lines_the_documents_do_not_support():
    from app.ai.tasks.posting_draft import DraftOut, PostingDraft

    inp = {"title": "서버 개발자", "knowledge": [{"title": "기술", "text": "Go와 PostgreSQL로 API를 만듭니다."}]}
    out = DraftOut(duties=["Go로 API를 개발합니다.", "Kafka를 운영합니다."], requirements=["PostgreSQL 사용 경험.", "3년 이상 경력."], preferred=["남성 우대."])
    assert "Kafka" in PostingDraft().check(inp, out, {}, False).feedback["terms"]
    assert PostingDraft().check(inp, out, {}, True).output == {"duties": ["Go로 API를 개발합니다."], "requirements": ["PostgreSQL 사용 경험."], "preferred": []}


def test_long_documents_are_split_within_the_column_limit():
    parts = knowledge.split_document("첫 문단.\n\n" + "긴 문장입니다. " * 200 + "\n" + "x" * 1500)
    assert len(parts) > 2 and all(0 < len(p) <= knowledge.CHUNK_CHARS for p in parts)


async def test_search_many_stays_in_scope_and_skips_used_documents(client, make_client, fake_ai):
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    other = uuid.uuid4()
    async with session_scope() as db:
        await db.execute(text("INSERT INTO companies (id, name) VALUES (:i, '다른 회사')"), {"i": other})
    mine = await _add(company, "정산 배치 재설계", DOC)
    second = await _add(company, "결제 승인 서버", DOC + " 승인 서버도 함께 다룹니다.")
    await _add(other, "남의 문서", DOC)
    await _embed(fake_ai)

    async def many(**kw):
        async with session_scope() as db:
            return await knowledge.search_many(db, fake_ai, MODEL, company_id=company, posting_id=posting, queries=[DOC, "정산 배치"], k=5, floor=0.1, **kw)

    found = await many()
    assert {c.id for c in found} == {mine, second} and all(c.doc_type == "PROJECT" for c in found)
    first_doc = next(c.doc_id for c in found if c.id == mine)
    assert {c.id for c in await many(exclude_docs=[first_doc])} == {second}
    with pytest.raises(ValueError):
        async with session_scope() as db:
            await knowledge.search_many(db, fake_ai, MODEL, company_id=None, posting_id=posting, queries=[DOC], k=5, floor=0.1)


async def test_a_company_without_documents_borrows_other_companies_documents_as_job_cases(client, make_client, fake_ai):
    from app.ai.tasks import TASKS
    _, app_id, _ = await setup_app(client, make_client)
    company, posting = await _posting_of(app_id)
    other = uuid.uuid4()
    async with session_scope() as db:
        await db.execute(text("INSERT INTO companies (id, name) VALUES (:i, '다른 회사')"), {"i": other})
    theirs = await _add(other, "정산 배치 재설계", DOC)
    await _embed(fake_ai)
    async with session_scope() as db:
        own = await knowledge.search_many(db, fake_ai, MODEL, company_id=company, posting_id=posting, queries=[DOC], k=5, floor=0.1)
        cases = await knowledge.search_many(db, fake_ai, MODEL, company_id=company, posting_id=posting, queries=[DOC], k=5, floor=0.1, shared=True)
    assert own == [] and [c.id for c in cases] == [theirs] and cases[0].shared and cases[0].as_input()["shared"] is True
    # T2 shows them apart from the company's own documents and drops a question that tells them as this company's work
    t2 = TASKS["question_generate"]
    inp = {"kind": "JOB", "n": 2, "posting": {"title": "백엔드"}, "posting_id": str(posting), "knowledge": [cases[0].as_input()]}
    user, _ = t2.build(inp, "n0")
    assert "직무 참고 사례" in user and "회사 자료:" not in user
    quote = DOC.split(".")[0]
    out = t2.out_model.model_validate({"questions": [
        {"text": f"귀사에서 {quote}을 맡는다면 무엇부터 어떻게 하시겠습니까?", "basis_quote": quote},
        {"text": f"{quote}을 해야 한다면 무엇부터 어떻게 하시겠습니까?", "basis_quote": quote}]})
    kept = t2.check(inp, out, {}, True).output["questions"]
    assert len(kept) <= 1 and all("귀사" not in q["text"] for q in kept)
    assert all(q["source"].get("shared") for q in kept)


async def test_one_search_per_duty_line():
    from app.questions.rag_graph import posting_queries

    jobs = posting_queries("JOB", "개발자", "소개", "정산 API 개발\n승인 서버 운영\n장애 대응 문서화", "SQL", None)
    assert jobs[:3] == ["개발자 정산 API 개발", "개발자 승인 서버 운영", "개발자 장애 대응 문서화"] and len(jobs) == 4


def _chunk(doc: uuid.UUID, title: str, body: str) -> knowledge.Chunk:
    return knowledge.Chunk(uuid.uuid4(), title, body, 0.8, doc, "PROJECT")


async def test_review_wants_concrete_questions_from_different_documents_until_the_last_round():
    from app.questions.rag_graph import _next, _review

    a, b = _chunk(uuid.uuid4(), "정산 배치 재설계", DOC), _chunk(uuid.uuid4(), "결제 승인 서버", "결제 승인 서버의 장애 전환을 줄입니다.")
    seen = {str(x.id): x for x in (a, b)}

    def q(chunk, words):
        return {"text": words, "source": {"type": "KNOWLEDGE", "id": str(chunk.id)}}

    vague, good, same_doc = q(a, "어떤 경험을 살리시겠습니까?"), q(a, "정산 배치 재설계에서 멱등 키를 어떻게 설계하시겠습니까?"), q(a, "정산 배치 파티션을 어떻게 나누시겠습니까?")
    state = {"n": 2, "round": 1, "accepted": [], "used_docs": [], "chunks": [a, b], "seen": seen, "candidates": [vague, good, same_doc]}
    first = await _review(state, {})
    assert first["accepted"] == [good]  # vague and second-from-the-same-document wait for another search
    assert _next({**state, **first}) == "retrieve"
    last = await _review({**state, "round": 2}, {})
    assert len(last["accepted"]) == 2 and _next({**state, "round": 2, **last}) != "retrieve"  # a grounded question beats the bank


async def test_motivation_sentence_uses_a_project_name_but_not_a_company_document(client, make_client, fake_ai):
    _, app_id, cand = await setup_app(client, make_client, title="결제 백엔드 개발자", duties=DOC)
    company, posting = await _posting_of(app_id)
    async with session_scope() as db:
        db.add(CompanyKnowledge(company_id=company, doc_id=uuid.uuid4(), doc_type="COMPANY", title="회사 소개", text=DOC, origin="SYNTHETIC"))
    await _embed(fake_ai)
    qs = await prepare_and_compose(cand, app_id)
    motivation = next(x for x in qs if x.slot_type == "MOTIVATION")
    assert "‘회사 소개’" not in motivation.text and motivation.grounding["sources"][0]["type"] == "KNOWLEDGE"


async def test_seed_rewrites_an_invented_posting_only_while_nobody_has_applied(fake_ai):
    rows = seed_knowledge.load_file()
    async with session_scope() as db:
        pid = await seed_knowledge.seed(db, rows[0])
        await db.execute(text("UPDATE job_postings SET duties = '옛 내용' WHERE id = :p"), {"p": pid})
    async with session_scope() as db:
        await seed_knowledge.seed(db, rows[0])
    assert await q("SELECT duties, employment_type FROM job_postings WHERE id = :p", p=pid) == [(rows[0]["posting"]["duties"], "정규직")]
    assert len(rows[0]["posting"]["duties"].splitlines()) >= 4
