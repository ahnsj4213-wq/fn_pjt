"""Rule-based measurements and the sub-item roll-up of the evaluation report (screen spec SC-A08).

Everything here is computed by code, not by the language model, and every number is reproducible from stored data:
- sub-item averages and the STAR rate come from the integers the answer evaluation returned (`payload["detail"]`);
- negative expressions are counted in the answer text with a fixed phrase list (NEGATIVE_PHRASES);
- answer time is read against the question's time limit; speaking speed is characters per minute of audio;
- clarity (뚜렷함) and pitch variation (음성 높낮이) are measured on the stored answer audio.
The scores that feed the area totals are `rule_scores` (0~100); scoring.compute blends them in with RULE_WEIGHT.
"""
import io
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from app.ai import rubric

DETAILS_VERSION = "details-v1"

# Phrases that lower the impression of confidence. A fixed list on purpose (the same text always gives the same count);
# the product owner can extend it. Every occurrence counts.
NEGATIVE_PHRASES = ("잘 모르겠", "모르겠습니다", "것 같", "아마", "어쩌면", "자신이 없", "자신 없", "자신은 없", "부족해서", "못 했", "못했",
                    "죄송", "그냥", "솔직히", "별로", "할 줄 모", "잘 못")
_NEG = re.compile("|".join(re.escape(p) for p in NEGATIVE_PHRASES))
RATE_FLOOR = 20

IDEAL_TIME_RATIO = (0.35, 1.0)  # share of the time limit; shorter answers are scored down, using all of it is fine
IDEAL_CPM = (240, 420)          # Korean speech, characters (without spaces) per minute
MIN_AUDIO_SEC = 3.0


def negative_expressions(text: str) -> list[str]:
    return _NEG.findall(text or "")


def negative_score(count: int) -> int:
    return max(0, 100 - 20 * count)


def time_score(used_sec: float, limit_sec: float) -> int:
    if limit_sec <= 0:
        return 100
    r = used_sec / limit_sec
    lo, _hi = IDEAL_TIME_RATIO
    return 100 if r >= lo else max(RATE_FLOOR, round(100 * r / lo))


def speed_cpm(text: str, duration_ms: int | None) -> float | None:
    if not duration_ms or duration_ms < MIN_AUDIO_SEC * 1000:
        return None
    chars = len(re.sub(r"\s+", "", text or ""))
    return chars / (duration_ms / 60000)


def speed_score(cpm: float) -> int:
    lo, hi = IDEAL_CPM
    if lo <= cpm <= hi:
        return 100
    if cpm < lo:
        return max(RATE_FLOOR, round(100 * cpm / lo))
    return max(RATE_FLOOR, round(100 - (cpm - hi) / 3))


def audio_metrics(flac: bytes) -> dict | None:
    """Clarity and pitch variation of one answer's audio, each 0~100, or None when there is too little speech."""
    import soundfile as sf
    try:
        x, sr = sf.read(io.BytesIO(flac), dtype="float32", always_2d=False)
    except Exception:  # noqa: BLE001 - an unreadable file only means no audio metrics
        return None
    if x.ndim > 1:
        x = x.mean(axis=1)
    frame, hop = int(sr * 0.03), int(sr * 0.015)
    if len(x) < frame * 20:
        return None
    n = 1 + (len(x) - frame) // hop
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx]
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    noise = max(float(np.percentile(rms, 10)), 1e-6)
    loud = float(np.percentile(rms, 90))
    speech = rms > max(noise * 2, loud * 0.35)
    if int(speech.sum()) < 20:
        return None
    on = np.flatnonzero(speech)
    span_sec = round(float((on[-1] - on[0] + 1) * hop / sr), 2)  # first to last spoken frame: lead-in and tail silence do not count
    snr = 20 * math.log10(float(rms[speech].mean()) / noise)
    clarity = round(min(1.0, max(0.0, (snr - 6) / 24)) * 100)
    # Pitch: autocorrelation of up to 300 evenly spaced speech frames.
    sel = np.flatnonzero(speech)
    sel = sel[np.linspace(0, len(sel) - 1, min(300, len(sel))).astype(int)]
    lo_lag, hi_lag = int(sr / 400), int(sr / 70)
    f0s = []
    for i in sel:
        f = frames[i] - frames[i].mean()
        ac = np.fft.irfft(np.abs(np.fft.rfft(f, 2 * frame)) ** 2)[:frame]
        if ac[0] <= 0:
            continue
        lag = lo_lag + int(np.argmax(ac[lo_lag:hi_lag]))
        if ac[lag] / ac[0] > 0.35:
            f0s.append(sr / lag)
    if len(f0s) < 15:
        return {"clarity": clarity, "pitch": None, "pitch_std_st": None, "span_sec": span_sec}
    st = 12 * np.log2(np.array(f0s) / float(np.median(f0s)))
    std = float(np.std(st))
    pitch = 100 if 1.5 <= std <= 5 else round(40 + std / 1.5 * 60) if std < 1.5 else max(40, round(100 - (std - 5) * 12))
    return {"clarity": clarity, "pitch": pitch, "pitch_std_st": round(std, 2), "span_sec": span_sec}


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


@dataclass
class Details:
    detail: dict
    rule_scores: dict[str, Decimal] = field(default_factory=dict)  # area key -> 0~100, only the areas that were measured


def sub_item_rollup(payloads: list[dict]) -> dict:
    """Average of the model's 1~5 sub-scores over the answered units (-> 0~100) and the share of units with each STAR part."""
    out: dict = {}
    for area, subs in rubric.SUB_ITEMS.items():
        rows = [p["detail"][f"{area}_sub"] for p in payloads if p.get("detail") and p["detail"].get(f"{area}_sub")]
        out[area] = [{"key": k, "name": name, "score": round(_avg([r[k] for r in rows]) * 20) if rows else None} for k, name, _ in subs]
    stars = [p["detail"]["star"] for p in payloads if p.get("detail") and p["detail"].get("star")]
    items = [{"key": k, "name": name, "rate": round(100 * sum(s[k] for s in stars) / len(stars)) if stars else None}
             for k, name in rubric.STAR]
    out["star"] = {"items": items, "score": round(_avg([i["rate"] for i in items])) if stars else None}
    return out


async def analyze(rows: list, units: dict, storage) -> Details:
    """rows: (main question, main answer, follow-up question, follow-up answer) per main question."""
    payloads = [units[a.id] for _, a, _, _ in rows if a is not None] + [units[fa.id] for _, _, _, fa in rows if fa is not None]
    detail: dict = {"version": DETAILS_VERSION, "sub": sub_item_rollup([p for p in payloads if p.get("llm")]), "rules": {}}
    rules = detail["rules"]
    neg_per, time_per, speed_per, audio_per = [], [], [], []
    for m, a, _fq, fa in rows:
        if a is None or a.is_no_answer:
            continue
        found = negative_expressions(a.eval_text or "") + (negative_expressions(fa.eval_text or "") if fa is not None else [])
        neg_per.append({"seq": m.seq, "count": len(found), "phrases": sorted(set(found))})
        if a.input_mode == "VOICE":
            used = (a.confirmed_at - a.opened_at).total_seconds()
            time_per.append({"seq": m.seq, "sec": round(used), "limit": m.answer_limit_sec,
                             "score": time_score(used, m.answer_limit_sec)})
            met = None
            if a.audio_key:
                blob = await storage.get(a.audio_key)
                met = audio_metrics(blob) if blob else None
                if met:
                    audio_per.append({"seq": m.seq, **met})
            # Speed is read over the spoken span (first to last speech), not the whole answer window with its silences.
            dur_ms = int(met["span_sec"] * 1000) if met else a.audio_duration_ms
            cpm = speed_cpm(a.eval_text or "", dur_ms)
            if cpm is not None:
                speed_per.append({"seq": m.seq, "cpm": round(cpm), "score": speed_score(cpm)})
    scores: dict[str, Decimal] = {}
    if neg_per:
        rules["negative"] = {"count": sum(x["count"] for x in neg_per), "per_question": neg_per,
                             "score": round(_avg([negative_score(x["count"]) for x in neg_per]))}
    if time_per:
        rules["answer_time"] = {"per_question": time_per, "score": round(_avg([x["score"] for x in time_per]))}
    attitude_parts = [rules[k]["score"] for k in ("negative", "answer_time") if k in rules]
    if attitude_parts:
        scores["attitude"] = Decimal(str(round(_avg(attitude_parts), 2)))
    if speed_per:
        rules["speed"] = {"avg_cpm": round(_avg([x["cpm"] for x in speed_per])), "per_question": speed_per,
                          "ideal": list(IDEAL_CPM), "score": round(_avg([x["score"] for x in speed_per]))}
    clar = [x["clarity"] for x in audio_per if x.get("clarity") is not None]
    pit = [x for x in audio_per if x.get("pitch") is not None]
    if clar:
        rules["clarity"] = {"score": round(_avg(clar))}
    if pit:
        rules["pitch"] = {"score": round(_avg([x["pitch"] for x in pit])),
                          "avg_std_semitones": round(_avg([x["pitch_std_st"] for x in pit]), 2)}
    delivery_parts = [rules[k]["score"] for k in ("speed", "clarity", "pitch") if k in rules]
    if delivery_parts:
        scores["delivery"] = Decimal(str(round(_avg(delivery_parts), 2)))
    return Details(detail, scores)
