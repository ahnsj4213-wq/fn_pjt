"""Quote verification (EVL-02, ADR-0008, consolidation C6).

Normalisation: NFKC, remove all whitespace (incl. zero-width), unify quote marks, ignore punctuation only at the
quote's edges. Internal punctuation, digits and symbols must match, so "3.5배" never verifies as "35배".
A verified quote is stored as the *source* substring (start, end), never as the LLM's string.
Measured: 47/48 genuine quotes accepted, 0/7 forged accepted (prototypes/results-quote-rules.txt).
"""
import re
import unicodedata
from dataclasses import dataclass

RULE_VERSION = "qn-2"
_QUOTE_MARKS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "「": '"', "」": '"', "『": '"', "』": '"'})
_SPACE = re.compile(r"[\s​‌‍﻿]")
EDGE_PUNCT = ".,!?;:…·\"'()[]{}-~"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str


def _norm_with_map(s: str) -> tuple[str, list[int]]:
    """Normalised string plus, for each normalised char, the index of the source char it came from."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        for n in unicodedata.normalize("NFKC", ch).translate(_QUOTE_MARKS):
            if _SPACE.match(n):
                continue
            out.append(n)
            idx.append(i)
    return "".join(out), idx


def normalize(s: str) -> str:
    return _norm_with_map(s)[0]


def find_span(quote: str, source: str, min_chars: int = 8) -> Span | None:
    """Locate `quote` in `source` under the rule above; None when it is not a faithful substring."""
    q = normalize(quote).strip(EDGE_PUNCT)
    src, idx = _norm_with_map(source)
    if not q:
        return None
    if len(q) < min_chars and len(q) < len(src.strip(EDGE_PUNCT)):
        return None  # too short to count as evidence unless it is the whole answer
    pos = src.find(q)
    if pos < 0:
        return None
    start, end = idx[pos], idx[pos + len(q) - 1] + 1
    return Span(start, end, source[start:end])


# Evidence by sentence number (ADR-0023): the answer is cut into numbered sentences by code, the model names a number, and the
# evidence is that sentence's span of the source. Nothing the model writes is stored as evidence, so it can neither misquote
# nor invent a sentence; a number outside the list is simply not evidence.
_SENT_END = re.compile(r"(?<=[.?!。…])\s+|\n+")
_COMMA = re.compile(r"(?<=,)\s+")
LONG_SENTENCE = 160  # an STT run-on without full stops is cut at its commas so a number still points at one thought
MAX_SENTENCES = 40


def sentences(source: str) -> list[Span]:
    """The answer's sentences as source spans, in order (1-based numbers are the list position + 1)."""
    out: list[Span] = []
    for part, start in _pieces(source, 0, len(source), _SENT_END):
        if len(part) > LONG_SENTENCE:
            out += [Span(s, s + len(p), p) for p, s in _pieces(source, start, start + len(part), _COMMA)]
        else:
            out.append(Span(start, start + len(part), part))
    if len(out) > MAX_SENTENCES:  # the rest stays citable as one last piece
        last = Span(out[MAX_SENTENCES - 1].start, out[-1].end, source[out[MAX_SENTENCES - 1].start:out[-1].end])
        out = out[:MAX_SENTENCES - 1] + [last]
    return out


def _pieces(source: str, lo: int, hi: int, sep: re.Pattern) -> list[tuple[str, int]]:
    out, pos = [], lo
    for m in sep.finditer(source, lo, hi):
        out.append((pos, m.start()))
        pos = m.end()
    out.append((pos, hi))
    pieces = []
    for a, b in out:
        text = source[a:b]
        lead = len(text) - len(text.lstrip())
        text = text.strip()
        if normalize(text).strip(EDGE_PUNCT):
            pieces.append((text, a + lead))
    return pieces


def numbered(spans: list[Span]) -> str:
    return "\n".join(f"[{i}] {s.text}" for i, s in enumerate(spans, 1))
