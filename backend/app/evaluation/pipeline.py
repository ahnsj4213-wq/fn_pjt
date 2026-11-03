"""Evaluation pipeline (TRD 05 §10, ADR-0006 §5~7, EVL-01~08).

- `evaluate_unit`: T5 for one answer unit (main or follow-up answer). Used by `pre_evaluate` during the interview
  and by the final run for units without a reusable pre-evaluation.
- `load_report_inputs` + `build_report`: the final run. The first reads everything in one short transaction; the
  second makes the model calls with no transaction open (they take minutes on the CPU, past the 30 s
  idle-in-transaction limit of core/db.py). Units come from `answer_pre_evaluations` with the current manifest hash
  or a fresh T5 call; scores are computed by code (scoring.py); T6 summary, T7 candidate feedback and T8 code review
  (P1, never scored) produce the prose. Nothing is written here: the caller persists inside its fenced transaction.
"""
import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import rubric
from app.ai.client import AIClient
from app.core.constants import CRITERIA, Criterion
from app.core.db import session_scope
from app.core.storage import get_storage
from app.evaluation import details
from app.evaluation.scoring import RULE_WEIGHT, SCORING_VERSION, QuestionScores, ScoreResult, UnitScores, compute, no_answer_unit
from app.models import (
    AnswerPreEvaluation,
    Application,
    CodingProblem,
    CodingSubmission,
    CodingTestcase,
    InterviewAnswer,
    InterviewQuestion,
    Job,
    JobPosting,
    PostingEvalSettings,
)
from app.coding.variants import problem_of
from app.questions.compose import job_brief

CRITERION_NAME = {i["criterion"]: i["name"] for i in rubric.ITEMS}
CRITERION_KEY = {i["criterion"]: i["key"] for i in rubric.ITEMS}


class EvaluationFailed(Exception):
    """A model step produced nothing usable (consumes the attempt; EVL-06 retries once, then ON_HOLD)."""


class CodingNotGraded(Exception):
    """The coding submission is still being graded: wait without consuming an attempt."""


CODE_MAX_CHARS = 6000  # keeps the prompt inside the eval model's context next to the answer


def unit_input(q: InterviewQuestion, a: InterviewAnswer, posting: JobPosting) -> dict:
    """T5 input: no resume, name, gender, age, photo or contact (EVL-07). eval_text is already redacted (rd-1)."""
    inp = {"question": q.text, "question_type": q.slot_type, "job_brief": job_brief(posting),
           "answer": a.eval_text or "", "is_no_answer": a.is_no_answer}
    if a.code_text:  # SC-A06 live coding: the submitted code is read with the answer (never executed)
        inp["code"], inp["code_language"] = a.code_text[:CODE_MAX_CHARS], a.code_language or ""
    return inp


async def evaluate_unit(ai: AIClient, q: InterviewQuestion, a: InterviewAnswer, posting: JobPosting) -> tuple[dict, str]:
    """Returns (payload, manifest_hash). Raises EvaluationFailed; AIUnavailable propagates (job deferral)."""
    r = await ai.llm("evaluate_answer", unit_input(q, a, posting), lane="BATCH")
    if r.status != "OK":
        raise EvaluationFailed(f"evaluate_answer {r.status}")
    return {**r.output, "flags": r.flags, "llm_calls": r.attempts.get("llm_calls", 0)}, r.manifest_hash


def unit_scores(payload: dict) -> UnitScores:
    items = payload["items"]
    scores = {Criterion(c): int(items[c]["score"]) for c in items}
    insufficient = {Criterion(c) for c in items if items[c]["evidence_status"] == "INSUFFICIENT"}
    no_answer = all(items[c]["evidence_status"] == "NO_ANSWER" for c in items)
    return UnitScores(scores, insufficient, no_answer)


@dataclass
class Report:
    manifest_hash: str
    units: dict  # answer_id -> payload
    scores: ScoreResult
    answered_main: int
    summary: dict
    feedback: dict
    code_review: dict | None
    coding: dict | None  # {"passed", "total"} when the posting has a coding test
    questions: list[tuple[InterviewQuestion, InterviewAnswer | None, InterviewQuestion | None, InterviewAnswer | None]] = \
        field(default_factory=list)
    detail: dict = field(default_factory=dict)  # sub-items and code-measured values (details.py), stored with the report


def _band(score: Decimal) -> str:
    return "상" if score >= 70 else "중" if score >= 50 else "하"


async def _coding(db: AsyncSession, app: Application, posting: JobPosting) -> tuple[dict | None, CodingSubmission | None]:
    if not posting.coding_enabled:
        return None, None
    sub = (await db.execute(select(CodingSubmission).where(CodingSubmission.application_id == app.id))).scalar_one_or_none()
    if sub is None:
        return {"passed": 0, "total": 0, "reached": False}, None  # OQ-07: never reached the coding step -> 0
    if sub.grading_status != "DONE":
        raise CodingNotGraded(str(sub.id))
    return {"passed": sub.passed, "total": sub.total, "score": sub.score, "reached": True}, sub


@dataclass
class ReportInputs:
    manifest_hash: str
    posting: JobPosting
    weights: dict
    questions: list[InterviewQuestion]
    answers: dict  # question_id -> answer
    cached: dict  # answer_id -> reusable pre-evaluation payload
    coding: dict | None
    submission: CodingSubmission | None
    problem: CodingProblem | None = None  # only when there is code to review (T8)
    public_tests: list[CodingTestcase] = field(default_factory=list)
    custom_items: list = field(default_factory=list)  # the company's own evaluation items of this posting's settings


async def load_report_inputs(db: AsyncSession, app: Application, manifest_hash: str) -> ReportInputs:
    """Raises CodingNotGraded before any model call when the submission is still being graded."""
    posting = await db.get(JobPosting, app.posting_id)
    settings = await db.get(PostingEvalSettings, app.eval_settings_id)
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == app.id)
                           .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars().all()
    answers = {a.question_id: a for a in (await db.execute(select(InterviewAnswer).where(
        InterviewAnswer.application_id == app.id))).scalars()}
    cached = {p.answer_id: p.payload for p in (await db.execute(select(AnswerPreEvaluation).where(
        AnswerPreEvaluation.answer_id.in_([a.id for a in answers.values()] or [None]),
        AnswerPreEvaluation.manifest_hash == manifest_hash))).scalars()}
    coding, sub = await _coding(db, app, posting)
    inp = ReportInputs(manifest_hash, posting, settings.all_weights(), list(qs), answers, cached, coding, sub,
                       custom_items=list(settings.custom_items or []))
    if sub is not None and sub.code.strip():
        inp.problem = await problem_of(db, app.id, posting)
        inp.public_tests = list((await db.execute(select(CodingTestcase).where(
            CodingTestcase.problem_id == inp.problem.id, CodingTestcase.visibility == "PUBLIC")
            .order_by(CodingTestcase.ordinal))).scalars().all())
    return inp


async def wait_for_pre_evaluations(inp: ReportInputs, max_wait_sec: float = 45.0) -> None:
    """Answers whose `pre_evaluate` job is still queued or running are waited for instead of being evaluated a second
    time: the same answer twice would occupy the model slot for twice as long. No job in flight = evaluate here."""
    deadline = time.monotonic() + max_wait_sec
    while True:
        missing = [a.id for a in inp.answers.values() if a.id not in inp.cached]
        if not missing or time.monotonic() >= deadline:
            return
        async with session_scope() as db:
            running = await db.scalar(select(func.count()).select_from(Job).where(
                Job.kind == "pre_evaluate", Job.state.in_(["QUEUED", "RUNNING"]),
                Job.dedupe_key.in_([f"pre:{m}" for m in missing])))
            for p in (await db.execute(select(AnswerPreEvaluation).where(
                    AnswerPreEvaluation.answer_id.in_(missing), AnswerPreEvaluation.manifest_hash == inp.manifest_hash))).scalars():
                inp.cached[p.answer_id] = p.payload
        if not running:
            return
        await asyncio.sleep(0.4)


async def build_report(ai: AIClient, app: Application, inp: ReportInputs) -> Report:
    posting, qs, answers, cached = inp.posting, inp.questions, inp.answers, inp.cached
    mains = [q for q in qs if q.role == "MAIN"]
    followups = {q.parent_id: q for q in qs if q.role == "FOLLOWUP"}
    units: dict = {}
    todo = []
    for q in qs:
        a = answers.get(q.id)
        if a is None:
            continue
        if cached.get(a.id):
            units[a.id] = cached[a.id]
        else:
            todo.append((q, a))
    for q, a in todo:  # normally only the last answer: the interview pre-evaluated the others
        units[a.id] = (await evaluate_unit(ai, q, a, posting))[0]

    rows, question_scores = [], []
    for m in mains:
        a = answers.get(m.id)
        fq = followups.get(m.id)
        fa = answers.get(fq.id) if fq else None
        rows.append((m, a, fq, fa))
        if a is None:
            if app.attempt_status == "SUBMITTED":
                question_scores.append(QuestionScores(m.seq, no_answer_unit()))  # never answered -> 답변 없음
            continue  # EXPIRED: only answered main questions count (OQ-07)
        question_scores.append(QuestionScores(m.seq, unit_scores(units[a.id]),
                                              unit_scores(units[fa.id]) if fa else None))
    if app.attempt_status == "SUBMITTED" and len(question_scores) != len(mains):
        raise EvaluationFailed(f"expected {len(mains)} main questions, found {len(question_scores)}")

    coding, weights = inp.coding, inp.weights
    coding_value = None
    if "coding" in weights:
        coding_value = Decimal(coding["score"]) if coding and coding.get("reached") else Decimal("0.00")
    det = await details.analyze(rows, units, get_storage())
    custom_scores, det.detail["custom"] = await _custom_items(ai, rows, inp.custom_items)
    scores = compute(question_scores, weights, coding_value, det.rule_scores, custom_scores)
    det.detail["blend"] = {"scoring": SCORING_VERSION, "rule_weight": float(RULE_WEIGHT), "rule_scores": {k: float(v) for k, v in det.rule_scores.items()}}

    summary = await _summary(ai, rows, units, scores)
    feedback = await _feedback(ai, rows, units, scores)
    review = await _code_review(ai, posting, inp) if inp.problem is not None else None
    answered_main = sum(1 for m, a, _, _ in rows if a is not None and not a.is_no_answer)
    return Report(inp.manifest_hash, units, scores, answered_main, summary, feedback, review, coding, rows, det.detail)


async def _custom_items(ai: AIClient, rows, items: list[dict]) -> tuple[dict[str, Decimal], list[dict]]:
    """Grades of the company's own items (0% items are not evaluated). Score = grade points x 20, by code."""
    active = [it for it in items if it.get("weight", 0) > 0]
    if not active:
        return {}, []
    answers = []
    for m, a, _fq, fa in rows:
        text = " ".join(x.eval_text for x in (a, fa) if x is not None and not x.is_no_answer and x.eval_text)
        if text.strip():
            answers.append({"no": m.seq, "question": m.text, "text": text})
    if answers:
        r = await ai.llm("custom_criteria", {"items": active, "answers": answers}, lane="BATCH")
        if r.status != "OK":
            raise EvaluationFailed(f"custom_criteria {r.status}")
        graded = r.output["items"]
    else:  # nothing was answered: every item is the lowest grade without evidence
        graded = {it["id"]: {"grade": "하", "score": 1, "evidence_status": "NO_ANSWER", "quote": None, "comment": ""} for it in active}
    scores, detail = {}, []
    for it in active:
        g = graded[it["id"]]
        scores[f"custom:{it['id']}"] = Decimal(g["score"] * 20)
        detail.append({"id": it["id"], "name": it["name"], "description": it.get("description") or "", "weight": it["weight"],
                       "grade": g["grade"], "score": g["score"] * 20, "evidence_status": g["evidence_status"],
                       "quote": (g["quote"] or {}).get("text"), "quote_no": (g["quote"] or {}).get("no"), "comment": g["comment"]})
    return scores, detail


def _comments(payload: dict | None) -> dict:
    if not payload:
        return {}
    return {c: v.get("comment", "") for c, v in payload["items"].items()}


async def _summary(ai: AIClient, rows, units: dict, scores: ScoreResult) -> dict:
    per_q = {q["seq"]: q for q in scores.question_scores}
    items = []
    for m, a, fq, fa in rows:
        if a is None:
            continue
        p = units[a.id]
        insufficient = [CRITERION_NAME[c] for c, v in p["items"].items() if v["evidence_status"] == "INSUFFICIENT"]
        items.append({"no": m.seq, "type": m.slot_type, "has_followup": fa is not None,
                      "scores": {CRITERION_NAME[c]: per_q[m.seq][CRITERION_KEY[c]] for c in p["items"]},
                      "comments": {CRITERION_NAME[c]: v for c, v in _comments(p).items()},
                      "insufficient": insufficient, "no_answer": a.is_no_answer})
    r = await ai.llm("report_summary", {"questions": items}, lane="BATCH")
    if r.status != "OK":
        raise EvaluationFailed(f"report_summary {r.status}")
    return r.output


async def _feedback(ai: AIClient, rows, units: dict, scores: ScoreResult) -> dict:
    """T7 sees criterion bands and comment gists only: no totals, quotes, weights or memos (SC-74)."""
    criteria = {}
    for c in CRITERIA:
        comments = [units[a.id]["items"][c.value].get("comment", "") for _, a, _, _ in rows
                    if a is not None and units[a.id]["items"][c.value]["evidence_status"] == "OK"]
        criteria[CRITERION_NAME[c.value]] = {"band": _band(scores.criteria[CRITERION_KEY[c.value]]),
                                             "comments": [x for x in comments if x][:5]}
    answers = [x.eval_text for _, a, _, fa in rows for x in (a, fa) if x is not None and x.eval_text]
    r = await ai.llm("candidate_feedback", {"criteria": criteria, "answers": answers}, lane="BATCH")
    if r.status != "OK":
        raise EvaluationFailed(f"candidate_feedback {r.status}")
    return r.output


async def _code_review(ai: AIClient, posting: JobPosting, report_in: ReportInputs) -> dict | None:
    """T8 gets the statement, PUBLIC examples and the code — hidden tests never reach the prompt (COD-06, SC-79)."""
    inp = {"problem": report_in.problem.statement_md, "language": posting.coding_language or "python",
           "code": report_in.submission.code,
           "examples": [{"input": t.input, "output": t.expected_output} for t in report_in.public_tests]}
    r = await ai.llm("code_review", inp, lane="BATCH")
    return r.output if r.status == "OK" else None  # P1, not scored: a failed review leaves it empty
