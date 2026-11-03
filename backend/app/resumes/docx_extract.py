"""DOCX text extraction (SC-A10 "PDF, DOCX를 받습니다"). Runs in a separate interpreter like pdf_extract.py:
`python -I docx_extract.py <file>`. Prints one JSON line: {"ok", "text", "error"}.

Only word/document.xml is read, with the standard library (zipfile + expat, which refuses external entities and
caps entity expansion). Archives that would inflate past the limits are refused before anything is parsed.
"""
import json
import sys
import zipfile
from xml.etree.ElementTree import iterparse

MAX_TOTAL_INFLATED = 50 * 1024 * 1024
MAX_DOCUMENT_XML = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000


def extract(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if sum(i.file_size for i in infos) > MAX_TOTAL_INFLATED:
            raise ValueError("TOO_LARGE")
        doc = z.getinfo("word/document.xml")
        if doc.file_size > MAX_DOCUMENT_XML:
            raise ValueError("TOO_LARGE")
        lines: list[str] = []
        parts: list[str] = []
        with z.open(doc) as f:
            for _, el in iterparse(f, events=("end",)):
                tag = el.tag.rsplit("}", 1)[-1]  # WordprocessingML local names (w:t, w:tab, w:p)
                if tag == "t" and el.text:
                    parts.append(el.text)
                elif tag == "tab":
                    parts.append(" ")
                elif tag == "p":  # paragraphs (table cells are paragraphs too)
                    line = "".join(parts).strip()
                    if line:
                        lines.append(line)
                    parts = []
                    el.clear()
        return "\n".join(lines)[:MAX_TEXT_CHARS]


def main(path: str) -> None:
    out = {"ok": False, "text": "", "error": None}
    try:
        out.update(ok=True, text=extract(path))
    except ValueError as e:
        out["error"] = "TOO_LARGE" if str(e) == "TOO_LARGE" else "UNREADABLE"
    except Exception:  # noqa: BLE001 - not a zip, no document part, broken XML: unreadable
        out["error"] = "UNREADABLE"
    sys.stdout.write(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main(sys.argv[1])
