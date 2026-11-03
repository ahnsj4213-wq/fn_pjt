"""템플릿 불러오기 (SC-R05) and 문의 쪽지 (SC-R15)."""
import io
import zipfile

import pytest

from app.recruiter.templates import split_posting_text
from tests.helpers import recruiter
from tests.test_handlers import setup_app

pytestmark = pytest.mark.integration


def _docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_split_posting_text_fills_the_three_fields():
    got = split_posting_text("백엔드 개발자 모집\n[주요 업무]\n- API 설계\n- 배포 자동화\n자격 요건:\n· 파이썬 경험\n우대 사항\n클라우드 경험")
    assert got == {"title": "백엔드 개발자 모집", "duties": "API 설계\n배포 자동화", "requirements": "파이썬 경험", "preferred": "클라우드 경험"}


async def test_saved_templates_are_company_scoped_and_deletable(client, make_client):
    await recruiter(client)
    r = await client.post("/api/recruiter/posting-templates", json={"name": "개발 기본", "job_family": "DEV", "duties": "API 설계"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert [t["name"] for t in (await client.get("/api/recruiter/posting-templates")).json()] == ["개발 기본"]

    other = make_client()
    await recruiter(other)  # another company
    assert (await other.get("/api/recruiter/posting-templates")).json() == []
    assert (await other.delete(f"/api/recruiter/posting-templates/{tid}")).status_code == 404
    assert (await client.delete(f"/api/recruiter/posting-templates/{tid}")).status_code == 204
    assert (await client.get("/api/recruiter/posting-templates")).json() == []


async def test_import_file_reads_docx_and_refuses_hwp_and_other_types(client):
    await recruiter(client)
    docx = _docx("데이터 엔지니어 모집", "주요 업무", "파이프라인 구축", "자격 요건", "SQL 능숙자", "우대 사항", "Spark 경험자")
    r = await client.post("/api/recruiter/postings/import-file", files={"file": ("공고.docx", docx)})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "데이터 엔지니어 모집" and r.json()["duties"] == "파이프라인 구축" and r.json()["preferred"] == "Spark 경험자"
    assert (await client.post("/api/recruiter/postings/import-file", files={"file": ("공고.hwp", b"x" * 40)})).json()["error"]["code"] == "HWP_UNSUPPORTED"
    assert (await client.post("/api/recruiter/postings/import-file", files={"file": ("공고.txt", b"x" * 40)})).status_code == 415


async def test_applicant_inquiry_reaches_only_its_company_and_the_reply_is_read_state_aware(client, make_client):
    _, app_id, cand = await setup_app(client, make_client)
    assert (await client.get("/api/recruiter/inquiries/unread-count")).json() == {"unread": 0}
    r = await cand.post(f"/api/candidate/applications/{app_id}/inquiry", json={"subject": "일정 문의", "body": "면접 결과는 언제 나오나요?"})
    assert r.status_code == 201, r.text
    again = await cand.post(f"/api/candidate/applications/{app_id}/inquiry", json={"subject": "무시됨", "body": "추가 질문입니다"})
    assert again.json()["id"] == r.json()["id"] and len(again.json()["messages"]) == 2  # one thread per application

    assert (await client.get("/api/recruiter/inquiries/unread-count")).json() == {"unread": 1}
    threads = (await client.get("/api/recruiter/inquiries")).json()
    assert [(t["kind"], t["subject"], t["unread"]) for t in threads] == [("APPLICANT", "일정 문의", True)]
    opened = (await client.get(f"/api/recruiter/inquiries/{threads[0]['id']}")).json()
    assert [m["mine"] for m in opened["messages"]] == [False, False]
    assert (await client.get("/api/recruiter/inquiries/unread-count")).json() == {"unread": 0}

    sent = await client.post(f"/api/recruiter/inquiries/{threads[0]['id']}/messages", json={"body": "다음 주에 안내드리겠습니다."})
    assert sent.status_code == 201 and sent.json()["messages"][-1]["mine"] is True
    mine = (await cand.get(f"/api/candidate/applications/{app_id}/inquiry")).json()
    assert [m["body"] for m in mine["messages"]][-1] == "다음 주에 안내드리겠습니다." and mine["messages"][-1]["mine"] is False

    other = make_client()
    await recruiter(other)
    assert (await other.get("/api/recruiter/inquiries")).json() == []
    assert (await other.get(f"/api/recruiter/inquiries/{threads[0]['id']}")).status_code == 404
    assert (await other.post(f"/api/recruiter/inquiries/{threads[0]['id']}/messages", json={"body": "침범"})).status_code == 404
    assert (await client.post(f"/api/recruiter/inquiries/{threads[0]['id']}/messages", json={"body": " "})).status_code == 422
