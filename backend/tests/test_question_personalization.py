"""Job and motivation questions that know the candidate (ADR-0032): T2 sees the resume sentences about this job, may connect a
question to one of them, and may not say the candidate did what the resume does not have. The code-made question connects the
posting's work to the resume's experience instead of copying the posting's line. All names and resumes are made up."""
import uuid
from types import SimpleNamespace

from app.ai.tasks.questions import GenOut, QuestionGenerate, resume_link
from app.questions import compose as C
from app.questions import quality, resume_match
from app.resumes.schemas import ResumeContent

POSTING = SimpleNamespace(title="백엔드 엔지니어", duties="주문 이벤트를 Kafka로 비동기 처리하는 시스템을 설계하고 개발합니다",
                          requirements="대용량 트래픽 처리 경험이 있으신 분", preferred="Redis 캐시 운영 경험 우대",
                          experience_level=None, job_family="DEV", id=uuid.uuid4())
SNAP_CACHE = ResumeContent(name="가상", career=[{"company": "가상커머스", "role": "백엔드 개발자",
                                                "description": "주문 API 응답 지연을 Redis 캐시로 450ms에서 120ms로 줄였습니다."}],
                           projects=[{"title": "배송 추적 서비스", "description": "배송 상태 API를 만들고 장애 시 재처리 큐를 설계했습니다."}],
                           skills=["Java", "Redis"]).model_dump()
SNAP_DATA = ResumeContent(name="가상", education=[{"school": "가상대학교"}],
                          projects=[{"title": "추천 모델 실험", "description": "사용자 로그로 추천 모델을 학습하고 정확도를 비교했습니다."}],
                          skills=["Python", "PyTorch"]).model_dump()
SNAP_DETAILS = {**ResumeContent(name="가상", education=[{"school": "가상대학교"}],
                                projects=[{"title": "프로젝트 A | 결제 알림 봇", "description": "결제 알림 봇입니다."}]).model_dump(),
                "details": [{"heading": "대표 프로젝트", "kind": "projects", "row": "projects:프로젝트 A | 결제 알림 봇", "how": "similar",
                             "lines": [3, 5], "text": "결제 실패 이벤트를 큐로 받아 비동기 처리하고 중복 알림을 막았습니다."}]}


def _ctx(snapshot: dict) -> C._Ctx:
    return C._Ctx(SimpleNamespace(resume_snapshot=snapshot, id=uuid.uuid4()), POSTING, SimpleNamespace(intro="", id=uuid.uuid4()),
                  SimpleNamespace(question_plan=None, custom_question=None, id=uuid.uuid4()), None)


def _posting_text() -> str:
    return "\n".join([POSTING.title, POSTING.duties, POSTING.requirements, POSTING.preferred])


# ── the excerpts ───────────────────────────────────────────────────────────────
def test_the_excerpts_are_the_resumes_own_sentences_about_this_job_one_per_row():
    ex = resume_match.relevant(SNAP_CACHE, _posting_text())
    assert ex and all(e.text in str(SNAP_CACHE) for e in ex)  # word for word
    assert ex[0].ref == "experience[0]" and "Redis 캐시" in ex[0].text and ex[0].where == "가상커머스 백엔드 개발자 업무에서"
    assert len({e.scene for e in ex}) == len(ex)


def test_a_block_read_from_the_file_is_an_excerpt_of_its_row():
    ex = resume_match.relevant(SNAP_DETAILS, "결제 이벤트를 비동기 처리")
    assert ex[0].ref == "details[0]" and ex[0].scene == "결제 알림 봇" and ex[0].where == "결제 알림 봇 프로젝트에서"


def test_nothing_shared_means_no_excerpt():
    assert resume_match.relevant(SNAP_CACHE, "회계 결산과 세무 신고") == []
    assert resume_match.relevant({}, _posting_text()) == []


def test_two_resumes_give_the_same_posting_different_excerpts():
    a = {e.text for e in resume_match.relevant(SNAP_CACHE, _posting_text())}
    b = {e.text for e in resume_match.relevant(SNAP_DATA, _posting_text() + " 추천 모델")}
    assert a and b and not a & b


# ── T2 ─────────────────────────────────────────────────────────────────────────
def _inp(snapshot: dict, **kw) -> dict:
    from app.questions.rag_graph import resume_input
    c = SimpleNamespace(app=SimpleNamespace(resume_snapshot=snapshot))
    return {"kind": "JOB", "n": 3, "posting_id": str(POSTING.id), "company_id": "c",
            "posting": {"title": POSTING.title, "duties": POSTING.duties, "requirements": POSTING.requirements,
                        "preferred": POSTING.preferred}, "level": "JUNIOR", **resume_input(c, [_posting_text()]), **kw}


def test_t2_sees_the_excerpts_in_their_own_fence_and_nothing_else_of_the_resume():
    user, _ = QuestionGenerate().build(_inp(SNAP_CACHE), "n0nce")
    assert "지원자 이력서의 경험" in user and "Redis 캐시로 450ms에서 120ms로 줄였습니다" in user
    assert "가상" + "커머스" in user  # the row's name, as the candidate wrote it
    user, _ = QuestionGenerate().build(_inp({}), "n0nce")
    assert "지원자 이력서의 경험" not in user


def test_a_question_saying_the_candidate_used_what_the_resume_does_not_have_is_dropped():
    inp = _inp(SNAP_CACHE)
    assert resume_link(inp, "Kafka를 운영해 보신 경험으로 주문 이벤트 처리를 맡는다면 무엇부터 확인하시겠습니까?", "")[0] == ["ASSUMED_EXPERIENCE"]
    assert resume_link(_inp({}), "대용량 트래픽을 처리하셨던 방식으로 어떻게 하시겠습니까?", "")[0] == ["ASSUMED_EXPERIENCE"]


def test_a_question_connecting_the_resumes_own_sentence_is_kept_with_its_link():
    inp = _inp(SNAP_CACHE)
    bad, link = resume_link(inp, "Redis 캐시로 응답 지연을 줄이셨는데, 주문 이벤트를 비동기로 처리한다면 무엇부터 확인하시겠습니까?",
                            "Redis 캐시로 450ms에서 120ms로 줄였습니다")
    assert bad == [] and link["ref"] == "experience[0]" and link["how"] == "quote"


def test_a_situation_from_the_posting_is_not_an_assumed_experience():
    bad, link = resume_link(_inp(SNAP_CACHE), "주문 이벤트를 Kafka로 비동기 처리하는 시스템을 맡는다면 무엇부터 확인하시겠습니까?", "")
    assert bad == []


def test_t2_check_records_the_link_and_drops_the_assumption():
    inp = _inp(SNAP_CACHE)
    p = GenOut(questions=[
        {"text": "Redis 캐시로 응답 지연을 줄이셨는데, 대용량 트래픽 처리를 맡는다면 그 경험을 어떻게 살리시겠습니까?",
         "basis_quote": "대용량 트래픽 처리 경험이 있으신 분", "resume_quote": "Redis 캐시로 450ms에서 120ms로 줄였습니다"},
        {"text": "Kafka를 운영해 보신 경험으로 주문 이벤트 처리를 맡는다면 무엇부터 확인하시겠습니까?",
         "basis_quote": "주문 이벤트를 Kafka로 비동기 처리하는 시스템을 설계하고 개발합니다", "resume_quote": ""}])
    out = QuestionGenerate().check(inp, p, {}, True).output
    assert [q["resume"]["ref"] for q in out["questions"]] == ["experience[0]"]
    assert out["failed"][0]["codes"] == ["ASSUMED_EXPERIENCE"]


# ── the code-made question ─────────────────────────────────────────────────────
def test_the_code_made_job_question_joins_the_resumes_experience_and_the_postings_work():
    c = _ctx(SNAP_CACHE)
    C.posting_fallback(c, "JOB", [(4, "JOB_SKILL"), (5, "JOB_SKILL")])
    texts = [p.text for p in c.picked]
    assert texts[0].startswith("가상커머스 백엔드 개발자 업무에서") and texts[0].endswith("어떻게 살리시겠습니까?")
    assert len(texts[0]) <= C.MAX_LEN["JUNIOR"]
    assert all(f"‘{line}’" not in t for t in texts for line in C.posting_lines(POSTING))  # the posting's line is not copied
    assert c.picked[0].grounding["resume"]["link"]["ref"] == "experience[0]"
    assert c.picked[1].grounding["resume"]["link"] is None or c.picked[1].grounding["resume"]["link"]["scene"] != "가상커머스 백엔드 개발자"
    for p in c.picked:
        assert quality.judge(c, p) == [], (p.text, quality.judge(c, p))


def test_two_resumes_get_different_code_made_questions_for_the_same_posting():
    a, b = _ctx(SNAP_CACHE), _ctx(SNAP_DETAILS)
    C.posting_fallback(a, "JOB", [(4, "JOB_SKILL")])
    C.posting_fallback(b, "JOB", [(4, "JOB_SKILL")])
    assert a.picked[0].text != b.picked[0].text


def test_without_a_matching_experience_the_question_is_about_the_work_alone():
    c = _ctx({})
    C.posting_fallback(c, "JOB", [(4, "JOB_SKILL")])
    assert c.picked[0].text.startswith("이 직무에서 ") and c.picked[0].grounding["resume"]["link"] is None
    assert quality.judge(c, c.picked[0]) == []


def test_the_postings_work_is_named_without_its_requirement_wording():
    assert C.job_topic("대용량 트래픽 처리 경험이 있으신 분") == "대용량 트래픽 처리"
    assert C.job_work("주문 이벤트를 Kafka로 비동기 처리하는 시스템을 설계하고 개발") == "주문 이벤트를 Kafka로 비동기 처리하는 시스템을 설계하고 개발하는 일"
    assert C.job_work("대용량 트래픽 처리") == "대용량 트래픽 처리와 관련된 일"
    assert C.job_topic("우대") is None


def test_the_motivation_sentence_names_the_resumes_row_only_when_there_is_one():
    assert C.motivation_question("주문 시스템", "배송 추적 서비스") == \
        "이 회사의 ‘주문 시스템’ 업무에서 어떤 점에 관심이 있고, ‘배송 추적 서비스’ 경험을 어떻게 살릴 수 있다고 보십니까?"
    assert C.motivation_question("주문 시스템", None) == C.MOTIVATION_QUESTION.format(title="주문 시스템")


# ── the model's question, with the candidate's row in front (code) ────────────
def test_a_job_question_gets_the_row_it_is_about_and_only_that_one():
    c = _ctx(SNAP_CACHE)
    out = C.personalise(c, "주문 API 응답 지연을 줄이려면 무엇부터 확인하시겠습니까?", "대용량 트래픽 처리 경험이 있으신 분")
    assert out is not None
    text_, link = out
    assert text_ == "가상커머스 백엔드 개발자 업무에서 쌓은 경험을 살려, 주문 API 응답 지연을 줄이려면 무엇부터 확인하시겠습니까?"
    assert link["ref"] == "experience[0]" and link["how"] == "code" and link["quote"] in str(SNAP_CACHE)
    assert quality.judge(c, C.Picked(4, "JOB_SKILL", text_, "AI", grounding={
        "sources": [{"type": "POSTING"}], "basis": {"text": POSTING.requirements}, "resume": {"link": link}})) == []


def test_no_shared_words_no_row_and_a_row_is_used_once():
    c = _ctx(SNAP_CACHE)
    assert C.personalise(c, "회계 결산 일정을 어떻게 관리하시겠습니까?", "회계 결산") is None
    first = C.personalise(c, "주문 API 응답 지연을 줄이려면 무엇부터 확인하시겠습니까?", "")
    c.picked.append(C.Picked(4, "JOB_SKILL", first[0], "AI", grounding={"resume": {"link": first[1]}}))
    again = C.personalise(c, "주문 API 응답 지연의 원인은 어떻게 찾으시겠습니까?", "")
    assert again is None or again[1]["scene"] != first[1]["scene"]


def test_a_question_too_long_with_the_row_stays_as_the_model_wrote_it():
    c = _ctx(SNAP_CACHE)
    long_q = "주문 API 응답 지연이 늘어나는 상황에서 캐시와 인덱스 중 무엇을 먼저 점검하고 어떤 지표로 판단하시겠습니까?"
    assert C.personalise(c, long_q, "") is None


def test_one_product_name_is_one_topic():
    from app.questions.level import techs
    assert techs("Spark Connect 테넌트를 어떻게 운영하시겠습니까?") == {"spark connect"}
    assert techs("GPU를 할당하는 GPU-aware 클러스터") == {"gpu-aware"}
    assert techs("Redis와 Kafka, MySQL") == {"redis", "kafka", "mysql"}


def test_the_example_in_the_instructions_is_never_a_question():
    from app.ai.tasks.questions import copies_example
    assert copies_example("학습자에게 너무 쉽거나 어렵지 않은 문제를 추천하려면 어떤 기준을 쓰시겠습니까?", {})


def test_an_experience_said_as_a_fact_needs_the_resume_but_a_supposition_does_not():
    from app.questions.resume_match import ASSUMES_EXPERIENCE
    assert ASSUMES_EXPERIENCE.search("커밋 지연을 분석할 때 지연 원인을 찾아 본 경험을 어떻게 살리시겠습니까?")
    assert not ASSUMES_EXPERIENCE.search("커밋 지연 원인을 찾아 본 경험이 있다면 어떻게 하시겠습니까?")
    assert not ASSUMES_EXPERIENCE.search("정산 배치 개선 프로젝트에서 쌓은 경험을 Kafka 기반 이벤트 처리에 어떻게 살리시겠습니까?")
