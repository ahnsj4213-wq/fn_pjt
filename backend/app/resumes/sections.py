"""Long resume text is cut by code, not copied by the model (RES-03).

T1 copies values into a JSON answer, so a long description cannot fit its token limit. The model only names the rows; this
module cuts the rest from the file's text: the cover letter by its heading, each career and project description from the
blocks that belong to the row (below), never across a heading into another row's text. All of it is verbatim. The cover letter does not go to T1 at all: its sentences used
to come back as invented companies and projects. A resume without recognisable headings keeps the model's short descriptions.

Every block under a heading is also kept word for word as a `details` entry (ADR-0032): its heading, the line range it came
from in the extracted text, and the row it belongs to. A block names its row (`how="name"`), or clearly shares that row's
words and no other's (`how="similar"`); otherwise it belongs to none. Nothing is summarised, invented or cut to a length:
a long block is split into consecutive entries. Contact and personal-information blocks are not kept, and an e-mail address
or phone number inside a kept block is masked.
"""
import re
from collections import Counter
from dataclasses import dataclass, field

INTRO_MAX = 10_000
DESCRIPTION_MAX = 3000
HEADING_MAX = 24
REPEATED_MAX = 40
REPEATED_PAGES = 3  # a line on three pages is a page header, whatever its length
DETAIL_MAX = 4000
DETAILS_MAX = 80

_HEADINGS = {
    "intro": {"자기소개서", "자기소개", "커버레터", "coverletter"},
    "career": {"경력", "경력사항", "경력기술서", "경력상세", "경력직무상세", "경력기술", "업무경력", "근무경력", "직무경험", "주요경력",
               "경력요약", "실무경험", "인턴경험", "인턴십", "업무경험", "주요경험", "추가경험", "협업및추가경험", "경험및활동"},
    "projects": {"프로젝트", "주요프로젝트", "프로젝트경험", "프로젝트경력", "수행프로젝트", "프로젝트소개서", "프로젝트소개",
                 "대표프로젝트", "프로젝트한눈에보기", "프로젝트상세", "프로젝트요약", "주요프로젝트경험", "문제해결", "기술적문제해결",
                 "트러블슈팅", "주요성과"},
    "skills": {"보유기술", "기술", "기술스택", "보유역량", "핵심역량", "기술역량", "기술역량세부", "기술및성장계획"},
    "summary": {"요약", "지원요약", "목표직무", "지원분야", "희망직무", "활동사항", "대외활동", "수상", "수상경력", "교육", "교육사항",
                "교육이수", "학습및활동", "포트폴리오", "입사후기여계획"},
    "facts": {"학력", "학력사항", "자격증", "보유자격증", "자격사항", "자격", "병역", "병역사항", "어학"},
    "contact": {"인적사항", "기본정보", "연락처", "개인정보", "기본인적사항"},
}
# The English name of a page ("홍길동 · 대표 프로젝트 / ARCHITECTURE") when its Korean name is not one of the above.
_ENGLISH = {"profile": "summary", "summary": "summary", "career": "career", "experience": "career", "workexperience": "career",
            "projects": "projects", "project": "projects", "architecture": "projects", "technicaldepth": "projects",
            "troubleshooting": "projects", "skills": "skills", "techstack": "skills", "education": "facts",
            "coverletter": "intro", "aboutme": "intro"}
_KINDS = {name: kind for kind, names in _HEADINGS.items() for name in names}
_PAGE_TITLE = re.compile(r"^(?:[^·|/]{1,20}·\s*)?(?P<ko>[^·|/]{1,24}?)\s*/\s*(?P<en>[A-Za-z][A-Za-z &]{1,30})$")
KEPT_KINDS = ("career", "projects", "skills", "summary")
_LABELS = {"경력직무", "회사명", "근무기간", "수행직무", "담당업무", "직위", "직책", "기간", "프로젝트주제", "프로젝트명", "수행기간"}
_NOT_WORD = re.compile(r"[^0-9a-z가-힣]")
_DATE_ONLY = re.compile(r"^[\d\s년월일~.\-/()총개재직중현재]+$")
_SEPARATORS = " \t/|·—–-:,"


def _word(line: str) -> str:
    return _NOT_WORD.sub("", line.lower())


def _kind(line: str) -> str | None:
    t = line.strip()
    if not t:
        return None
    m = _PAGE_TITLE.match(t)
    if m:  # a page title: its Korean name if known, else its English one
        return _KINDS.get(_word(m["ko"]).strip("0123456789")) or _ENGLISH.get(_word(m["en"]))
    if len(t) > HEADING_MAX:
        return None
    return _KINDS.get(_word(t).strip("0123456789"))


def heading_name(line: str) -> str:
    """The heading as a reader names it: "홍길동 · 대표 프로젝트 / ARCHITECTURE" -> "대표 프로젝트"."""
    t = line.strip()
    m = _PAGE_TITLE.match(t)
    return m["ko"].strip() if m and m["ko"].strip() else t


def _drop_repeated(lines: list[str]) -> list[str]:
    """Page footers and table labels repeat with at most a different number; a sentence does not. A line on three pages is
    a page header however long it is (word for word: a long line differing in a number is not)."""
    def shape(x: str) -> str:
        return re.sub(r"\d+", "", x.strip())

    seen = Counter(shape(x) for x in lines if x.strip())
    exact = Counter(x.strip() for x in lines if x.strip())
    return [x for x in lines if not (x.strip() and (len(x.strip()) <= REPEATED_MAX and seen[shape(x)] > 1
                                                    or exact[x.strip()] >= REPEATED_PAGES))]


@dataclass
class Block:
    """The lines under one heading, word for word, without page headers and footers: (1-based line number in the extracted
    text, line)."""
    heading: str
    kind: str
    lines: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Sections:
    body: str  # what T1 reads: everything but the cover letter
    intro: str | None
    career: str
    projects: str
    blocks: list[Block] = field(default_factory=list)


def read_sections(text: str) -> Sections:
    lines = text.splitlines()
    repeated = set(lines) - set(_drop_repeated(lines))
    parts: dict[str, list[str]] = {"intro": [], "career": [], "projects": []}
    body: list[str] = []
    blocks: list[Block] = []
    kind = None
    for n, line in enumerate(lines, 1):
        heading = _kind(line)
        if heading:
            kind = heading
            blocks.append(Block(heading_name(line), kind))
            if kind != "intro":
                body.append(line)
            continue
        if kind != "intro":
            body.append(line)
        if kind in parts and line not in repeated:
            parts[kind].append(line)
        if blocks and line.strip() and line not in repeated:
            blocks[-1].lines.append((n, line))
    if not "".join(parts["intro"]).strip():  # no cover-letter heading: the paragraph above the first section is one
        lead = _lead_paragraph(lines)
        if lead:
            parts["intro"] = lines[lead[0]:lead[1]]
            body = [x for i, x in enumerate(lines) if not lead[0] <= i < lead[1] and _kind(x) != "intro"]
    intro = "\n".join(parts["intro"]).strip()[:INTRO_MAX]
    return Sections("\n".join(body), intro or None, "\n".join(parts["career"]), "\n".join(parts["projects"]),
                    [b for b in blocks if b.lines])


_SENTENCE_END = re.compile(r"(다|요|니다)[.!]?\s*$|[.!?]\s*$")
LEAD_LINE_MIN = 30  # a prose line, not a name, a phone number or "경력 8년 3개월"


def _lead_paragraph(lines: list[str]) -> tuple[int, int] | None:
    """(first, end) of the prose at the top of a resume, before any section heading: the self-introduction many resumes open with.
    It starts at the first long line and ends where a finished sentence is followed by a short line (a section's first row).
    Two finished sentences at least, or it is not prose. The lines are kept as they are (verbatim)."""
    stop = next((i for i, x in enumerate(lines) if _kind(x)), len(lines))
    first = next((i for i in range(stop) if len(lines[i].strip()) >= LEAD_LINE_MIN), None)
    if first is None:
        return None
    end = first + 1
    while end < stop:
        line, before = lines[end].strip(), lines[end - 1].strip()
        if _SENTENCE_END.search(before) and len(line) < LEAD_LINE_MIN // 2:
            break
        end += 1
    while end > first and not _SENTENCE_END.search(lines[end - 1].strip()):
        end -= 1  # end on a finished sentence, never in the middle of a table row
    text = " ".join(x.strip() for x in lines[first:end])
    return (first, end) if len(re.findall(r"(다|요)[.!]", text)) >= 2 else None


def _clean(span: str, row: dict, key: str) -> str:
    """One line (the form keeps one row per line), without what the row already says and table labels."""
    return " ".join(_clean_lines(span, row, key))[:DESCRIPTION_MAX]


def _clean_lines(span: str, row: dict, key: str) -> list[str]:
    known = [str(row[f]) for f in (key, "role", "period") if row.get(f)]
    out: list[str] = []
    for raw in span.splitlines():
        line = raw.strip()
        if not out:  # the rest of the row's own heading: "/ 과장 / DBA — 대용량 DB 운영"
            before = None
            while line != before:
                before = line
                line = line.lstrip(_SEPARATORS)
                for k in known:
                    if line.startswith(k):
                        line = line[len(k):]
        if line and line not in known and _word(line) not in _LABELS and not _DATE_ONLY.match(line):
            out.append(line)
    return out


def apply_sections(fields: dict, s: Sections) -> dict:
    """`fields` in the form shape (from_ai_resume), completed from the file's text. A row's description becomes the text of
    the blocks that belong to it; a row no block belongs to keeps the model's short description."""
    fields["intro"] = s.intro
    fields["details"] = details(fields, s.blocks)
    _complete(fields, fields["details"])
    return fields


# ── details: every block word for word, where it came from and which row it belongs to (ADR-0032) ────────────────────
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?82[- ]?|0)1[016-9][- .]?\d{3,4}[- .]?\d{4}")
_TOKEN = re.compile(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣+#]*")
_STOP = {"프로젝트", "담당", "설계", "개발", "구현", "그리고", "에서", "으로", "있는", "하는", "했습니다", "합니다", "위해", "통해",
         "대한", "기반", "관련", "the", "and", "for", "with"}
NAME_WITHIN = 30  # a row's name this close to a line's start opens that row's text
SIMILAR_MIN = 0.4  # share of a row's words a block must hold to belong to it without naming it
SIMILAR_WORDS = 4  # words a row needs to be matched this way at all
SIMILAR_HITS = 3  # of which the text must hold this many
SIMILAR_MARGIN = 2.0  # ... and at least this many times the share of any other row


def _private(text: str) -> str:
    return _PHONE.sub("[연락처]", _EMAIL.sub("[이메일]", text))


def _rows(fields: dict) -> list[tuple[str, str, dict]]:
    """(section, row name, row) for every career and project row."""
    out = []
    for section, key in (("career", "company"), ("projects", "title")):
        for row in fields.get(section) or []:
            if row.get(key):
                out.append((section, row[key], row))
    return out


def row_ref(section: str, name: str) -> str:
    return f"{section}:{name}"[:300]


def _names(name: str) -> list[str]:
    """A row's name and, for "프로젝트 A | 고객지원 AI 에이전트", the part after the label."""
    name = name.strip()
    names = [name]
    tail = re.split(r"\s*[|:]\s*", name, maxsplit=1)
    if len(tail) == 2 and len(tail[1]) >= 4:
        names.append(tail[1])
    return names


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text) if len(t) >= 2 and t.lower() not in _STOP and not t.isdigit()}


def _similar(text: str, rows: list[tuple[str, str, dict]]) -> tuple[str, str] | None:
    """The one row whose words (name, role and the model's description) the text clearly holds, or None when it is unclear."""
    low = text.lower()
    scores = []
    for section, name, row in rows:
        words = _tokens(" ".join(x for x in (name, row.get("role"), row.get("description")) if x))
        hits = sum(w in low for w in words)
        # a row of a word or two ("에이전트 개발 프로젝트") matches anything that says that word: it is never a candidate
        scores.append((hits / len(words) if len(words) >= SIMILAR_WORDS else 0.0, hits, section, name))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < SIMILAR_MIN or scores[0][1] < SIMILAR_HITS:
        return None
    if len(scores) > 1 and scores[0][0] < SIMILAR_MARGIN * scores[1][0]:
        return None
    return scores[0][2], scores[0][3]


def _pieces(block: Block, rows: list[tuple[str, str, dict]]) -> list[tuple[list[tuple[int, str]], tuple[str, str] | None]]:
    """The block cut where a line names a row near its start ("2016.12 ~ 가상테크(주) / 과장"): the piece belongs to that row."""
    out: list[tuple[list[tuple[int, str]], tuple[str, str] | None]] = [([], None)]
    for n, line in block.lines:
        head = line.strip()[:NAME_WITHIN + 60]
        named = next(((sec, name) for sec, name, _ in rows
                      if any(0 <= head.find(x) <= NAME_WITHIN for x in _names(name))), None)
        if named:
            out.append(([], named))
        out[-1][0].append((n, line))
    return [p for p in out if p[0]]


def _split(lines: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Consecutive runs of at most DETAIL_MAX characters: a long block is split, never cut."""
    out: list[list[tuple[int, str]]] = [[]]
    size = 0
    for n, x in lines:
        x = x.strip()[:DETAIL_MAX]
        if out[-1] and size + len(x) + 1 > DETAIL_MAX:
            out.append([])
            size = 0
        out[-1].append((n, x))
        size += len(x) + 1
    return [c for c in out if c]


def details(fields: dict, blocks: list[Block]) -> list[dict]:
    rows = _rows(fields)
    out: list[dict] = []
    for b in blocks:
        if b.kind not in KEPT_KINDS:
            continue
        for lines, named in _pieces(b, rows):
            how = "name" if named else None
            if not named and b.kind in ("career", "projects"):
                named = _similar("\n".join(x for _, x in lines), rows)
                how = "similar" if named else None
            for chunk in _split(lines):
                out.append({"heading": b.heading[:200], "kind": b.kind,
                            "text": _private("\n".join(x for _, x in chunk)),
                            "row": row_ref(*named) if named else None, "how": how,
                            "lines": [chunk[0][0], chunk[-1][0]]})
    return out[:DETAILS_MAX]


def _complete(fields: dict, items: list[dict]) -> None:
    """A row the section did not describe gets the text of the details that belong to it, word for word, up to the form's
    limit at a line boundary (the details keep all of it)."""
    for section, name, row in _rows(fields):
        mine = [d for d in items if d["row"] == row_ref(section, name)]
        if not mine:
            continue
        parts: list[str] = []
        full = False
        for d in mine:
            for line in _clean_lines(d["text"], row, "company" if section == "career" else "title"):
                if line in parts:
                    continue
                if len(" ".join(parts + [line])) > DESCRIPTION_MAX:
                    full = True
                    break
                parts.append(line)
            if full:
                break
        if parts:
            row["description"] = " ".join(parts)
