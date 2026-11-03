"""Resume OCR glue (RES-04): which pages need it, running ocr_extract.py in its own interpreter, and what the review
form should flag. Text pages keep the pypdf text; only pages with fewer than MIN_CHARS_PER_PAGE characters and photos
are read by OCR. A mean confidence below `ocr_fail_conf` counts as "could not read" so the candidate types the resume
instead of reviewing guesses.
"""
import asyncio
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Awaitable, Callable

from app.core.config import get_settings

MIN_CHARS_PER_PAGE = 50  # fewer characters on a page => a scanned page
Line = tuple[str, float]  # (line text, confidence 0~1)
_SCRIPT = Path(__file__).with_name("ocr_extract.py")


class OcrFailed(RuntimeError):
    """OCR_UNAVAILABLE (no engine / language data) or UNREADABLE; the candidate types the resume instead."""


def scanned_pages(page_texts: list[str]) -> list[int]:
    return [i for i, t in enumerate(page_texts) if len(t.strip()) < MIN_CHARS_PER_PAGE]


async def _subprocess_ocr(path: str, kind: str, pages: list[int]) -> list[Line]:
    cfg = get_settings()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(_SCRIPT), path, kind, ",".join(map(str, pages)), cfg.ocr_lang, str(cfg.ocr_dpi),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.ocr_timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise OcrFailed("TIMEOUT") from None
    try:
        res = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        raise OcrFailed("UNREADABLE") from None
    if not res.get("ok"):
        raise OcrFailed(res.get("error") or "UNREADABLE")
    return [(str(t), float(c)) for t, c in res["lines"]]


_runner: Callable[[str, str, list[int]], Awaitable[list[Line]]] = _subprocess_ocr


def set_runner(fn: Callable[[str, str, list[int]], Awaitable[list[Line]]] | None) -> None:
    """Tests swap in a fake engine (Tesseract is not installed on every dev box)."""
    global _runner
    _runner = fn or _subprocess_ocr


async def read(path: str, kind: str, pages: list[int]) -> list[Line]:
    return await _runner(path, kind, pages)


def summarize(lines: list[Line]) -> tuple[str, float]:
    """Text joined by newlines and the mean line confidence; ('', 0) when nothing was read."""
    if not lines:
        return "", 0.0
    return "\n".join(t for t, _ in lines), statistics.fmean(c for _, c in lines)


def _n(s) -> str:
    return re.sub(r"[\s\W_]+", "", str(s)).lower()


_SECTIONS = {"education": ("school", "major", "degree", "period"), "career": ("company", "role", "period", "description"),
             "projects": ("title", "role", "period", "description"), "certificates": ("name", "issuer", "date")}


def low_conf_fields(fields: dict, lines: list[Line], threshold: float) -> list[str]:
    """Form fields fed by a line read with confidence below `threshold`: 'education.0', 'projects.1', 'skills', 'name'.
    The review form asks the candidate to check those against the original."""
    low = [_n(t) for t, c in lines if c < threshold and _n(t)]
    if not low:
        return []

    def hit(*values) -> bool:
        return any(len(v := _n(x)) >= 2 and any(v in line or (len(line) >= 4 and line in v) for line in low)
                   for x in values if x)

    out = []
    for key, names in _SECTIONS.items():
        out += [f"{key}.{i}" for i, it in enumerate(fields.get(key) or []) if hit(*(it.get(f) for f in names))]
    if hit(fields.get("name")):
        out.append("name")
    if hit(*(fields.get("skills") or [])):
        out.append("skills")
    return out
