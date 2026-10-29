"""Question-set composition (ADR-0005, 6.1, QST-01~07). Runs as the `compose_questions` job (INTERACTIVE lane).

Slots are fixed: [인성 1·2, 이력서 3·4, 기업 5·6, 직무 7~10]; a custom question takes seq 7 and the job bank fills 3.
- 인성/기업/직무: bank first (least used, ties random), scope `is_active AND (company_id IS NULL OR = posting
  company)` (C2: one company's AI questions never reach another), skipping questions similar to ones already picked.
  Shortfalls for 기업/직무 are generated with T2; questions that pass are added to the company's bank (source=AI)
  unless an exact or embedding duplicate (cosine >= 0.85) is already there.
- 이력서: T3 against the application's resume snapshot; whatever T3 cannot ground is filled from the job bank
  (source=BANK_FALLBACK, slot stays RESUME, OQ-37).
- Generation is tried at most twice per slot group (the task regenerates once inside, T17); job attempts are only for
  infrastructure failures. Last resort: shared seed questions of the same kind regardless of job family.

Everything is composed in memory and written in one transaction (the snapshot rows are immutable, QST-06).
Usage counts are bumped when the interview starts, not here.
"""
import hashlib
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, AIUnavailable
from app.ai.tasks import TASKS
from app.ai.tasks.questions import similar_question
from app.core.constants import EMBED_DIM, MAIN_ANSWER_LIMIT_SEC, SLOT_ORDER, Slot
from app.core.logging import log_event
from app.core.timeutil import utcnow
from app.models import (
    Application,
    Company,
    InterviewQuestion,
    JobPosting,
    PostingEvalSettings,
    QuestionBank,
    QuestionSet,
)
from app.resumes.schemas import to_ai_resume

log = logging.getLogger("app.questions")
COMPOSER_VERSION = "compose-v1"
EMBED_DUP_COSINE = 0.85  # TRD initial value, calibrated by S-14
CUSTOM_SEQ = 7  # OQ-36 (provisional): the first job slot


class ComposeError(Exception):
    """Not enough questions even after the seed fallback (operations checklist: seed minimums)."""


def norm_hash(s: str) -> bytes:
    """Same rule as the seed migration: NFKC, all whitespace removed, SHA-256."""
    return hashlib.sha256(re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).encode()).digest()


@dataclass
class Picked:
    seq: int
    slot: Slot
    text: str
    source: str  # BANK | AI | CUSTOM | BANK_FALLBACK
    bank_id: uuid.UUID | None = None
    grounding: dict | None = None
    prompt_version: str | None = None


@dataclass
class _Ctx:
    app: Application
    posting: JobPosting
    company: Company
    settings: PostingEvalSettings
    ai: AIClient
    picked: list[Picked] = field(default_factory=list)

    def texts(self) -> list[str]:
        return [p.text for p in self.picked]

    def taken_ids(self) -> set[uuid.UUID]:
        return {p.bank_id for p in self.picked if p.bank_id}


def job_brief(posting: JobPosting) -> str:
    parts = [posting.title or "", f"업무: {posting.duties}" if posting.duties else "",
             f"자격요건: {posting.requirements}" if posting.requirements else ""]
    return "\n".join(p for p in parts if p)[:800]


async def _bank_candidates(db: AsyncSession, kind: str, company_id: uuid.UUID, job_family: str | None,
                           *, any_family: bool = False) -> list[QuestionBank]:
    stmt = select(QuestionBank).where(QuestionBank.is_active, QuestionBank.kind == kind,
                                      (QuestionBank.company_id.is_(None)) | (QuestionBank.company_id == company_id))
    if kind == "JOB" and not any_family:
        stmt = stmt.where(QuestionBank.job_family == job_family)
    elif kind == "COMPANY" and not any_family:
        stmt = stmt.where((QuestionBank.job_family.is_(None)) | (QuestionBank.job_family == job_family))
    stmt = stmt.order_by(QuestionBank.usage_count, text("random()"))
    return list((await db.execute(stmt)).scalars())


def _take_from_bank(c: _Ctx, rows: list[QuestionBank], n: int, slots: list[tuple[int, Slot]], source: str) -> int:
    taken = 0
    for row in rows:
        if taken >= n:
            break
        if row.id in c.taken_ids() or any(similar_question(row.text, t) for t in c.texts()):
            continue
        seq, slot = slots[taken]
        c.picked.append(Picked(seq, slot, row.text, source, bank_id=row.id))
        taken += 1
    return taken


async def _generate(c: _Ctx, kind: str, n: int) -> list[dict]:
    """T2 for COMPANY/JOB. Returns validated questions (maybe fewer than n). AIUnavailable propagates (defer)."""
    p = c.posting
    inp = {"kind": kind, "job_family": p.job_family or "", "n": n, "existing": c.texts(),
           "posting": {"title": p.title, "duties": p.duties, "requirements": p.requirements, "preferred": p.preferred},
           "company_intro": c.company.intro or ""}
    r = await c.ai.llm("question_generate", inp, lane="INTERACTIVE")
    if r.status != "OK":
        log_event(log, "question_generate_failed", application_id=str(c.app.id), status=r.status)
        return []
    return r.output.get("questions", [])


async def _embed_or_none(ai: AIClient, texts: list[str]) -> list[list[float]] | None:
    try:
        return await ai.embed(texts)
    except AIUnavailable:
        return None  # 14: embeddings down -> exact-duplicate check only


async def _near_duplicate(db: AsyncSession, ids: list[uuid.UUID], vec: list[float], model: str) -> bool:
    """pgvector cosine distance (HNSW index, migration 0004) against the bank questions in scope."""
    if not ids:
        return False
    return (await db.execute(text(
        "SELECT 1 FROM question_bank WHERE id = ANY(:ids) AND embedding IS NOT NULL AND embedding_model = :m "
        "AND embedding <=> CAST(:v AS vector) <= :d LIMIT 1"),
        {"ids": ids, "m": model, "v": "[" + ",".join(f"{x:.7g}" for x in vec) + "]",
         "d": 1.0 - EMBED_DUP_COSINE})).first() is not None


async def _add_to_bank(db: AsyncSession, c: _Ctx, kind: str, items: list[dict], embed_model: str) -> list[uuid.UUID | None]:
    """Insert generated questions into the company's bank unless a duplicate exists (QST-01). Returns bank ids."""
    if not items:
        return []
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"qbank:{c.company.id}"})
    scope = await _bank_candidates(db, kind, c.company.id, c.posting.job_family)
    vectors = await _embed_or_none(c.ai, [i["text"] for i in items])
    ids: list[uuid.UUID | None] = []
    for n, item in enumerate(items):
        h = norm_hash(item["text"])
        if any(r.text_norm_hash == h for r in scope):
            ids.append(next(r.id for r in scope if r.text_norm_hash == h))
            continue
        vec = vectors[n] if vectors and len(vectors[n]) == EMBED_DIM else None
        if vec and await _near_duplicate(db, [r.id for r in scope], vec, embed_model):
            ids.append(None)  # near-duplicate of a bank question: used in this set, not stored again
            continue
        row = QuestionBank(kind=kind, job_family=c.posting.job_family if kind == "JOB" else None,
                           company_id=c.company.id, text=item["text"], text_norm_hash=h, source="AI",
                           embedding=vec, embedding_model=embed_model if vec else None)
        db.add(row)
        await db.flush()
        scope.append(row)
        ids.append(row.id)
    return ids


async def _fill_generated_kind(db: AsyncSession, c: _Ctx, kind: str, slots: list[tuple[int, Slot]],
                               embed_model: str) -> None:
    rows = await _bank_candidates(db, kind, c.company.id, c.posting.job_family)
    got = _take_from_bank(c, rows, len(slots), slots, "BANK")
    rest = slots[got:]
    if rest:
        gen = await _generate(c, kind, len(rest))
        gen = [g for g in gen if not any(similar_question(g["text"], t) for t in c.texts())][: len(rest)]
        bank_ids = await _add_to_bank(db, c, kind, gen, embed_model)
        version = TASKS["question_generate"].full_version
        for (seq, slot), g, bid in zip(rest, gen, bank_ids):
            c.picked.append(Picked(seq, slot, g["text"], "AI", bank_id=bid, grounding={"basis": g.get("basis")},
                                   prompt_version=version))
        rest = rest[len(gen):]
    if rest:  # last resort: shared seed of this kind for any job family
        seeds = [r for r in await _bank_candidates(db, kind, c.company.id, None, any_family=True) if r.source == "SEED"]
        rest = rest[_take_from_bank(c, seeds, len(rest), rest, "BANK"):]
    if rest:
        raise ComposeError(f"{kind}: {len(rest)} slot(s) left after seed fallback")


async def _fill_resume(db: AsyncSession, c: _Ctx, slots: list[tuple[int, Slot]]) -> None:
    inp = {"resume": to_ai_resume(c.app.resume_snapshot), "job_brief": job_brief(c.posting), "n": len(slots)}
    r = await c.ai.llm("resume_question", inp, lane="INTERACTIVE")
    qs = r.output.get("questions", []) if r.status == "OK" else []
    version = TASKS["resume_question"].full_version
    used = 0
    for (seq, slot), q in zip(slots, qs):
        if any(similar_question(q["text"], t) for t in c.texts()):
            continue
        c.picked.append(Picked(seq, slot, q["text"], "AI", grounding={"evidence": q["evidence"]}, prompt_version=version))
        used += 1
    rest = slots[used:]
    if rest:  # OQ-37: same job family's job bank, slot stays RESUME
        rows = await _bank_candidates(db, "JOB", c.company.id, c.posting.job_family)
        rest = rest[_take_from_bank(c, rows, len(rest), rest, "BANK_FALLBACK"):]
    if rest:
        seeds = [x for x in await _bank_candidates(db, "JOB", c.company.id, None, any_family=True) if x.source == "SEED"]
        rest = rest[_take_from_bank(c, seeds, len(rest), rest, "BANK_FALLBACK"):]
    if rest:
        raise ComposeError(f"RESUME: {len(rest)} slot(s) left after fallback")


async def compose(db: AsyncSession, app: Application, settings: PostingEvalSettings, ai: AIClient,
                  embed_model: str) -> list[Picked]:
    posting = await db.get(JobPosting, app.posting_id)
    company = await db.get(Company, app.company_id)
    c = _Ctx(app, posting, company, settings, ai)
    slots = list(enumerate(SLOT_ORDER, start=1))
    by_slot = {s: [(n, t) for n, t in slots if t == s] for s in Slot}

    rows = await _bank_candidates(db, "PERSONALITY", company.id, None)
    if _take_from_bank(c, rows, 2, by_slot[Slot.PERSONALITY], "BANK") < 2:
        raise ComposeError("PERSONALITY: bank has fewer than 2 usable questions")
    job_slots = by_slot[Slot.JOB]
    custom = (settings.custom_question or "").strip()
    if custom:
        c.picked.append(Picked(CUSTOM_SEQ, Slot.JOB, custom, "CUSTOM"))
        job_slots = [s for s in job_slots if s[0] != CUSTOM_SEQ]
    await _fill_resume(db, c, by_slot[Slot.RESUME])
    await _fill_generated_kind(db, c, "COMPANY", by_slot[Slot.COMPANY], embed_model)
    await _fill_generated_kind(db, c, "JOB", job_slots, embed_model)
    return sorted(c.picked, key=lambda p: p.seq)


async def write_set(db: AsyncSession, app: Application, settings_id: uuid.UUID, picked: list[Picked]) -> QuestionSet:
    """One READY set with its 10 immutable MAIN rows (the live-set unique index forbids a second live set)."""
    qset = QuestionSet(application_id=app.id, company_id=app.company_id, settings_id=settings_id, status="READY",
                       composer_version=COMPOSER_VERSION, ready_at=utcnow())
    db.add(qset)
    await db.flush()
    for p in picked:
        db.add(InterviewQuestion(question_set_id=qset.id, application_id=app.id, company_id=app.company_id, seq=p.seq,
                                 role="MAIN", slot_type=p.slot.value, source=p.source, bank_question_id=p.bank_id,
                                 text=p.text, grounding=p.grounding, answer_limit_sec=MAIN_ANSWER_LIMIT_SEC,
                                 prompt_version=p.prompt_version))
    await db.flush()
    return qset
