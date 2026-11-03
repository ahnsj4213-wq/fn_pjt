"""My Page (SC-A09 지원 현황, SC-A10 이력서, SC-A11 프로필, SC-A15 면접 일정)."""
import io
import zipfile
from datetime import timedelta

from app.core.db import session_scope
from app.core.timeutil import utcnow
from app.models import InterviewSchedule
from tests.fixtures import RESUME
from tests.helpers import candidate
from tests.test_handlers import drain, fake_ai, q, setup_app  # noqa: F401  fixtures + helpers


async def test_profile_read_update_and_password_link(make_client):
    cand = make_client()
    email = await candidate(cand)
    r = await cand.get("/api/candidate/profile")
    assert r.json() == {"name": "김지원", "email": email, "birth_date": "1998-03-14", "gender": "F", "phone": "010-1234-5678"}
    r = await cand.put("/api/candidate/profile", json={"name": "김지수", "birth_date": "1999-01-02", "gender": "M",
                                                       "phone": "010-9999-8888"})
    assert r.status_code == 200 and r.json()["name"] == "김지수" and r.json()["email"] == email
    assert (await cand.get("/api/auth/me")).json()["user"]["name"] == "김지수"  # the top bar name follows (SC-A11 6)
    assert (await cand.put("/api/candidate/profile", json={"name": "", "phone": "010-1"})).status_code == 422
    r = await cand.put("/api/candidate/profile", json={"name": "김", "phone": "010-1111-2222", "email": "x@y.z"})
    assert r.status_code == 422  # the id cannot change
    r = await cand.post("/api/candidate/profile/password-link")
    assert r.status_code == 202 and r.json() == {"email": email}
    assert await q("SELECT count(*) FROM password_reset_tokens") == [(1,)]


async def test_schedules_are_the_candidates_own_and_confirm_progress(client, make_client, fake_ai):
    company_id, app_id, cand = await setup_app(client, make_client)
    _, other_app, other = await setup_app(make_client(), make_client)
    async with session_scope() as db:
        for aid, status in ((app_id, "ACCEPTED"), (other_app, "PROPOSED")):
            (cid,) = (await q("SELECT candidate_id FROM applications WHERE id = :a", a=aid))[0]
            (comp,) = (await q("SELECT company_id FROM applications WHERE id = :a", a=aid))[0]
            db.add(InterviewSchedule(application_id=aid, company_id=comp, candidate_id=cid, title="2차 대면 면접",
                                     starts_at=utcnow() + timedelta(days=3), place="본사 7층", status=status))
    mine = (await cand.get("/api/candidate/schedules")).json()
    assert [(s["application_id"], s["status"], s["place"]) for s in mine] == [(app_id, "ACCEPTED", "본사 7층")]
    assert mine[0]["company_name"]
    apps = (await cand.get("/api/candidate/applications")).json()
    assert apps[0]["progress_label"] == "2차 면접 일정 확정" and apps[0]["progress_kind"] == "good"
    other_apps = (await other.get("/api/candidate/applications")).json()
    assert other_apps[0]["progress_label"] == "면접 전" and other_apps[0]["main_count"] is None


async def test_progress_labels():
    from types import SimpleNamespace

    from app.applications.router import progress

    def app(a, d="UNDECIDED", share=True):
        return SimpleNamespace(attempt_status=a, decision_status=d, share_result=share)

    assert progress(app("SUBMITTED"), False) == ("기업 검토 중", "hold")
    assert progress(app("SUBMITTED", share=False), False) == ("연습 모드", "none")
    assert progress(app("INTERRUPTED"), False) == ("면접 중단", "none")
    assert progress(app("SUBMITTED", "PASS"), True) == ("합격", "good")
    assert progress(app("SUBMITTED", "FAIL"), False) == ("불합격", "bad")


def docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


async def test_docx_upload_is_read_and_extracted(make_client, fake_ai):
    cand = make_client()
    await candidate(cand)
    lines = ["김지원", "학력: 한국대학교 컴퓨터공학과", "경력: 커머스랩 백엔드 개발자 2020-2024", "기술: Python, PostgreSQL"]
    r = await cand.post("/api/candidate/resume-files", files={"file": ("cv.docx", docx(lines), "application/octet-stream")})
    assert r.status_code == 201 and r.json()["extraction_status"] == "PENDING", r.text
    await drain()
    out = (await cand.get(f"/api/candidate/resume-files/{r.json()['file_id']}")).json()
    assert out["extraction_status"] == "DONE" and out["method"] == "TEXT"
    (text,) = (await q("SELECT extracted_text FROM resume_files WHERE id = :i", i=r.json()["file_id"]))[0]
    assert text.splitlines() == lines
    bad = await cand.post("/api/candidate/resume-files", files={"file": ("cv.docx", b"PK\x03\x04broken", "x")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "DOCX_UNREADABLE"


async def test_resume_form_fields_stay_out_of_ai_input(make_client):
    from app.resumes.schemas import to_ai_resume

    cand = make_client()
    await candidate(cand)
    content = {**RESUME, "desired_job": "DEV", "experience_level": "Y1_3", "intro": "성실한 개발자입니다."}
    r = await cand.put("/api/candidate/resume", json={"content": content})
    assert r.status_code == 200, r.text
    saved = (await cand.get("/api/candidate/resume")).json()["resume"]["content"]
    assert saved["desired_job"] == "DEV" and saved["intro"] == "성실한 개발자입니다."
    assert not {"desired_job", "experience_level", "intro", "name"} & set(to_ai_resume(saved))
    bad = await cand.put("/api/candidate/resume", json={"content": {**RESUME, "desired_job": "CHEF"}})
    assert bad.status_code == 422
