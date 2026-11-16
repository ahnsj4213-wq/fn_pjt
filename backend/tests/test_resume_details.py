"""A multi-page resume whose pages are titled "이름 · 대표 프로젝트 / ARCHITECTURE" (ADR-0032): every block is kept word for word
with the lines it came from and the row it belongs to; descriptions never take another row's text. All names are made up."""
from app.ai.tasks.resume import ResumeExtract, ResumeOut
from app.resumes.schemas import ResumeContent, flatten, to_ai_resume
from app.resumes.sections import DESCRIPTION_MAX, apply_sections, read_sections

HEADER = "가상 이력서 | 테스트용 데이터 | 실존 인물과 무관한 긴 머리말 문장입니다"
RESUME = f"""{HEADER}
1 / 3
홍길동 · 이력서 / PROFILE
백엔드 · 검색 | Backend Engineer
지원 요약
검색 서비스를 만드는 백엔드 지원자입니다. 장애를 재현하고 측정하는 것을 중요하게 생각합니다.
기본 정보
이메일: hong.gildong@example.com | 전화: 010-1234-5678
학력: 가상대학교 컴퓨터공학과 학사
프로젝트 한눈에 보기
프로젝트 A | 상품 검색 랭킹 개선 (2025.03~08)
BM25와 임베딩을 결합한 상품 검색 랭킹을 개선했습니다. 3인 팀에서 색인 설계 담당.
프로젝트 B | 사내 배포 알림 봇 (2024.09~12)
배포 이벤트를 슬랙으로 알리는 봇입니다. 2인 팀에서 웹훅 처리 담당.
{HEADER}
2 / 3
홍길동 · 대표 프로젝트 / ARCHITECTURE
백엔드 · 검색 | Backend Engineer
상품 검색 결과가 사용자 의도와 맞지 않아 이탈이 늘어나는 상황이었습니다.
BM25 점수와 임베딩 유사도를 결합해 랭킹을 다시 계산하고 색인 설계를 바꿨습니다.
상품 검색 클릭률을 측정해 랭킹 개선 효과를 확인했습니다.
기술 스택
Python, Elasticsearch, FastAPI
{HEADER}
3 / 3
홍길동 · 협업 및 추가 경험 / EXPERIENCE
백엔드 · 검색 | Backend Engineer
프로젝트 B | 사내 배포 알림 봇
배포가 실패하면 웹훅 재시도 큐로 보내 알림 유실을 막았습니다.
문의는 hong.gildong@example.com 으로 받았습니다.
자기소개
저는 측정할 수 있는 개선을 좋아합니다. 근거를 남기는 개발자가 되겠습니다.
"""
A = "프로젝트 A | 상품 검색 랭킹 개선"
B = "프로젝트 B | 사내 배포 알림 봇"


def model_fields() -> dict:
    """What T1 gives: rows with short descriptions copied from the file."""
    return {"name": "홍길동", "education": [{"school": "가상대학교"}], "skills": ["Python"], "certificates": [],
            "career": [],
            "projects": [{"title": A, "role": None, "period": "2025.03~08", "description": "BM25와 임베딩을 결합한 상품 검색 랭킹을 개선했습니다."},
                         {"title": B, "role": None, "period": "2024.09~12", "description": "배포 이벤트를 슬랙으로 알리는 봇입니다."}]}


def parsed() -> dict:
    return apply_sections(model_fields(), read_sections(RESUME))


def test_page_titles_are_headings_and_their_names_are_kept():
    kinds = {b.heading: b.kind for b in read_sections(RESUME).blocks}
    assert kinds["대표 프로젝트"] == "projects" and kinds["협업 및 추가 경험"] == "career"
    assert kinds["지원 요약"] == "summary" and kinds["기본 정보"] == "contact" and kinds["자기소개"] == "intro"
    assert "홍길동" not in str(kinds)


def test_a_header_on_every_page_is_dropped_however_long():
    assert HEADER not in str(parsed()["details"]) and "1 / 3" not in str(parsed()["details"])


def test_every_block_is_kept_word_for_word_with_the_lines_it_came_from():
    lines = RESUME.splitlines()
    for d in parsed()["details"]:
        first, last = d["lines"]
        source = [x.strip() for x in lines[first - 1:last] if x.strip() and x != HEADER and "/ 3" not in x]
        assert d["text"].replace("[이메일]", "hong.gildong@example.com").splitlines() == source


def test_blocks_go_to_the_row_they_name_or_clearly_resemble_and_nothing_else():
    by_heading = {}
    for d in parsed()["details"]:
        by_heading.setdefault(d["heading"], []).append((d["row"], d["how"]))
    assert by_heading["프로젝트 한눈에 보기"] == [(f"projects:{A}", "name"), (f"projects:{B}", "name")]
    assert by_heading["대표 프로젝트"] == [(f"projects:{A}", "similar")]  # names no project; its words are A's
    assert by_heading["협업 및 추가 경험"] == [(f"projects:{B}", "name")]
    assert by_heading["지원 요약"] == [(None, None)] and by_heading["기술 스택"] == [(None, None)]


def test_a_description_never_takes_another_rows_text():
    a, b = parsed()["projects"]
    assert "클릭률을 측정해" in a["description"] and "웹훅 재시도 큐" not in a["description"]
    assert "웹훅 재시도 큐" in b["description"] and "클릭률" not in b["description"]
    assert "\n" not in a["description"] and len(a["description"]) <= DESCRIPTION_MAX


def test_a_block_that_resembles_two_rows_belongs_to_neither():
    text = "대표 프로젝트\n상품 검색 랭킹과 배포 알림 봇을 함께 운영했습니다. 웹훅 슬랙 BM25 임베딩 색인.\n"
    fields = model_fields()
    assert apply_sections(fields, read_sections(text))["details"][0]["row"] is None


def test_a_row_of_one_or_two_words_is_never_matched_by_resemblance():
    fields = {**model_fields(), "projects": [{"title": "검색 프로젝트", "role": None, "period": None, "description": None}]}
    out = apply_sections(fields, read_sections("대표 프로젝트\n검색 품질을 개선했습니다.\n"))
    assert out["details"][0]["row"] is None and out["projects"][0]["description"] is None


def test_contact_blocks_are_not_kept_and_addresses_in_other_blocks_are_masked():
    details = parsed()["details"]
    assert "010-1234-5678" not in str(details) and "example.com" not in str(details)
    assert any("[이메일]" in d["text"] for d in details)
    assert not any(d["heading"] == "기본 정보" for d in details)


def test_the_snapshot_holds_the_details_and_the_model_does_not_yet():
    c = ResumeContent.model_validate(parsed())
    text = flatten(c)
    assert "BM25 점수와 임베딩 유사도를 결합해" in text and "웹훅 재시도 큐" in text
    assert "details" not in to_ai_resume(c.model_dump())


def test_t1_drops_a_company_the_file_does_not_contain():
    p = ResumeOut(experience=[{"company": "없는회사(주)"}, {"company": "가상테크(주)"}])
    out = ResumeExtract().check({"source_text": "가상테크(주) / 과장"}, p, {}, True).output
    assert [r["company"] for r in out["experience"]] == ["가상테크(주)"]


def test_a_resume_without_details_still_validates():
    ResumeContent.model_validate({"name": "홍길동", "career": [{"company": "가상"}]})
