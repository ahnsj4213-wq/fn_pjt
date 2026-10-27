"""Server-side score computation (PRD 6.4, 6.5, EVL-04, P-5). The LLM supplies only integer 1-5 judgements.

Rounding (consolidation T18): criterion scores are quantized to 0.01 (ROUND_HALF_UP) and stored; the total is
computed from the stored criterion scores and the coding score, then quantized to 0.01.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import CRITERIA, Criterion

SCORING_VERSION = "score-v1"
Q = Decimal("0.01")
WEIGHT_KEYS = {Criterion.EXPERTISE: "expertise", Criterion.LOGIC: "logic", Criterion.SPECIFICITY: "specificity",
               Criterion.DELIVERY: "delivery"}


def q2(x: Decimal) -> Decimal:
    return x.quantize(Q, rounding=ROUND_HALF_UP)


@dataclass
class UnitScores:
    """Scores of one answer unit (main or follow-up answer) per criterion; 1 for insufficient/no-answer."""
    scores: dict[Criterion, int]
    insufficient: set[Criterion] = field(default_factory=set)
    no_answer: bool = False


@dataclass
class QuestionScores:
    seq: int
    main: UnitScores
    followup: UnitScores | None = None   # None when no follow-up was asked or it was never answered (6.2)

    def per_criterion(self) -> dict[Criterion, Decimal]:
        out = {}
        for c in CRITERIA:
            if self.followup is None:
                out[c] = Decimal(self.main.scores[c])
            else:
                out[c] = (Decimal(self.main.scores[c]) + Decimal(self.followup.scores[c])) / 2
        return out


@dataclass
class ScoreResult:
    criteria: dict[str, Decimal]       # 100-point scale, keyed expertise/logic/specificity/delivery
    coding: Decimal | None
    total: Decimal
    question_scores: list[dict]
    insufficient_count: int
    no_answer_count: int
    insufficient_by_criterion: dict[str, int]


def no_answer_unit() -> UnitScores:
    return UnitScores({c: 1 for c in CRITERIA}, no_answer=True)  # 6.4: '답변 없음' -> all criteria 1


def coding_score(passed: int, total: int) -> Decimal:
    """6.5: passed / total x 100 over public + hidden tests of the final submission."""
    if total <= 0:
        return Decimal("0.00")
    return q2(Decimal(passed) * 100 / Decimal(total))


def compute(questions: list[QuestionScores], weights: dict[str, int], coding: Decimal | None) -> ScoreResult:
    if not questions:
        raise ValueError("no answered questions to score")
    if sum(weights.values()) != 100:
        raise ValueError("weights must sum to 100")
    if ("coding" in weights) != (coding is not None):
        raise ValueError("coding weight and coding score must both be present or absent")
    n = Decimal(len(questions))
    per_q = [q.per_criterion() for q in questions]
    criteria = {WEIGHT_KEYS[c]: q2(sum(p[c] for p in per_q) / n * 20) for c in CRITERIA}
    total = sum(criteria[k] * weights[k] for k in criteria) / 100
    if coding is not None:
        total += coding * weights["coding"] / 100
    insufficient_by = {WEIGHT_KEYS[c]: 0 for c in CRITERIA}
    no_answer = 0
    for q in questions:
        for unit in (q.main, q.followup):
            if unit is None:
                continue
            no_answer += unit.no_answer
            for c in unit.insufficient:
                insufficient_by[WEIGHT_KEYS[c]] += 1
    qs = [{"seq": q.seq, **{WEIGHT_KEYS[c]: str(p[c]) for c in CRITERIA}} for q, p in zip(questions, per_q)]
    return ScoreResult(criteria=criteria, coding=coding, total=q2(total), question_scores=qs,
                       insufficient_count=sum(insufficient_by.values()), no_answer_count=no_answer,
                       insufficient_by_criterion=insufficient_by)
