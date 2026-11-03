"""Registered resume (RES-07) and file upload/extraction (RES-02~05): text PDFs, scanned PDFs and photos (OCR), DOCX."""
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CandidateCtx, require_candidate
from app.auth.scope import get_for_owner
from app.core.constants import RESUME_MAX_BYTES
from app.core.db import get_db
from app.core.errors import AppError
from app.core.storage import get_storage
from app.core.timeutil import utcnow
from app.jobs.queue import enqueue
from app.models import Resume, ResumeFile
from app.resumes import ocr
from app.resumes.schemas import ResumeContent

router = APIRouter(prefix="/api/candidate", tags=["candidate"])

PARSE_TIMEOUT_SEC = 20
_EXTRACTOR = Path(__file__).with_name("pdf_extract.py")
_DOCX_EXTRACTOR = Path(__file__).with_name("docx_extract.py")
# Accepted uploads: extension -> (magic bytes check, media type). Photos are read by OCR (RES-04); DOCX by SC-A10.
FILE_TYPES = {
    ".pdf": (lambda d: b"%PDF-" in d[:1024], "application/pdf"),
    ".png": (lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"),
    ".jpg": (lambda d: d.startswith(b"\xff\xd8\xff"), "image/jpeg"),
    ".jpeg": (lambda d: d.startswith(b"\xff\xd8\xff"), "image/jpeg"),
    ".docx": (lambda d: d.startswith(b"PK\x03\x04"),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}


def storage_key(name: str) -> str:
    """`resume_files.storage_key` holds the bare name; the storage key adds the folder."""
    return f"resumes/{name}"


def media_type(name: str) -> str:
    return FILE_TYPES[Path(name).suffix.lower()][1]


async def _read_limited(upload: UploadFile) -> bytes:
    buf = bytearray()
    while chunk := await upload.read(64 * 1024):
        buf += chunk
        if len(buf) > RESUME_MAX_BYTES:
            raise AppError(413, "FILE_TOO_LARGE", "파일 크기는 10MB 이하여야 합니다.")
    return bytes(buf)


async def parse_pdf(data: bytes) -> dict:
    return await _run_extractor(_EXTRACTOR, data, ".pdf")


async def parse_docx(data: bytes) -> dict:
    return await _run_extractor(_DOCX_EXTRACTOR, data, ".docx")


async def _run_extractor(script: Path, data: bytes, suffix: str) -> dict:
    """Parses in a separate interpreter (T22) from a private temporary file, deleted right after."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aihire-parse-") as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(sys.executable, "-I", str(script), str(path),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=PARSE_TIMEOUT_SEC)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": "TIMEOUT"}
    try:
        return json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "UNREADABLE"}


@router.post("/resume-files", status_code=201)
async def upload_resume(file: UploadFile, ctx: CandidateCtx = Depends(require_candidate),
                        db: AsyncSession = Depends(get_db)) -> dict:
    data = await _read_limited(file)
    ext = Path((file.filename or "").lower()).suffix
    if ext not in FILE_TYPES or not FILE_TYPES[ext][0](data):
        raise AppError(415, "UNSUPPORTED_FILE", "PDF, DOCX, PNG, JPG 파일만 올릴 수 있습니다.")
    key = f"{uuid.uuid4().hex}{'.jpg' if ext == '.jpeg' else ext}"  # server-generated; the user's filename is never a path
    if ext == ".pdf":
        parsed = await parse_pdf(data)
        if not parsed.get("ok"):
            if parsed.get("error") == "ENCRYPTED":
                raise AppError(422, "PDF_ENCRYPTED", "암호가 걸린 PDF는 올릴 수 없습니다.")
            if parsed.get("error") == "TOO_MANY_PAGES":
                raise AppError(422, "PDF_TOO_MANY_PAGES", "50쪽 이하의 PDF만 올릴 수 있습니다.")
            raise AppError(422, "PDF_UNREADABLE", "PDF 파일이 손상되었거나 열 수 없습니다. 다른 파일을 올려 주세요.")
        page_texts = parsed.get("page_texts") or []
        scanned = ocr.scanned_pages(page_texts)  # pages under 50 characters go to OCR, the rest keep their text
        text = "\n".join(t for i, t in enumerate(page_texts) if i not in scanned)
        pages = parsed.get("pages")
    elif ext == ".docx":
        parsed = await parse_docx(data)
        if not parsed.get("ok"):
            raise AppError(422, "DOCX_UNREADABLE", "DOCX 파일이 손상되었거나 열 수 없습니다. 다른 파일을 올려 주세요.")
        scanned, text, pages = [], (parsed.get("text") or "").strip(), None  # no pages, no OCR: the text is all there is
    else:
        scanned, text, pages = [0], "", 1  # a photo is one scanned page
    await get_storage().put(storage_key(key), data, media_type(key))
    rf = ResumeFile(owner_id=ctx.user_id, purpose="UPLOAD", storage_key=key, size_bytes=len(data),
                    sha256=hashlib.sha256(data).digest(), page_count=pages, text_extractable=not scanned,
                    extracted_text=text or None, ocr_pages=scanned or None, extraction_status="PENDING")
    db.add(rf)
    await db.flush()
    await enqueue(db, "extract_resume", {"file_id": str(rf.id)}, dedupe_key=f"extract:{rf.id}")
    await db.commit()
    return _file_out(rf)


async def copy_for_application(db: AsyncSession, src: ResumeFile) -> uuid.UUID:
    """Snapshot copy of the PDF for one application (RES-07); deleting the upload never affects it."""
    key = f"{uuid.uuid4().hex}{Path(src.storage_key).suffix}"
    await get_storage().copy(storage_key(src.storage_key), storage_key(key))
    rf = ResumeFile(owner_id=src.owner_id, purpose="APPLICATION", storage_key=key, size_bytes=src.size_bytes,
                    sha256=src.sha256, page_count=src.page_count, text_extractable=src.text_extractable,
                    extracted_text=src.extracted_text, extraction_status=src.extraction_status,
                    extracted_fields=src.extracted_fields, ocr_pages=src.ocr_pages,
                    extraction_method=src.extraction_method, ocr_confidence=src.ocr_confidence,
                    low_conf_fields=src.low_conf_fields)
    db.add(rf)
    await db.flush()
    return rf.id


def _file_out(rf: ResumeFile) -> dict:
    return {"file_id": str(rf.id), "page_count": rf.page_count, "text_extractable": rf.text_extractable,
            "extraction_status": rf.extraction_status, "fields": rf.extracted_fields,
            "method": rf.extraction_method,
            "ocr_confidence": float(rf.ocr_confidence) if rf.ocr_confidence is not None else None,
            "low_conf_fields": rf.low_conf_fields or [],
            "manual_input_required": rf.extraction_status in ("NO_TEXT", "FAILED")}


@router.get("/resume-files/{file_id}")
async def get_resume_file(file_id: uuid.UUID, ctx: CandidateCtx = Depends(require_candidate),
                          db: AsyncSession = Depends(get_db)) -> dict:
    return _file_out(await get_for_owner(db, ResumeFile, file_id, ctx.user_id, owner_col="owner_id"))


@router.get("/resume")
async def get_resume(ctx: CandidateCtx = Depends(require_candidate), db: AsyncSession = Depends(get_db)) -> dict:
    r = await db.get(Resume, ctx.user_id)
    if r is None:
        return {"resume": None}
    return {"resume": {"content": r.content, "source": r.source, "file_id": r.file_id, "updated_at": r.updated_at}}


@router.put("/resume")
async def put_resume(request: Request, ctx: CandidateCtx = Depends(require_candidate),
                     db: AsyncSession = Depends(get_db)) -> dict:
    body = await request.json()
    content = parse_resume_content(body.get("content"))
    file_id = body.get("file_id")
    if file_id:
        await get_for_owner(db, ResumeFile, file_id, ctx.user_id, owner_col="owner_id")
    await db.execute(insert(Resume).values(
        candidate_id=ctx.user_id, content=content.model_dump(), source="PDF" if file_id else "MANUAL", file_id=file_id,
        updated_at=utcnow()).on_conflict_do_update(index_elements=["candidate_id"], set_={
            "content": content.model_dump(), "source": "PDF" if file_id else "MANUAL", "file_id": file_id,
            "updated_at": utcnow()}))
    await db.commit()
    return {"ok": True}


def parse_resume_content(raw) -> ResumeContent:
    from pydantic import ValidationError
    try:
        return ResumeContent.model_validate(raw)
    except ValidationError as e:
        if any("RESUME_MIN_FIELDS" in str(err.get("msg", "")) for err in e.errors()):
            raise AppError(422, "RESUME_MIN_FIELDS", "성명과 학력 또는 경력을 1건 이상 입력해 주세요.") from None
        fields = [{"loc": ["content", *[str(p) for p in err["loc"]]], "type": err["type"]} for err in e.errors()]
        raise AppError(422, "VALIDATION", "입력값을 확인해 주세요.", fields=fields) from None
