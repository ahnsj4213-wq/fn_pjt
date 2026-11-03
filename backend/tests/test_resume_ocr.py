"""Resume OCR (RES-04): scanned PDF pages and photos are read by OCR in the extraction job, low-confidence reads fall
back to manual input, and the fields fed by unsure lines are flagged for the review form. The OCR engine is faked
(Tesseract lives in the worker image); the subprocess path is checked for its "engine missing" answer."""
import io
import json

import pytest

from app.ai.tasks.base import TaskSpec, run_task
from app.resumes import ocr
from app.resumes.ocr_extract import best_angle
from tests.fixtures import text_pdf
from tests.helpers import candidate
from tests.test_handlers import drain, fake_ai, q  # noqa: F401  fixtures + job loop

URL = "/api/candidate/resume-files"
LINES = ["김지원", "경력: 커머스랩 백엔드 개발자 2020-2024", "주문 처리 API 응답 지연 개선", "기술: Python, PostgreSQL"]


def _reply(inp, msgs):
    return {"name": "김지원", "education": [], "projects": [], "skills": ["Python"],
            "experience": [{"company": "커머스랩", "role": "백엔드 개발자", "period": "2020-2024", "description": None}],
            "certifications": []}


def mixed_pdf() -> bytes:
    """Page 1 has a text layer, page 2 is 'scanned' (shapes only)."""
    from pypdf import PdfReader, PdfWriter
    from tests.fixtures import image_only_pdf

    w = PdfWriter()
    for src in (text_pdf(LINES), image_only_pdf()):
        for page in PdfReader(io.BytesIO(src)).pages:
            w.add_page(page)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def ocr_engine():
    calls: list[tuple[str, list[int]]] = []
    result: dict = {"lines": [], "fail": None}

    async def fake(path: str, kind: str, pages: list[int]):
        calls.append((kind, pages))
        if result["fail"]:
            raise ocr.OcrFailed(result["fail"])
        return result["lines"]

    ocr.set_runner(fake)
    yield calls, result
    ocr.set_runner(None)


async def test_scanned_page_goes_to_ocr_and_unsure_fields_are_flagged(make_client, fake_ai, ocr_engine):
    calls, result = ocr_engine
    result["lines"] = [("기술: Python, PostgreSQL", 0.93), ("경력: 커머스랩 백엔드 개발자 2020-2024", 0.55)]
    fake_ai.responses["resume_extract"] = _reply
    cand = make_client()
    await candidate(cand)
    r = await cand.post(URL, files={"file": ("cv.pdf", mixed_pdf(), "application/pdf")})
    body = r.json()
    assert r.status_code == 201 and body["extraction_status"] == "PENDING" and body["text_extractable"] is False
    assert body["manual_input_required"] is False
    await drain()
    assert calls == [("pdf", [1])]  # only the page without a text layer
    out = (await cand.get(f"{URL}/{body['file_id']}")).json()
    assert out["extraction_status"] == "DONE" and out["method"] == "OCR"
    assert out["ocr_confidence"] == pytest.approx(0.74)
    assert out["low_conf_fields"] == ["career.0"]  # the line read at 0.55 fed the career row
    (row,) = await q("SELECT extracted_text FROM resume_files WHERE id = :i", i=body["file_id"])
    assert "주문 처리 API" in row[0] and "기술: Python, PostgreSQL" in row[0]  # text page + OCR text


async def test_low_confidence_or_missing_engine_means_manual_input(make_client, fake_ai, ocr_engine):
    calls, result = ocr_engine
    fake_ai.responses["resume_extract"] = _reply
    cand = make_client()
    await candidate(cand)
    result["lines"] = [("김지 원 거력", 0.31), ("커머 스랩", 0.42)]
    r = await cand.post(URL, files={"file": ("photo.png", png(), "image/png")})
    assert r.status_code == 201 and r.json()["page_count"] == 1
    await drain()
    out = (await cand.get(f"{URL}/{r.json()['file_id']}")).json()
    assert out["extraction_status"] == "NO_TEXT" and out["manual_input_required"] is True and out["fields"] is None
    assert calls == [("image", [0])]

    result["fail"] = "OCR_UNAVAILABLE"
    r = await cand.post(URL, files={"file": ("scan.jpg", b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg")})
    await drain()
    out = (await cand.get(f"{URL}/{r.json()['file_id']}")).json()
    assert out["extraction_status"] == "NO_TEXT" and out["manual_input_required"] is True


async def test_text_pdf_skips_ocr(make_client, fake_ai, ocr_engine):
    calls, _ = ocr_engine
    fake_ai.responses["resume_extract"] = _reply
    cand = make_client()
    await candidate(cand)
    r = await cand.post(URL, files={"file": ("cv.pdf", text_pdf(LINES), "application/pdf")})
    await drain()
    out = (await cand.get(f"{URL}/{r.json()['file_id']}")).json()
    assert calls == [] and out["extraction_status"] == "DONE" and out["method"] == "TEXT" and out["low_conf_fields"] == []


async def test_unsupported_types_are_refused(make_client):
    cand = make_client()
    await candidate(cand)
    r = await cand.post(URL, files={"file": ("cv.gif", b"GIF89a" + b"0" * 64, "image/gif")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_FILE"
    r = await cand.post(URL, files={"file": ("cv.png", b"%PDF-1.4 not a png", "image/png")})
    assert r.status_code == 415


def test_scanned_pages_and_low_conf_fields():
    assert ocr.scanned_pages(["가" * 60, " \n ", "나" * 49]) == [1, 2]
    fields = {"name": "김지원", "education": [{"school": "한국대학교", "major": "컴퓨터공학"}],
              "career": [], "projects": [{"title": "예약 서비스", "description": None}], "skills": ["Python"]}
    lines = [("한국대학교 컴퓨터공학과", 0.4), ("예약 서비스 백엔드", 0.9), ("Python", 0.6)]
    assert ocr.low_conf_fields(fields, lines, 0.7) == ["education.0", "skills"]
    assert ocr.low_conf_fields(fields, [("한국대학교", 0.95)], 0.7) == []


def test_deskew_finds_the_tilt():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 800), "white")
    d = ImageDraw.Draw(img)
    for y in range(80, 760, 40):
        d.rectangle([60, y, 540, y + 12], fill="black")
    assert best_angle(img) == 0
    assert best_angle(img.rotate(3, expand=True, fillcolor=(255, 255, 255))) == -3


async def test_missing_tesseract_answers_ocr_unavailable(tmp_path, monkeypatch):
    import shutil

    if shutil.which("tesseract"):
        pytest.skip("tesseract is installed here")
    p = tmp_path / "a.png"
    p.write_bytes(png())
    with pytest.raises(ocr.OcrFailed, match="OCR_UNAVAILABLE"):
        await ocr._subprocess_ocr(str(p), "image", [0])


async def test_cut_output_asks_for_shorter_values():
    from pydantic import BaseModel

    class Out(BaseModel):
        items: list[str]

    class Spec(TaskSpec):
        name = "t"
        version = "t@1"
        num_predict = 10
        out_model = Out
        system = "s"

        def build(self, inp, nonce):
            return "u", {}

    seen: list[list[dict]] = []

    async def call(messages, schema, options):
        seen.append(list(messages))
        if len(seen) == 1:
            return '{"items": ["aaaa', {"completion_tokens": 10}
        return json.dumps({"items": ["a"]}), {"completion_tokens": 5}

    out = await run_task(Spec(), {}, call)
    assert out.status == "OK" and "잘렸다" in seen[1][-1]["content"]


def test_korean_syllables_are_joined_by_real_gaps():
    from app.resumes.ocr_extract import join_words

    # measured boxes from a 200 dpi scan (text height ~29 px): "경력: 커머스랩 백엔드 개발자"
    words = [("경력:", 0, 60, 29), ("커", 79, 25, 29), ("머", 116, 25, 29), ("스", 154, 25, 23), ("랩", 190, 25, 28),
             ("백", 236, 25, 30), ("엔", 274, 25, 30), ("드", 312, 25, 23), ("개발자", 351, 75, 29)]
    assert join_words(words) == "경력: 커머스랩 백엔드 개발자"
    assert join_words([("Python,", 0, 80, 24), ("FastAPI", 95, 80, 24)]) == "Python, FastAPI"
