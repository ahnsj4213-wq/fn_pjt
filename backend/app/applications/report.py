"""결과 리포트 for the applicant (screen spec SC-A08, slides 30~34).

The applicant sees the report as soon as the evaluation is done, whatever the recruiter decides later: the decision
(합격·불합격) and its feedback are a separate screen (applications/router.py get_result). This module only reads what the
evaluation wrote; it never calls a model. It leaves out what is for the company: the AI reference, notes, model and
prompt versions, the weights history.

Scores: criterion and total scores are the stored 100-point values (scoring.py). A question's score is the mean of its
four criterion scores x 20, and a follow-up answer has its own. Highlights come from the quotes the evaluation proved to
be verbatim pieces of the answer: a quote behind a score of 4 or 5 is a 가점 phrase, 1 or 2 a 감점 phrase.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import audit
from app.auth.deps import CandidateCtx, require_candidate
from app.auth.scope import get_for_owner
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import conflict
from app.evaluation.details import negative_expressions
from app.models import (
    AnswerEvaluation, Application, Company, FollowupDecision, InterviewAnswer, InterviewQuestion, JobPosting, ProctorEvent,
    RecruiterReport, User,
)
from app.retention.service import purge_audio

router = APIRouter(prefix="/api/candidate", tags=["candidate"])

CRITERIA = [("LOGIC", "logic", "논리성", "이유와 사례로 이어 말하는지"),
            ("DELIVERY", "delivery", "전달력", "핵심을 먼저 분명하게 말하는지"),
            ("ATTITUDE", "attitude", "표현 및 태도", "적극성 · 확신 있는 표현"),
            ("EXPERTISE", "expertise", "전문성", "직무 지식·기술을 알고 쓰는지")]
SLOT_LABEL = {"PERSONALITY": "인성", "MOTIVATION": "지원 동기", "JOB_SKILL": "직무", "PROJECT": "경험",
              "COLLABORATION": "협업", "RESUME": "이력서", "COMPANY": "기업", "JOB": "직무", "CUSTOM": "공고 질문"}
# Hedging phrases that lower the confidence of an answer (rule-based, not a model judgement).
NOTICE = "AI 분석 결과는 연습을 돕기 위한 참고 자료입니다."


def grade(total: float) -> tuple[str, str]:
    if total >= 90:
        return "매우 우수", "good"
    if total >= 80:
        return "우수", "good"
    if total >= 70:
        return "양호", "good"
    if total >= 60:
        return "보통", "hold"
    return "노력 필요", "bad"


class Highlight(BaseModel):
    start: int
    end: int
    kind: str  # good | bad
    criterion: str


class Evidence(BaseModel):
    criterion: str
    label: str
    kind: str  # good | bad
    quote: str
    comment: str | None


class AnswerOut(BaseModel):
    text: str | None
    seconds: int | None
    input_mode: str
    no_answer: bool
    highlights: list[Highlight]
    negative: list[str]
    evidence: list[Evidence]
    criteria: list[dict]  # [{key, label, score, note}] on the 100-point scale; note = evidence_note (None for an ordinary score)
    score: int | None
    good: str | None
    fix: str | None
    code: str | None
    code_language: str | None


class FollowupOut(BaseModel):
    label: str
    text: str
    why: str | None
    answer: AnswerOut


class QuestionOut(BaseModel):
    no: int
    label: str
    type_label: str
    text: str
    answer: AnswerOut | None
    followup: FollowupOut | None
    limit_sec: int | None


class PointOut(BaseModel):
    rank: int
    text: str
    refs: list[int]


class ReportOut(BaseModel):
    application_id: uuid.UUID
    posting_id: uuid.UUID
    posting_title: str
    company_name: str
    candidate_name: str
    interviewed_at: datetime | None
    main_count: int
    followup_count: int
    avg_answer_sec: float | None
    total: float
    grade: str
    grade_kind: str
    criteria: list[dict]  # [{key, label, desc, score, weight}]
    coding: dict | None
    summary: str
    strengths: list[PointOut]
    improvements: list[PointOut]
    chips: list[dict]  # [{label, no, score, followup}]
    questions: list[QuestionOut]
    areas: list[dict]  # [{key, label, desc, score, items:[{label, score, comment}]}]
    attitude: dict  # {negative: [..], times: [{label, sec}], limit_sec}
    proctor: dict
    media: dict  # {audio_deleted, audio_auto_delete_days}
    share_result: bool
    notice: str
    detail: dict  # sub-items, STAR and code-measured values (evaluation/details.py); {} for older reports


def crit_key(e: AnswerEvaluation) -> str:
    """Evaluations written before migration 0019 say SPECIFICITY; they read as the fourth area (ATTITUDE). Rows are never edited."""
    return "ATTITUDE" if e.criterion == "SPECIFICITY" else e.criterion


def legacy_keys(d: dict | None) -> dict:
    """Stored weights and counts of reports made before migration 0019 use `specificity` for the fourth area."""
    d = dict(d or {})
    if "specificity" in d:
        d.setdefault("attitude", d.pop("specificity"))
    return d


def _seconds(a: InterviewAnswer) -> int | None:
    if a.opened_at is None or a.confirmed_at is None:
        return None
    return max(0, round((a.confirmed_at - a.opened_at).total_seconds()))


def _find_negative(text: str | None) -> list[str]:
    return sorted(set(negative_expressions(text or "")))  # the same list the score counts (evaluation/details.py)


def evidence_note(e: AnswerEvaluation) -> str | None:
    """How a criterion score came about when it is not an ordinary judgement with its evidence sentence (ADR-0023):
    근거 미연결 = the model's score stands but no sentence was linked; 내용 부족 = nothing in the answer to point at (the model's
    1~2, or the TOO_SHORT rule); 근거 추출 실패 = a run before ADR-0023 that floored a failed citation to 1 point."""
    if e.evidence_status == "UNLINKED":
        return "근거 미연결"
    if e.evidence_status == "NO_ANSWER":
        return "답변 없음"
    if e.evidence_status == "INSUFFICIENT":
        return "근거 추출 실패" if e.insufficient_reason in ("QUOTE_MISMATCH", "NO_EVIDENCE") else "내용 부족"
    return None


def _answer_out(aid: uuid.UUID, a: InterviewAnswer, items: list[AnswerEvaluation]) -> AnswerOut:
    order = {k: i for i, (k, *_r) in enumerate(CRITERIA)}
    items = sorted(items, key=lambda e: order[crit_key(e)])
    labels = {k: lab for k, _s, lab, _d in CRITERIA}
    highlights: list[Highlight] = []
    evidence: list[Evidence] = []
    for e in items:
        if e.evidence_status != "OK" or not e.quote or e.quote_start is None:
            continue
        kind = "good" if e.score >= 4 else "bad" if e.score <= 2 else None
        if kind is None:
            continue
        highlights.append(Highlight(start=e.quote_start, end=e.quote_end, kind=kind, criterion=labels[crit_key(e)]))
        evidence.append(Evidence(criterion=crit_key(e), label=labels[crit_key(e)], kind=kind, quote=e.quote, comment=e.comment))
    crit = [{"key": crit_key(e), "label": labels[crit_key(e)], "score": e.score * 20, "note": evidence_note(e)} for e in items]
    score = round(sum(c["score"] for c in crit) / len(crit)) if crit else None
    scored = [e for e in items if e.evidence_status == "OK" and e.comment]
    best = max(scored, key=lambda e: e.score, default=None)
    worst = min(scored, key=lambda e: e.score, default=None)
    return AnswerOut(
        text=a.eval_text, seconds=_seconds(a), input_mode=a.input_mode, no_answer=a.is_no_answer, highlights=highlights,
        negative=_find_negative(a.eval_text), evidence=evidence, criteria=crit, score=score,
        good=best.comment if best is not None and best.score >= 4 else None,
        fix=worst.comment if worst is not None and worst.score <= 3 and worst is not best else None,
        code=a.code_text, code_language=a.code_language)


@router.get("/applications/{aid}/report")
async def my_report(aid: uuid.UUID, ctx: CandidateCtx = Depends(require_candidate),
                    db: AsyncSession = Depends(get_db)) -> ReportOut:
    return await build_report(db, await get_for_owner(db, Application, aid, ctx.user_id))


async def build_report(db: AsyncSession, app: Application) -> ReportOut:
    """The SC-A08 / SC-R10 report of one finished application. The caller has already checked who may see it."""
    rep = await db.get(RecruiterReport, app.id)
    if rep is None or app.eval_status != "DONE":
        raise conflict("REPORT_NOT_READY", "리포트를 만들고 있습니다. 잠시 뒤 다시 확인해 주세요.")
    posting = await db.get(JobPosting, app.posting_id)
    company = await db.get(Company, app.company_id)
    user = await db.get(User, app.candidate_id)
    qs = (await db.execute(select(InterviewQuestion).where(InterviewQuestion.application_id == app.id)
                           .order_by(InterviewQuestion.seq, InterviewQuestion.role.desc()))).scalars().all()
    answers = {a.question_id: a for a in (await db.execute(select(InterviewAnswer).where(
        InterviewAnswer.application_id == app.id))).scalars()}
    evals: dict[uuid.UUID, list[AnswerEvaluation]] = {}
    for e in (await db.execute(select(AnswerEvaluation).where(AnswerEvaluation.run_id == rep.run_id))).scalars():
        evals.setdefault(e.answer_id, []).append(e)
    why = {d.main_question_id: d.reason for d in (await db.execute(select(FollowupDecision).where(
        FollowupDecision.application_id == app.id))).scalars()}

    questions: list[QuestionOut] = []
    chips: list[dict] = []
    area_items: dict[str, list[dict]] = {k: [] for k, *_r in CRITERIA}
    times: list[dict] = []
    seconds_all: list[int] = []
    negative_all: list[str] = []
    fu_by_parent = {q.parent_id: q for q in qs if q.role == "FOLLOWUP"}
    for m in (q for q in qs if q.role == "MAIN"):
        a = answers.get(m.id)
        main_out = _answer_out(app.id, a, evals.get(a.id, [])) if a is not None else None
        fq = fu_by_parent.get(m.id)
        fa = answers.get(fq.id) if fq is not None else None
        fu_out = None
        if fq is not None and fa is not None:
            fu_out = FollowupOut(label=f"Q{m.seq}-1", text=fq.text, why=why.get(m.id), answer=_answer_out(app.id, fa, evals.get(fa.id, [])))
        questions.append(QuestionOut(no=m.seq, label=f"Q{m.seq}", type_label=SLOT_LABEL.get(m.slot_type, m.slot_type), text=m.text,
                                     answer=main_out, followup=fu_out, limit_sec=m.answer_limit_sec))
        if main_out is None:
            continue
        chips.append({"label": f"Q{m.seq}", "no": m.seq, "score": main_out.score, "followup": False})
        for unit_label, unit in ((f"Q{m.seq}", main_out), (f"Q{m.seq}-1", fu_out.answer if fu_out else None)):
            if unit is None:
                continue
            for c in unit.criteria:
                comment = next((e.comment for e in evidence_of(unit, c["key"])), None)
                area_items[c["key"]].append({"label": unit_label, "score": c["score"], "comment": comment})
            negative_all += unit.negative
            if unit.seconds is not None:
                seconds_all.append(unit.seconds)
        if fu_out is not None:
            chips.append({"label": f"Q{m.seq}-1", "no": m.seq, "score": fu_out.answer.score, "followup": True})
        if main_out.seconds is not None:
            times.append({"label": f"Q{m.seq}", "sec": main_out.seconds, "limit": m.answer_limit_sec})

    weights = legacy_keys(rep.weights)
    total = float(rep.total_score)
    g, kind = grade(total)
    crit_scores = {"EXPERTISE": rep.score_expertise, "LOGIC": rep.score_logic, "ATTITUDE": rep.score_attitude,
                   "DELIVERY": rep.score_delivery}
    criteria = [{"key": k, "label": lab, "desc": desc, "score": round(float(crit_scores[k])), "weight": weights[w]}
                for k, w, lab, desc in CRITERIA]
    # The company's own items (SC-R07) sit after the fixed areas, with the same card shape.
    for c in (rep.detail or {}).get("custom", []):
        criteria.append({"key": f"custom:{c['id']}", "label": c["name"], "desc": c["description"], "score": c["score"],
                         "weight": weights.get(f"custom:{c['id']}", c["weight"]), "custom": True})
    refs = rep.point_refs or {}

    def points(texts: list, key: str) -> list[PointOut]:
        r = refs.get(key) or []
        return [PointOut(rank=i + 1, text=t, refs=list(r[i]) if i < len(r) else []) for i, t in enumerate(texts)]

    leave = (await db.execute(select(func.count(), func.coalesce(func.sum(ProctorEvent.away_ms), 0)).where(
        ProctorEvent.application_id == app.id, ProctorEvent.kind == "LEAVE"))).one()
    return ReportOut(
        application_id=app.id, posting_id=posting.id, posting_title=posting.title or "", company_name=company.name, candidate_name=(user.name if user else "") or "",
        interviewed_at=app.submitted_at, main_count=len([q for q in qs if q.role == "MAIN"]), followup_count=sum(1 for q in qs if q.role == "FOLLOWUP" and q.id in answers),
        avg_answer_sec=round(sum(seconds_all) / len(seconds_all), 1) if seconds_all else None, total=round(total, 1), grade=g, grade_kind=kind,
        criteria=criteria, coding=coding_view(rep, weights),
        summary=rep.summary, strengths=points(rep.strengths, "strengths"), improvements=points(rep.improvements, "improvements"),
        chips=chips, questions=questions,
        areas=[{"key": k, "label": lab, "desc": desc, "score": next(c["score"] for c in criteria if c["key"] == k), "items": area_items[k]}
               for k, _w, lab, desc in CRITERIA] + [
            {"key": c["key"], "label": c["label"], "desc": c["desc"], "score": c["score"], "custom": True,
             "items": [{"label": "면접 전체", "score": c["score"], "comment": next((d["comment"] for d in rep.detail["custom"] if f"custom:{d['id']}" == c["key"]), None)}]}
            for c in criteria if c.get("custom")],
        attitude={"negative": sorted(set(negative_all)), "times": times, "limit_sec": times[0]["limit"] if times else None},
        proctor={"leave_count": leave[0], "away_ms": int(leave[1])},
        media={"audio_deleted": app.audio_purged_at is not None, "audio_auto_delete_days": get_settings().retention_audio_days},
        share_result=app.share_result, notice=NOTICE, detail=rep.detail or {})


def coding_view(rep: RecruiterReport, weights: dict) -> dict | None:
    """The coding score and its four metrics (ADR-0017). Reports from before score-v3 have no metrics: `items` is empty."""
    if rep.score_coding is None:
        return None
    d = (rep.detail or {}).get("coding") or {}
    return {"score": round(float(rep.score_coding)), "weight": weights.get("coding"), "items": d.get("items", []),
            "time": d.get("time"), "improvements": d.get("improvements", [])}


def evidence_of(unit: AnswerOut, key: str) -> list[Evidence]:
    return [e for e in unit.evidence if e.criterion == key]


@router.post("/applications/{aid}/media/delete", status_code=204)
async def delete_my_media(aid: uuid.UUID, request: Request, ctx: CandidateCtx = Depends(require_candidate),
                          db: AsyncSession = Depends(get_db)) -> None:
    """'영상·음성 지금 삭제': the answer audio files go now. Scores and texts stay (SC-A08 10). Camera video is never stored."""
    app = await get_for_owner(db, Application, aid, ctx.user_id, for_update=True)
    if app.attempt_status not in ("SUBMITTED", "EXPIRED"):
        raise conflict("INVALID_STATE", "면접이 끝난 뒤에 지울 수 있습니다.", current=app.attempt_status)
    await purge_audio(db, [app.id], "CANDIDATE_REQUEST")
    await audit(db, action="MEDIA_DELETE", target_type="application", target_id=app.id, actor_id=ctx.user_id,
                actor_role="CANDIDATE", company_id=app.company_id, request_id=getattr(request.state, "request_id", None))
    await db.commit()

