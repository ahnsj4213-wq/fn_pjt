"""Runs in a separate interpreter (`python -I pdf_extract.py <file>`) so a hostile or broken PDF cannot hang or
crash the API process (07-security, SC-22). Prints one JSON line: {"ok", "pages", "encrypted", "text", "error"}.
Only pypdf (pure Python, BSD) is used — no native parser."""
import json
import sys

MAX_PAGES = 50
MAX_CHARS = 200_000


def main(path: str) -> None:
    out = {"ok": False, "pages": 0, "encrypted": False, "text": "", "error": None}
    try:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            out.update(encrypted=True, error="ENCRYPTED")
        else:
            out["pages"] = len(reader.pages)
            if out["pages"] > MAX_PAGES:
                out["error"] = "TOO_MANY_PAGES"
            else:
                parts, total = [], 0
                for page in reader.pages:
                    t = page.extract_text() or ""
                    parts.append(t)
                    total += len(t)
                    if total > MAX_CHARS:
                        break
                out["text"] = "\n".join(parts)[:MAX_CHARS]
                out["ok"] = True
    except Exception as e:  # noqa: BLE001 - any parser failure means "unreadable PDF"
        out["error"] = "UNREADABLE:" + type(e).__name__
    sys.stdout.write(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main(sys.argv[1])
