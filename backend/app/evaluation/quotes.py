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
