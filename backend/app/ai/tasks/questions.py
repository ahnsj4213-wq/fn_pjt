"""T2 question_generate (QST-01 bank top-up for COMPANY/JOB) and T3 resume_question (QST-03, AC-07).

Generation attempts: at most 2 (one regeneration, T17/QST-07). The task returns only questions that passed the
checks — possibly fewer than requested; the composer fills the rest from the bank (BANK_FALLBACK for resume slots).
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskSpec
from app.ai.textrules import (content_tokens, fence, has_p8, is_forbidden_question, looks_like_question,
                              shares_content)
from app.evaluation.quotes import find_span, normalize

QUESTION_MAX = 120


def _similar(a: str, b: str) -> bool:
    if normalize(a) == normalize(b):
        return True
    ta, tb = content_tokens(a), content_tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.8


def _shape_ok(text: str) -> bool:
    t = text.strip()
    return (0 < len(t) <= QUESTION_MAX and looks_like_question(t) and not is_forbidden_question(t) and not has_p8(t))


# ── T2 ─────────────────────────────────────────────────────────────────────────
class _GenQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=300)
    basis_quote: str = Field(max_length=400)


class GenOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[_GenQ] = Field(max_length=6)


class QuestionGenerate(TaskSpec):
    name = "question_generate"
    version = "question_generate@1.0.0"
    lane = "INTERACTIVE"
    temperature = 0.7
    num_predict = 400
    regen_limits = {"gen": 1}
    out_model = GenOut
    system = ("너는 채용 면접 질문 작성기다. 주어진 공고·회사 소개에 실제로 적힌 내용만 근거로, 요청한 유형(COMPANY=회사·사업 이해, "
              "JOB=직무 지식·업무 수행)의 면접 질문을 만든다. 질문은 한 문장, 120자 이내, 물음표나 '~말씀해 주세요'로 끝난다. "
              "basis_quote에는 근거가 된 공고·회사 소개 문장을 글자 그대로 복사한다. 나이·가족·출신·종교·건강 등 사적인 질문은 만들지 않는다.")

    @staticmethod
    def _source(inp: dict) -> str:
        p = inp.get("posting") or {}
        parts = [p.get(k) or "" for k in ("title", "duties", "requirements", "preferred")] + [inp.get("company_intro") or ""]
        return "\n".join(x for x in parts if x)

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        user = (f"질문 유형: {inp['kind']}\n직무군: {inp.get('job_family', '')}\n만들 개수: {inp.get('n', 3)}\n"
                f"공고·회사 소개:\n{fence('POSTING', self._source(inp), nonce)}")
        return user, {}

    def check(self, inp: dict, p: GenOut, ctx: dict, final: bool) -> Check:
        src, n = self._source(inp), int(inp.get("n", 3))
        existing = list(inp.get("existing") or [])
        valid: list[dict] = []
        for q in p.questions:
            text = q.text.strip()
            span = find_span(q.basis_quote, src, min_chars=4)
            if span is None or not _shape_ok(text):
                continue
            if any(_similar(text, x) for x in existing + [v["text"] for v in valid]):
                continue
            valid.append({"text": text, "basis": {"start": span.start, "end": span.end, "text": span.text}})
            if len(valid) >= n:
                break
        if len(valid) < n and not final:
            return Check(feedback={"gen": f"조건을 만족한 질문이 {len(valid)}개뿐이다. basis_quote는 공고 원문 그대로, 질문은 한 문장으로 "
                                          f"{n}개를 다시 만들어라."})
        return Check(output={"questions": valid}, flags={"valid": len(valid)})


# ── T3 ─────────────────────────────────────────────────────────────────────────
class _Ev(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(max_length=80)
    quote: str = Field(max_length=400)


class _ResQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=300)
    evidence: list[_Ev] = Field(max_length=3)


class ResQOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[_ResQ] = Field(max_length=5)


def flatten_resume(resume: dict) -> tuple[str, list[tuple[str, int, int]]]:
    """'label: value' lines (name/contact excluded) plus each value's (label, start, end) in the flat text."""
    lines: list[str] = []
    ranges: list[tuple[str, int, int]] = []
    pos = 0

    def add(label: str, value: str) -> None:
        nonlocal pos
        prefix = f"{label}: "
        lines.append(prefix + value)
        ranges.append((label, pos + len(prefix), pos + len(prefix) + len(value)))
        pos += len(prefix) + len(value) + 1

    for section in ("education", "experience", "projects", "certifications"):
        for i, row in enumerate(resume.get(section) or []):
            for f, v in (row or {}).items():
                if isinstance(v, str) and v.strip():
                    add(f"{section}[{i}].{f}", v)
    for i, s in enumerate(resume.get("skills") or []):
        if isinstance(s, str) and s.strip():
            add(f"skills[{i}]", s)
    return "\n".join(lines), ranges


class ResumeQuestion(TaskSpec):
    name = "resume_question"
    version = "resume_question@1.0.0"
    lane = "INTERACTIVE"
    temperature = 0.5
    num_predict = 500
    regen_limits = {"gen": 1}
    out_model = ResQOut
    system = ("너는 이력서 기반 면접 질문 작성기다. 이력서에 실제로 적힌 경험·프로젝트만 근거로, 지원자의 역할·행동·결과를 확인하는 질문을 "
              "3개 만든다. 질문은 한 문장, 120자 이내이며 근거가 된 경험·프로젝트를 질문 안에서 직접 언급한다. evidence에는 근거 항목의 "
              "필드 이름(field)과 그 값에서 글자 그대로 복사한 구절(quote, 8자 이상)을 쓴다. 이력서에 없는 내용을 지어내지 않는다. "
              "나이·가족·출신·종교·건강 등 사적인 질문은 만들지 않는다.")

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        flat, ranges = flatten_resume(inp.get("resume") or {})
        user = (f"지원 직무 요약:\n{fence('JOB', inp.get('job_brief', ''), nonce)}\n"
                f"이력서 표준 항목:\n{fence('RESUME', flat, nonce)}")
        return user, {"flat": flat, "ranges": ranges}

    def check(self, inp: dict, p: ResQOut, ctx: dict, final: bool) -> Check:
        flat, ranges, n = ctx["flat"], ctx["ranges"], int(inp.get("n", 2))
        valid: list[dict] = []
        for q in p.questions:
            text = q.text.strip()
            if not _shape_ok(text):
                continue
            evidence = []
            for ev in q.evidence:
                span = find_span(ev.quote, flat, min_chars=8)
                if span is None:
                    continue
                owner = next((r for r in ranges if r[1] <= span.start and span.end <= r[2]), None)
                if owner is None:
                    continue
                evidence.append({"field": owner[0], "start": span.start - owner[1], "end": span.end - owner[1],
                                 "text": span.text})
            if not evidence or not any(shares_content(text, e["text"]) for e in evidence):
                continue  # AC-07: the question must mention what the resume actually says
            if any(_similar(text, v["text"]) for v in valid):
                continue
            valid.append({"text": text, "evidence": evidence})
            if len(valid) >= n:
                break
        if len(valid) < n and not final:
            return Check(feedback={"gen": f"이력서 근거 검증을 통과한 질문이 {len(valid)}개뿐이다. quote는 이력서 값에서 그대로 복사하고, "
                                          "질문에 그 경험·프로젝트 이름을 넣어 3개를 다시 만들어라."})
        return Check(output={"questions": valid}, flags={"valid": len(valid)})


Kind = Literal["COMPANY", "JOB"]
