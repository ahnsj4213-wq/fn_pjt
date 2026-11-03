"""템플릿 불러오기 (SC-R05): templates the company saved, and a posting body read from a DOCX/PDF file.

The previous-posting and basic-template tabs need no endpoint of their own (the form already reads a company's
postings; the basic templates are fixed texts of the screen). Everything here is scoped by the session's company.
"""
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import RecruiterCtx, require_recruiter
from app.auth.scope import get_for_company
from app.core.db import get_db
from app.core.errors import AppError
from app.models import PostingTemplate
from app.resumes.router import FILE_TYPES, _read_limited, parse_docx, parse_pdf

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
MAX_TEMPLATES = 50
IMPORT_TYPES = {".docx", ".pdf"}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    job_family: str | None = Field(None, max_length=30)
    experience_level: str | None = Field(None, max_length=60)
    employment_type: str | None = Field(None, max_length=60)
    duties: str | None = Field(None, max_length=5000)
    requirements: str | None = Field(None, max_length=5000)
    preferred: str | None = Field(None, max_length=5000)


class TemplateOut(TemplateIn):
    id: uuid.UUID
    created_at: datetime


def _out(t: PostingTemplate) -> TemplateOut:
    return TemplateOut(id=t.id, name=t.name, job_family=t.job_family, experience_level=t.experience_level,
                       employment_type=t.employment_type, duties=t.duties, requirements=t.requirements,
                       preferred=t.preferred, created_at=t.created_at)


@router.get("/posting-templates")
async def list_templates(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> list[TemplateOut]:
    rows = (await db.execute(select(PostingTemplate).where(PostingTemplate.company_id == ctx.company_id)
                             .order_by(PostingTemplate.created_at.desc()))).scalars().all()
    return [_out(t) for t in rows]


@router.post("/posting-templates", status_code=201)
async def save_template(body: TemplateIn, ctx: RecruiterCtx = Depends(require_recruiter),
                        db: AsyncSession = Depends(get_db)) -> TemplateOut:
    count = len((await db.execute(select(PostingTemplate.id).where(PostingTemplate.company_id == ctx.company_id))).all())
    if count >= MAX_TEMPLATES:
        raise AppError(409, "TEMPLATE_LIMIT", f"템플릿은 {MAX_TEMPLATES}개까지 저장할 수 있습니다. 쓰지 않는 템플릿을 지워 주십시오.")
    t = PostingTemplate(company_id=ctx.company_id, created_by=ctx.user_id, **body.model_dump())
    db.add(t)
    await db.flush()
    await audit(db, action="POSTING_TEMPLATE_SAVE", target_type="posting_template", target_id=t.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()
    return _out(t)


@router.delete("/posting-templates/{template_id}", status_code=204)
async def delete_template(template_id: uuid.UUID, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)) -> None:
    t = await get_for_company(db, PostingTemplate, template_id, ctx.company_id)
    await db.execute(delete(PostingTemplate).where(PostingTemplate.id == t.id))
    await audit(db, action="POSTING_TEMPLATE_DELETE", target_type="posting_template", target_id=t.id, actor_id=ctx.user_id,
                actor_role="RECRUITER", company_id=ctx.company_id)
    await db.commit()


# ---- 외부 파일 -------------------------------------------------------------------------------------------------------
# A heading line starts a section; the text after it (until the next heading) is that section's body.
SECTIONS = {
    "duties": ("주요 업무", "담당 업무", "수행 업무", "하는 일", "업무 내용", "직무 소개"),
    "requirements": ("자격 요건", "지원 자격", "필수 요건", "필수 자격", "자격요건", "지원자격"),
    "preferred": ("우대 사항", "우대사항", "우대 조건", "우대"),
}
_HEADING_END = re.compile(r"^[\s\[\]()<>【】■□●○▶▷·•\-*#\d.]*")


def _heading_of(line: str) -> str | None:
    s = _HEADING_END.sub("", line.strip()).rstrip(" :：]】)>")
    if not s or len(s) > 12:
        return None
    for key, names in SECTIONS.items():
        if s in names:
            return key
    return None


def split_posting_text(text: str) -> dict[str, str | None]:
    """Fills the three body fields from a posting's text; the title is the first non-empty line before any heading."""
    out: dict[str, list[str]] = {"duties": [], "requirements": [], "preferred": []}
    title, current = None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        key = _heading_of(line)
        if key:
            current = key
            continue
        if current is None:
            title = title or line[:100]
        else:
            out[current].append(re.sub(r"^[\s·•\-*●○▶▷■□]+", "", line))
    return {"title": title, **{k: "\n".join(v)[:5000] or None for k, v in out.items()}}


class ImportOut(BaseModel):
    title: str | None
    duties: str | None
    requirements: str | None
    preferred: str | None
    file_name: str


@router.post("/postings/import-file")
async def import_file(file: UploadFile, ctx: RecruiterCtx = Depends(require_recruiter)) -> ImportOut:
    name = file.filename or ""
    ext = Path(name.lower()).suffix
    if ext == ".hwp":
        raise AppError(415, "HWP_UNSUPPORTED", "HWP 파일은 아직 읽을 수 없습니다. 한글에서 DOCX나 PDF로 저장해 올려 주십시오.")
    data = await _read_limited(file)
    if ext not in IMPORT_TYPES or not FILE_TYPES[ext][0](data):
        raise AppError(415, "UNSUPPORTED_FILE", "DOCX나 PDF 파일만 올릴 수 있습니다.")
    parsed = await (parse_docx(data) if ext == ".docx" else parse_pdf(data))
    if not parsed.get("ok"):
        raise AppError(422, "FILE_UNREADABLE", "파일을 읽을 수 없습니다. 암호가 걸려 있거나 손상된 파일은 읽지 못합니다.")
    text = parsed.get("text") if ext == ".docx" else "\n".join(parsed.get("page_texts") or [])
    text = (text or "").strip()
    if len(text) < 20:
        raise AppError(422, "FILE_EMPTY", "파일에서 글자를 읽지 못했습니다. 글자를 복사할 수 있는 파일인지 확인해 주십시오.")
    return ImportOut(file_name=name[:100], **split_posting_text(text))
