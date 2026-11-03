"""Sub-items, STAR and the code-measured values of the report (evaluation/details.py), and how they blend into scores."""
import io
from decimal import Decimal

import numpy as np
import soundfile as sf

from app.evaluation import details as D
from app.evaluation.scoring import RULE_WEIGHT, QuestionScores, UnitScores, compute



def test_negative_expressions_are_counted_and_scored():
    text = "아마 맞을 것 같습니다. 솔직히 잘 모르겠습니다. 그래도 해 보겠습니다."
    found = D.negative_expressions(text)
    assert found.count("것 같") == 1 and "아마" in found and "솔직히" in found and len(found) >= 4
    assert D.negative_expressions("저는 결제 시스템의 지연을 절반으로 줄였습니다.") == []
    assert D.negative_score(0) == 100 and D.negative_score(2) == 60 and D.negative_score(9) == 0


def test_time_and_speed_scores():
    assert D.time_score(60, 60) == 100 and D.time_score(25, 60) == 100 and D.time_score(10, 60) < 60
    assert D.time_score(1, 60) == D.RATE_FLOOR
    assert D.speed_cpm("가" * 300, 60_000) == 300 and D.speed_cpm("가" * 300, 2000) is None  # too short to measure
    assert D.speed_score(300) == 100 and D.speed_score(100) < 60 and D.speed_score(900) < 60 and D.speed_score(5000) == D.RATE_FLOOR


def _flac(signal: np.ndarray, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, signal.astype("float32"), sr, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


def _voice(sr: int, seconds: float, vibrato_st: float, noise: float, seed: int = 1) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    f0 = 140 * 2 ** (vibrato_st * np.sin(2 * np.pi * 0.7 * t) / 12)  # pitch swings by +-vibrato semitones
    phase = 2 * np.pi * np.cumsum(f0) / sr
    sig = 0.4 * (np.sin(phase) + 0.4 * np.sin(2 * phase))
    sig *= (np.sin(2 * np.pi * 1.5 * t) > -0.6)  # pauses, so a noise floor exists
    return sig + np.random.default_rng(seed).normal(0, noise, len(t))


def test_audio_metrics_tell_flat_from_lively_and_clean_from_noisy():
    flat = D.audio_metrics(_flac(_voice(16000, 8, 0.0, 0.002)))
    lively = D.audio_metrics(_flac(_voice(16000, 8, 3.0, 0.002)))
    noisy = D.audio_metrics(_flac(_voice(16000, 8, 3.0, 0.08)))
    assert flat and lively and noisy
    assert lively["pitch_std_st"] > flat["pitch_std_st"] + 1.0 and lively["pitch"] > flat["pitch"]
    assert lively["clarity"] > noisy["clarity"] + 20
    assert D.audio_metrics(_flac(np.zeros(16000 * 3))) is None  # silence
    assert D.audio_metrics(b"not audio") is None


def test_sub_item_rollup_averages_model_scores_and_star_rates():
    p = lambda e, lg, st: {"llm": True, "detail": {"expertise_sub": e, "logic_sub": lg, "star": st}}  # noqa: E731
    out = D.sub_item_rollup([
        p({"a": 5, "b": 3, "c": 1}, {"a": 4, "b": 4, "c": 4}, {"situation": 1, "task": 0, "action": 1, "result": 1}),
        p({"a": 3, "b": 3, "c": 3}, {"a": 2, "b": 2, "c": 2}, {"situation": 1, "task": 0, "action": 0, "result": 1}),
        {"llm": True, "detail": None},  # a rule-scored (short) answer has no sub-scores and is left out
    ])
    assert [x["score"] for x in out["expertise"]] == [80, 60, 40] and [x["score"] for x in out["logic"]] == [60, 60, 60]
    assert {x["key"]: x["rate"] for x in out["star"]["items"]} == {"situation": 100, "task": 0, "action": 50, "result": 100}
    assert out["star"]["score"] == 62
    assert all(x["score"] is None for x in D.sub_item_rollup([])["logic"])


def test_rule_scores_pull_their_area_only_and_weights_still_sum():
    unit = UnitScores({c: 4 for c in (cr for cr in __import__("app.core.constants", fromlist=["CRITERIA"]).CRITERIA)})
    qs = [QuestionScores(1, unit)]
    w = {"expertise": 30, "logic": 30, "attitude": 20, "delivery": 20}
    base = compute(qs, w, None)
    blended = compute(qs, w, None, {"attitude": Decimal("40"), "delivery": Decimal("100")})
    assert base.criteria["attitude"] == Decimal("80.00")
    assert blended.criteria["attitude"] == Decimal("80.00") * (1 - RULE_WEIGHT) + Decimal("40") * RULE_WEIGHT == Decimal("68.00")
    assert blended.criteria["delivery"] == Decimal("86.00")
    assert blended.criteria["expertise"] == base.criteria["expertise"] and blended.criteria["logic"] == base.criteria["logic"]
    assert blended.total < base.total or blended.total != base.total


def test_speed_is_read_over_the_spoken_span_not_the_silence_around_it():
    sr = 16000
    speech = _voice(sr, 6, 2.0, 0.002)
    padded = np.concatenate([np.zeros(sr * 5) + 0.0005, speech, np.zeros(sr * 5) + 0.0005])  # 5 s of near-silence each side
    m = D.audio_metrics(_flac(padded))
    assert m and 4.5 <= m["span_sec"] <= 6.6, m  # the 16 s file is read as about 6 s of speech
    assert D.speed_cpm("가" * 30, int(m["span_sec"] * 1000)) > 2.5 * D.speed_cpm("가" * 30, 16000)
