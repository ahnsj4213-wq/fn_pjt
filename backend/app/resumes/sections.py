"""Long resume text is cut by code, not copied by the model (RES-03).

T1 copies values into a JSON answer, so a long description cannot fit its token limit. The model only names the rows; this
module cuts the rest from the file's text: the cover letter by its heading, each career and project description as the text
from that row's name to the next row's. All of it is verbatim. The cover letter does not go to T1 at all: its sentences used
to come back as invented companies and projects. A resume without recognisable headings keeps the model's short descriptions.
"""
import re
from collections import Counter
from dataclasses import dataclass

INTRO_MAX = 10_000
DESCRIPTION_MAX = 3000
HEADING_MAX = 24
REPEATED_MAX = 40

_HEADINGS = {
    "intro": {"자기소개서", "자기소개", "커버레터", "coverletter"},
    "career": {"경력", "경력사항", "경력기술서", "경력상세", "경력직무상세", "경력기술", "업무경력", "근무경력", "직무경험"},
    "projects": {"프로젝트", "주요프로젝트", "프로젝트경험", "프로젝트경력", "수행프로젝트", "프로젝트소개서", "프로젝트소개"},
    "other": {"학력", "학력사항", "보유기술", "기술", "기술스택", "보유역량", "자격증", "보유자격증", "자격사항", "자격", "인적사항",
              "병역", "병역사항", "교육", "교육사항", "교육이수", "활동사항", "대외활동", "수상", "수상경력", "어학", "포트폴리오", "요약"},
}
_KINDS = {name: kind for kind, names in _HEADINGS.items() for name in names}
_LABELS = {"경력직무", "회사명", "근무기간", "수행직무", "담당업무", "직위", "직책", "기간", "프로젝트주제", "프로젝트명", "수행기간"}
_NOT_WORD = re.compile(r"[^0-9a-z가-힣]")
_DATE_ONLY = re.compile(r"^[\d\s년월일~.\-/()총개재직중현재]+$")
_SEPARATORS = " \t/|·—–-:,"


def _word(line: str) -> str:
    return _NOT_WORD.sub("", line.lower())


def _kind(line: str) -> str | None:
    t = line.strip()
    if not t or len(t) > HEADING_MAX:
        return None
    return _KINDS.get(_word(t).strip("0123456789"))


def _drop_repeated(lines: list[str]) -> list[str]:
    """Page footers and table labels repeat with at most a different number; a sentence does not."""
    def shape(x: str) -> str:
        return re.sub(r"\d+", "", x.strip())

    seen = Counter(shape(x) for x in lines if x.strip())
    return [x for x in lines if not (x.strip() and len(x.strip()) <= REPEATED_MAX and seen[shape(x)] > 1)]


@dataclass
class Sections:
    body: str  # what T1 reads: everything but the cover letter
    intro: str | None
    career: str
    projects: str


def read_sections(text: str) -> Sections:
    lines = text.splitlines()
    repeated = set(lines) - set(_drop_repeated(lines))
    parts: dict[str, list[str]] = {"intro": [], "career": [], "projects": []}
    body: list[str] = []
    kind = None
    for line in lines:
        heading = _kind(line)
        if heading:
            kind = heading
            if kind != "intro":
                body.append(line)
            continue
        if kind != "intro":
            body.append(line)
        if kind in parts and line not in repeated:
            parts[kind].append(line)
    if not "".join(parts["intro"]).strip():  # no cover-letter heading: the paragraph above the first section is one
        lead = _lead_paragraph(lines)
        if lead:
            parts["intro"] = lines[lead[0]:lead[1]]
            body = [x for i, x in enumerate(lines) if not lead[0] <= i < lead[1] and _kind(x) != "intro"]
    intro = "\n".join(parts["intro"]).strip()[:INTRO_MAX]
    return Sections("\n".join(body), intro or None, "\n".join(parts["career"]), "\n".join(parts["projects"]))


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
    return " ".join(out)[:DESCRIPTION_MAX]


def _fill(rows: list[dict], key: str, text: str) -> None:
    if not text.strip():
        return
    marks: list[tuple[int, int, dict]] = []
    used: dict[str, int] = {}
    for row in rows:
        name = row.get(key) or ""
        at = text.find(name, used.get(name, 0)) if name else -1
        if at >= 0:  # a row whose name is not in this section keeps the model's description
            used[name] = at + len(name)
            marks.append((at, at + len(name), row))
    marks.sort(key=lambda m: m[0])
    for n, (_, end, row) in enumerate(marks):
        stop = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        description = _clean(text[end:stop], row, key)
        if description:
            row["description"] = description


def apply_sections(fields: dict, s: Sections) -> dict:
    """`fields` in the form shape (from_ai_resume), completed from the file's text."""
    _fill(fields.get("career") or [], "company", s.career)
    _fill(fields.get("projects") or [], "title", s.projects)
    fields["intro"] = s.intro
    return fields
