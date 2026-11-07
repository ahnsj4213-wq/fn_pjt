"""RAG·이력서 추출 테스트용 가상 이력서 PDF를 만든다. 실행: .venv\\Scripts\\python tools\\sample_resumes\\generate.py

모든 인물·회사·수치는 지어낸 것이다. 앱의 추출기(pypdf)가 읽는 순서가 흐트러지지 않도록 1단 구성만 쓴다.
"""
from pathlib import Path
from xml.sax.saxutils import escape

from agri import R as AGRI
from apparel import R as APPAREL
from chemical import R as CHEMICAL
from extras import EXTRAS
from finance import R as FINANCE
from manufacturing import R as MANUFACTURING
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer
from retail import R as RETAIL

OUT = Path(__file__).parent / "pdf"
RESUMES = {"manufacturing": MANUFACTURING, "retail": RETAIL, "apparel": APPAREL, "chemical": CHEMICAL,
           "agriculture": AGRI, "finance": FINANCE}
NOTICE = "테스트용 가상 이력서"
INK, MUTED, RULE = HexColor("#1f2933"), HexColor("#52606d"), HexColor("#9aa5b1")

pdfmetrics.registerFont(TTFont("Malgun", r"C:\Windows\Fonts\malgun.ttf"))
pdfmetrics.registerFont(TTFont("Malgun-Bold", r"C:\Windows\Fonts\malgunbd.ttf"))
pdfmetrics.registerFontFamily("Malgun", normal="Malgun", bold="Malgun-Bold", italic="Malgun", boldItalic="Malgun-Bold")

BODY = ParagraphStyle("body", fontName="Malgun", fontSize=10.5, leading=17.5, textColor=INK, spaceAfter=4)
NAME = ParagraphStyle("name", parent=BODY, fontName="Malgun-Bold", fontSize=20, leading=26, spaceAfter=2)
HEAD = ParagraphStyle("headline", parent=BODY, fontSize=11, leading=16, textColor=MUTED, spaceAfter=2)
SECTION = ParagraphStyle("section", parent=BODY, fontName="Malgun-Bold", fontSize=12.5, leading=18, spaceBefore=12, spaceAfter=3)
ITEM = ParagraphStyle("item", parent=BODY, fontName="Malgun-Bold", fontSize=10.5, leading=16, spaceBefore=7, spaceAfter=1)
META = ParagraphStyle("meta", parent=BODY, textColor=MUTED, spaceAfter=3)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=11, bulletIndent=1, spaceAfter=2.5)


def p(text: str, style: ParagraphStyle = BODY, **kw) -> Paragraph:
    return Paragraph(escape(text), style, **kw)


def section(title: str) -> list:
    return [p(title, SECTION), HRFlowable(width="100%", thickness=0.7, color=RULE, spaceAfter=5)]


def bullets(rows: list[str]) -> list:
    return [p(r, BULLET, bulletText="•") for r in rows]


def story(r: dict) -> list:
    s = [p(r["name"], NAME), p(r["headline"], HEAD), p(f"{r['email']} | {r['phone']} | {r['region']}", META)]
    s += section("요약")
    s += [p(x) for x in r["summary"]]

    s += section("학력")
    for e in r["education"]:
        s.append(p(f"{e['school']} {e['major']} {e['degree']} ({e['period']})"))
        if e.get("note"):
            s += bullets([e["note"]])

    s += section("경력")
    for c in r["career"]:
        s.append(KeepTogether([p(c["company"], ITEM), p(f"{c['role']} | {c['period']}", META), p(c["about"])]))
        s += bullets(c["duties"])

    s += section("주요 프로젝트")
    for x in r["projects"]:
        s.append(KeepTogether([p(x["title"], ITEM), p(f"{x['role']} | {x['period']}", META), p("배경: " + x["background"])]))
        s += bullets(x["actions"])
        s.append(p("결과: " + x["result"]))

    s += section("보유 기술")
    s += [p(f"{g}: {', '.join(items)}") for g, items in r["skills"]]

    s += section("자격증")
    s += [p(f"{c['name']} | {c['issuer']} | {c['date']}") for c in r["certificates"]]

    if r.get("training"):
        s += section("교육 이수")
        s += bullets(r["training"])

    s += section("자기소개")
    for title, body in r["essays"]:
        s.append(KeepTogether([p(title, ITEM), p(body)]))
    return s


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Malgun", 8)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(A4[0] / 2, 11 * mm, f"{NOTICE} - {doc.page}")
    canvas.restoreState()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for key, r in RESUMES.items():
        r = {**r, "projects": r["projects"] + EXTRAS[key]["projects"], "essays": r["essays"][:-1] + EXTRAS[key]["essays"] + r["essays"][-1:]}
        path = OUT / f"resume_{key}.pdf"
        doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                                bottomMargin=20 * mm, title=f"{NOTICE} - {r['headline']}", author="AI Hire test data",
                                subject=NOTICE)
        doc.build(story(r), onFirstPage=footer, onLaterPages=footer)
        print(path.name, doc.page)


if __name__ == "__main__":
    main()
