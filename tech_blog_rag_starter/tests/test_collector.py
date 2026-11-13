"""Offline tests for the collector and the validator (no network). Run: python -m pytest tests -q"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import collect as C  # noqa: E402


class FakeResponse:
    def __init__(self, content: bytes, ctype: str):
        self.content = content
        self.headers = {"Content-Type": ctype}
        self.encoding = None

    @property
    def text(self):
        return self.content.decode(self.encoding or "iso-8859-1", errors="replace")

    @property
    def apparent_encoding(self):
        return "utf-8"


def test_a_page_without_charset_is_read_as_utf8():
    r = FakeResponse("컬리 기술 블로그".encode(), "text/html")
    assert C.decoded(r) == "컬리 기술 블로그"


def test_only_article_urls_are_candidates():
    assert C.is_article("https://tech.kakao.com/posts/845", r"^/posts/\d+/?$")
    assert not C.is_article("https://tech.kakao.com/tags", r"^/posts/\d+/?$")
    assert not C.is_article("https://helloworld.kurly.com/blog/page/2/", r"^/blog/[^/]+/?$")
    assert C.is_article("https://toss.tech/article/taste", r"^/article/[^/]+/?$")


def _nuxt_page(post: dict) -> str:
    # devalue layout: [wrapper, {data}, ..., post object whose values are indices into the array]
    arr = [["ShallowReactive", 1], {"data": 2}, ["ShallowReactive", 3], {"$key": 4}, None,
           post["title"], post["content"], "2026.11.03", [9], {"code": 10}, post["category"]]
    arr[4] = {"title": 5, "content": 6, "releaseDate": 7, "categories": 8}
    return f'<html><body><script type="application/json" id="__NUXT_DATA__">{json.dumps(arr, ensure_ascii=False)}</script></body></html>'


def test_the_post_embedded_in_a_nuxt_page_is_read():
    html = _nuxt_page({"title": "CDC 전환 기록", "content": "<h2>요약</h2><p>본문입니다.</p>", "category": "blog"})
    post = C.nuxt_post(html)
    assert post["title"] == "CDC 전환 기록" and "본문입니다." in post["content"]
    assert post["releaseDate"] == "2026.11.03" and post["categories"] == ["blog"]
    assert C.nuxt_post("<html><body>no data</body></html>") is None


def test_related_posts_and_recruiting_lines_at_the_end_are_removed():
    body = "\n".join(["본문 첫 줄", "본문 둘째 줄", "본문 셋째 줄", "본문 넷째 줄", "관련 글 목록", "- 다른 글 1", "- 다른 글 2"])
    assert C.strip_trailing_promo(body).endswith("본문 넷째 줄")
    body = "\n".join(["가", "나", "다", "라", "컬리에서 함께 개발하고 싶다면: 채용 공고"])
    assert C.strip_trailing_promo(body) == "가\n나\n다\n라"


def test_only_sentence_length_lines_shared_by_many_posts_are_removed():
    shared = "이 글은 회사 블로그의 공통 안내 문구입니다"
    docs = [{"content": f"고유 문장 {i}\n}}\n|---|---|\n{shared}"} for i in range(4)]
    out = C.remove_repeated_lines(docs)
    for d in out:
        assert shared not in d["content"] and "}" in d["content"] and "|---|---|" in d["content"]
        assert d["content_sha256"] == hashlib.sha256(d["content"].encode()).hexdigest()


def test_language_detection():
    assert C.language_of("한국어로 쓴 기술 블로그 글입니다. Kafka와 Redis를 씁니다.") == "ko"
    assert C.language_of("How documents find developers at Toss") == "en"


def _doc(company_id, name, url, text):
    return {"document_id": "x" + url[-6:], "company_id": company_id, "company_name": name, "title": "t", "source_url": url,
            "document_type": "tech_blog", "published_at": None, "is_synthetic": False, "content": text,
            "content_length": len(text), "content_sha256": hashlib.sha256(text.encode()).hexdigest()}


@pytest.fixture
def isolated_root(tmp_path):
    """A copy of the project layout (config + scripts) with its own data folder."""
    for sub in ("config", "scripts"):
        (tmp_path / sub).mkdir()
        for f in (ROOT / sub).glob("*.*"):
            (tmp_path / sub / f.name).write_bytes(f.read_bytes())
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    return tmp_path


def _run_validate(root):
    return subprocess.run([sys.executable, str(root / "scripts" / "validate.py")], capture_output=True, text=True,
                          encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})


def test_validate_rejects_another_companys_post_in_a_company_file(isolated_root):
    text = "본문 " * 600
    bad = _doc("kakao", "카카오", "https://toss.tech/article/x1", text)  # a Toss URL filed under kakao
    (isolated_root / "data" / "raw" / "kakao.jsonl").write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
    res = _run_validate(isolated_root)
    assert res.returncode == 1 and "source_url host is not the blog of kakao" in res.stdout


def test_validate_rejects_duplicate_content_across_companies(isolated_root):
    text = "같은 본문 " * 300
    (isolated_root / "data" / "raw" / "kurly.jsonl").write_text(
        json.dumps(_doc("kurly", "컬리", "https://helloworld.kurly.com/blog/a", text), ensure_ascii=False) + "\n", encoding="utf-8")
    (isolated_root / "data" / "raw" / "toss.jsonl").write_text(
        json.dumps(_doc("toss", "토스", "https://toss.tech/article/a", text), ensure_ascii=False) + "\n", encoding="utf-8")
    res = _run_validate(isolated_root)
    assert res.returncode == 1 and "duplicate content" in res.stdout


def test_copyright_captions_inside_the_body_are_dropped():
    import yaml
    patterns = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text(encoding="utf-8"))["settings"]["drop_line_patterns"]
    body = "Kafka 컨슈머 오프셋을 옮긴 방법입니다.\n© 2024.Kurly.All right reserved\n코드는 아래와 같습니다.\n© 2024. Kurly. All rights reserved. © 2024. Kurly. All rights reserved."
    doc = C.make_doc({"company_id": "kurly", "name": "컬리"}, "t", "https://helloworld.kurly.com/blog/a", None, body, "html", 5)
    out = C.drop_lines([doc], patterns)[0]
    assert out["content"] == "Kafka 컨슈머 오프셋을 옮긴 방법입니다.\n코드는 아래와 같습니다."
    assert out["content_sha256"] == hashlib.sha256(out["content"].encode()).hexdigest()


def test_the_published_date_is_the_one_shown_under_the_title():
    data = '"publishedTime\\":\\"2026-09-01T09:00:00+09:00\\"" "publishedTime\\":\\"2026-09-15T10:42:00+09:00\\""'
    page = f'<header><h1>GPU 클러스터</h1><div>장재영</div><div>2026년 9월 15일</div></header><script>{data}</script>'
    assert C.page_published_date(page) == "2026-09-15T10:42:00+09:00"  # the related post's date is not taken
    assert C.page_published_date("<header><h1>제목</h1><div>2026년 9월 5일</div></header>") == "2026-09-05"
    assert C.page_published_date(f"<header><h1>제목</h1></header><script>{data}</script>") is None  # nothing shown: no date


def test_an_event_blurb_after_the_post_is_removed():
    body = "\n".join(["그래프 검색 품질", "후보 수를 제한했습니다.", "마치며", "읽어주셔서 감사합니다.",
                      "테크톡톡은 지식공유 포럼이에요.", "토스증권의 기술과 경험을 나누는 행사로, 엔지니어들이 공유해요."])
    assert C.strip_trailing_promo(body).endswith("읽어주셔서 감사합니다.")
