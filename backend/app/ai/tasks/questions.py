"""T2 question_generate (QST-01 bank top-up for COMPANY/JOB) and T3 resume_question (QST-03, AC-07).

One model answer per request (2026-11-11, T2 1.9.0 / T3 1.7.0): the task checks the answer and returns the questions that passed
(maybe fewer than requested) together with the ones that failed and their failure codes (`failed`, at most FEEDBACK_MAX). The
one regeneration is the composer's second request (questions/quality.py): its input carries `feedback` (the failed questions
and their codes), rendered by `feedback_block`, and `existing` (the questions already picked). There is no regeneration inside the
task any more, so a composition makes at most 2 requests x (1 + format_retries) model calls per area. `last_chance` (the second
request) relaxes the rules a question made from the posting may break instead of a document (NEEDS_DOCUMENT, YES_NO).

Every dropped question has a code (quote mismatch, unsupported term, shape, copied example, duplicate, same basis, level/type
problems, and the clarity rules of textrules qc-1: foreign script, stacked conditions); the counts per code are returned in the
flags (`rejects`), never the text.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.tasks.base import Check, TaskSpec
from app.ai.textrules import (clarity_problems, content_tokens, fence, has_p8, is_forbidden_question, looks_like_question,
                              shares_content)
from app.evaluation.quotes import find_span, normalize
from app.questions.level import REJECT_TEXT, complexity, problems
from app.questions.level import level_block as _level_block


_GLUE = re.compile(r"([A-Za-z0-9])\s+(에서|에게|에|를|을|로|으로|의|은|는|이|가|와|과|도)(?=[\s?.,]|$)")


def _compact(s: str) -> str:
    return normalize(s).strip(".,")

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
    return shape_problem(text) is None


def shape_problem(text: str) -> str | None:
    t = text.strip()
    if is_forbidden_question(t) or has_p8(t):
        return "FORBIDDEN"
    if not (0 < len(t) <= QUESTION_MAX and looks_like_question(t)):
        return "NOT_A_QUESTION"
    return None


# ── failure codes (2026-11-10) ─────────────────────────────────────────────────
# Every question a check drops gets a code. The codes go back to the model, question by question, in the one regeneration, and
# their counts (never the question text) go to the log through the task's flags. level.REJECT_TEXT holds the level/type codes.
CHECK_TEXT = {
    **REJECT_TEXT,
    "QUOTE_MISMATCH": "근거 인용이 원문에 글자 그대로 없다. 근거 문장을 그대로 복사한다.",
    "NOT_A_QUESTION": "질문은 120자 이내의 한 문장이고 '?'나 '~말씀해 주세요'로 끝난다.",
    "FORBIDDEN": "사적인 질문(나이·가족·출신·종교·건강 등)이나 합격·불합격 표현을 쓰지 않는다.",
    "COPIES_EXAMPLE": "참고 예시 질문을 베끼지 않는다.",
    "OFF_QUOTE": "질문에 근거 문장의 말을 넣어, 질문이 그 근거에 관한 것임을 드러낸다.",
    "UNSUPPORTED_TERM": "원문에 없는 기술 이름·수치를 넣지 않는다.",
    "NEEDS_DOCUMENT": "회사 자료가 있으니 근거는 회사 자료 문장에서 고른다(질문마다 다른 자료).",
    "ASKS_COMPANY": "회사가 하는 일을 묻지 말고, 지원자의 판단이나 경험을 무엇·어떻게·왜로 묻는다.",
    "SHARED_AS_OWN": "다른 회사의 참고 사례를 이 회사(귀사·당사)의 일처럼 쓰지 않는다.",
    "DUPLICATE": "이미 있는 질문과 겹친다. 다른 내용을 묻는다.",
    "SAME_BASIS": "이미 다른 질문의 근거로 쓴 문장이다. 다른 문장에서 만든다.",
    "YES_NO": "예·아니오로 답할 수 있는 질문은 안 된다. 무엇을 어떻게, 왜 했는지 묻는다.",
    "FOREIGN_SCRIPT": "한자·일본어 등 외국 문자를 섞지 말고 한국어로만 쓴다(기술 이름의 영문은 된다).",
    "NESTED": "질문 하나에 물음 하나, 조건은 하나까지만 둔다.",
    "UNGROUNDED": "이력서·회사 자료·공고에 실제로 적힌 일(기간·학교·회사 이름이 아닌 한 일)을 근거로 묻는다.",
    "NOT_CONCRETE": "회사 자료에 적힌 구체적인 이름·과제를 질문에 넣는다.",
    "SAME_DOCUMENT": "질문마다 다른 회사 자료를 쓴다.",
    "RAG_COPY": "참고 예시 질문을 베끼지 않는다.",
}
FEEDBACK_MAX = 6


def _counts(codes: list[tuple[int, list[str]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _i, per in codes:
        for c in per:
            counts[c] = counts.get(c, 0) + 1
    return counts


def feedback_block(inp: dict, nonce: str) -> str:
    """The regeneration (the composer's second request): the questions of the first request that failed, each with its failure
    codes and what the code asks for, and the questions already picked (not to be made again)."""
    fb = [f for f in inp.get("feedback") or [] if isinstance(f, dict) and f.get("text")][:FEEDBACK_MAX]
    if not fb:
        return ""
    reasons = "\n".join(f"- {n}번: " + " ".join(f"[{c}] {CHECK_TEXT.get(c, '')}".strip() for c in f.get("codes") or [])
                        for n, f in enumerate(fb, start=1))
    texts = "\n".join(f"{n}. {f['text']}" for n, f in enumerate(fb, start=1))
    out = ("\n직전 시도에서 아래 질문은 검증을 통과하지 못해 빠졌다. 이유를 고쳐 새 질문을 만들어라. 같은 질문을 다시 쓰지 마라.\n"
           + fence("REJECTED", texts, nonce) + "\n" + reasons)
    existing = [e for e in inp.get("existing") or [] if isinstance(e, str) and e]
    if existing:
        out += "\n이미 정해진 질문(다시 쓰지 않는다):\n" + fence("PICKED", "\n".join(existing), nonce)
    return out


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
    version = "question_generate@1.9.0"
    format_retries = 1  # llama.cpp grammar keeps the JSON valid; one repair (a cut answer) is enough
    lane = "INTERACTIVE"
    temperature = 0.7
    num_predict = 500
    regen_limits: dict = {}  # the regeneration is the composer's second request (questions/quality.py)
    out_model = GenOut
    system = ("너는 채용 면접 질문 작성기다. 주어진 공고·회사 소개·회사 자료에 실제로 적힌 내용만 근거로, 요청한 유형(COMPANY=회사의 프로젝트·기술과 "
              "지원자의 관심 연결, JOB=직무 지식·업무 수행)의 면접 질문을 만든다. 질문은 짧고 쉬운 한국어 한 문장(맨 앞에 주어진 지원자 수준의 글자 수 이내, 한자·외국 문자 없이), "
              "물음표나 '~말씀해 주세요'로 끝난다. 질문 하나는 핵심 목적 하나만 묻고, 기술 이름은 하나까지만 넣는다.\n"
              "- 회사 자료가 주어지면 자료에 적힌 프로젝트 이름이나 기술 하나만 질문에 넣는다. 지원자는 회사 내부 사정을 모르므로 '어떻게 운영하나요?'처럼 "
              "회사가 하는 일을 묻지 말고, '~라면 어떻게 하시겠습니까?'처럼 지원자의 판단을 묻는다. 지원자가 했다고 알려지지 않은 일을 '~하셨을 때'처럼 가정하지 않는다. "
              "자료 내용을 길게 되풀이하지 않는다.\n"
              "- COMPANY 유형은 지원 동기 질문이다. 그 회사·프로젝트의 어떤 점에 관심이 있고 무엇을 하고 싶은지 묻는다. 설계·아키텍처·기술 비교는 묻지 않는다. 형태 예: '<프로젝트 이름>에 본인의 어떤 경험을 살릴 수 있다고 보십니까?'\n"
              "- JOB 유형은 자료에 적힌 실제 과제나 제약 하나를 상황으로 제시하고, 그 안에서 지원자가 무엇을 근거로 어떻게 판단·설계할지 묻는다. 질문마다 다른 자료를 쓴다. "
              "형태 예: '<자료에 적힌 과제>를 맡는다면 무엇부터 확인하시겠습니까?' 난이도가 기초면 개념이나 기본 사용법을 묻는다. "
              "자료 문장을 전제로 그대로 옮기지 말고, 질문만 읽어도 무엇을 하려는 상황인지(목적) 알 수 있게 쓴다. "
              "예: 자료 '맞힐 확률이 60~80%인 문제를 후보로 고릅니다' → '학습자에게 너무 쉽거나 어렵지 않은 문제를 추천하려면 어떤 기준을 쓰시겠습니까?'\n"
              "- '직무 참고 사례'는 다른 회사에서 같은 일을 한 사례다. 이 회사의 사실이 아니므로 '귀사·우리 회사·당사'와 묶지 말고, "
              "사례의 과제·제약을 '~하는 서비스에서 ~해야 한다면'처럼 가정한 상황으로만 제시한다. 회사 자료가 없으면 이 사례로 JOB 질문을 만든다.\n"
              "- 자료에 없는 회사 사실, 기술 이름, 수치를 지어내지 않는다. 필요 없는 숫자와 여러 겹의 가정·조건을 넣지 않는다.\n"
              "- 질문 하나는 근거 문장 하나에서만 만든다. 다른 줄의 표현을 이어 붙이지 않고, 문장의 주어와 서술어가 맞는지 확인한다.\n"
              "- '이미 쓴 근거'에 있는 문장은 다시 근거로 쓰지 않는다.\n"
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
        used = [b for b in inp.get("used_basis") or [] if b]
        if used:
            user += "\n이미 쓴 근거(다시 근거로 쓰지 않는다):\n" + fence("USED", "\n".join(used), nonce)
        return _level_block(inp) + user + examples_block(inp, nonce) + feedback_block(inp, nonce), {}

    def check(self, inp: dict, p: GenOut, ctx: dict, final: bool) -> Check:
        # `last_chance` (the composer's second request, with the feedback): the relaxed rules of the last attempt.
        final = bool(inp.get("last_chance"))
        parts, n = self._parts(inp), int(inp.get("n", 3))
        src = "\n".join(t for _, t in parts)
        level = inp.get("level") or "JUNIOR"
        # This company's own documents were found: a job question comes from one of them, not from the posting's lines (2026-11-09:
        # three job questions all came from one posting line while four documents were in the prompt). The documents make a question
        # concrete; they do not decide its purpose or difficulty (ADR-0025): a new graduate's basic question may come from the posting.
        must_use_docs = ((inp["kind"] == "COMPANY" or level != "NEW")
                         and any(not k.get("shared") for k in inp.get("knowledge") or []))
        advanced_left = sum(1 for d in inp.get("difficulties") or [] if d == "ADVANCED") if inp.get("difficulties") else n
        existing = list(inp.get("existing") or [])
        used_basis = [_compact(b) for b in inp.get("used_basis") or [] if b]
        valid: list[dict] = []
        codes: list[tuple[int, list[str]]] = []
        failed: list[dict] = []
        for no, q in enumerate(p.questions, start=1):
            if len(valid) >= n:
                break
            text = _GLUE.sub(r"\1\2", q.text.strip())  # "API 에" -> "API에"
            bad, item = self._judge(inp, text, q.basis_quote, parts, src, level, must_use_docs, final, existing, used_basis, valid,
                                    advanced_left)
            if bad:
                codes.append((no, bad))
                failed.append({"text": text[:QUESTION_MAX * 2], "codes": bad})
                continue
            advanced_left -= item["difficulty"] == "ADVANCED"
            valid.append(item)
        flags = {"valid": len(valid), "rejects": _counts(codes)}
        return Check(output={"questions": valid, "failed": failed[:FEEDBACK_MAX]}, flags=flags)

    def _judge(self, inp: dict, text: str, quote: str, parts: list, src: str, level: str, must_use_docs: bool, final: bool,
               existing: list[str], used_basis: list[str], valid: list[dict], advanced_left: int) -> tuple[list[str], dict | None]:
        """([], question) when it may be asked, else its failure codes (all the code checks that apply, cheapest first)."""
        found = next(((ref, s) for ref, t in parts if (s := find_span(quote, t, min_chars=4)) is not None), None)
        if found is None:
            return ["QUOTE_MISMATCH"], None
        shape = shape_problem(text)
        if shape:
            return [shape], None
        if copies_example(text, inp):
            return ["COPIES_EXAMPLE"], None
        ref, span = found
        out = clarity_problems(text)
        if not shares_content(text, span.text):
            out.append("OFF_QUOTE")  # the question must be about its quote
        if unsupported_terms(text, src):
            out.append("UNSUPPORTED_TERM")  # ... and states nothing the sources do not have
        if must_use_docs and ref["type"] != "KNOWLEDGE" and not final:
            out.append("NEEDS_DOCUMENT")  # on the retry a posting question still beats a code-made one
        if ref["type"] == "KNOWLEDGE" and not asks_the_candidate(text):
            out.append("ASKS_COMPANY")
        if ref.get("shared") and _OWN_COMPANY.search(text):
            out.append("SHARED_AS_OWN")  # another company's case told as this company's work
        if any(similar_question(text, x) for x in existing + [v["text"] for v in valid]):
            out.append("DUPLICATE")
        # a motivation question from the company's own document is asked in a fixed sentence (compose.MOTIVATION_QUESTION),
        # checked there: here only what the model's wording would carry over
        own_doc = inp["kind"] == "COMPANY" and ref["type"] == "KNOWLEDGE" and not ref.get("shared")
        level_bad = [r for r in problems(text, kind=inp["kind"], level=level, basis=span.text) if not (own_doc and r == "NOT_MOTIVATION")]
        hard = complexity(text) == "ADVANCED"
        if not level_bad and hard and advanced_left <= 0:
            level_bad = ["TOO_HARD"]  # the level's mix has no (more) room for a design question
        out += level_bad
        here = _compact(span.text)
        if len(here) >= 8 and any(here in b or (len(b) >= 8 and b in here) for b in used_basis + [_compact(v["basis"]["text"]) for v in valid]):
            out.append("SAME_BASIS")  # the same sentence already gave a question of this set: another sentence, another question
        if out:
            return list(dict.fromkeys(out)), None
        return [], {"text": text, "basis": {"start": span.start, "end": span.end, "text": span.text}, "source": ref,
                    "difficulty": complexity(text)}


# ── T3 ─────────────────────────────────────────────────────────────────────────
# The fields of a resume row a question can be about: the work itself, not when or where it happened (2026-11-11: a question
# "<학교> 과정에서 2016-2020 경험에 대해 여쭙겠습니다" was grounded in a period).
_NO_WORK_FIELD = re.compile(r"\.(period|date|degree|issuer|name|school|major|company|role|title)$")


def is_work_evidence(field: str | None, text: str | None) -> bool:
    """A resume quote a question may stand on: from a field that describes work, with a word that is not a number or a date."""
    words = {t for t in content_tokens(text or "") if not t.replace("-", "").replace(".", "").replace("~", "").isdigit()}
    return bool(words) and not _NO_WORK_FIELD.search(field or "")

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
    version = "resume_question@1.7.0"
    format_retries = 1  # as T2: the grammar keeps the JSON valid (2026-11-11: was 2; bounds the model calls per request)
    lane = "INTERACTIVE"
    temperature = 0.5
    num_predict = 500
    regen_limits: dict = {}  # the regeneration is the composer's second request (questions/quality.py)
    out_model = ResQOut
    system = ("너는 이력서 기반 면접 질문 작성기다. 이력서에 실제로 적힌 경험·프로젝트만 근거로, 지원자의 역할·행동·결과를 확인하는 질문을 "
              "3개 만든다. 질문은 짧고 쉬운 한 문장(맨 앞에 주어진 지원자 수준의 글자 수 이내)이고, 핵심 목적 하나만 묻는다.\n"
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
        return _level_block(inp) + user + feedback_block(inp, nonce), {"flat": flat, "ranges": ranges}

    def check(self, inp: dict, p: ResQOut, ctx: dict, final: bool) -> Check:
        flat, ranges, n = ctx["flat"], ctx["ranges"], int(inp.get("n", 2))
        level = inp.get("level") or "JUNIOR"
        final = bool(inp.get("last_chance"))  # the composer's second request: the relaxed rules of the last attempt
        advanced_left = sum(1 for d in inp.get("difficulties") or [] if d == "ADVANCED") if inp.get("difficulties") else n
        valid: list[dict] = []
        codes: list[tuple[int, list[str]]] = []
        failed: list[dict] = []
        for no, q in enumerate(p.questions, start=1):
            if len(valid) >= n:
                break
            text = q.text.strip()
            bad, item = self._judge(inp, text, q.evidence, flat, ranges, level, final, valid, advanced_left)
            if bad:
                codes.append((no, bad))
                failed.append({"text": text[:QUESTION_MAX * 2], "codes": bad})
                continue
            advanced_left -= item["difficulty"] == "ADVANCED"
            valid.append(item)
        shared = set(inp.get("shared_terms") or [])
        valid.sort(key=lambda v: not (shared & content_tokens(v["text"])))  # stable: what posting and resume share comes first
        valid = valid[:n]
        flags = {"valid": len(valid), "rejects": _counts(codes)}
        return Check(output={"questions": valid, "failed": failed[:FEEDBACK_MAX]}, flags=flags)

    @staticmethod
    def _judge(inp: dict, text: str, evs, flat: str, ranges, level: str, final: bool, valid: list[dict],
               advanced_left: int) -> tuple[list[str], dict | None]:
        shape = shape_problem(text)
        if shape:
            return [shape], None
        if copies_example(text, inp):
            return ["COPIES_EXAMPLE"], None
        out = clarity_problems(text)
        if is_yes_no(text) and not final:
            out.append("YES_NO")  # "...경험이 있나요?" tells the interviewer nothing: ask once more for a how/why question
        evidence = []
        for ev in evs:
            span = find_span(ev.quote, flat, min_chars=8)
            if span is None:
                continue
            owner = next((r for r in ranges if r[1] <= span.start and span.end <= r[2]), None)
            if owner is None:
                continue
            evidence.append({"field": owner[0], "start": span.start - owner[1], "end": span.end - owner[1], "text": span.text})
        if not evidence:
            return out + ["QUOTE_MISMATCH"], None
        evidence = [e for e in evidence if is_work_evidence(e["field"], e["text"])]
        if not evidence:
            return out + ["UNGROUNDED"], None  # a period, a school or a job title is not something the candidate did
        if not any(shares_content(text, e["text"]) for e in evidence):
            out.append("OFF_QUOTE")  # AC-07: the question must mention what the resume actually says
        if any(similar_question(text, v["text"]) for v in valid):
            out.append("DUPLICATE")
        bad = problems(text, kind="RESUME", level=level, basis=" ".join(e["text"] for e in evidence) + " " + flat, from_resume=True)
        if not bad and complexity(text) == "ADVANCED" and advanced_left <= 0:
            bad = ["TOO_HARD"]
        out += bad
        if out:
            return list(dict.fromkeys(out)), None
        return [], {"text": text, "evidence": evidence, "difficulty": complexity(text)}


Kind = Literal["COMPANY", "JOB"]
