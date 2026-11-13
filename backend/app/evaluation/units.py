"""One answer unit's evaluation (T5) and the rules a stored unit result must pass before it is used (EVL-02·05·08).

Shared by the `pre_evaluate` job (during the interview) and the final run's unit node (graph.py): one implementation of
"load in a short transaction -> T5 with no transaction open -> check the result".

A stored result (`answer_pre_evaluations`, keyed by answer and manifest hash) is reused only when `reuse_problems` finds
nothing. The manifest hash already pins the model, every prompt and its options and the rubric hash (app/ai/manifest.py);
the payload is checked as well, because the answer text it was made from can change after it was stored:
- SHAPE           not the four criteria, a score outside 1~5, a status or reason that cannot go together
- RUBRIC_VERSION  made under another rubric version than the one this run records (evaluation_runs.rubric_version)
- STALE           a rule result (답변 없음, 매우 짧은 답변) the answer as it is now would not get
- QUOTE_MISMATCH  an OK quote that is not the same span of the answer text as it is now
- UNLINKED        a score of 3 or more without a sentence of the answer (근거 미연결), in a result that did not go through the
                  verification loop of this version (checks.CHECK_VERSION). A settled UNLINKED was already re-judged once and
                  is reused as it is.
Any of these: the unit is evaluated again (once per run, graph.py). The checks of a fresh judgement (score-reason-evidence,
spread of the samples, the one re-judgement) are in checks.py and run inside evaluate_unit, for pre_evaluate and the final run alike.

What the outcome of an item means (requirement 1 of the evaluation graph):
- system side  UNLINKED, INSUFFICIENT/QUOTE_MISMATCH, INSUFFICIENT/NO_EVIDENCE: the evidence could not be linked or verified.
               Not a judgement of the answer; too many of them hold the evaluation (graph.verify).
- content side INSUFFICIENT/LOW_CONTENT, INSUFFICIENT/TOO_SHORT, NO_ANSWER: the answer has too little to evaluate.
"""
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import rubric
from app.ai.client import AIClient
from app.ai.textrules import is_short_or_no_content
from app.core.config import get_settings
from app.core.constants import CRITERIA
from app.core.db import session_scope
from app.evaluation import checks
from app.evaluation.pipeline import EvaluationFailed, evaluate_unit
from app.models import AnswerPreEvaluation, Application, InterviewAnswer, InterviewQuestion, JobPosting

STATUSES = ("OK", "UNLINKED", "INSUFFICIENT", "NO_ANSWER")
CONTENT_REASONS = ("LOW_CONTENT", "TOO_SHORT")
SYSTEM_REASONS = ("QUOTE_MISMATCH", "NO_EVIDENCE")
REUSE_ORDER = ("SHAPE", "RUBRIC_VERSION", "STALE", "QUOTE_MISMATCH", "UNLINKED")  # most serious first


def _item_problem(item: dict, text: str) -> str | None:
    p = checks.item_problem(item, text)
    return "UNLINKED" if p == "NO_EVIDENCE" else p


def settled(payload: dict) -> bool:
    """Made by the verification loop of this version (checks.py): what is left in it was decided there, not a fault."""
    return (payload.get("check") or {}).get("version") == checks.CHECK_VERSION


def reuse_problems(payload: dict | None, answer: InterviewAnswer) -> list[str]:
    """Why a stored unit result cannot be used for this answer now ([] = it can), most serious first."""
    if not payload or not isinstance(payload.get("items"), dict):
        return ["SHAPE"]
    items = payload["items"]
    if set(items) != {c.value for c in CRITERIA}:
        return ["SHAPE"]
    found = set()
    if payload.get("rubric_version") != rubric.RUBRIC_VERSION:
        found.add("RUBRIC_VERSION")
    text = answer.eval_text or ""
    silent = all(it.get("evidence_status") == "NO_ANSWER" for it in items.values())
    too_short = all(it.get("insufficient_reason") == "TOO_SHORT" for it in items.values())
    if silent != bool(answer.is_no_answer) or (
            too_short and not answer.is_no_answer and not is_short_or_no_content(text, get_settings().short_answer_min_chars)):
        found.add("STALE")
    for it in items.values():
        p = _item_problem(it, text) if isinstance(it, dict) else "SHAPE"
        if p == "UNLINKED" and settled(payload):
            continue  # already re-judged once and settled as 근거 미연결: evaluating it again would only repeat that
        if p:
            found.add(p)
    return [p for p in REUSE_ORDER if p in found]


def seal_fresh(payload: dict, answer: InterviewAnswer) -> dict:
    """A result just made by T5 (its own regeneration budget is spent): what still fails is settled here, never asked again.
    UNLINKED stays (the model's score, no quote); a quote that is not a span of the answer becomes UNLINKED too (system
    side, the model's score stays: an extraction failure never makes the score, ADR-0023). A wrong shape or rubric version cannot be settled: EvaluationFailed."""
    problems = reuse_problems(payload, answer)
    hard = [p for p in problems if p in ("SHAPE", "RUBRIC_VERSION", "STALE")]
    if hard:
        raise EvaluationFailed(f"evaluate_answer result {hard[0]}")
    text = answer.eval_text or ""
    items = {}
    for crit, it in payload["items"].items():
        if it["evidence_status"] == "OK" and _item_problem(it, text) == "QUOTE_MISMATCH":  # unverified evidence: score stays
            it = {**it, "evidence_status": "UNLINKED", "insufficient_reason": None, "quote": None}
        items[crit] = it
    return {**payload, "items": items}


def outcome_counts(payloads: list[dict]) -> dict:
    """Item outcomes split into system side (evidence not linked/verified) and content side (too little in the answer)."""
    judged = system = content = no_answer = 0
    for p in payloads:
        for it in p["items"].values():
            st, reason = it["evidence_status"], it.get("insufficient_reason")
            if p.get("llm"):
                judged += 1
            if st == "UNLINKED" or (st == "INSUFFICIENT" and reason in SYSTEM_REASONS):
                system += 1
            elif st == "INSUFFICIENT":
                content += 1
            elif st == "NO_ANSWER":
                no_answer += 1
    return {"judged_items": judged, "system_items": system, "content_items": content, "no_answer_items": no_answer}


def check_counts(payloads: list[dict]) -> dict:
    """What the verification loop (checks.py) did, counts only: units re-judged, reason codes found before the re-judgement,
    criteria left unstable. Results made before the loop existed count nothing."""
    rejudged, unstable, found = 0, 0, {}
    for p in payloads:
        c = p.get("check") or {}
        rejudged += bool(c.get("rejudged"))
        unstable += sum("UNSTABLE" in codes for codes in (c.get("left") or {}).values())
        for codes in (c.get("found") or {}).values():
            for code in codes:
                found[code] = found.get(code, 0) + 1
    return {"rejudged_units": rejudged, "unstable_items": unstable, "check_found": found}


async def load_unit(answer_id: UUID) -> tuple[InterviewQuestion, InterviewAnswer, JobPosting, Application]:
    async with session_scope() as db:
        a = await db.get(InterviewAnswer, answer_id)
        q = await db.get(InterviewQuestion, a.question_id)
        app = await db.get(Application, a.application_id)
        posting = await db.get(JobPosting, app.posting_id)
    return q, a, posting, app


async def judge(ai: AIClient, answer_id: UUID) -> tuple[dict, str, InterviewAnswer]:
    """T5 for one unit with no transaction open, sealed. Returns (payload, manifest hash, the answer it was made from).
    Raises EvaluationFailed; AIUnavailable propagates (job deferral)."""
    q, a, posting, app = await load_unit(answer_id)
    payload, manifest = await evaluate_unit(ai, q, a, posting, app)
    return seal_fresh(payload, a), manifest, a


async def store(db: AsyncSession, answer_id: UUID, manifest: str, payload: dict, *, replace: bool) -> None:
    """`replace`: the final run evaluated the unit again because the stored one failed `reuse_problems`."""
    stmt = insert(AnswerPreEvaluation).values(answer_id=answer_id, manifest_hash=manifest, payload=payload)
    if replace:
        stmt = stmt.on_conflict_do_update(index_elements=["answer_id", "manifest_hash"], set_={"payload": payload})
    else:
        stmt = stmt.on_conflict_do_nothing()
    await db.execute(stmt)
