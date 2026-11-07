"""Question-set composition (ADR-0005, QST-01~07, screen spec SC-A06/SC-R07). Runs as the `compose_questions` job.

The posting settings hold a question plan of 1~10 slots [{area, text}] (default: 자기소개, 지원 동기, 직무, 경험, 협업).
A slot with text is asked as written (source=CUSTOM); an empty slot is filled by area:
- 인성 PERSONALITY, 협업 COLLABORATION: bank only (least used, ties random), skipping similar questions.
- 지원 동기 MOTIVATION: T2 from the company's documents (questions/knowledge.py, ADR-0020). A company without relevant documents
  gets a posting-based job question instead, never unverified facts. What T2 cannot make comes from the COMPANY bank.
- 직무 기술 JOB_SKILL: T2 from the posting and the company's documents, then the JOB bank of the job family.
  All spoken; a coding test comes after the last question.
- 프로젝트 경험 PROJECT: T3 against the resume snapshot; what T3 cannot ground comes from the job bank
  (BANK_FALLBACK). In 개발·데이터·AI postings the candidate may open the whiteboard.
Before T2/T3 the composer also looks up similar reference questions (RAG, app/questions/rag.py) and passes them as
examples; a generated question that copies one is dropped.
Generated questions grounded in the posting are added to the company's bank unless an exact or embedding duplicate
(cosine >= 0.85) is there; one grounded in a company document is not (the document may belong to one posting, and may be
removed). Last resort: shared seed questions of the same kind regardless of job family.
Every question's `grounding.sources` lists what it came from as {type, id}; `grounding.retrieved` the documents searched.

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
from app.ai.textrules import polite_did as _did, shared_terms, tidy_particles
from app.questions import knowledge, rag
from app.resumes.schemas import to_ai_resume

log = logging.getLogger("app.questions")
COMPOSER_VERSION = "compose-v5"  # v5: generated questions go through questions/rag_graph.py (ADR-0020)
RESUME_LINE_FLOOR = 0.45
EMBED_DUP_COSINE = 0.85  # TRD initial value, calibrated by S-14


# The motivation question: the model picks which document to ask about, the sentence is fixed (same reason as RESUME_TAILS below).
MOTIVATION_QUESTION = "이 회사의 ‘{title}’ 업무에서 어떤 점에 관심이 있고, 본인의 어떤 경험을 살릴 수 있다고 보십니까?"
CODE_QUESTION = "화면의 문제를 코드로 풀어 주십시오. 풀이 설명은 말하지 말고 코드에 주석으로 적어 주십시오."  # no speech on this turn


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
    if custom:  # the old single custom question (X-09) takes the first job slot of the default plan
        first_job = next((i for i, (a, _t) in enumerate(plan) if a == Area.JOB_SKILL), None)
        plan = [(a, custom if i == first_job else t) for i, (a, t) in enumerate(plan)]
    return plan


def custom_pick(settings: PostingEvalSettings, sl: Slot) -> Picked:
    """A question the company wrote itself: asked as written, its source is the settings version it was saved in."""
    return Picked(sl.seq, sl.area.value, sl.text, "CUSTOM", grounding={"sources": [{"type": "POSTING_SETTINGS", "id": str(settings.id)}]})


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
    answers: list[str] = field(default_factory=list)  # live composition: the candidate's latest answers, newest first

    def texts(self) -> list[str]:
        return [p.text for p in self.picked]

    def taken_ids(self) -> set[uuid.UUID]:
        return {p.bank_id for p in self.picked if p.bank_id}


def job_brief(posting: JobPosting) -> str:
    parts = [posting.title or "", f"업무: {posting.duties}" if posting.duties else "",
             f"자격요건: {posting.requirements}" if posting.requirements else "",
             f"우대사항: {posting.preferred}" if posting.preferred else ""]
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
        c.picked.append(Picked(seq, slot, row.text, source, bank_id=row.id, grounding={"sources": [{"type": "BANK", "id": str(row.id)}]}))
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
    from app.questions import rag_graph

    out = await rag_graph.run(c, kind, n)
    items, seen = out["accepted"], out["seen"]
    for i in items:
        i["retrieved"] = list(seen)
        chunk = seen.get(i["source"]["id"]) if i["source"]["type"] == "KNOWLEDGE" else None
        # A project or technology has a name to put in the fixed sentence; "회사 소개" does not, so the model's checked sentence stays.
        if kind == "COMPANY" and chunk is not None and chunk.doc_type != "COMPANY" and not chunk.shared:  # never name another company's project
            i["text"] = MOTIVATION_QUESTION.format(title=chunk.title)
        if out["no_company_info"]:
            i["replaced"] = "NO_COMPANY_KNOWLEDGE"
    return items


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


async def _rank_by_relevance(c: _Ctx, rows: list[QuestionBank], query: str) -> list[QuestionBank]:
    """Bank questions nearest to the posting first (the job bank of a family mixes front-end, back-end and operations
    questions; a back-end posting should not get a screen-rendering question while a database one is waiting).
    Embeds the few bank texts on the fly; embeddings down or a size mismatch keeps the usage-count order."""
    if len(rows) < 2 or not query.strip():
        return rows
    try:
        vecs = await c.ai.embed([query[:1500]] + [r.text for r in rows])
    except AIUnavailable:
        return rows
    if len(vecs) != len(rows) + 1 or any(len(v) != EMBED_DIM for v in vecs):
        return rows
    import numpy as np

    q, m = np.array(vecs[0]), np.array(vecs[1:])
    sims = (m @ q) / ((np.linalg.norm(m, axis=1) * np.linalg.norm(q)) + 1e-9)
    order = sorted(range(len(rows)), key=lambda i: -float(sims[i]))  # stable: equal scores keep the least-used first
    return [rows[i] for i in order]


async def _fill_generated_kind(db: AsyncSession, c: _Ctx, kind: str, slots: list[tuple[int, str]],
                               embed_model: str) -> None:
    gen = await _generate(c, kind, len(slots))
    gen = [g for g in gen if not any(similar_question(g["text"], t) for t in c.texts())][: len(slots)]
    shareable = [g for g in gen if g["source"]["type"] != "KNOWLEDGE"]
    bank_ids = dict(zip((id(g) for g in shareable), await _add_to_bank(db, c, kind, shareable, embed_model)))
    version = TASKS["question_generate"].full_version
    for (seq, slot), g in zip(slots, gen):
        grounding = {"basis": g.get("basis"), "sources": [g["source"]], "retrieved": g["retrieved"]}
        if g.get("replaced"):
            grounding["replaced"] = g["replaced"]
        c.picked.append(Picked(seq, slot, g["text"], "AI", bank_id=bank_ids.get(id(g)), grounding=grounding, prompt_version=version))
    rest = slots[len(gen):]
    if rest:
        rows = await _bank_candidates(db, kind, c.company.id, c.posting.job_family)
        if kind == "JOB":
            rows = await _rank_by_relevance(c, rows, job_brief(c.posting))
        rest = rest[_take_from_bank(c, rows, len(rest), rest, "BANK"):]
    if rest:  # last resort: shared seed of this kind for any job family
        seeds = [r for r in await _bank_candidates(db, kind, c.company.id, None, any_family=True) if r.source == "SEED"]
        rest = rest[_take_from_bank(c, seeds, len(rest), rest, "BANK"):]
    if rest:
        raise ComposeError(f"{kind}: {len(rest)} slot(s) left after seed fallback")


def resume_context(resume: dict, field: str) -> str:
    """Where in the resume a question comes from, in words the candidate recognises: the company and role, the project, ..."""
    m = re.match(r"(experience|projects|education|certifications)\[(\d+)\]\.", field or "")
    if not m:
        return "보유 기술" if (field or "").startswith("skills") else ""
    rows = resume.get(m.group(1)) or []
    row = rows[int(m.group(2))] if int(m.group(2)) < len(rows) else {}
    get = lambda k: (row.get(k) or "").strip()  # noqa: E731
    period = f"({get('period')})" if get("period") else ""
    if m.group(1) == "experience":
        return " ".join(x for x in (f"‘{get('company')}’" if get("company") else "", get("role"), "경력" + period) if x)
    if m.group(1) == "projects":
        return " ".join(x for x in (f"‘{get('title')}’" if get("title") else "", "프로젝트" + period) if x)
    if m.group(1) == "education":
        return " ".join(x for x in (get("school"), get("major"), "학력") if x)
    return f"자격증 ‘{get('name')}’" if get("name") else "자격증"


def _sentence_around(resume: dict, ev: dict) -> str | None:
    """The whole sentence of the resume value that the verified quote sits in (a three-word quote alone says little)."""
    m = re.match(r"(experience|projects|education|certifications)\[(\d+)\]\.(\w+)", ev.get("field") or "")
    if not m:
        return None
    rows = resume.get(m.group(1)) or []
    value = (rows[int(m.group(2))].get(m.group(3)) or "") if int(m.group(2)) < len(rows) else ""
    if not value or ev.get("start") is None:
        return None
    # A sentence ends at "." followed by a space or the end, so "3.2초" and "v1.4" stay whole.
    ends = [m.end() for m in re.finditer(r"[.!?。](?=\s|$)", value)]
    start = max((e for e in ends if e <= ev["start"]), default=0)
    end = min((e for e in ends if e >= ev["end"]), default=len(value))
    return value[start:end].strip() or None


# What is asked about the named part of the resume. Fixed on purpose: the model chooses WHICH part of the resume to ask about
# (and its quote is verified); a small model's own sentence turned into yes/no questions or added facts the resume does not
# have (a wrong job title), so the words around the verified quote come from code. Rotated so three questions differ.
RESUME_TAILS = (
    "이때 맡으신 역할은 무엇이었고, 무엇을 근거로 어떻게 해결하셨으며, 결과는 어땠습니까?",
    "그 과정에서 가장 어려웠던 점은 무엇이었고, 어떻게 극복하셨습니까?",
    "그 결과를 어떻게 확인하셨고, 다시 한다면 무엇을 다르게 하시겠습니까?",
)


def _scene(resume: dict, field: str) -> str:
    """Where the experience happened, as an interviewer would say it ("데이터웍스 DB 엔지니어 업무에서"): no "이력서", no dates."""
    m = re.match(r"(experience|projects|education|certifications)\[(\d+)\]\.", field or "")
    if not m:
        return ""
    rows = resume.get(m.group(1)) or []
    row = rows[int(m.group(2))] if int(m.group(2)) < len(rows) else {}
    get = lambda k: (row.get(k) or "").strip()  # noqa: E731
    if m.group(1) == "experience":
        who = " ".join(x for x in (get("company"), get("role")) if x)
        return f"{who} 업무에서" if who else ""
    if m.group(1) == "projects":
        t = get("title")
        return f"{t}{'' if t.endswith('프로젝트') else ' 프로젝트'}에서" if t else ""
    if m.group(1) == "education":
        who = " ".join(x for x in (get("school"), get("major")) if x)
        return f"{who} 과정에서" if who else ""
    return f"{get('name')} 자격증을 준비하시면서" if get("name") else ""


def _shorten(sentence: str, limit: int = 80) -> str:
    """Keep the end of a long sentence (where its verb is), cut at a clause boundary."""
    if len(sentence) <= limit:
        return sentence
    for m in re.finditer(r"(?:며|고|서|면|,)\s+", sentence):
        if len(sentence) - m.end() <= limit:
            return sentence[m.end():]
    return sentence[-limit:].split(" ", 1)[-1]


def ground_question(resume: dict, q: dict, n: int = 0) -> str:
    """The question: the verified resume sentence told back as the interviewer's own knowledge, then a fixed how/why question
    (RESUME_TAILS[n % 3]): "데이터웍스 DB 엔지니어 업무에서 … 원인을 찾으셨는데, <how/why question>". The resume is never named
    and nothing is quoted: it should sound like a question prepared from the resume, not one read off it. The facts come from the
    verified quote, so they are true whatever the model wrote."""
    ev = q["evidence"][0]
    excerpt = tidy_particles(_shorten((_sentence_around(resume, ev) or ev["text"]).strip().replace("\n", " ")))
    scene = _scene(resume, ev["field"])
    tail = RESUME_TAILS[n % len(RESUME_TAILS)]
    done = _did(excerpt)
    if done:
        return " ".join(x for x in (scene, f"{done}, {tail}") if x)
    topic = re.sub(r"[\s.…!]+$", "", excerpt)
    return " ".join(x for x in (scene, f"{topic} 경험에 대해 여쭙겠습니다. {tail}") if x)


async def _fill_resume(db: AsyncSession, c: _Ctx, slots: list[tuple[int, str]]) -> None:
    resume = to_ai_resume(c.app.resume_snapshot)
    flat = flatten_resume(resume)[0]
    # One search per resume line: the whole resume at once scores below the floor (ADR-0020). Lines without a sentence (skills) are left out.
    queries = [ln.split(": ", 1)[-1] for ln in flat.splitlines() if ln.split(": ", 1)[-1].count(" ") >= 2][:5]
    chunks = await knowledge.search_many(c.db, c.ai, c.embed_model, company_id=c.company.id, posting_id=c.posting.id, queries=queries or [flat],
                                         k=get_settings().rag_knowledge_count, floor=RESUME_LINE_FLOOR, kind="RESUME")
    # what the job and the company actually use, and what the candidate has just talked about (live composition)
    wanted = "\n".join([*c.answers, job_brief(c.posting), *(f"{x.title} {x.text}" for x in chunks)])
    inp = {"resume": resume, "job_brief": job_brief(c.posting), "n": len(slots), "shared_terms": shared_terms(wanted, flat)}
    examples = await _with_examples(c, "RESUME", flat, inp)
    r = await c.ai.llm("resume_question", inp, lane="INTERACTIVE")
    qs = await _drop_copies(c, r.output.get("questions", []), examples) if r.status == "OK" else []
    version = TASKS["resume_question"].full_version
    used = 0
    for (seq, slot), q in zip(slots, qs):
        if any(similar_question(q["text"], t) for t in c.texts()):
            continue
        c.picked.append(Picked(seq, slot, ground_question(resume, q, used), "AI", grounding={"evidence": q["evidence"], "asked": q["text"],
                                                                                 "sources": [{"type": "RESUME_SNAPSHOT", "id": str(c.app.id), "field": e["field"]} for e in q["evidence"]],
                                                                                 "retrieved": [str(x.id) for x in chunks]},
                               prompt_version=version))
        used += 1
    rest = slots[used:]
    if rest:  # OQ-37: same job family's job bank, slot stays RESUME
        rows = await _rank_by_relevance(c, await _bank_candidates(db, "JOB", c.company.id, c.posting.job_family), job_brief(c.posting))
        rest = rest[_take_from_bank(c, rows, len(rest), rest, "BANK_FALLBACK"):]
    if rest:
        seeds = [x for x in await _bank_candidates(db, "JOB", c.company.id, None, any_family=True) if x.source == "SEED"]
        rest = rest[_take_from_bank(c, seeds, len(rest), rest, "BANK_FALLBACK"):]
    if rest:
        raise ComposeError(f"RESUME: {len(rest)} slot(s) left after fallback")


async def plan_slots(db: AsyncSession, posting: JobPosting, settings: PostingEvalSettings
                     ) -> tuple[list[Slot], CodingProblem | None]:
    """Every question is spoken; a coding test runs after the last one (turns.py -> CODING). The CODE branches of compose
    remain for the shape of a live-coding slot: question sets composed earlier still hold such turns."""
    return [Slot(seq, area, txt) for seq, (area, txt) in enumerate(plan_of(settings), start=1)], None


async def _fill_bank_only(db: AsyncSession, c: _Ctx, kind: str, slots: list[tuple[int, str]], about: str = "") -> None:
    """Bank questions nearest to this candidate's work and the company's documents first, then adapted (_adapt_bank_picks)."""
    rows = await _rank_by_relevance(c, await _bank_candidates(db, kind, c.company.id, None), about)
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
            c.picked.append(custom_pick(settings, sl))
        elif sl.mode == "CODE":
            c.picked.append(Picked(sl.seq, sl.area.value, f"{problem.title}: {CODE_QUESTION}", "PROBLEM", grounding={"problem_id": str(problem.id)}))
        else:
            empty[sl.area].append((sl.seq, sl.area.value))
    resume, docs, about = await adapt_context(c)
    await _fill_bank_only(db, c, "PERSONALITY", empty[Area.PERSONALITY], about)
    await _fill_bank_only(db, c, "COLLABORATION", empty[Area.COLLABORATION], about)
    if empty[Area.PROJECT]:
        await _fill_resume(db, c, empty[Area.PROJECT])
    if empty[Area.MOTIVATION]:
        await _fill_generated_kind(db, c, "COMPANY", empty[Area.MOTIVATION], embed_model)
    if empty[Area.JOB_SKILL]:
        await _fill_generated_kind(db, c, "JOB", empty[Area.JOB_SKILL], embed_model)
    await _adapt_bank_picks(c, resume, docs)
    return plan, sorted(c.picked, key=lambda p: p.seq)


ADAPT_DOCUMENTS = 4


async def adapt_context(c: _Ctx) -> tuple[dict, list, str]:
    """The candidate's resume (no name or contact), the company documents for the posting, and both as one text to rank bank
    questions by (the graph's personality, collaboration and adapt nodes each ask for it again: nothing of it is checkpointed)."""
    resume = to_ai_resume(c.app.resume_snapshot) if c.app.resume_snapshot else {}
    docs = await _adapt_documents(c)
    return resume, docs, "\n".join([*c.answers, flatten_resume(resume)[0], *(f"{x.title} {x.text}" for x in docs)])[:1500]


async def _adapt_documents(c: _Ctx) -> list:
    """Company documents for fitting bank questions: this company's, or the other companies' as job cases when it has none."""
    from app.questions import rag_graph

    p = c.posting
    queries = rag_graph.posting_queries("JOB", p.title or "", c.company.intro or "", p.duties, p.requirements, p.preferred)
    search = dict(company_id=c.company.id, posting_id=p.id, queries=queries, k=ADAPT_DOCUMENTS, floor=rag_graph.LINE_QUERY_FLOOR, kind="ADAPT")
    return (await knowledge.search_many(c.db, c.ai, c.embed_model, **search)
            or await knowledge.search_many(c.db, c.ai, c.embed_model, shared=True, **search))


async def _adapt_bank_picks(c: _Ctx, resume: dict, docs: list, only: set[int] | None = None) -> None:
    """T12: every common question taken from the bank is asked again with something concrete from the resume or the company documents.
    One call for the whole set; a question that fails the checks keeps the bank's text. Embeddings or model down -> bank text.
    `only`: the seqs to adapt (live composition adapts the one question it just made, not the ones already asked)."""
    picks = [p for p in c.picked if p.source in ("BANK", "BANK_FALLBACK") and (only is None or p.seq in only)]
    if not picks or not (resume or docs):
        return
    items = [{"id": str(i), "kind": p.slot, "text": p.text} for i, p in enumerate(picks)]
    inp = {"items": items, "resume": resume, "job_brief": job_brief(c.posting), "knowledge": [x.as_input() for x in docs]}
    try:
        r = await c.ai.llm("question_adapt", inp, lane="INTERACTIVE")
    except AIUnavailable:
        return
    if r.status != "OK":
        log_event(log, "question_adapt_failed", application_id=str(c.app.id), status=r.status)
        return
    version = TASKS["question_adapt"].full_version
    adapted = 0
    for q in r.output.get("questions", []):
        p = picks[int(q["id"])]
        others = [x.text for x in c.picked if x is not p]
        if any(similar_question(q["text"], t) for t in others):
            continue
        source = q["source"] | {"field": q["basis"]["field"], "id": str(c.app.id)} if "field" in q["basis"] else q["source"]
        p.grounding = {**(p.grounding or {}), "adapted_from": p.text, "basis": q["basis"],
                       "sources": [*(p.grounding or {}).get("sources", []), source], "retrieved": [str(x.id) for x in docs]}
        p.text, p.prompt_version = q["text"], version
        adapted += 1
    log_event(log, "question_adapt", application_id=str(c.app.id), asked=len(picks), adapted=adapted)


def question_row(qset: QuestionSet, app: Application, settings: PostingEvalSettings, sl: Slot, p: Picked) -> InterviewQuestion:
    limit = {"CODE": CODE_ANSWER_LIMIT_SEC}.get(sl.mode, settings.answer_sec)
    return InterviewQuestion(question_set_id=qset.id, application_id=app.id, company_id=app.company_id, seq=p.seq,
                             role="MAIN", slot_type=p.slot, source=p.source, bank_question_id=p.bank_id,
                             text=p.text, grounding=p.grounding, answer_limit_sec=limit, answer_mode=sl.mode,
                             think_sec=settings.think_sec, prompt_version=p.prompt_version)


def plan_to_json(plan: list[Slot]) -> list[dict]:
    return [{"seq": s.seq, "area": s.area.value, "text": s.text, "mode": s.mode} for s in plan]


def plan_from_json(rows: list[dict]) -> list[Slot]:
    return [Slot(r["seq"], Area(r["area"]), r.get("text"), r.get("mode") or "SPEECH") for r in rows]


async def write_set(db: AsyncSession, app: Application, settings: PostingEvalSettings, plan: list[Slot],
                    picked: list[Picked], *, live: bool = False) -> QuestionSet:
    """One READY set with its immutable MAIN rows (the live-set unique index forbids a second live set). `live`: only the first
    question is in `picked`; the plan is kept so app.questions.live composes the others during the interview."""
    qset = QuestionSet(application_id=app.id, company_id=app.company_id, settings_id=settings.id, status="READY",
                       composer_version=COMPOSER_VERSION, ready_at=utcnow(), main_count=len(plan),
                       followup_max=settings.followup_max, plan=plan_to_json(plan) if live else None)
    db.add(qset)
    await db.flush()
    by_seq = {sl.seq: sl for sl in plan}
    for p in picked:
        db.add(question_row(qset, app, settings, by_seq[p.seq], p))
    await db.flush()
    return qset
