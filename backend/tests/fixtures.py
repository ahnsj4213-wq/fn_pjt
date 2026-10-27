"""Domain fixtures built through the public API (so tests exercise the same paths as the UI)."""
import io
from datetime import date, timedelta

import httpx

RESUME = {
    "name": "김지원",
    "education": [{"school": "한국대학교", "major": "컴퓨터공학", "degree": "학사", "period": "2016-2020"}],
    "career": [{"company": "커머스랩", "role": "백엔드 개발자", "period": "2020-2024",
                "description": "주문 처리 API의 응답 지연을 줄이기 위해 복합 인덱스를 추가하고 N+1 쿼리를 배치 조회로 바꿨다."}],
    "projects": [{"title": "정산 배치 개선", "role": "리드", "period": "2023",
                  "description": "야간 정산 배치를 파티션 단위로 병렬화해 처리 시간을 4시간에서 50분으로 줄였다."}],
    "skills": ["Python", "PostgreSQL", "Kafka"],
    "certificates": [{"name": "정보처리기사", "issuer": "한국산업인력공단", "date": "2019"}],
}


def posting_body(**over) -> dict:
    body = {"title": "백엔드 개발자", "job_family": "BACKEND", "requirements": "Python 웹 서비스 개발 경험 3년 이상",
            "preferred": "대용량 트래픽 경험", "duties": "주문·결제 API 개발과 운영", "headcount": 2,
            "deadline_date": (date.today() + timedelta(days=14)).isoformat(), "coding_enabled": False}
    body.update(over)
    return body


async def create_posting(rc: httpx.AsyncClient, publish: bool = True, **over) -> dict:
    r = await rc.post("/api/recruiter/postings", json=posting_body(**over))
    assert r.status_code == 201, r.text
    p = r.json()
    if publish:
        r = await rc.post(f"/api/recruiter/postings/{p['id']}/publish")
        assert r.status_code == 200, r.text
        p = r.json()
    return p


async def consent(cc: httpx.AsyncClient, posting_id: str, agree_all: bool = True) -> dict:
    doc = (await cc.get("/api/consent/current")).json()
    answers = {i["code"]: True for i in doc["items"]}
    if not agree_all:
        answers[doc["items"][0]["code"]] = False
    r = await cc.post(f"/api/candidate/postings/{posting_id}/consents", json={"version": doc["version"], "answers": answers})
    assert r.status_code == 201, r.text
    return r.json()


async def apply(cc: httpx.AsyncClient, posting_id: str, resume: dict | None = None) -> httpx.Response:
    c = await consent(cc, posting_id)
    return await cc.post(f"/api/candidate/postings/{posting_id}/applications",
                         json={"consent_id": c["consent_id"], "resume": resume or RESUME})


def text_pdf(lines: list[str]) -> bytes:
    """Korean text PDF built locally with the Windows Malgun Gothic font (no downloads)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    try:
        pdfmetrics.getFont("Malgun")
    except KeyError:
        pdfmetrics.registerFont(TTFont("Malgun", "C:/Windows/Fonts/malgun.ttf"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Malgun", 11)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 18
    c.save()
    return buf.getvalue()


def image_only_pdf() -> bytes:
    """A 'scanned' PDF: shapes only, no extractable text (RES-04)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i in range(20):
        c.rect(40 + i * 10, 400 + i * 5, 200, 12, fill=1)
    c.save()
    return buf.getvalue()
