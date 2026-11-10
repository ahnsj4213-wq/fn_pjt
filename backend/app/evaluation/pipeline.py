"""Evaluation steps (TRD 05 §10, ADR-0006 §5~7, EVL-01~08). The order, the checkpoints and the retries are in graph.py.

- `evaluate_unit`: T5 for one answer unit (main or follow-up answer), shared by `pre_evaluate` (through units.judge) and the
  final run's unit node. `eval_samples` judgements, the per-criterion median is kept (ADR-0023).
- scores are computed by code (scoring.py) from the unit payloads; T8 code review, the company's own items, T6 summary and
  T7 candidate feedback are one model call each. Nothing here opens a transaction around a model call and nothing here
  writes: the graph nodes load in short transactions and the handler persists inside its fenced transaction.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import rubric
from app.ai.client import AIClient
from app.coding.grading import strip_comments
from app.core.config import get_settings
from app.core.constants import CRITERIA, Criterion
from app.evaluation.scoring import (
    CODING_METRIC_WEIGHTS,
    QuestionScores,
    ScoreResult,
    UnitScores,
    coding_total,
    measured_efficiency,
    no_answer_unit,
)
from app.models import (
    Application,
    CodingProblem,
    CodingSubmission,
    CodingTestcase,
    CodingTestResult,
    InterviewAnswer,
    InterviewQuestion,
    JobPosting,
)
from app.coding.levels import LEVELS
from app.questions.compose import job_brief
from app.questions.level import career_level
from app.resumes.schemas import to_ai_resume

CRITERION_NAME = {i["criterion"]: i["name"] for i in rubric.ITEMS}
CRITERION_KEY = {i["criterion"]: i["key"] for i in rubric.ITEMS}


class EvaluationFailed(Exception):
    """A model step produced nothing usable (consumes the attempt; EVL-06 retries once, then ON_HOLD)."""


class CodingNotGraded(Exception):
    """The coding submission is still being graded: wait without consuming an attempt."""


CODE_MAX_CHARS = 6000  # keeps the prompt inside the eval model's context next to the answer


def candidate_level(app: Application | None, posting: JobPosting) -> str | None:
    """NEW / JUNIOR / SENIOR from the resume's work periods (app/questions/level.py), the only thing T5 learns from the resume:
    it decides how the rubric is read (rubric-v5 LEVEL_VIEW), never a score."""
    if app is None or not app.resume_snapshot:
        return None
    try:
        return career_level(to_ai_resume(app.resume_snapshot),
                            " ".join(filter(None, (posting.experience_level, posting.title, posting.requirements))))
    except (AttributeError, TypeError, ValueError):
        return None


def unit_input(q: InterviewQuestion, a: InterviewAnswer, posting: JobPosting, level: str | None = None) -> dict:
    """T5 input: no resume, name, gender, age, photo or contact (EVL-07); of the resume only the career level (rubric-v5).
    eval_text is already redacted (rd-1)."""
    inp = {"question": q.text, "question_type": q.slot_type, "job_brief": job_brief(posting),
           "question_kind": rubric.question_kind(q.slot_type, q.text, bool(a.code_text)),  # what 논리성·전문성 need (ADR-0023)
           "answer": a.eval_text or "", "is_no_answer": a.is_no_answer}
    if level:
        inp["level"] = level
    if a.code_text:  # SC-A06 live coding: the submitted code is read with the answer (never executed)
        inp["code"], inp["code_language"] = a.code_text[:CODE_MAX_CHARS], a.code_language or ""
    return inp


async def evaluate_unit(ai: AIClient, q: InterviewQuestion, a: InterviewAnswer, posting: JobPosting,
                        app: Application | None = None) -> tuple[dict, str]:
    """Returns (payload, manifest_hash). Raises EvaluationFailed; AIUnavailable propagates (job deferral).

    `eval_samples` independent T5 judgements, then per criterion the median one with its own evidence and comment (ADR-0023).
    Every kept score is one the model gave; code only picks the middle of the model's own judgements. A rule result (답변 없음,
    TOO_SHORT) has no model call and is not repeated. Samples that failed are left out; none left = EvaluationFailed."""
    inp = unit_input(q, a, posting, candidate_level(app, posting))
    samples, calls, manifest, status = [], 0, None, "INVALID"
    for _ in range(max(1, get_settings().eval_samples)):
        r = await ai.llm("evaluate_answer", inp, lane="BATCH")
        calls += r.attempts.get("llm_calls", 0)
        status = r.status
        if r.status == "OK":
            samples.append(r)
            manifest = r.manifest_hash
            if not r.output.get("llm", True):
                break  # a rule result: the same every time
    if not samples:
        raise EvaluationFailed(f"evaluate_answer {status}")
    out = median_judgement([s.output for s in samples])
    flags: dict = {}
    for s in samples:
        flags.update({k: v for k, v in (s.flags or {}).items() if v})
    return {**out, "flags": flags, "llm_calls": calls, "samples": len(samples)}, manifest


def median_judgement(outputs: list[dict]) -> dict:
    """Per criterion, the judgement whose score is the median of the samples (the lower middle of an even count), kept whole
    with its evidence sentence and comment. The sub-items and STAR come from the sample whose 논리성 was kept."""
    if len(outputs) == 1:
        return outputs[0]
    items, logic_from = {}, outputs[0]
    for crit in outputs[0]["items"]:
        ranked = sorted(outputs, key=lambda o: o["items"][crit]["score"])
        pick = ranked[(len(ranked) - 1) // 2]
        items[crit] = pick["items"][crit]
        if crit == Criterion.LOGIC.value:
            logic_from = pick
    return {**logic_from, "items": items, "scores_sampled": {c: [o["items"][c]["score"] for o in outputs] for c in items}}


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
    limit_hit = await db.scalar(select(func.count()).select_from(CodingTestResult).where(
        CodingTestResult.submission_id == sub.id, CodingTestResult.outcome.in_(["TIMEOUT", "MEMORY_LIMIT"])))
    return {"passed": sub.passed, "total": sub.total, "score": sub.score, "reached": True, "limit_hit": bool(limit_hit)}, sub


def question_rows(qs: list[InterviewQuestion], answers: dict) -> list[tuple]:
    """(main question, its answer, follow-up question, its answer) per main question, in question order."""
    followups = {q.parent_id: q for q in qs if q.role == "FOLLOWUP"}
    rows = []
    for m in (q for q in qs if q.role == "MAIN"):
        fq = followups.get(m.id)
        rows.append((m, answers.get(m.id), fq, answers.get(fq.id) if fq else None))
    return rows


def question_scores(rows, units: dict, attempt_status: str) -> list[QuestionScores]:
    """Per main question the unit scores the area totals are computed from (6.2, 6.4, OQ-07)."""
    out = []
    for m, a, _fq, fa in rows:
        if a is None:
            if attempt_status == "SUBMITTED":
                out.append(QuestionScores(m.seq, no_answer_unit()))  # never answered -> 답변 없음
            continue  # EXPIRED: only answered main questions count (OQ-07)
        out.append(QuestionScores(m.seq, unit_scores(units[a.id]), unit_scores(units[fa.id]) if fa else None))
    if attempt_status == "SUBMITTED" and len(out) != len(rows):
        raise EvaluationFailed(f"expected {len(rows)} main questions, found {len(out)}")
    return out


def answered_main(rows) -> int:
    return sum(1 for _m, a, _, _ in rows if a is not None and not a.is_no_answer)


def custom_answers(rows) -> list[dict]:
    """The answers T-custom reads: per main question its answer and follow-up answer as one text."""
    out = []
    for m, a, _fq, fa in rows:
        text = " ".join(x.eval_text for x in (a, fa) if x is not None and not x.is_no_answer and x.eval_text)
        if text.strip():
            out.append({"no": m.seq, "question": m.text, "text": text})
    return out


def active_custom_items(items: list[dict] | None) -> list[dict]:
    return [it for it in (items or []) if it.get("weight", 0) > 0]  # 0% items are not evaluated


async def grade_custom_items(ai: AIClient, rows, items: list[dict]) -> dict:
    """The model's grades of the company's own items, keyed by item id (one call over the whole interview)."""
    active = active_custom_items(items)
    answers = custom_answers(rows)
    if not active:
        return {}
    if not answers:  # nothing was answered: every item is the lowest grade without evidence
        return {it["id"]: {"grade": "하", "score": 1, "evidence_status": "NO_ANSWER", "quote": None, "comment": ""} for it in active}
    r = await ai.llm("custom_criteria", {"items": active, "answers": answers}, lane="BATCH")
    if r.status != "OK":
        raise EvaluationFailed(f"custom_criteria {r.status}")
    return r.output["items"]


def custom_item_scores(items: list[dict], graded: dict) -> tuple[dict[str, Decimal], list[dict]]:
    """Score = grade points x 20, by code; the detail the report shows."""
    scores, detail = {}, []
    for it in active_custom_items(items):
        g = graded[it["id"]]
        scores[f"custom:{it['id']}"] = Decimal(g["score"] * 20)
        detail.append({"id": it["id"], "name": it["name"], "description": it.get("description") or "", "weight": it["weight"],
                       "grade": g["grade"], "score": g["score"] * 20, "evidence_status": g["evidence_status"],
                       "quote": (g["quote"] or {}).get("text"), "quote_no": (g["quote"] or {}).get("no"), "comment": g["comment"]})
    return scores, detail


NO_ANSWER_SUMMARY = {
    "overall": "면접 질문에 답변한 내용이 없어 항목별로 평가할 수 없었습니다. 모든 평가 항목은 최저점으로 처리되었습니다.",
    "strengths": [],
    "improvements": [{"text": "질문마다 먼저 결론을 말하고 이유와 사례를 덧붙이는 방식으로 답변을 시작해 보세요. 답변이 있어야 평가할 수 있습니다.",
                      "question_refs": []}],
}
NO_ANSWER_FEEDBACK = {
    "strengths": [],
    "improvements": ["질문마다 짧게라도 결론을 먼저 말하고, 이유와 경험을 한두 문장 덧붙여 답변해 보세요."],
}


def _anything_answered(rows) -> bool:
    """True when at least one main or follow-up answer has content (a silent or empty answer is not an answer)."""
    return any(x is not None and not x.is_no_answer for _m, a, _fq, fa in rows for x in (a, fa))


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
                    if a is not None and units[a.id]["items"][c.value]["evidence_status"] in ("OK", "UNLINKED")]
        criteria[CRITERION_NAME[c.value]] = {"band": _band(scores.criteria[CRITERION_KEY[c.value]]),
                                             "comments": [x for x in comments if x][:5]}
    answers = [x.eval_text for _, a, _, fa in rows for x in (a, fa) if x is not None and x.eval_text]
    r = await ai.llm("candidate_feedback", {"criteria": criteria, "answers": answers}, lane="BATCH")
    if r.status != "OK":
        raise EvaluationFailed(f"candidate_feedback {r.status}")
    return r.output


async def _code_review(ai: AIClient, posting: JobPosting, problem: CodingProblem, public_tests: list[CodingTestcase],
                       code: str) -> dict:
    """T8 gets the statement, PUBLIC examples and the code without comments: hidden tests and their results never reach
    the prompt (COD-06, SC-79). The result is part of the score, so a failure fails the attempt (EVL-06)."""
    level = LEVELS.get(problem.level)
    inp = {"problem": problem.statement_md, "language": posting.coding_language or "python",
           "level_expectation": f"Lv.{level.level}: {level.expected}" if level else "",
           "code": strip_comments(code)[:CODE_MAX_CHARS],
           "examples": [{"input": t.input, "output": t.expected_output} for t in public_tests]}
    r = await ai.llm("code_review", inp, lane="BATCH")
    if r.status != "OK":
        raise EvaluationFailed(f"code_review {r.status}")
    return r.output


CODING_LABELS = {"correctness": "정확성", "efficiency": "효율성", "approach": "문제 해결 방식", "quality": "코드 품질"}
NO_CODE_COMMENT = "제출한 코드가 없습니다."


def _coding_metrics(coding: dict | None, review: dict | None) -> tuple[Decimal, dict]:
    """The coding score and what the report shows of it. No counts of hidden tests here: the applicant reads this."""
    w = CODING_METRIC_WEIGHTS
    if not coding or not coding.get("reached") or review is None:  # never reached it, or nothing was written
        items = [{"key": k, "label": CODING_LABELS[k], "score": 0, "weight": w[k], "comment": NO_CODE_COMMENT,
                  "quote": None, "evidence_status": "NO_CODE"} for k in w]
        return Decimal("0.00"), {"items": items, "time": None, "improvements": []}
    correctness = Decimal(coding["score"])
    m = review["metrics"]
    scores = {k: int(m[k]["score"]) for k in ("efficiency", "approach", "quality")}
    scores["efficiency"] = measured_efficiency(scores["efficiency"], coding["passed"], coding["limit_hit"])
    items = [{"key": "correctness", "label": CODING_LABELS["correctness"], "score": float(correctness), "weight": w["correctness"],
              "comment": "공개·비공개 테스트를 실행한 통과율입니다.", "quote": None, "evidence_status": "MEASURED"}]
    for k, v in scores.items():
        items.append({"key": k, "label": CODING_LABELS[k], "score": v * 20, "weight": w[k], "comment": m[k]["comment"],
                      "quote": (m[k]["quote"] or {}).get("text"), "evidence_status": m[k]["evidence_status"]})
    total = coding_total(correctness, scores["efficiency"], scores["approach"], scores["quality"])
    return total, {"items": items, "time": review["complexity"]["time"], "improvements": review["improvements"]}
