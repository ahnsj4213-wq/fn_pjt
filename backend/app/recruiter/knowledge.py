"""The company's knowledge base (ADR-0020): project and technology documents the interview questions are made from.

Always the signed-in recruiter's company; a posting must be that company's own. A saved document is split into chunks and
embedded by the `index_company_knowledge` job. Deleting does not touch question sets already written: they keep the quote.
"""
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import RecruiterCtx, require_recruiter
from app.auth.scope import get_for_company
from app.core.db import get_db
from app.core.errors import not_found
from app.jobs.queue import enqueue
from app.models import CompanyKnowledge, JobPosting
from app.questions.knowledge import split_document

router = APIRouter(prefix="/api/recruiter/knowledge", tags=["recruiter"])


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    doc_type: Literal["PROJECT", "TECH", "COMPANY"]
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=20, max_length=20000)
    posting_id: UUID | None = None  # omitted: every posting of the company


class DocumentOut(BaseModel):
    doc_id: UUID
    doc_type: str
    title: str
    posting_id: UUID | None
    chunks: int
    indexed: bool


async def _documents(db: AsyncSession, company_id: UUID, doc_id: UUID | None = None) -> list[DocumentOut]:
    K = CompanyKnowledge
    stmt = (select(K.doc_id, K.doc_type, K.title, K.posting_id, func.count(), func.count(K.embedding), func.min(K.created_at))
            .where(K.company_id == company_id).group_by(K.doc_id, K.doc_type, K.title, K.posting_id)
            .order_by(func.min(K.created_at).desc()))
    if doc_id is not None:
        stmt = stmt.where(K.doc_id == doc_id)
    return [DocumentOut(doc_id=d, doc_type=t, title=title, posting_id=p, chunks=n, indexed=n == done)
            for d, t, title, p, n, done, _ in (await db.execute(stmt)).all()]


@router.get("")
async def list_documents(ctx: RecruiterCtx = Depends(require_recruiter), db: AsyncSession = Depends(get_db)) -> list[DocumentOut]:
    return await _documents(db, ctx.company_id)


@router.post("", status_code=201)
async def add_document(body: DocumentIn, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                       db: AsyncSession = Depends(get_db)) -> DocumentOut:
    if body.posting_id is not None:
        await get_for_company(db, JobPosting, body.posting_id, ctx.company_id)  # someone else's posting -> 404
    doc_id = uuid4()
    for n, chunk in enumerate(split_document(body.text), start=1):
        db.add(CompanyKnowledge(company_id=ctx.company_id, posting_id=body.posting_id, doc_id=doc_id, chunk_no=n, doc_type=body.doc_type,
                                title=body.title, text=chunk, origin="RECRUITER", created_by=ctx.user_id))
    await db.flush()
    await enqueue(db, "index_company_knowledge", {}, dedupe_key="index_company_knowledge")
    await audit(db, action="KNOWLEDGE_ADD", target_type="knowledge_doc", target_id=doc_id, actor_id=ctx.user_id, actor_role="RECRUITER",
                company_id=ctx.company_id, request_id=getattr(request.state, "request_id", None))
    out = (await _documents(db, ctx.company_id, doc_id))[0]
    await db.commit()
    return out


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: UUID, request: Request, ctx: RecruiterCtx = Depends(require_recruiter),
                          db: AsyncSession = Depends(get_db)) -> None:
    res = await db.execute(delete(CompanyKnowledge).where(CompanyKnowledge.doc_id == doc_id, CompanyKnowledge.company_id == ctx.company_id))
    if not res.rowcount:
        raise not_found()
    await audit(db, action="KNOWLEDGE_DELETE", target_type="knowledge_doc", target_id=doc_id, actor_id=ctx.user_id, actor_role="RECRUITER",
                company_id=ctx.company_id, request_id=getattr(request.state, "request_id", None))
    await db.commit()
