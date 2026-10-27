"""Evaluation pipeline (TRD 05 §10, ADR-0006 §5~7, EVL-01~08).

- `evaluate_unit`: T5 for one answer unit (main or follow-up answer). Used by `pre_evaluate` during the interview
  and by the final run for units without a reusable pre-evaluation.
- `build_report`: the final run. Units come from `answer_pre_evaluations` with the current manifest hash or a fresh
  T5 call; scores are computed by code (scoring.py); T6 summary, T7 candidate feedback and T8 code review (P1, never
  scored) produce the prose. Nothing is written here: the caller persists inside its fenced transaction.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import rubric
from app.ai.client import AIClient
from app.core.constants import CRITERIA, MAIN_QUESTION_COUNT, Criterion
from app.evaluation.scoring import QuestionScores, ScoreResult, UnitScores, compute, no_answer_unit
from app.models import (
    AnswerPreEvaluation,
    Application,
    CodingProblem,
    CodingSubmission,
    CodingTestcase,
    InterviewAnswer,
    InterviewQuestion,
    JobPosting,
    PostingEvalSettings,
)
from app.questions.compose import job_brief

CRITERION_NAME = {i["criterion"]: i["name"] for i in rubric.ITEMS}
CRITERION_KEY = {i["criterion"]: i["key"] for i in rubric.ITEMS}


class EvaluationFailed(Exception):
    """A model step produced nothing usable (consumes the attempt; EVL-06 retries once, then ON_HOLD)."""


class CodingNotGraded(Exception):
    """The coding submission is still being graded: wait without consuming an attempt."""


def unit_input(q: InterviewQuestion, a: InterviewAnswer, posting: JobPosting) -> dict:
    """T5 input: no resume, name, gender, age, photo or contact (EVL-07). eval_text is already redacted (rd-1)."""
    return {"question": q.text, "question_type": q.slot_type, "job_brief": job_brief(posting),
            "answer": a.eval_text or "", "is_no_answer": a.is_no_answer}


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


async def build_report(db: AsyncSession, ai: AIClient, app: Application, manifest_hash: str) -> Report:
    posting = await db.get(JobPosting, app.posting_id)
    settings = await db.get(PostingEvalSettings, app.eval_settings_id)
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == app.id)
                           .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars().all()
    answers = {a.question_id: a for a in (await db.execute(select(InterviewAnswer).where(
        InterviewAnswer.application_id == app.id))).scalars()}
    cached = {p.answer_id: p.payload for p in (await db.execute(select(AnswerPreEvaluation).where(
        AnswerPreEvaluation.answer_id.in_([a.id for a in answers.values()] or [None]),
        AnswerPreEvaluation.manifest_hash == manifest_hash))).scalars()}

    mains = [q for q in qs if q.role == "MAIN"]
    followups = {q.parent_id: q for q in qs if q.role == "FOLLOWUP"}
    units: dict = {}
    for q in qs:
        a = answers.get(q.id)
        if a is None:
            continue
        units[a.id] = cached.get(a.id) or (await evaluate_unit(ai, q, a, posting))[0]

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
    if app.attempt_status == "SUBMITTED" and len(question_scores) != MAIN_QUESTION_COUNT:
        raise EvaluationFailed(f"expected {MAIN_QUESTION_COUNT} main questions, found {len(question_scores)}")

    coding, sub = await _coding(db, app, posting)
    weights = settings.weights()
    coding_value = None
    if "coding" in weights:
        coding_value = Decimal(coding["score"]) if coding and coding.get("reached") else Decimal("0.00")
    scores = compute(question_scores, weights, coding_value)

    summary = await _summary(ai, rows, units, scores)
    feedback = await _feedback(ai, rows, units, scores)
    review = await _code_review(db, ai, posting, sub) if sub is not None and sub.code.strip() else None
    answered_main = sum(1 for m, a, _, _ in rows if a is not None and not a.is_no_answer)
    return Report(manifest_hash, units, scores, answered_main, summary, feedback, review, coding, rows)


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


async def _code_review(db: AsyncSession, ai: AIClient, posting: JobPosting, sub: CodingSubmission) -> dict | None:
    """T8 gets the statement, PUBLIC examples and the code — hidden tests never reach the prompt (COD-06, SC-79)."""
    problem = await db.get(CodingProblem, posting.coding_problem_id)
    public = (await db.execute(select(CodingTestcase).where(CodingTestcase.problem_id == problem.id,
                                                            CodingTestcase.visibility == "PUBLIC")
                               .order_by(CodingTestcase.ordinal))).scalars().all()
    inp = {"problem": problem.statement_md, "language": posting.coding_language or "python", "code": sub.code,
           "examples": [{"input": t.input, "output": t.expected_output} for t in public]}
    r = await ai.llm("code_review", inp, lane="BATCH")
    return r.output if r.status == "OK" else None  # P1, not scored: a failed review leaves it empty
