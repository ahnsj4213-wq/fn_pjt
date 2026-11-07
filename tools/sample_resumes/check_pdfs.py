"""생성한 PDF를 앱의 추출기로 읽어 페이지 수와 필수 문구를 확인하고, 화면 점검용 PNG를 만든다."""
import json
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).parents[2]
EXTRACT = ROOT / "backend" / "app" / "resumes" / "pdf_extract.py"
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from generate import RESUMES  # noqa: E402

out_png = Path(sys.argv[1]) if len(sys.argv) > 1 else None
for key, r in RESUMES.items():
    pdf = HERE / "pdf" / f"resume_{key}.pdf"
    d = json.loads(subprocess.check_output([sys.executable, "-I", str(EXTRACT), str(pdf)]))
    text = "\n".join(d["page_texts"])
    squeezed = "".join(text.split())
    # 페이지 내용이 잘리거나 글자가 깨지지 않았는지 이력서의 고유 문구로 확인한다.
    anchors = [r["name"], r["career"][0]["company"], r["projects"][0]["title"], r["certificates"][-1]["name"], r["essays"][-1][0]]
    missing = [a for a in anchors if "".join(a.split()) not in squeezed]
    bad = sum(text.count(c) for c in ("�", "■"))
    print(key, "ok", d["ok"], "pages", d["pages"], "chars", len(text), "last_page", len(d["page_texts"][-1]), "missing", missing, "bad", bad)
    if out_png:
        out_png.mkdir(parents=True, exist_ok=True)
        doc = pdfium.PdfDocument(str(pdf))
        for i in (0, len(doc) - 1):
            doc[i].render(scale=1.1).to_pil().save(out_png / f"{key}_{i + 1}.png")
