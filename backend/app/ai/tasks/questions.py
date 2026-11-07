"""T2 question_generate (QST-01 bank top-up for COMPANY/JOB) and T3 resume_question (QST-03, AC-07).

Generation attempts: at most 2 (one regeneration, T17/QST-07). The task returns only questions that passed the
checks — possibly fewer than requested; the composer fills the rest from the bank (BANK_FALLBACK for resume slots).
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskSpec
from app.ai.textrules import (content_tokens, fence, has_p8, is_forbidden_question, looks_like_question,
                              shares_content)
from app.evaluation.quotes import find_span, normalize

QUESTION_MAX = 120


def similar_question(a: str, b: str) -> bool:
    """Same text after normalisation, or >= 80% shared content tokens (cheap duplicate check, no embeddings)."""
    if normalize(a) == normalize(b):
        return True
    ta, tb = content_tokens(a), content_tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.8


def examples_block(inp: dict, nonce: str) -> str:
    """RAG examples (ai-architecture 6.1): reference questions for tone and level, never to be copied."""
    ex = [e for e in (inp.get("examples") or []) if isinstance(e, str) and e.strip()]
    if not ex:
        return ""
    return ("\n참고 예시 질문(말투와 난이도만 참고하고, 그대로 베끼거나 단어만 바꿔 쓰지 마라. 예시에 나온 사실을 근거로 쓰지 마라):\n"
            + fence("EXAMPLES", "\n".join(f"- {e}" for e in ex), nonce))


def copies_example(text: str, inp: dict) -> bool:
    return any(similar_question(text, e) for e in (inp.get("examples") or []))


_YES_NO = re.compile(r"(있나요|있습니까|있으신가요|있었나요|있었습니까|하셨나요|하셨습니까|해 보셨나요|해 보셨습니까)\s*[?？]?\s*$")
_WH = re.compile(r"무엇|무슨|어떻게|어떤|왜|어디|언제|얼마|어느|몇|누구|어땠|어떠|어째서")


def is_yes_no(text: str) -> bool:
    """A question that can be answered with yes or no ("...한 경험이 있나요?"). One with an interrogative word (무엇, 어떻게, 왜 ...) is not."""
    return bool(_YES_NO.search(text)) and not _WH.search(text)


_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#.-]+|\d{2,}")


def unsupported_terms(question: str, source: str) -> list[str]:
    """Technology names and figures the question states but its source does not have: facts the model made up."""
    src = (source or "").lower()
    return [t for t in _TERM.findall(question) if t.strip(".-").lower() not in src]


# A candidate cannot know how the company runs things today: a question from its documents asks what they would do or think.
_OWN_COMPANY = re.compile(r"귀사|당사|우리\s*회사|저희\s*회사|우리\s*팀|저희\s*팀|이\s*회사")
_TO_CANDIDATE = re.compile(r"시겠|보십니까|보시나요|생각하|본인|경험|관심|살리|말씀해")


# "이중화하신 시스템": the company's work described as if the candidate had done it.
_CLAIMS_THEIR_WORK = re.compile(r"\S+신\s*(프로젝트|시스템|서비스|방식|구조|체계|모델)")


def asks_the_candidate(text: str) -> bool:
    """Their judgement or experience, in a question that takes more than yes or no, about work that is not said to be theirs."""
    return bool(_TO_CANDIDATE.search(text)) and bool(_WH.search(text) or "말씀해" in text) and not _CLAIMS_THEIR_WORK.search(text)


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
    version = "question_generate@1.4.0"
    lane = "INTERACTIVE"
    temperature = 0.7
    num_predict = 500
    regen_limits = {"gen": 1}
    out_model = GenOut
    system = ("너는 채용 면접 질문 작성기다. 주어진 공고·회사 소개·회사 자료에 실제로 적힌 내용만 근거로, 요청한 유형(COMPANY=회사의 프로젝트·기술과 "
              "지원자의 관심 연결, JOB=직무 지식·업무 수행)의 면접 질문을 만든다. 질문은 한 문장, 120자 이내, 물음표나 '~말씀해 주세요'로 끝난다.\n"
              "- 회사 자료가 주어지면 자료에 적힌 프로젝트 이름이나 기술 하나만 질문에 넣는다. 지원자는 회사 내부 사정을 모르므로 '어떻게 운영하나요?'처럼 "
              "회사가 하는 일을 묻지 말고, '~라면 어떻게 하시겠습니까?'처럼 지원자의 판단이나 경험을 묻는다. 자료 내용을 길게 되풀이하지 말고 80자 안팎으로 쓴다.\n"
              "- COMPANY 유형은 그 프로젝트·기술의 어떤 점에 관심이 있고 본인 경험을 어떻게 살릴지 묻는다. 형태 예: '<프로젝트 이름>에 본인의 어떤 경험을 살릴 수 있다고 보십니까?'\n"
              "- JOB 유형은 자료에 적힌 실제 과제나 제약 하나를 상황으로 제시하고, 그 안에서 지원자가 무엇을 근거로 어떻게 판단·설계할지 묻는다. 질문마다 다른 자료를 쓴다. "
              "형태 예: '<자료에 적힌 제약>을 지키면서 <자료에 적힌 과제>를 해야 한다면 무엇부터 어떻게 하시겠습니까?'\n"
              "- '직무 참고 사례'는 다른 회사에서 같은 일을 한 사례다. 이 회사의 사실이 아니므로 '귀사·우리 회사·당사'와 묶지 말고, "
              "사례의 과제·제약을 '~하는 서비스에서 ~해야 한다면'처럼 가정한 상황으로만 제시한다. 회사 자료가 없으면 이 사례로 JOB 질문을 만든다.\n"
              "- 자료에 없는 회사 사실, 기술 이름, 수치를 지어내지 않는다.\n"
              "- basis_quote에는 근거가 된 문장을 글자 그대로 복사한다. 나이·가족·출신·종교·건강 등 사적인 질문은 만들지 않는다.")

    @staticmethod
    def _parts(inp: dict) -> list[tuple[dict, str]]:
        """Where a question may be grounded: (source reference, text)."""
        p = inp.get("posting") or {}
        posting = "\n".join(x for x in (p.get(k) or "" for k in ("title", "duties", "requirements", "preferred")) if x)
        parts = [({"type": "POSTING", "id": inp.get("posting_id")}, posting),
                 ({"type": "COMPANY_INTRO", "id": inp.get("company_id")}, inp.get("company_intro") or "")]
        parts += [({"type": "KNOWLEDGE", "id": k["id"]} | ({"shared": True} if k.get("shared") else {}), f"{k['title']}: {k['text']}")
                  for k in inp.get("knowledge") or []]
        return [(ref, t) for ref, t in parts if t]

    @classmethod
    def _source(cls, inp: dict) -> str:
        return "\n".join(t for _, t in cls._parts(inp))

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        base = "\n".join(t for ref, t in self._parts(inp) if ref["type"] != "KNOWLEDGE")
        docs = "\n".join(t for ref, t in self._parts(inp) if ref["type"] == "KNOWLEDGE" and not ref.get("shared"))
        cases = "\n".join(t for ref, t in self._parts(inp) if ref.get("shared"))
        user = (f"질문 유형: {inp['kind']}\n직무군: {inp.get('job_family', '')}\n만들 개수: {inp.get('n', 3)}\n"
                f"공고·회사 소개:\n{fence('POSTING', base, nonce)}")
        if docs:
            user += f"\n회사 자료:\n{fence('COMPANY_DOCS', docs, nonce)}"
        if cases:
            user += f"\n직무 참고 사례(다른 회사, 이 회사의 사실 아님):\n{fence('JOB_CASES', cases, nonce)}"
        return user + examples_block(inp, nonce), {}

    def check(self, inp: dict, p: GenOut, ctx: dict, final: bool) -> Check:
        parts, n = self._parts(inp), int(inp.get("n", 3))
        src = "\n".join(t for _, t in parts)
        must_use_docs = inp["kind"] == "COMPANY" and any(not k.get("shared") for k in inp.get("knowledge") or [])
        existing = list(inp.get("existing") or [])
        valid: list[dict] = []
        for q in p.questions:
            text = q.text.strip()
            found = next(((ref, s) for ref, t in parts if (s := find_span(q.basis_quote, t, min_chars=4)) is not None), None)
            if found is None or not _shape_ok(text) or copies_example(text, inp):
                continue
            ref, span = found
            if not shares_content(text, span.text) or unsupported_terms(text, src):
                continue  # the question must be about its quote, and states nothing the sources do not have
            if must_use_docs and ref["type"] != "KNOWLEDGE":
                continue
            if ref["type"] == "KNOWLEDGE" and not asks_the_candidate(text):
                continue
            if ref.get("shared") and _OWN_COMPANY.search(text):
                continue  # another company's case told as this company's work
            if any(similar_question(text, x) for x in existing + [v["text"] for v in valid]):
                continue
            valid.append({"text": text, "basis": {"start": span.start, "end": span.end, "text": span.text}, "source": ref})
            if len(valid) >= n:
                break
        if len(valid) < n and not final:
            return Check(feedback={"gen": f"조건을 만족한 질문이 {len(valid)}개뿐이다. basis_quote는 공고 원문 그대로, 질문은 한 문장으로, "
                                          f"질문에 근거 문장의 말을 넣고 자료에 없는 기술 이름·수치는 빼고, 회사가 하는 일을 묻지 말고 지원자의 판단과 경험을 묻고, 참고 예시를 베끼지 말고 {n}개를 다시 만들어라."})
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
    version = "resume_question@1.4.0"
    lane = "INTERACTIVE"
    temperature = 0.5
    num_predict = 500
    regen_limits = {"gen": 1}
    out_model = ResQOut
    system = ("너는 이력서 기반 면접 질문 작성기다. 이력서에 실제로 적힌 경험·프로젝트만 근거로, 지원자의 역할·행동·결과를 확인하는 질문을 "
              "3개 만든다. 질문은 한 문장, 120자 이내다.\n"
              "- 질문마다 이력서의 서로 다른 항목 하나를 고르고, 그 항목에 적힌 구체적인 상황·시스템·기술 이름(예: '고객 배치잡 지연', "
              "'Greenplum 쿼리 지연')을 질문 안에 그대로 쓴다. '실행계획', '지표', '원인'처럼 무엇을 가리키는지 모호한 말만 쓰지 않는다.\n"
              "- '공고와 이력서의 공통 항목'이 주어지면 그 기술·경험이 적힌 이력서 항목을 먼저 고르고, 공고의 업무와 연결해 어떻게 썼는지 묻는다.\n"
              "- 질문은 반드시 의문문이고 '?'로 끝난다. '~했습니다.', '~설명해 달라.'처럼 평서문·명령문은 질문이 아니다. "
              "'~한 경험이 있나요?'처럼 예·아니오로 답할 수 있는 질문도 쓰지 않는다. 그 상황에서 무엇을 근거로 어떻게 했고 왜 그렇게 했는지, "
              "결과는 어땠는지를 묻는다. 형태 예: '<이력서에 적힌 상황>일 때 무엇을 근거로 어떻게 해결하셨고, 결과는 어땠습니까?'\n"
              "- evidence에는 근거 항목의 필드 이름(field)과 그 값에서 글자 그대로 복사한 구절(quote, 8자 이상)을 쓴다.\n"
              "- 이력서에 없는 내용을 지어내지 않는다. 나이·가족·출신·종교·건강 등 사적인 질문은 만들지 않는다.")

    def build(self, inp: dict, nonce: str) -> tuple[str, dict]:
        flat, ranges = flatten_resume(inp.get("resume") or {})
        user = (f"지원 직무 요약:\n{fence('JOB', inp.get('job_brief', ''), nonce)}\n"
                f"이력서 표준 항목:\n{fence('RESUME', flat, nonce)}{examples_block(inp, nonce)}")
        if inp.get("shared_terms"):
            user += f"\n공고와 이력서의 공통 항목: {', '.join(inp['shared_terms'])}"
        return user, {"flat": flat, "ranges": ranges}

    def check(self, inp: dict, p: ResQOut, ctx: dict, final: bool) -> Check:
        flat, ranges, n = ctx["flat"], ctx["ranges"], int(inp.get("n", 2))
        valid: list[dict] = []
        for q in p.questions:
            text = q.text.strip()
            if not _shape_ok(text) or copies_example(text, inp):
                continue
            if is_yes_no(text) and not final:
                continue  # "...경험이 있나요?" tells the interviewer nothing: ask once more for a how/why question
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
            if any(similar_question(text, v["text"]) for v in valid):
                continue
            valid.append({"text": text, "evidence": evidence})
        shared = set(inp.get("shared_terms") or [])
        valid.sort(key=lambda v: not (shared & content_tokens(v["text"])))  # stable: what posting and resume share comes first
        valid = valid[:n]
        if len(valid) < n and not final:
            return Check(feedback={"gen": f"이력서 근거 검증을 통과한 질문이 {len(valid)}개뿐이다. quote는 이력서 값에서 그대로 복사하고, "
                                          "질문에 그 경험·프로젝트의 구체적인 상황 이름을 넣고, 반드시 '?'로 끝나는 의문문('~습니까?', '~주시겠어요?')으로 무엇을 어떻게 했는지 "
                                          "묻는 질문 3개를 다시 만들어라. 평서문과 예·아니오 질문은 안 된다."})
        return Check(output={"questions": valid}, flags={"valid": len(valid)})


Kind = Literal["COMPANY", "JOB"]
