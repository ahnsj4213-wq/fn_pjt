"""Career level, question difficulty and the code checks every generated question passes (ADR-0025).

2026-11-09 user report from real interviews: a new graduate was asked a design question that combined Kafka, Redis and
Prometheus, the motivation slot asked about system architecture, and sentences were long with numbers and stacked conditions.

  career_level      NEW (under 1 year of work) | JUNIOR (1~5 years) | SENIOR, from the resume's work periods (internships and
                    part-time jobs do not count). A posting that asks for new graduates only caps it at JUNIOR.
  difficulty_plan   the technical slots (job skill, project) get BASIC / APPLIED / ADVANCED in the level's mix, easiest first:
                    NEW 50:40:10, JUNIOR 30:50:20, SENIOR 10:50:40 (largest remainder). Follow-ups move from there with the answer.
  complexity        what a question asks for, read by code: BASIC (one idea), APPLIED (one technology in a situation, or one
                    condition), ADVANCED (design, two technologies, or stacked conditions).
  problems          why a question must not be asked: three or more technologies, the wrong kind for its slot (a motivation slot
                    asking about architecture), too long, a number the sources do not have, or presuming work the candidate is
                    not known to have done. The model is never asked to judge this: it is code, so it costs no model call.

Nothing here scores or decides anything about a candidate; it only shapes the questions.
"""
import re
from datetime import date
from difflib import SequenceMatcher

LEVELS = ("NEW", "JUNIOR", "SENIOR")
DIFFICULTIES = ("BASIC", "APPLIED", "ADVANCED")
MIX = {"NEW": (0.5, 0.4, 0.1), "JUNIOR": (0.3, 0.5, 0.2), "SENIOR": (0.1, 0.5, 0.4)}
LEVEL_TEXT = {"NEW": "신입(실무 경력 1년 미만)", "JUNIOR": "주니어(경력 1~5년)", "SENIOR": "경력자(5년 이상)"}
DIFFICULTY_TEXT = {
    "BASIC": "기초: 개념 하나의 이해나 기본 사용법을 묻는다. 조건·수치·여러 기술 없이 한 문장.",
    "APPLIED": "실무 적용: 기술 하나 또는 상황 하나를 주고 어떻게 할지 묻는다. 조건은 하나까지.",
    "ADVANCED": "심화: 선택지의 장단점·설계 판단을 묻는다. 그래도 핵심 목적은 하나.",
}
MAX_LEN = {"NEW": 80, "JUNIOR": 90, "SENIOR": 100}

_NOT_WORK = re.compile(r"인턴|intern|아르바이트|알바|파트타임|part.?time|봉사|동아리|교육생|수강|부트캠프|국비", re.I)
_DATE = re.compile(r"((?:19|20)\d{2})(?:\s*[.\-/년]\s*(\d{1,2}))?")
_NOW = re.compile(r"현재|재직|진행\s*중|present|now", re.I)
_YEARS_REQ = re.compile(r"경력\s*(\d{1,2})\s*년")
_NEW_ONLY = re.compile(r"신입")


def _months(period: str | None, today: date) -> int:
    dates = [(int(y), int(m or 1)) for y, m in _DATE.findall(period or "")]
    if not dates:
        return 0
    start = dates[0]
    end = dates[1] if len(dates) > 1 else ((today.year, today.month) if _NOW.search(period or "") else None)
    if end is None:
        return 0
    return max(0, (end[0] - start[0]) * 12 + end[1] - start[1])


def work_months(resume: dict, today: date | None = None) -> int:
    today = today or date.today()
    total = 0
    for row in resume.get("experience") or []:
        if not isinstance(row, dict):
            continue
        if _NOT_WORK.search(" ".join(str(row.get(k) or "") for k in ("company", "role", "title"))):
            continue
        total += _months(row.get("period"), today)
    return total


def career_level(resume: dict, requirements: str = "", today: date | None = None) -> str:
    months = work_months(resume or {}, today)
    level = "NEW" if months < 12 else "JUNIOR" if months < 60 else "SENIOR"
    req = requirements or ""
    if _NEW_ONLY.search(req) and not _YEARS_REQ.search(req) and level == "SENIOR":
        level = "JUNIOR"  # a posting for new graduates: no senior-level design questions
    return level


def difficulty_plan(level: str, n: int) -> list[str]:
    """n difficulties in the level's mix, easiest first (largest remainder)."""
    if n <= 0:
        return []
    shares = [r * n for r in MIX.get(level, MIX["JUNIOR"])]
    counts = [int(s) for s in shares]
    for i in sorted(range(3), key=lambda i: shares[i] - counts[i], reverse=True)[: n - sum(counts)]:
        counts[i] += 1
    return [d for d, k in zip(DIFFICULTIES, counts) for _ in range(k)]


# ── reading a question ─────────────────────────────────────────────────────────
_TECH = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9+#.\-]*[A-Za-z0-9+#]|(?<![A-Za-z])[A-Z](?![A-Za-z])")
_GENERIC = {"ai", "it", "q", "a"}
_DESIGN = re.compile(r"설계|아키텍처|구조를\s*어떻게|확장성|대용량|분산\s*처리|고가용|트레이드\s*오프|병목")
# asking the candidate to design (a project named "정산 배치 재설계" in a motivation question is not that)
_DESIGN_ASK = re.compile(r"(설계|아키텍처|구조)(를|을)?\s*(어떻게|어떤\s*방식|하시|하겠|하실)|설계하|트레이드\s*오프|확장성|고가용|병목")
_STACKED = re.compile(r"하면서|동시에|면서도|지키면서|유지하면서|고려하면서|줄이면서|높이면서")
_MOTIVE = re.compile(r"지원|관심|동기|입사|합류|기여|매력|하고\s*싶|이유|선택|공감|일하고|성장|목표|비전|가치|살릴|살리|활용할")
_MOTIVE_STRICT = re.compile(r"지원\s*동기|지원하게\s*된|입사|합류하고|관심을\s*갖게|관심을\s*가지게")
_PRESUME = re.compile(r"하셨|셨던|셨을\s*때|셨나요|보셨|해\s*보신|하신\s")
_IF_ANY = re.compile(r"있다면|있으신가요|있나요|있으십니까|있으시면|있으셨|해\s*본\s*적")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def techs(text: str) -> set[str]:
    return {t.lower() for t in _TECH.findall(text or "") if t.lower() not in _GENERIC}


_SITUATION = re.compile(r"한다면|라면|하시겠|상황|경우에")


def complexity(text: str) -> str:
    t = text or ""
    score = (len(techs(t)) >= 2) + bool(_DESIGN.search(t)) + bool(_STACKED.search(t)) + bool(_SITUATION.search(t))
    return DIFFICULTIES[min(score, 2)]


def _compact(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "")


def copies_fact(text: str, basis: str) -> bool:
    """True when the question carries over most of its source sentence word for word (a long common run of characters)."""
    q, b = _compact(text), _compact(basis)
    if len(b) < 20:
        return False  # a name or a title ("결제엔진 QA 엔지니어") may be quoted whole; a sentence may not
    m = SequenceMatcher(None, q, b, autojunk=False).find_longest_match(0, len(q), 0, len(b))
    return m.size >= max(15, 0.6 * len(b))


def problems(text: str, *, kind: str, level: str = "JUNIOR", basis: str = "", from_resume: bool = False) -> list[str]:
    """Reasons this question must not be asked; [] when it may. `kind`: COMPANY (motivation) | JOB | RESUME.
    `basis`: the source sentence it was made from (numbers must come from there)."""
    t = (text or "").strip()
    out: list[str] = []
    n_tech = len(techs(t))
    if n_tech >= 3 or (level == "NEW" and n_tech >= 2 and complexity(t) == "ADVANCED"):
        out.append("MANY_TOPICS")  # one question, one thing to judge
    if len(t) > MAX_LEN.get(level, 90):
        out.append("TOO_LONG")
    if t.count(",") + t.count("，") > 2:
        out.append("TOO_LONG")
    if any(n not in (basis or "") for n in _NUMBER.findall(t)):
        out.append("NUMBER")  # a figure the sources do not have
    elif kind == "JOB" and level == "NEW" and _NUMBER.search(t):
        out.append("NUMBER")  # a new graduate is asked the idea, not the company's figure (2026-11-09: "맞힐 확률이 60~80 퍼센트인…")
    if kind == "JOB" and copies_fact(t, basis):
        out.append("COPIES_FACT")  # the source sentence set down as the premise: what is to be judged is left unsaid
    if kind == "COMPANY":
        if not _MOTIVE.search(t) or _DESIGN_ASK.search(t) or n_tech >= 2:
            out.append("NOT_MOTIVATION")
    elif kind == "JOB" and _MOTIVE_STRICT.search(t):
        out.append("NOT_JOB")
    if not from_resume and _PRESUME.search(t) and not _IF_ANY.search(t):
        out.append("PRESUMES_EXPERIENCE")  # the candidate is not known to have done this
    return list(dict.fromkeys(out))


REJECT_TEXT = {
    "MANY_TOPICS": "한 질문에는 기술·주제 하나만 묻는다(여러 기술을 엮지 않는다).",
    "TOO_LONG": "질문은 짧고 쉬운 한 문장으로 쓴다(쉼표 두 개 이하).",
    "NUMBER": "근거 문장에 없는 숫자·수치를 넣지 않는다. 신입에게는 직무 질문에 수치를 넣지 않고 개념으로 묻는다.",
    "COPIES_FACT": "자료 문장을 질문에 그대로 옮기지 않는다. 무엇을 하려는 상황인지(목적)를 먼저 말하고, 그 안에서 판단할 것 하나를 묻는다.",
    "NOT_MOTIVATION": "지원 동기 질문은 회사·직무에 관심을 갖게 된 이유나 하고 싶은 일을 묻는다(설계·기술 질문 금지).",
    "NOT_JOB": "직무 질문은 지원 동기가 아니라 업무 지식·대처를 묻는다.",
    "PRESUMES_EXPERIENCE": "지원자가 했다고 알려지지 않은 일을 했다고 가정하지 않는다('~해 본 적이 있다면'처럼 묻는다).",
    "TOO_HARD": "지원자 수준에 비해 어렵다. 설계·여러 조건 대신 개념이나 상황 하나를 묻는다.",
}


def level_block(inp: dict) -> str:
    """The first lines of a generation prompt: who is asked and how hard; sources only make the question concrete."""
    level = inp.get("level")
    if not level:
        return ""
    lines = [f"지원자 수준: {LEVEL_TEXT.get(level, level)}."]
    diffs = inp.get("difficulties") or []
    if diffs:
        lines.append("질문별 난이도(순서대로): " + " / ".join(DIFFICULTY_TEXT[d].split(":")[0] for d in diffs) + ".")
        lines += [f"- {DIFFICULTY_TEXT[d]}" for d in dict.fromkeys(diffs)]
    lines.append("질문의 목적과 난이도가 먼저다. 참고 자료는 질문을 구체적으로 만드는 데만 쓴다. "
                 f"한 질문에 핵심 목적 하나, {MAX_LEN.get(level, 90)}자 이내의 쉬운 한국어 한 문장.")
    return "\n".join(lines) + "\n\n"


# A bank question put in a slot must be of the slot's type (2026-11-10: a RAG design question asked as 지원 동기). The company bank
# holds questions the model wrote before the motivation check existed, so the type is checked when a question is taken, not stored.
_MOTIVE_ASK = re.compile(r"지원|관심|입사|합류|기여|이루고\s*싶|하고\s*싶|살릴\s*수|매력|이유")
_JOB_ASK = re.compile(r"고려하시겠|설계하시겠|적용하시겠|구축할\s*때|해결하시겠|찾으시겠|찾겠습니까|어떻게\s*하시겠")


def fits_type(kind: str, text: str) -> bool:
    """Whether a bank question of `kind` (COMPANY = 지원 동기, JOB = 직무) really asks that."""
    if kind == "COMPANY":
        return ("NOT_MOTIVATION" not in problems(text, kind="COMPANY") and bool(_MOTIVE_ASK.search(text or ""))
                and not _JOB_ASK.search(text or ""))
    if kind == "JOB":
        return "NOT_JOB" not in problems(text, kind="JOB")
    return True


def fits_level(text: str, level: str) -> bool:
    """For a bank question used in place of a generated one (QST-07): within the level's reach."""
    return not (level == "NEW" and complexity(text) == "ADVANCED") and len(techs(text)) < 3
