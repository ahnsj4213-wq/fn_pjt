"""Question quality gate (2026-11-11): one place that decides whether a composed question may be asked, by code only (no model
call), and the one bounded loop that asks the model again with the reasons.

    judge(c, pick)       the reasons a question must not be asked ([] = it may), as failure codes:
      grounding          UNGROUNDED     a generated question without a source it was made from: the resume quote is not in the
                                        resume or names no work (a period, a degree), a posting basis that is not in the posting
      level              TOO_HARD, TOO_LONG, MANY_TOPICS, NUMBER, COPIES_FACT, PRESUMES_EXPERIENCE   (questions/level.py)
      type               NOT_MOTIVATION (지원 동기 slot), NOT_JOB (직무 slot), UNGROUNDED for an experience question that does
                         not come from the resume
      clarity            FOREIGN_SCRIPT, NESTED (textrules qc-1), NOT_A_QUESTION, FORBIDDEN
      repetition         DUPLICATE (like a question already in the set or asked), SAME_BASIS (the same resume sentence, posting
                         line or document passage as another question)
    fill(c, slots, ...)  attempt 1 -> judge -> (anything missing) attempt 2 with the reasons as feedback -> judge -> code-made
                         fallback (TEMPLATE). Never a third request.

Bounds (tested in tests/test_question_quality.py): at most MAX_REQUESTS model requests per area of a composition (an area's
slots share a request; live composition fills one slot at a time, so per slot), each at most 1 + format_retries model calls
inside the task (no regeneration inside the task any more: the second request here is the only regeneration). With
format_retries 1 that is MAX_MODEL_CALLS = 4 model calls, where the earlier design allowed 5 for T2 (two RAG rounds, one
in-task regeneration) and 4 for T3.

Custom questions and live-coding problems are the company's own and are never judged. Code-made questions (TEMPLATE) pass the
same gate (check_fallback): one that fails (a posting line with foreign script, a repeat) is replaced by a fixed safe sentence of
its area (SAFE_TEXT, no company or candidate text in it); when none of those passes either, the composition fails explicitly
(ComposeError) instead of asking a question that failed the gate (PR #2 review).
Nothing here scores or decides anything about a candidate, and the reasons are logged as counts, never with the text.
"""
import re
from dataclasses import replace

from app.ai.tasks import TASKS
from app.ai.tasks.questions import FEEDBACK_MAX, flatten_resume, is_work_evidence, shape_problem, similar_question
from app.ai.textrules import clarity_problems
from app.core.constants import Area
from app.evaluation.quotes import normalize
from app.questions.level import complexity, problems
from app.resumes.schemas import to_ai_resume

MAX_REQUESTS = 2  # the first generation and one regeneration with the failure reasons
GEN_TASKS = ("question_generate", "resume_question")

# Every reason by the rule it belongs to (for the report); each has its feedback sentence in CHECK_TEXT.
REASON_GROUP = {
    "UNGROUNDED": "grounding", "QUOTE_MISMATCH": "grounding", "OFF_QUOTE": "grounding", "UNSUPPORTED_TERM": "grounding",
    "NEEDS_DOCUMENT": "grounding",
    "TOO_HARD": "level", "TOO_LONG": "level", "MANY_TOPICS": "level", "NUMBER": "level", "COPIES_FACT": "level",
    "PRESUMES_EXPERIENCE": "level",
    "NOT_MOTIVATION": "type", "NOT_JOB": "type", "ASKS_COMPANY": "type", "SHARED_AS_OWN": "type",
    "FOREIGN_SCRIPT": "clarity", "NESTED": "clarity", "NOT_A_QUESTION": "clarity", "FORBIDDEN": "clarity", "YES_NO": "clarity",
    "DUPLICATE": "repetition", "SAME_BASIS": "repetition", "COPIES_EXAMPLE": "repetition", "RAG_COPY": "repetition",
    "NOT_CONCRETE": "grounding", "SAME_DOCUMENT": "repetition",
}


def max_model_calls() -> int:
    """The most model calls one area of a composition can make: MAX_REQUESTS requests, each with its format repairs."""
    return MAX_REQUESTS * (1 + max(TASKS[t].format_retries for t in GEN_TASKS))


# ── what a question stands on ──────────────────────────────────────────────────
def _squeeze(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def _basis_keys(p, resume: dict) -> set[str]:
    """The source passages a question was made from, compared across the set (SAME_BASIS): the resume sentence a quote sits in
    (two quotes from one sentence are one part of the resume), the posting line or document passage of a generated question."""
    from app.questions.compose import _evidence_key  # compose imports this module

    g = p.grounding or {}
    keys = {"ev:%s:%s" % _evidence_key(resume, ev) for ev in g.get("evidence") or []}
    basis = _squeeze((g.get("basis") or {}).get("text"))
    if len(basis) >= 8:
        keys.add("basis:" + basis)
    return keys


def _resume(c) -> dict:
    return to_ai_resume(c.app.resume_snapshot) if getattr(c.app, "resume_snapshot", None) else {}


def _posting_text(c) -> str:
    p = c.posting
    return "\n".join(x for x in (p.title, p.duties, p.requirements, p.preferred, getattr(c.company, "intro", None)) if x)


def _grounding(c, p) -> list[str]:
    """A generated question must stand on something the candidate or the company actually wrote."""
    g = p.grounding or {}
    if p.slot == Area.PROJECT.value:
        evs = g.get("evidence") or []
        if not evs:
            return ["UNGROUNDED"]  # an experience question that does not come from the resume
        flat = normalize(flatten_resume(_resume(c))[0])
        for ev in evs:
            text = ev.get("text") or ""
            if not is_work_evidence(ev.get("field"), text) or normalize(text) not in flat:
                return ["UNGROUNDED"]
        return []
    if not g.get("sources"):
        return ["UNGROUNDED"]
    basis = (g.get("basis") or {}).get("text")
    src = (g.get("sources") or [{}])[0]
    if basis and src.get("type") in ("POSTING", "COMPANY_INTRO") and normalize(basis) not in normalize(_posting_text(c)):
        return ["UNGROUNDED"]
    return []


def judge(c, p, others=None) -> list[str]:
    """The reasons `p` must not be asked in this set ([] when it may). `others`: the questions it must not repeat (default: the
    ones picked so far, which in live composition include the questions already asked)."""
    if p.source in ("CUSTOM", "PROBLEM"):
        return []
    others = [o for o in (c.picked if others is None else others) if o is not p]
    text = (p.text or "").strip()
    out: list[str] = []
    shape = shape_problem(text)
    if shape:
        out.append(shape)
    out += clarity_problems(text)
    g = p.grounding or {}
    if p.source == "AI":
        out += _grounding(c, p)
        if p.slot == Area.MOTIVATION.value and not p.fixed:  # a fixed sentence (MOTIVATION_QUESTION) is a motivation question
            out += problems(text, kind="COMPANY", level=c.level, basis=(g.get("basis") or {}).get("text") or "")
        elif p.slot == Area.JOB_SKILL.value:
            out += problems(text, kind="JOB", level=c.level, basis=(g.get("basis") or {}).get("text") or "")
            hard = complexity(text) == "ADVANCED"
            if hard and (c.level == "NEW" and g.get("difficulty") != "ADVANCED" or g.get("difficulty") == "BASIC"):
                out.append("TOO_HARD")
        # PROJECT: the model's own sentence passed the level rules in T3; the sentence asked is code around a verified quote
    if any(similar_question(text, o.text) for o in others):
        out.append("DUPLICATE")
    resume = _resume(c)
    if _basis_keys(p, resume) & {k for o in others for k in _basis_keys(o, resume)}:
        out.append("SAME_BASIS")
    return list(dict.fromkeys(out))


# ── the loop ───────────────────────────────────────────────────────────────────
def take(c, p) -> bool:
    """Judge one candidate: picked (True) or counted and remembered for the regeneration (False)."""
    reasons = judge(c, p)
    if not reasons:
        c.picked.append(p)
        return True
    for r in reasons:
        c.reject(r)
    c.failed.append({"text": p.text, "codes": reasons})
    return False


# Fixed sentences a failed code-made question is replaced by: no company or candidate text, checked by tests to pass judge.
SAFE_TEXT = {
    Area.PERSONALITY.value: ("스스로 세운 목표를 이루기 위해 꾸준히 노력했던 경험을 말씀해 주시겠습니까?",
                             "실수를 했던 경험이 있다면, 그 뒤에 무엇을 바꾸셨습니까?"),
    Area.COLLABORATION.value: ("함께 일하는 사람과 생각이 달랐던 경험이 있다면, 어떻게 풀어 가셨습니까?",
                               "팀에서 맡은 역할을 끝까지 해내기 위해 어떤 노력을 하셨습니까?"),
    Area.MOTIVATION.value: ("이 회사에 지원하게 된 가장 큰 이유는 무엇입니까?",
                            "입사한다면 이 회사에서 가장 먼저 해 보고 싶은 일은 무엇입니까?"),
    Area.JOB_SKILL.value: ("지원하신 직무에서 가장 중요하다고 생각하는 역량은 무엇이며, 그 이유는 무엇입니까?",
                           "지원하신 직무의 일을 처음 맡게 된다면 무엇부터 확인하시겠습니까?",
                           # a third: the default plan has three 직무 and three 경험 slots (a posting or resume whose every line
                           # has foreign script would otherwise fail the composition at the third slot)
                           "지원하신 직무를 잘 해내기 위해 지금 준비하고 있는 것은 무엇입니까?"),
    Area.PROJECT.value: ("지금까지 해 본 일 가운데 가장 어려웠던 문제는 무엇이었고, 어떻게 해결하셨습니까?",
                         "지금까지 해 본 일 가운데 가장 기억에 남는 일은 무엇이었고, 본인은 어떤 역할을 맡았습니까?",
                         "직접 맡아서 끝까지 마무리했던 일 하나를 골라, 그 과정을 말씀해 주시겠습니까?"),
}


def check_fallback(c, start: int) -> None:
    """The code-made questions picked since index `start` pass the same gate as generated ones. A failing one is replaced by a
    SAFE_TEXT sentence of its area that passes; with none left, ComposeError (never a question that failed the gate)."""
    from app.questions.compose import ComposeError  # compose imports this module

    for i in range(start, len(c.picked)):
        p = c.picked[i]
        reasons = judge(c, p)
        if not reasons:
            continue
        for r in reasons:
            c.reject(r)
        others = [o for o in c.picked if o is not p]
        for alt in SAFE_TEXT.get(p.slot, ()):
            q = replace(p, text=alt, grounding={"sources": [{"type": "TEMPLATE"}], "fallback": "SAFE"})
            if not judge(c, q, others):
                c.picked[i] = q
                break
        else:
            raise ComposeError(f"{p.slot}: no code-made question passes the quality gate for seq {p.seq}")


async def fill(c, slots: list[tuple[int, str]], attempt, fallback) -> None:
    """Fill `slots` (one area): `attempt(c, empty)` makes one model request and passes each candidate through `take`;
    whatever is still empty after MAX_REQUESTS requests (or a timeout) is made by `fallback(c, empty)` (code, no model)."""
    c.feedback, c.failed = [], []
    for n in range(MAX_REQUESTS):
        empty = [s for s in slots if s[0] not in {p.seq for p in c.picked}]
        if not empty:
            break
        c.attempt = n
        status = await attempt(c, empty)
        c.requests += 1
        if status == "TIMEOUT":
            break  # the model is too slow for a second request: the code-made question
        c.feedback, c.failed = c.failed[-FEEDBACK_MAX:], []
    c.attempt, c.feedback, c.failed = 0, [], []
    rest = [s for s in slots if s[0] not in {p.seq for p in c.picked}]
    if rest:
        start = len(c.picked)
        fallback(c, rest)
        check_fallback(c, start)
