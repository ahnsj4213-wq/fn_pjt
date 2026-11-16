"""The candidate's own experience for the job and motivation questions (T2, ADR-0032).

T2 used to see the posting and the company's documents only: two candidates with different resumes got the same questions. Here
code picks the resume sentences that share words with the posting and the documents found for it, word for word from the
application's snapshot (a row's description or a block read from the file), at most one per row. T2 may connect a question to
one of them; it may not say the candidate did anything else (questions.resume_link checks the quote and the terms). Nothing here
is written by a model, and the name, contact details and dates never leave the snapshot.
"""
import re
from dataclasses import dataclass

from app.ai.textrules import content_tokens

EXCERPTS = 3
EXCERPT_MAX = 200  # one or two sentences: enough to name the experience, short enough for the prompt
_SENTENCE = re.compile(r"[^.!?。\n]+(?:[.!?。]+|$)")
_DATE = re.compile(r"^[\d\s.~\-/()년월일]+$")
# What a question says when it tells something as the candidate's own past work ("…하셨는데", "운영해 보신").
ASSUMES_EXPERIENCE = re.compile(r"(?<!생각)(?<!말씀)(?:하셨던|하셨을|하셨는데|하셨고|하신\s?경험)|해\s?보신|해\s?보셨|경험하신|사용하신|써\s?보신|"
                                r"맡으셨|맡으신|진행하신|구축하신|개발하신|설계하신|운영하신|만드신|다뤄\s?보신|다루신|적용하신|도입하신|"
                                r"겪으신|구현하신|개선하신|줄이신|높이신|쌓으신|해\s?오신|"
                                r"(?:아|어|해)\s?본\s?경험을")  # "찾아 본 경험을 살리시겠습니까": said as a fact; "…본 경험이 있다면" stays a supposition


@dataclass(frozen=True)
class Item:
    ref: str  # "projects[0]", "experience[1]", "details[3]": where in the snapshot (the AI contract's names)
    scene: str  # the row, as the candidate named it ("서버 운영 점검 Copilot", "가상테크 백엔드 개발자"); "" for a block of no row
    where: str  # the row as an interviewer says it: "서버 운영 점검 Copilot 프로젝트에서", "가상테크 백엔드 개발자 업무에서"
    text: str  # word for word from the snapshot


@dataclass(frozen=True)
class Excerpt:
    ref: str
    scene: str
    where: str
    text: str  # one or two sentences of the item, word for word

    def as_input(self) -> dict:
        return {"ref": self.ref, "scene": self.scene, "text": self.text}


def _scene_of(row: dict, key: str) -> str:
    if key == "company":
        return " ".join(x for x in (row.get("company"), row.get("role")) if x).strip()
    title = (row.get("title") or "").strip()
    return re.split(r"\s*\|\s*", title, maxsplit=1)[-1] if "|" in title else title


def _where(scene: str, section: str) -> str:
    if not scene:
        return ""
    if section == "career":
        return f"{scene} 업무에서"
    return f"{scene}{'' if scene.endswith('프로젝트') else ' 프로젝트'}에서"


def items(snapshot: dict | None) -> list[Item]:
    """Everything the candidate wrote about their work: career and project rows, the blocks read from the file (form shape)."""
    s = snapshot or {}
    out: list[Item] = []
    rows: dict[str, tuple[str, str]] = {}
    for section, ai_name, key in (("career", "experience", "company"), ("projects", "projects", "title")):
        for i, row in enumerate(s.get(section) or []):
            scene = _scene_of(row, key)
            rows[f"{section}:{row.get(key)}"] = (scene, _where(scene, section))
            if row.get("description"):  # the row's name and role are its scene, not something it says the candidate did
                out.append(Item(f"{ai_name}[{i}]", scene, rows[f"{section}:{row.get(key)}"][1], row["description"]))
    for i, d in enumerate(s.get("details") or []):
        if d.get("kind") in ("career", "projects", "skills", "summary") and d.get("text"):
            scene, where = rows.get(d.get("row") or "", ("", ""))
            out.append(Item(f"details[{i}]", scene, where, d["text"]))
    return out


def _sentences(text: str) -> list[str]:
    out = []
    for m in _SENTENCE.finditer(text.replace("\n", " ")):
        s = m.group(0).strip(" •-·")
        if len(s) >= 12 and not _DATE.match(s):
            out.append(s[:EXCERPT_MAX])
    return out


def relevant(snapshot: dict | None, *targets: str, k: int = EXCERPTS, skip_rows: set[str] | frozenset = frozenset()
             ) -> list[Excerpt]:
    """The resume sentences sharing the most words with the targets (the posting, the documents found), one per row: two
    sentences about one project are one experience. A sentence sharing no word is never picked."""
    want = set().union(*(content_tokens(t) for t in targets if t)) if targets else set()
    if not want:
        return []
    scored: list[tuple[int, int, Item, str]] = []
    for order, it in enumerate(items(snapshot)):
        for s in _sentences(it.text):
            hits = len(content_tokens(s) & want)
            if hits:
                scored.append((hits, -order, it, s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out: list[Excerpt] = []
    rows_used = set(skip_rows)
    for _, _, it, s in scored:
        row = it.scene or it.ref
        if row in rows_used:
            continue
        rows_used.add(row)
        out.append(Excerpt(it.ref, it.scene, it.where, s))
        if len(out) >= k:
            break
    return out


def resume_words(snapshot: dict | None) -> str:
    """Everything the candidate wrote about their work and skills, lower-cased: a term a question says they used must be here."""
    s = snapshot or {}
    names = [f"{r.get('company') or ''} {r.get('role') or ''}" for r in s.get("career") or []] + \
        [r.get("title") or "" for r in s.get("projects") or []]
    return "\n".join([it.text for it in items(s)] + names + list(s.get("skills") or [])).lower()
