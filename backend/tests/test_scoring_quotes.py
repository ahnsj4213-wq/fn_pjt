"""AC-16 (quote verification) and AC-17 (score formula) — pure unit tests, checked against an independent oracle."""
import random
from decimal import Decimal
from fractions import Fraction

import pytest

from app.core.constants import CRITERIA, DEFAULT_WEIGHTS_CODING, DEFAULT_WEIGHTS_NO_CODING
from app.evaluation.quotes import find_span
from app.evaluation.scoring import QuestionScores, UnitScores, coding_score, compute, no_answer_unit

ANSWER = ("저는 이전 회사에서 주문 처리 API의 응답 시간이 길어지는 문제를 맡았습니다. 먼저 APM으로 느린 쿼리를 찾았고, "
          "주문 목록 조회에 인덱스가 없다는 걸 확인했습니다. 성능을 3.5배 개선했고 팀원은 2~3명이었습니다.")


@pytest.mark.parametrize("quote", [
    "먼저 APM 으로 느린 쿼리를 찾았고",              # model inserts spaces (measured failure mode)
    "주문 처리 API 의 응답 시간이 길어지는 문제를 맡았습니다.",
    "“성능을 3.5배 개선했고”",                        # quote marks + edge punctuation
    "주문 목록 조회에 인덱스가 없다는 걸 확인했습니다",  # trailing period dropped
])
def test_genuine_quotes_verify_and_store_source_text(quote):
    span = find_span(quote, ANSWER)
    assert span is not None
    assert ANSWER[span.start:span.end] == span.text  # stored text is the source, not the LLM string


@pytest.mark.parametrize("source,forged", [
    ("성능을 3.5배 개선했습니다", "35배 개선"),
    ("C 언어로 구현했습니다", "C++ 언어로 구현"),
    ("C 언어로 구현했습니다", "C# 언어로 구현"),
    ("팀원은 2~3명이었습니다", "23명이었습니다"),
    ("1:1 멘토링을 했습니다", "11 멘토링을 했습니다"),
    ("오류율을 10% 줄였습니다", "오류율을 10 줄였습니다"),
    (ANSWER, "Redis 캐시를 도입했습니다"),              # invented content
])
def test_forged_quotes_rejected(source, forged):
    assert find_span(forged, source) is None


def test_too_short_quote_rejected_unless_whole_answer():
    assert find_span("주문", ANSWER) is None
    assert find_span("네, 맞습니다", "네, 맞습니다.") is not None


def unit(s: int) -> UnitScores:
    return UnitScores({c: s for c in CRITERIA})


def oracle(questions: list[tuple[list[int], list[int] | None]], weights: dict[str, int], coding: Fraction | None):
    """Independent re-implementation of 6.4 with exact fractions; rounding per T18 applied at the end."""
    keys = ["expertise", "logic", "specificity", "delivery"]

    def round2(fr: Fraction) -> Decimal:
        return (Decimal(fr.numerator) / Decimal(fr.denominator)).quantize(Decimal("0.01"), "ROUND_HALF_UP")

    crit = {}
    for i, k in enumerate(keys):
        vals = [Fraction(m[i]) if f is None else (Fraction(m[i]) + Fraction(f[i])) / 2 for m, f in questions]
        crit[k] = round2(sum(vals) / len(vals) * 20)
    total = sum(Fraction(str(crit[k])) * weights[k] for k in keys) / 100
    if coding is not None:
        total += coding * weights["coding"] / 100
    return crit, Decimal(total.numerator) / Decimal(total.denominator)


def test_ac17_total_matches_hand_computation():
    # 10 questions: q1 has a follow-up (avg), q2 is '답변 없음', q3 has insufficient evidence on LOGIC (scored 1).
    qs = [QuestionScores(1, unit(4), unit(3)), QuestionScores(2, no_answer_unit()),
          QuestionScores(3, UnitScores({**{c: 5 for c in CRITERIA}, CRITERIA[1]: 1}, insufficient={CRITERIA[1]}))]
    qs += [QuestionScores(i, unit(3)) for i in range(4, 11)]
    r = compute(qs, DEFAULT_WEIGHTS_CODING, coding_score(3, 5))
    # expertise: (3.5 + 1 + 5 + 3*7) / 10 * 20 = 61.00 ; logic: (3.5 + 1 + 1 + 21)/10*20 = 53.00
    assert r.criteria == {"expertise": Decimal("61.00"), "logic": Decimal("53.00"),
                          "specificity": Decimal("61.00"), "delivery": Decimal("61.00")}
    assert r.coding == Decimal("60.00")
    # 61*0.30 + 53*0.20 + 61*0.20 + 61*0.10 + 60*0.20 = 18.3 + 10.6 + 12.2 + 6.1 + 12 = 59.20
    assert r.total == Decimal("59.20")
    assert r.no_answer_count == 1 and r.insufficient_count == 1 and r.insufficient_by_criterion["logic"] == 1


def test_ac17_random_cases_against_fraction_oracle():
    rng = random.Random(7)
    for _ in range(500):
        n = rng.randint(1, 10)
        raw = []
        qs = []
        for i in range(n):
            m = [rng.randint(1, 5) for _ in CRITERIA]
            f = [rng.randint(1, 5) for _ in CRITERIA] if rng.random() < 0.4 else None
            raw.append((m, f))
            qs.append(QuestionScores(i + 1, UnitScores(dict(zip(CRITERIA, m))),
                                     UnitScores(dict(zip(CRITERIA, f))) if f else None))
        with_coding = rng.random() < 0.5
        weights = dict(DEFAULT_WEIGHTS_CODING if with_coding else DEFAULT_WEIGHTS_NO_CODING)
        passed, total_tc = rng.randint(0, 7), 7
        coding = coding_score(passed, total_tc) if with_coding else None
        r = compute(qs, weights, coding)
        crit, total = oracle(raw, weights, Fraction(str(coding)) if coding is not None else None)
        assert r.criteria == crit
        assert r.total == total.quantize(Decimal("0.01"), "ROUND_HALF_UP")
        assert Decimal(0) <= r.total <= Decimal(100)


def test_coding_score_edges():
    assert coding_score(0, 5) == Decimal("0.00") and coding_score(5, 5) == Decimal("100.00")
    assert coding_score(2, 3) == Decimal("66.67")


def test_weights_must_sum_to_100_and_match_coding():
    qs = [QuestionScores(1, unit(3))]
    with pytest.raises(ValueError):
        compute(qs, {"expertise": 35, "logic": 25, "specificity": 25, "delivery": 14}, None)
    with pytest.raises(ValueError):
        compute(qs, DEFAULT_WEIGHTS_NO_CODING, Decimal("50"))
