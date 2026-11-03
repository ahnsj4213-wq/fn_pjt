"""OCR of scanned PDF pages and resume photos (RES-04). Runs in a separate interpreter like pdf_extract.py:
`python -I ocr_extract.py <file> <pdf|image> <pages> <lang> <dpi>` (pages: comma-separated 0-based indexes, "" for a
photo). Prints one JSON line: {"ok", "lines": [[text, confidence 0~1], ...], "error"}.

Pages are rendered with pypdfium2, straightened (projection-profile deskew), then read by Tesseract. Rendered images
stay in memory only and are dropped right after each page (no intermediate files). A missing engine or language pack
answers OCR_UNAVAILABLE so the caller falls back to manual input.
"""
import json
import statistics
import sys

MAX_PIXELS = 60_000_000  # a "photo" bigger than this is a decompression bomb, not a resume


def load_image(path: str):
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")  # phone photos carry their rotation in EXIF


def render_pdf_pages(path: str, indexes: list[int], dpi: int):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        for i in indexes:
            yield pdf[i].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        pdf.close()


def best_angle(img, span: int = 5) -> int:
    """Skew in degrees that makes text rows sharpest: the angle whose row sums vary the most (projection profile)."""
    from PIL import Image, ImageOps

    g = ImageOps.autocontrast(img.convert("L"))
    g.thumbnail((600, 800))
    bw = g.point(lambda p: 0 if p < 140 else 255).point(lambda p: 255 - p)  # ink becomes the large value
    best, score = 0, -1.0
    for a in range(-span, span + 1):
        rows = list(bw.rotate(a, expand=False, fillcolor=0).resize((1, bw.height), Image.BOX).tobytes())
        m = sum(rows) / len(rows)
        v = sum((r - m) ** 2 for r in rows)
        if v > score:
            best, score = a, v
    return best


def deskew(img):
    from PIL import Image

    a = best_angle(img)
    return img if a == 0 else img.rotate(a, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)


# A gap narrower than this share of the text height is inside a word. Measured on a 200 dpi Korean scan (text height
# ~29 px): gaps inside a word 4~13 px, real spaces 14~23 px. Between two lone syllables the wider limit applies.
GAP_RATIO = 0.3
SYLLABLE_GAP_RATIO = 0.55


def _syllable(t: str) -> bool:
    return len(t) == 1 and "가" <= t <= "힣"


def join_words(words: list[tuple[str, int, int, int]]) -> str:
    """Words (text, left, width, height) of one line -> text. Tesseract's Korean model often returns every syllable
    as its own "word" ("커 머 스 랩"); spaces are put back only where the picture has a real gap."""
    if not words:
        return ""
    height = statistics.median(h for _, _, _, h in words) or 1
    out = words[0][0]
    for (pt, pl, pw, _), (t, left, _, _) in zip(words, words[1:]):
        limit = SYLLABLE_GAP_RATIO if _syllable(pt) and _syllable(t) else GAP_RATIO
        out += ("" if left - (pl + pw) < limit * height else " ") + t
    return out


def tesseract_lines(img, lang: str, psm: int = 3) -> list[list]:
    """[[line text, mean word confidence 0~1], ...] in reading order."""
    import pytesseract

    d = pytesseract.image_to_data(img, lang=lang, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    lines: dict[tuple, list[tuple[str, float, int, int, int]]] = {}
    for i, t in enumerate(d["text"]):
        t, conf = t.strip(), float(d["conf"][i])
        if t and conf >= 0:  # conf -1 marks layout boxes, not words
            lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(
                (t, conf / 100, d["left"][i], d["width"][i], d["height"][i]))
    return [[join_words([(w, x, wd, h) for w, _, x, wd, h in ws]), statistics.fmean(c for _, c, *_ in ws)]
            for ws in lines.values()]


def read_page(img, lang: str) -> list[list]:
    """Automatic layout (psm 3) sometimes drops whole blocks of a straightened page; single-column mode (psm 4) keeps
    them but mixes two-column layouts. Both are read and the one with more confidently read text wins."""
    def score(lines: list[list]) -> float:
        return sum(len(t) * c for t, c in lines)
    return max((tesseract_lines(img, lang, psm) for psm in (3, 4)), key=score)


def main(path: str, kind: str, pages: str, lang: str, dpi: int) -> None:
    out = {"ok": False, "lines": [], "error": None}
    try:
        import pytesseract

        try:
            images = [load_image(path)] if kind == "image" else render_pdf_pages(path, [int(x) for x in pages.split(",") if x], dpi)
            for img in images:
                out["lines"] += read_page(deskew(img), lang)
                del img
            out["ok"] = True
        except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as e:  # no binary / no language data
            out.update(lines=[], error="OCR_UNAVAILABLE:" + type(e).__name__)
    except ImportError as e:
        out["error"] = "OCR_UNAVAILABLE:" + type(e).__name__
    except Exception as e:  # noqa: BLE001 - a broken image or page means "unreadable"
        out.update(lines=[], error="UNREADABLE:" + type(e).__name__)
    sys.stdout.write(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
