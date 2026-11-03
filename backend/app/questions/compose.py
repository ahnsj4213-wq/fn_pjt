"""Question-set composition (ADR-0005, QST-01~07, screen spec SC-A06/SC-R07). Runs as the `compose_questions` job.

The posting settings hold a question plan of 1~10 slots [{area, text}] (default: 자기소개, 지원 동기, 직무, 경험, 협업).
A slot with text is asked as written (source=CUSTOM); an empty slot is filled by area:
- 인성 PERSONALITY, 협업 COLLABORATION: bank only (least used, ties random), skipping similar questions.
- 지원 동기 MOTIVATION: the COMPANY bank, topped up with T2 (company-scoped, C2: never reaches another company).
- 직무 기술 JOB_SKILL: the JOB bank of the job family, topped up with T2. The first empty job slot is live coding
  (answer_mode=CODE, 3 min) in 개발·데이터·AI postings when a coding problem exists, otherwise a whiteboard question
  (2 min, the spoken explanation is evaluated).
- 프로젝트 경험 PROJECT: T3 against the resume snapshot; what T3 cannot ground comes from the job bank
  (BANK_FALLBACK). In 개발·데이터·AI postings the candidate may open the whiteboard.
Before T2/T3 the composer looks up similar reference questions (RAG, app/questions/rag.py) and passes them as
examples; a generated question that copies one is dropped.
Generated questions that pass are added to the company's bank unless an exact or embedding duplicate
(cosine >= 0.85) is there. Last resort: shared seed questions of the same kind regardless of job family.

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
from app.ai.tasks.questions import flatten_resume, similar_question
from app.core.config import get_settings
from app.core.constants import (
    CODE_ANSWER_LIMIT_SEC,
    CODING_FAMILIES,
    DEFAULT_PLAN,
    EMBED_DIM,
    Area,
)
from app.core.logging import log_event
from app.core.timeutil import utcnow
from app.models import (
    Application,
    CodingProblem,
    Company,
    InterviewQuestion,
    JobPosting,
    PostingEvalSettings,
    QuestionBank,
    QuestionSet,
)
from app.questions import rag
from app.resumes.schemas import to_ai_resume

log = logging.getLogger("app.questions")
COMPOSER_VERSION = "compose-v3"  # v3: RAG examples for T2/T3
EMBED_DUP_COSINE = 0.85  # TRD initial value, calibrated by S-14


class ComposeError(Exception):
    """Not enough questions even after the seed fallback (operations checklist: seed minimums)."""


def norm_hash(s: str) -> bytes:
    """Same rule as the seed migration: NFKC, all whitespace removed, SHA-256."""
    return hashlib.sha256(re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).encode()).digest()


@dataclass
class Picked:
    seq: int
    slot: str  # Area
    text: str
    source: str  # BANK | AI | CUSTOM | BANK_FALLBACK | PROBLEM
    bank_id: uuid.UUID | None = None
    grounding: dict | None = None
    prompt_version: str | None = None


@dataclass
class Slot:
    seq: int
    area: Area
    text: str | None
    mode: str = "SPEECH"  # SPEECH | CODE


def plan_of(settings: PostingEvalSettings) -> list[tuple[Area, str | None]]:
    if settings.question_plan:
        return [(Area(x["area"]), (x.get("text") or "").strip() or None) for x in settings.question_plan]
    plan = list(DEFAULT_PLAN)
    custom = (settings.custom_question or "").strip()
    if custom:  # the old single custom question (X-09) takes the job slot of the default plan
        plan = [(a, custom if a == Area.JOB_SKILL else t) for a, t in plan]
    return plan


@dataclass
class _Ctx:
    app: Application
    posting: JobPosting
    company: Company
    settings: PostingEvalSettings
    ai: AIClient
    embed_model: str = ""
    db: AsyncSession | None = None
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


def _take_from_bank(c: _Ctx, rows: list[QuestionBank], n: int, slots: list[tuple[int, str]], source: str) -> int:
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


async def _with_examples(c: _Ctx, kind: str, query: str, inp: dict) -> list[rag.Example]:
    """RAG (ai-architecture 6.1): nearest reference questions of the same kind go into the prompt as examples."""
    examples = await rag.find_examples(c.db, c.ai, c.embed_model, kind, query, get_settings().rag_example_count,
                                       exclude=c.texts())
    inp["examples"] = [e.text for e in examples]
    return examples


async def _drop_copies(c: _Ctx, items: list[dict], examples: list[rag.Example]) -> list[dict]:
    hit = await rag.copied(c.db, c.ai, c.embed_model, [i["text"] for i in items], examples,
                           get_settings().rag_copy_cosine)
    if hit:
        log_event(log, "rag_copy_dropped", application_id=str(c.app.id), count=len(hit))
    return [i for n, i in enumerate(items) if n not in hit]


async def _generate(c: _Ctx, kind: str, n: int) -> list[dict]:
    """T2 for COMPANY/JOB. Returns validated questions (maybe fewer than n). AIUnavailable propagates (defer)."""
    p = c.posting
    inp = {"kind": kind, "job_family": p.job_family or "", "n": n, "existing": c.texts(),
           "posting": {"title": p.title, "duties": p.duties, "requirements": p.requirements, "preferred": p.preferred},
           "company_intro": c.company.intro or ""}
    query = job_brief(p) if kind == "JOB" else "\n".join(x for x in (p.title, c.company.intro or "", p.duties or "") if x)
    examples = await _with_examples(c, kind, query, inp)
    r = await c.ai.llm("question_generate", inp, lane="INTERACTIVE")
    if r.status != "OK":
        log_event(log, "question_generate_failed", application_id=str(c.app.id), status=r.status)
        return []
    return await _drop_copies(c, r.output.get("questions", []), examples)


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


async def _fill_generated_kind(db: AsyncSession, c: _Ctx, kind: str, slots: list[tuple[int, str]],
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


async def _fill_resume(db: AsyncSession, c: _Ctx, slots: list[tuple[int, str]]) -> None:
    resume = to_ai_resume(c.app.resume_snapshot)
    inp = {"resume": resume, "job_brief": job_brief(c.posting), "n": len(slots)}
    examples = await _with_examples(c, "RESUME", flatten_resume(resume)[0], inp)
    r = await c.ai.llm("resume_question", inp, lane="INTERACTIVE")
    qs = await _drop_copies(c, r.output.get("questions", []), examples) if r.status == "OK" else []
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


async def _coding_problem(db: AsyncSession, posting: JobPosting) -> CodingProblem | None:
    """The posting's problem, else the first active one (until SC-R04 lets every posting pick its own)."""
    if posting.coding_problem_id:
        return await db.get(CodingProblem, posting.coding_problem_id)
    return (await db.execute(select(CodingProblem).where(CodingProblem.is_active)
                             .order_by(CodingProblem.title).limit(1))).scalar_one_or_none()


async def plan_slots(db: AsyncSession, posting: JobPosting, settings: PostingEvalSettings
                     ) -> tuple[list[Slot], CodingProblem | None]:
    coding_family = posting.job_family in CODING_FAMILIES
    problem = await _coding_problem(db, posting) if coding_family else None
    slots, job_mode_set = [], False
    for seq, (area, txt) in enumerate(plan_of(settings), start=1):
        sl = Slot(seq, area, txt)
        if area == Area.JOB_SKILL and txt is None and not job_mode_set:
            if problem is not None:
                sl.mode = "CODE"  # no coding problem: the job question is an ordinary spoken one (no whiteboard anymore)
            job_mode_set = True
        slots.append(sl)
    return slots, problem


async def _fill_bank_only(db: AsyncSession, c: _Ctx, kind: str, slots: list[tuple[int, str]]) -> None:
    rows = await _bank_candidates(db, kind, c.company.id, None)
    if _take_from_bank(c, rows, len(slots), slots, "BANK") < len(slots):
        raise ComposeError(f"{kind}: bank has fewer than {len(slots)} usable questions")


async def compose(db: AsyncSession, app: Application, settings: PostingEvalSettings, ai: AIClient,
                  embed_model: str) -> tuple[list[Slot], list[Picked]]:
    posting = await db.get(JobPosting, app.posting_id)
    company = await db.get(Company, app.company_id)
    c = _Ctx(app, posting, company, settings, ai, embed_model, db)
    plan, problem = await plan_slots(db, posting, settings)
    empty: dict[Area, list[tuple[int, str]]] = {a: [] for a in Area}
    for sl in plan:
        if sl.text is not None:
            c.picked.append(Picked(sl.seq, sl.area.value, sl.text, "CUSTOM"))
        elif sl.mode == "CODE":
            c.picked.append(Picked(sl.seq, sl.area.value, f"{problem.title}: 화면의 문제를 코드로 풀면서, 어떻게 "
                                   "풀었는지 말로 설명해 주십시오.", "PROBLEM", grounding={"problem_id": str(problem.id)}))
        else:
            empty[sl.area].append((sl.seq, sl.area.value))
    await _fill_bank_only(db, c, "PERSONALITY", empty[Area.PERSONALITY])
    await _fill_bank_only(db, c, "COLLABORATION", empty[Area.COLLABORATION])
    if empty[Area.PROJECT]:
        await _fill_resume(db, c, empty[Area.PROJECT])
    if empty[Area.MOTIVATION]:
        await _fill_generated_kind(db, c, "COMPANY", empty[Area.MOTIVATION], embed_model)
    if empty[Area.JOB_SKILL]:
        await _fill_generated_kind(db, c, "JOB", empty[Area.JOB_SKILL], embed_model)
    return plan, sorted(c.picked, key=lambda p: p.seq)


async def write_set(db: AsyncSession, app: Application, settings: PostingEvalSettings, plan: list[Slot],
                    picked: list[Picked]) -> QuestionSet:
    """One READY set with its immutable MAIN rows (the live-set unique index forbids a second live set)."""
    qset = QuestionSet(application_id=app.id, company_id=app.company_id, settings_id=settings.id, status="READY",
                       composer_version=COMPOSER_VERSION, ready_at=utcnow(), main_count=len(plan),
                       followup_max=settings.followup_max)
    db.add(qset)
    await db.flush()
    by_seq = {sl.seq: sl for sl in plan}
    for p in picked:
        sl = by_seq[p.seq]
        limit = {"CODE": CODE_ANSWER_LIMIT_SEC}.get(sl.mode, settings.answer_sec)
        db.add(InterviewQuestion(question_set_id=qset.id, application_id=app.id, company_id=app.company_id, seq=p.seq,
                                 role="MAIN", slot_type=p.slot, source=p.source, bank_question_id=p.bank_id,
                                 text=p.text, grounding=p.grounding, answer_limit_sec=limit, answer_mode=sl.mode,
                                 think_sec=settings.think_sec,
                                 prompt_version=p.prompt_version))
    await db.flush()
    return qset
