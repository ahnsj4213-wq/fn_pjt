"""Server-side verification of one answer unit's judgement (code only: no model, no database). EVL-02·03·04, ADR-0023.

Who does what in the evaluation of an answer:
- the model (T5 `evaluate_answer`, called from pipeline.ask_model_for_judgements) gives per criterion a score candidate 1~5,
  the reason (comment) and a sentence number. Nothing else: no total, no pass/fail, no recommendation.
- this module checks every judgement and decides what happens to the ones that fail (`find_problems`, `settle`);
- scoring.compute turns the checked scores into area scores and the total (weights must sum to 100).

The verification loop (pipeline.evaluate_unit), at most once per evaluation of a unit:
    samples (eval_samples T5 calls) -> median -> find_problems
      nothing found              -> settled as it is
      something found            -> ONE more T5 call with the reasons as feedback (`review_lines`), the failed criteria are
                                    taken from it (`merge_rejudge`; an only-unstable criterion keeps its median and
                                    the extra judgement only confirms it) -> find_problems again -> `settle`
`settle` never asks the model again. What still fails is put on one of two sides:
- system side  the evidence could not be linked or verified (UNLINKED, INSUFFICIENT/QUOTE_MISMATCH): the model's judgement
               is not trusted as evidence; too many of these hold the evaluation (graph.verify, SYSTEM_HOLD_RATIO).
- content side the model's own reason says the answer lacks it: INSUFFICIENT/LOW_CONTENT with the score capped at CONTENT_CAP.
A wrong shape (criterion missing, score outside 1~5) cannot be settled: the unit fails (EvaluationFailed, held by EVL-06).
A criterion that stays unstable keeps its median score and is counted (UNSTABLE), it is neither held nor capped.

Reason codes (CHECK_VERSION):
    SHAPE           a criterion missing, a score that is not an integer 1~5, a status that cannot go with its score
    QUOTE_MISMATCH  an OK item whose quote is not exactly that span of the answer text
    NO_EVIDENCE     a score of LINK_MIN_SCORE (3) or more without a verified sentence of the answer
    REASON_SCORE    the reason says the answer lacks it (부족, 드러나지 않음, ...) but the score is REASON_HIGH (4) or more
    UNSTABLE        the samples of one criterion are UNSTABLE_SPREAD (3) or more points apart; after the re-judgement it stays
                    UNSTABLE when the extra judgement is more than 1 point away from the kept score
"""
import re

from app.ai import rubric
from app.ai.tasks.evaluate import LINK_MIN_SCORE
from app.core.constants import CRITERIA, Criterion

CHECK_VERSION = "unit-check-1"
REASON_HIGH = 4
CONTENT_CAP = 2  # = the LOW_CONTENT ceiling of answer_evaluations (migration 0033)
UNSTABLE_SPREAD = 3
MAX_REJUDGE = 1  # per evaluation of a unit; the loop has no other way back to the model

ITEM_CODES = ("SHAPE", "QUOTE_MISMATCH", "NO_EVIDENCE", "REASON_SCORE")
CODES = ITEM_CODES + ("UNSTABLE",)
STATUSES = ("OK", "UNLINKED", "INSUFFICIENT", "NO_ANSWER")
# The reason says the answer lacks what the criterion needs. Narrow on purpose: '없다' alone is often praise ('오류가 없다').
_LACKING = re.compile(r"부족|드러나지\s*않|확인되지\s*않|언급하지\s*않|언급이\s*없|제시하지\s*않|설명하지\s*않|모호|불명확"
                      r"|(근거|내용|사례|설명|결과|이유|구체성)(가|이|는|은)?\s*없")
_NAME = {i["criterion"]: f"{i['key']}({i['name']})" for i in rubric.ITEMS}


def lacking(comment: str | None) -> bool:
    return bool(_LACKING.search(comment or ""))


def _score_ok(sc) -> bool:
    return isinstance(sc, int) and not isinstance(sc, bool) and 1 <= sc <= 5


def item_problem(item: dict, text: str) -> str | None:
    """The form and evidence rule of one item (shared with the reuse rules of stored results, units.reuse_problems).
    None = fine; SHAPE; QUOTE_MISMATCH; NO_EVIDENCE (a score of 3+ without a sentence)."""
    st, sc, q, reason = item.get("evidence_status"), item.get("score"), item.get("quote"), item.get("insufficient_reason")
    if st not in STATUSES or not _score_ok(sc):
        return "SHAPE"
    if st == "OK":
        if not q:
            return "NO_EVIDENCE" if sc >= LINK_MIN_SCORE else "SHAPE"
        s, e = q.get("start"), q.get("end")
        if not isinstance(s, int) or not isinstance(e, int) or not 0 <= s < e <= len(text) or text[s:e] != q.get("text"):
            return "QUOTE_MISMATCH"
        return None
    if st == "UNLINKED":
        return "SHAPE" if q else ("NO_EVIDENCE" if sc >= LINK_MIN_SCORE else None)
    if st == "INSUFFICIENT":
        if reason == "LOW_CONTENT":
            return None if sc <= CONTENT_CAP else "SHAPE"
        return None if reason in ("TOO_SHORT", "QUOTE_MISMATCH", "NO_EVIDENCE") and sc == 1 else "SHAPE"
    return None if sc == 1 else "SHAPE"  # NO_ANSWER


def spread(outputs: list[dict], crit: str) -> tuple[int, int] | None:
    scores = [o["items"][crit]["score"] for o in outputs if _score_ok((o.get("items", {}).get(crit) or {}).get("score"))]
    return (min(scores), max(scores)) if len(scores) >= 2 else None


def find_problems(judged: dict, text: str, outputs: list[dict] | None = None) -> dict[str, list[str]]:
    """Reason codes per criterion of a median judgement ({} = it passes). `outputs` (the samples it came from) adds UNSTABLE."""
    items = judged.get("items") if isinstance(judged.get("items"), dict) else {}
    found: dict[str, list[str]] = {}
    for c in CRITERIA:
        it = items.get(c.value)
        codes = []
        p = item_problem(it, text) if isinstance(it, dict) else "SHAPE"
        if p:
            codes.append(p)
        if p != "SHAPE" and it["score"] >= REASON_HIGH and it["evidence_status"] in ("OK", "UNLINKED") and lacking(it.get("comment")):
            codes.append("REASON_SCORE")
        lo_hi = spread(outputs or [], c.value)
        if p != "SHAPE" and lo_hi and lo_hi[1] - lo_hi[0] >= UNSTABLE_SPREAD:
            codes.append("UNSTABLE")
        if codes:
            found[c.value] = codes
    extra = set(items) - {c.value for c in CRITERIA}
    if extra:
        found.setdefault(sorted(extra)[0], []).append("SHAPE")
    return found


def review_lines(problems: dict[str, list[str]], judged: dict, outputs: list[dict]) -> list[str]:
    """The feedback of the re-judgement: written by code from the reason codes only (no answer text, no other applicant)."""
    lines = []
    for crit, codes in problems.items():
        name = _NAME.get(crit, crit)
        it = (judged.get("items") or {}).get(crit) or {}
        sc = it.get("score")
        for code in codes:
            if code == "SHAPE":
                lines.append(f"{name}: 형식이 맞지 않았다. 항목 4개를 모두, 점수는 1~5 정수로 써라.")
            elif code == "QUOTE_MISMATCH":
                lines.append(f"{name}: 근거로 고른 문장이 답변 원문과 맞지 않았다. 답변 문장 목록의 번호만 써라.")
            elif code == "NO_EVIDENCE":
                lines.append(f"{name}: {sc}점인데 근거 문장 번호가 없다(0이거나 답변에 없는 번호). 그 점수의 근거가 드러난 문장 번호를 "
                             "골라라. 가리킬 문장이 정말 없으면 sent 0과 함께 점수를 다시 판단하라.")
            elif code == "REASON_SCORE":
                lines.append(f"{name}: 이유(comment)는 답변에 부족한 점을 말하는데 점수가 {sc}점이다. 이유와 점수가 서로 맞도록 "
                             "답변 원문으로 다시 판단하라.")
            elif code == "UNSTABLE":
                lo, hi = spread(outputs, crit) or (sc, sc)
                lines.append(f"{name}: 같은 답변의 독립 판정 점수가 {lo}~{hi}점으로 크게 달랐다. 루브릭의 점수별 기준 문장과 "
                             "답변 문장을 하나씩 다시 대조해 판정하라.")
    return lines


def merge_rejudge(judged: dict, extra: dict, problems: dict[str, list[str]]) -> dict:
    """The criteria that failed an item check are taken from the re-judgement `extra`. A criterion that was only unstable keeps
    its median judgement: the re-judgement only confirms it (recheck), it never moves the score."""
    items = dict(judged["items"])
    top = judged
    for crit, codes in problems.items():
        new = (extra.get("items") or {}).get(crit)
        if new is not None and any(c in ITEM_CODES for c in codes):
            items[crit] = new
            if crit == Criterion.LOGIC.value:
                top = extra  # the sub-items and STAR go with the 논리성 judgement that is kept
    out = {**top, "items": items}
    if "scores_sampled" in judged:
        out["scores_sampled"] = {c: v + [extra["items"][c]["score"]] for c, v in judged["scores_sampled"].items()
                                 if c in (extra.get("items") or {})}
    return out


def recheck(merged: dict, text: str, before: dict[str, list[str]], extra: dict | None, kept_before: dict) -> dict[str, list[str]]:
    """Reason codes after the re-judgement. UNSTABLE stays only when the extra judgement is more than 1 point from the score
    kept before (the confirming judgement disagreed)."""
    left = find_problems(merged, text)
    for crit, codes in before.items():
        if "UNSTABLE" not in codes or any(c in ITEM_CODES for c in codes):
            continue
        prev = (kept_before.get(crit) or {}).get("score")
        new = ((extra or {}).get("items", {}).get(crit) or {}).get("score")
        if extra is None or not _score_ok(new) or not _score_ok(prev) or abs(new - prev) > 1:
            left.setdefault(crit, []).append("UNSTABLE")
    return left


def settle(judged: dict, left: dict[str, list[str]]) -> tuple[dict, dict]:
    """The final items of a unit and their side. Never calls the model. Raises ValueError on SHAPE (cannot be scored)."""
    items, side = {}, {}
    for crit, it in judged["items"].items():
        codes = left.get(crit, [])
        if "SHAPE" in codes:
            raise ValueError(f"{crit} SHAPE")
        if "QUOTE_MISMATCH" in codes:  # system: the sentence is not one of the answer
            it = {**it, "score": 1, "evidence_status": "INSUFFICIENT", "insufficient_reason": "QUOTE_MISMATCH", "quote": None}
            side[crit] = "SYSTEM"
        elif "REASON_SCORE" in codes or ("NO_EVIDENCE" in codes and lacking(it.get("comment"))):
            # content: the model's own reason says the answer lacks it -> 내용 부족, the score capped
            it = {**it, "score": min(it["score"], CONTENT_CAP), "evidence_status": "INSUFFICIENT",
                  "insufficient_reason": "LOW_CONTENT", "quote": None}
            side[crit] = "CONTENT"
        elif "NO_EVIDENCE" in codes:  # system: a score the answer was not linked to; the model's score stays (ADR-0023)
            it = {**it, "evidence_status": "UNLINKED", "insufficient_reason": None, "quote": None}
            side[crit] = "SYSTEM"
        if "UNSTABLE" in codes:
            side.setdefault(crit, "UNSTABLE")
        items[crit] = it
    return items, side


def unit_call_cap(samples: int) -> int:
    """The most model calls one evaluation of a unit can make: (samples + MAX_REJUDGE) requests, each inside run_task's
    budget (1 + format retries + every regeneration kind once)."""
    from app.ai.tasks import TASKS

    spec = TASKS["evaluate_answer"]
    return (max(1, samples) + MAX_REJUDGE) * (1 + spec.format_retries + sum(spec.regen_limits.values()))
