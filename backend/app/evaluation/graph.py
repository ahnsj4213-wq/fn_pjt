"""The final evaluation as a LangGraph run (ADR-0016 pattern, TRD 05 §10, EVL-01~08): one run per `evaluate_application`
job, `thread_id` = eval:<application>:<job id>, checkpoints in PostgreSQL (app/interview/checkpoint.py).

    START -> prepare -+-> unit -+-> verify -> code_review -> custom -> score -> summary -> feedback -> END
                      |   ^  |  |
                      |   +--+  |   (one answer unit per step, in question order: never two T5 calls at once, GPU 6 GB)
                      +---------+

  prepare      short reads: questions, answers, the stored unit results of this manifest (waits up to 45 s for a
               `pre_evaluate` still running), the coding submission (CodingNotGraded = wait, no attempt used). Every stored
               result goes through units.reuse_problems: reused, or put on the list with the reason (MISSING, SHAPE,
               RUBRIC_VERSION, STALE, QUOTE_MISMATCH, UNLINKED).
  unit         T5 for the next unit on the list (units.judge: eval_samples judgements, median), sealed, stored in
               answer_pre_evaluations with the run's marker. At most one fresh evaluation per unit per run.
  verify       every unit used is checked again; the outcomes are split into system side (evidence not linked or verified)
               and content side (too little in the answer). System side over SYSTEM_HOLD_RATIO of the judged items =
               EvidenceUnverifiable: the attempt fails, and EVL-06 holds the evaluation after the retry (ON_HOLD).
  code_review  T8 (ADR-0017), only with submitted code; public examples only.
  custom       the company's own items, one call (none when every item is 0 %).
  score        code only: scoring.compute (weights must sum to 100) with the code-measured details (details.py).
  summary      T6 for the recruiter;  feedback  T7 for the candidate (fixed texts when nothing was answered).

The handler then builds the report (`finalize`, code only, the scores are computed again and must be the same) and writes it
in its fenced transaction; the thread is deleted after that write. Retries:
- inside a step: only run_task's format / regeneration budget (app/ai/tasks/base.py) and the median samples of T5;
- the job's retry (EVL-06: 2 attempts; AI outage = deferral without using one) continues at the step that stopped: the units
  and model steps that finished are not called again (their results are in the checkpoint or in answer_pre_evaluations);
- a manifest that changed between attempts (model or prompt reloaded) starts the run again: results of two versions are
  never mixed (EVL-08).
No step holds a database transaction while the model runs (core.db.open_scopes() == 0 at every call, tested), and a worker
whose lease was taken over stops before its next model call. The state holds ids, reasons, counts, the scores and the
model's own texts; answer text and quotes are never in it (quotes are kept as spans and read back from the database).
"""
import asyncio
import copy
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select, text

from app.ai.client import get_ai_client
from app.coding.grading import strip_comments
from app.coding.variants import problem_of
from app.core.db import session_scope
from app.core.logging import log_event
from app.core.storage import get_storage
from app.evaluation import details, pipeline, units
from app.evaluation.pipeline import EvaluationFailed
from app.evaluation.scoring import RULE_WEIGHT, SCORING_VERSION, ScoreResult, compute
from app.interview.checkpoint import get_checkpointer
from app.models import (
    AnswerPreEvaluation,
    Application,
    CodingSubmission,
    CodingTestcase,
    InterviewAnswer,
    InterviewQuestion,
    Job,
    JobPosting,
    PostingEvalSettings,
)

log = logging.getLogger("app.evaluation")

SYSTEM_HOLD_RATIO = 0.5  # more than half of the judged items without verified evidence: the run is held, not scored
PRE_EVAL_WAIT_SEC = 45.0


class EvidenceUnverifiable(EvaluationFailed):
    """Too many items whose evidence could not be linked or verified after the limited re-evaluation (system side)."""


class ManifestChanged(Exception):
    """The model server's manifest is unknown or changed during the run: retry later without using an attempt."""


class LeaseLost(Exception):
    """This worker's lease was taken over: stop before the next model call (the new owner continues the thread)."""


class State(TypedDict, total=False):
    application_id: str
    manifest: str
    attempt_status: str
    todo: list[list[str]]  # [[answer id, reason], ...] still to evaluate
    units: dict[str, dict]  # answer id -> {"src": PRE | FRESH, "why": reason, "llm_calls": n}
    has_code: bool
    coding: dict | None  # passed / total / score / reached / limit_hit: numbers only
    quality: dict
    review: dict | None  # T8 output, quotes as spans of the comment-stripped code
    custom: dict | None  # custom-item grades, quotes as spans of the joined answers of a question
    rule_scores: dict[str, str]
    detail: dict
    scores: dict
    summary: dict
    feedback: dict
    finished: bool


# ── helpers ────────────────────────────────────────────────────────────────────
def thread_id(app_id: UUID | str, job_id: int | None) -> str:
    return f"eval:{app_id}:{job_id if job_id is not None else '-'}"


def _conf(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable", {})


async def _lease_ok(db, config: RunnableConfig | None) -> bool:
    c = _conf(config)
    if c.get("job_id") is None:
        return True
    return bool(await db.scalar(text("SELECT 1 FROM jobs WHERE id=:id AND lease_token=:t AND state='RUNNING'"),
                                {"id": c["job_id"], "t": c.get("lease_token")}))


async def _guard(config: RunnableConfig | None) -> None:
    """Before a model call: a worker that lost its lease does not spend the model on a result nobody will write."""
    if _conf(config).get("job_id") is None:
        return
    async with session_scope() as db:
        ok = await _lease_ok(db, config)
    if not ok:
        raise LeaseLost(_conf(config)["job_id"])


def dump_scores(s: ScoreResult) -> dict:
    return {"criteria": {k: str(v) for k, v in s.criteria.items()}, "coding": None if s.coding is None else str(s.coding),
            "total": str(s.total), "question_scores": s.question_scores, "insufficient_count": s.insufficient_count,
            "no_answer_count": s.no_answer_count, "insufficient_by_criterion": dict(s.insufficient_by_criterion),
            "custom": {k: str(v) for k, v in s.custom.items()}}


def load_scores(d: dict) -> ScoreResult:
    return ScoreResult(criteria={k: Decimal(v) for k, v in d["criteria"].items()},
                       coding=None if d["coding"] is None else Decimal(d["coding"]), total=Decimal(d["total"]),
                       question_scores=d["question_scores"], insufficient_count=d["insufficient_count"],
                       no_answer_count=d["no_answer_count"], insufficient_by_criterion=d["insufficient_by_criterion"],
                       custom={k: Decimal(v) for k, v in d["custom"].items()})


@dataclass
class Loaded:
    app: Application
    posting: JobPosting
    settings: PostingEvalSettings
    rows: list
    units: dict  # answer UUID -> payload


async def _load(state: State) -> Loaded:
    """One short transaction: everything a step reads. Unit payloads come from answer_pre_evaluations of the run's manifest."""
    app_id = UUID(state["application_id"])
    ids = [UUID(x) for x in state.get("units", {})]
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        posting = await db.get(JobPosting, app.posting_id)
        settings = await db.get(PostingEvalSettings, app.eval_settings_id)
        qs = list((await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == app_id)
                                    .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars())
        answers = {a.question_id: a for a in (await db.execute(select(InterviewAnswer).where(
            InterviewAnswer.application_id == app_id))).scalars()}
        payloads = {p.answer_id: p.payload for p in (await db.execute(select(AnswerPreEvaluation).where(
            AnswerPreEvaluation.answer_id.in_(ids or [None]), AnswerPreEvaluation.manifest_hash == state["manifest"]))).scalars()}
    if set(ids) - set(payloads):
        raise EvaluationFailed("unit result missing")
    if {a.id for a in answers.values()} != set(ids):
        raise EvaluationFailed("answers changed during the evaluation")
    return Loaded(app, posting, settings, pipeline.question_rows(qs, answers), payloads)


async def _wait_pre(missing: list[UUID], manifest: str) -> dict:
    """Answers whose `pre_evaluate` job is still queued or running are waited for instead of being evaluated a second time
    (the same answer twice would hold the model slot twice as long). No job in flight = evaluate here."""
    found: dict = {}
    deadline = time.monotonic() + PRE_EVAL_WAIT_SEC
    while True:
        rest = [m for m in missing if m not in found]
        if not rest or time.monotonic() >= deadline:
            return found
        async with session_scope() as db:
            running = await db.scalar(select(func.count()).select_from(Job).where(
                Job.kind == "pre_evaluate", Job.state.in_(["QUEUED", "RUNNING"]), Job.dedupe_key.in_([f"pre:{m}" for m in rest])))
            for p in (await db.execute(select(AnswerPreEvaluation).where(
                    AnswerPreEvaluation.answer_id.in_(rest), AnswerPreEvaluation.manifest_hash == manifest))).scalars():
                found[p.answer_id] = p.payload
        if not running:
            return found
        await asyncio.sleep(0.4)


# quotes out of the state, back in for the report ──────────────────────────────
def _code_source(code: str) -> str:
    return strip_comments(code)[:pipeline.CODE_MAX_CHARS]  # what T8 read, so its spans point into this


def review_without_text(review: dict) -> dict:
    r = copy.deepcopy(review)
    for m in r.get("metrics", {}).values():
        if m.get("quote"):
            m["quote"] = {"start": m["quote"]["start"], "end": m["quote"]["end"]}
    return r


def review_with_text(review: dict | None, code: str) -> dict | None:
    if review is None:
        return None
    r, src = copy.deepcopy(review), _code_source(code)
    for m in r.get("metrics", {}).values():
        if m.get("quote"):
            m["quote"]["text"] = src[m["quote"]["start"]:m["quote"]["end"]]
    return r


def custom_without_text(graded: dict, rows) -> dict:
    by_no = {x["no"]: x["text"] for x in pipeline.custom_answers(rows)}
    out = copy.deepcopy(graded)
    for g in out.values():
        q = g.get("quote")
        if q:
            pos = by_no.get(q.get("no"), "").find(q.get("text") or "\0")
            g["quote"] = {"no": q["no"], "start": pos, "end": pos + len(q["text"])} if pos >= 0 else None
    return out


def custom_with_text(graded: dict | None, rows) -> dict:
    by_no = {x["no"]: x["text"] for x in pipeline.custom_answers(rows)}
    out = copy.deepcopy(graded or {})
    for g in out.values():
        q = g.get("quote")
        if q:
            g["quote"] = {"no": q["no"], "text": by_no.get(q["no"], "")[q["start"]:q["end"]]}
    return out


# ── nodes ──────────────────────────────────────────────────────────────────────
async def prepare(state: State, config: RunnableConfig) -> dict:
    app_id, manifest = UUID(state["application_id"]), state["manifest"]
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        posting = await db.get(JobPosting, app.posting_id)
        coding, sub = await pipeline._coding(db, app, posting)  # CodingNotGraded: wait without using an attempt
        qs = list((await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == app_id)
                                    .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars())
        answers = {a.question_id: a for a in (await db.execute(select(InterviewAnswer).where(
            InterviewAnswer.application_id == app_id))).scalars()}
        cached = {p.answer_id: p.payload for p in (await db.execute(select(AnswerPreEvaluation).where(
            AnswerPreEvaluation.answer_id.in_([a.id for a in answers.values()] or [None]),
            AnswerPreEvaluation.manifest_hash == manifest))).scalars()}
        attempt_status, has_code = app.attempt_status, bool(sub is not None and sub.code.strip())
    order = [answers[q.id] for q in qs if q.id in answers]
    missing = [a.id for a in order if a.id not in cached]
    if missing:
        cached.update(await _wait_pre(missing, manifest))  # the last answer's pre-evaluation is usually still running
    done, todo = {}, []
    for a in order:
        problems = units.reuse_problems(cached[a.id], a) if a.id in cached else ["MISSING"]
        if problems:
            todo.append([str(a.id), problems[0]])
        else:
            done[str(a.id)] = {"src": "PRE"}
    if coding and "score" in coding:
        coding = {**coding, "score": str(coding["score"])}
    if todo:
        log_event(log, "evaluation_units_planned", application_id=str(app_id), count=len(todo),
                  reason=",".join(sorted({w for _, w in todo})))
    return {"units": done, "todo": todo, "coding": coding, "has_code": has_code, "attempt_status": attempt_status}


async def unit(state: State, config: RunnableConfig) -> dict:
    """One unit: T5 (eval_samples judgements), sealed and stored before the checkpoint says it is done. A crash between the
    two finds the stored result by its run marker and does not call the model again."""
    (aid_s, why), rest = state["todo"][0], state["todo"][1:]
    aid, manifest, marker = UUID(aid_s), state["manifest"], _conf(config).get("thread_id")
    async with session_scope() as db:
        a = await db.get(InterviewAnswer, aid)
        row = await db.get(AnswerPreEvaluation, (aid, manifest))
    done = None
    if row is not None and row.payload.get("run_ref") == marker:
        done = {"src": "FRESH", "why": why, "llm_calls": row.payload.get("llm_calls", 0)}
    elif row is not None and why == "MISSING" and not units.reuse_problems(row.payload, a):
        done = {"src": "PRE"}  # its pre_evaluate job finished after prepare
    if done is None:
        await _guard(config)
        payload, got, _a = await units.judge(get_ai_client(), aid)
        if got != manifest:
            raise ManifestChanged("manifest changed during the run")
        payload["run_ref"] = marker
        async with session_scope() as db:
            if not await _lease_ok(db, config):
                raise LeaseLost(_conf(config).get("job_id"))
            await units.store(db, aid, manifest, payload, replace=True)
        done = {"src": "FRESH", "why": why, "llm_calls": payload.get("llm_calls", 0)}
    return {"todo": rest, "units": {**state.get("units", {}), aid_s: done}}


async def verify(state: State, config: RunnableConfig) -> dict:
    L = await _load(state)
    answers = {x.id: x for _m, a, _fq, fa in L.rows for x in (a, fa) if x is not None}
    reasons: dict[str, int] = {}
    for aid_s, info in state["units"].items():
        aid = UUID(aid_s)
        problems = units.reuse_problems(L.units[aid], answers[aid])
        # A fresh result may keep UNLINKED (T5 already asked once more); anything else here is a fault, not an outcome.
        bad = [p for p in problems if not (info["src"] == "FRESH" and p == "UNLINKED")]
        if bad:
            raise EvaluationFailed(f"unit result {bad[0]}")
        if info.get("why"):
            reasons[info["why"]] = reasons.get(info["why"], 0) + 1
    counts = units.outcome_counts(list(L.units.values()))
    fresh = [i for i in state["units"].values() if i["src"] == "FRESH"]
    quality = {**counts, "reused": len(state["units"]) - len(fresh), "evaluated": len(fresh),
               "reevaluated": sum(1 for i in fresh if i.get("why") != "MISSING"), "reasons": reasons,
               "hold_ratio": SYSTEM_HOLD_RATIO}
    held = counts["judged_items"] > 0 and counts["system_items"] / counts["judged_items"] > SYSTEM_HOLD_RATIO
    log_event(log, "evaluation_units_checked", application_id=state["application_id"],
              status="SYSTEM_FAILURE" if held else "OK", count=counts["judged_items"],
              reason=f"system={counts['system_items']};content={counts['content_items']};no_answer={counts['no_answer_items']}")
    if held:
        raise EvidenceUnverifiable(f"{counts['system_items']}/{counts['judged_items']} items without verified evidence")
    return {"quality": quality}


async def code_review(state: State, config: RunnableConfig) -> dict:
    if not state.get("has_code"):
        return {"review": None}
    app_id = UUID(state["application_id"])
    async with session_scope() as db:
        app = await db.get(Application, app_id)
        posting = await db.get(JobPosting, app.posting_id)
        sub = (await db.execute(select(CodingSubmission).where(CodingSubmission.application_id == app_id))).scalar_one()
        problem = await problem_of(db, app_id, posting)
        public = list((await db.execute(select(CodingTestcase).where(
            CodingTestcase.problem_id == problem.id, CodingTestcase.visibility == "PUBLIC")
            .order_by(CodingTestcase.ordinal))).scalars())
        code = sub.code
    await _guard(config)
    review = await pipeline._code_review(get_ai_client(), posting, problem, public, code)
    return {"review": review_without_text(review)}


async def custom(state: State, config: RunnableConfig) -> dict:
    L = await _load(state)
    items = list(L.settings.custom_items or [])
    if not pipeline.active_custom_items(items):
        return {"custom": None}
    if pipeline.custom_answers(L.rows):
        await _guard(config)
    graded = await pipeline.grade_custom_items(get_ai_client(), L.rows, items)
    return {"custom": custom_without_text(graded, L.rows)}


def _scored(state: State, L: Loaded, rule_scores: dict[str, Decimal], review: dict | None, graded: dict
            ) -> tuple[ScoreResult, dict | None, list]:
    weights = L.settings.all_weights()
    qscores = pipeline.question_scores(L.rows, L.units, L.app.attempt_status)
    coding_value, coding_detail = (pipeline._coding_metrics(state.get("coding"), review) if "coding" in weights else (None, None))
    custom_scores, custom_detail = pipeline.custom_item_scores(list(L.settings.custom_items or []), graded)
    return compute(qscores, weights, coding_value, rule_scores, custom_scores), coding_detail, custom_detail


async def score(state: State, config: RunnableConfig) -> dict:
    """Code only (EVL-04): the weights must sum to 100 or compute refuses (the attempt fails)."""
    L = await _load(state)
    det = await details.analyze(L.rows, L.units, get_storage())
    scores, _c, _d = _scored(state, L, det.rule_scores, state.get("review"), state.get("custom") or {})
    detail = {**det.detail, "blend": {"scoring": SCORING_VERSION, "rule_weight": float(RULE_WEIGHT),
                                      "rule_scores": {k: float(v) for k, v in det.rule_scores.items()}}}
    return {"scores": dump_scores(scores), "detail": detail, "rule_scores": {k: str(v) for k, v in det.rule_scores.items()}}


async def summary(state: State, config: RunnableConfig) -> dict:
    L = await _load(state)
    if not pipeline._anything_answered(L.rows):  # nothing to read: the model would only make something up
        return {"summary": dict(pipeline.NO_ANSWER_SUMMARY)}
    await _guard(config)
    return {"summary": await pipeline._summary(get_ai_client(), L.rows, L.units, load_scores(state["scores"]))}


async def feedback(state: State, config: RunnableConfig) -> dict:
    L = await _load(state)
    if not pipeline._anything_answered(L.rows):
        return {"feedback": dict(pipeline.NO_ANSWER_FEEDBACK), "finished": True}
    await _guard(config)
    return {"feedback": await pipeline._feedback(get_ai_client(), L.rows, L.units, load_scores(state["scores"])),
            "finished": True}


# ── the report from a finished run (code only) ─────────────────────────────────
async def finalize(values: State) -> pipeline.Report:
    """The report the handler writes. Scores are computed again from the stored units and must equal the ones T6/T7 read."""
    L = await _load(values)
    code = ""
    if values.get("review") is not None:
        async with session_scope() as db:
            code = (await db.execute(select(CodingSubmission.code).where(
                CodingSubmission.application_id == UUID(values["application_id"])))).scalar_one()
    review = review_with_text(values.get("review"), code)
    graded = custom_with_text(values.get("custom"), L.rows)
    rule_scores = {k: Decimal(v) for k, v in values.get("rule_scores", {}).items()}
    scores, coding_detail, custom_detail = _scored(values, L, rule_scores, review, graded)
    if dump_scores(scores) != values["scores"]:
        raise EvaluationFailed("scores changed between the score step and the report")
    detail = dict(values["detail"])
    if coding_detail is not None:
        detail["coding"] = coding_detail
    detail["custom"] = custom_detail
    detail["evidence"] = values["quality"]  # system side vs content side, counts only (requirement 1)
    coding = values.get("coding")
    if coding and "score" in coding:
        coding = {**coding, "score": Decimal(coding["score"])}
    return pipeline.Report(values["manifest"], L.units, scores, pipeline.answered_main(L.rows), values["summary"],
                           values["feedback"], review, coding, L.rows, detail)


# ── graph ──────────────────────────────────────────────────────────────────────
def _timed(name: str, fn):
    """One `eval_stage` event per step with its duration; nothing of the answers."""
    async def node(state: State, config: RunnableConfig) -> dict:
        t0 = time.monotonic()
        try:
            return await fn(state, config)
        finally:
            log_event(log, "eval_stage", application_id=state.get("application_id"), stage=name,
                      duration_ms=int((time.monotonic() - t0) * 1000))
    node.__name__ = name
    return node


def _after_units(state: State) -> str:
    return "unit" if state.get("todo") else "verify"


CHAIN = ("verify", "code_review", "custom", "score", "summary", "feedback")


def build() -> StateGraph:
    g = StateGraph(State)
    for name, fn in (("prepare", prepare), ("unit", unit), ("verify", verify), ("code_review", code_review),
                     ("custom", custom), ("score", score), ("summary", summary), ("feedback", feedback)):
        g.add_node(name, _timed(name, fn))
    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", _after_units, ["unit", "verify"])
    g.add_conditional_edges("unit", _after_units, ["unit", "verify"])
    for a, b in zip(CHAIN, CHAIN[1:]):
        g.add_edge(a, b)
    g.add_edge("feedback", END)
    return g


_compiled = None


async def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build().compile(checkpointer=await get_checkpointer())
    return _compiled


async def run(app_id: UUID, manifest: str, *, job_id: int | None = None, lease_token: int | None = None) -> State:
    """Runs (or continues) the evaluation of one job; returns the finished state. Nothing of the report is written here."""
    if not manifest:
        raise ManifestChanged("manifest unknown")
    graph = await get_graph()
    tid = thread_id(app_id, job_id)
    cfg: RunnableConfig = {"configurable": {"thread_id": tid, "job_id": job_id, "lease_token": lease_token},
                           "recursion_limit": 200}
    snap = await graph.aget_state(cfg)
    if snap.values and snap.values.get("manifest") != manifest:
        log_event(log, "evaluation_restarted", application_id=str(app_id), reason="MANIFEST_CHANGED")
        await forget(app_id, job_id)
        snap = await graph.aget_state(cfg)
    if snap.next:  # an earlier attempt stopped inside the run: carry on at that step
        log_event(log, "evaluation_resumed", application_id=str(app_id), stage=snap.next[0])
        await graph.ainvoke(None, cfg, durability="sync")
    elif not snap.values.get("finished"):  # finished but not written (crash before the write): nothing to call again
        await graph.ainvoke({"application_id": str(app_id), "manifest": manifest, "units": {}, "todo": []}, cfg,
                            durability="sync")
    return (await graph.aget_state(cfg)).values


async def forget(app_id: UUID | str, job_id: int | None) -> None:
    await (await get_checkpointer()).adelete_thread(thread_id(app_id, job_id))
