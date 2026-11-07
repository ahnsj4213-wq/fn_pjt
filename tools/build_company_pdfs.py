"""Builds docs/synthetic-companies/<회사명>_회사자료.pdf, about 10 pages per fictional company with an open posting.

Sources: backend/app/questions/data/synthetic_companies.json (the posting and the project and tech paragraphs, quoted word for word,
so a PDF never disagrees with what question generation searches) and backend/app/questions/data/company_profiles/<key>.json (the rest of the story).
Every company, figure and event here is invented; each page says so.

    python tools/build_company_pdfs.py [key ...]
"""
import json
import sys
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "backend" / "app" / "questions" / "data" / "synthetic_companies.json"
PROFILES = ROOT / "backend" / "app" / "questions" / "data" / "company_profiles"
OUT = ROOT / "docs" / "synthetic-companies"

pdfmetrics.registerFont(TTFont("Malgun", r"C:\Windows\Fonts\malgun.ttf"))
pdfmetrics.registerFont(TTFont("MalgunBd", r"C:\Windows\Fonts\malgunbd.ttf"))
pdfmetrics.registerFontFamily("Malgun", normal="Malgun", bold="MalgunBd", italic="Malgun", boldItalic="MalgunBd")

INK = colors.HexColor("#1f2433")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#dfe3ec")
PANEL = colors.HexColor("#f6f7fb")
WIDTH = A4[0] - 40 * mm
NOTICE = "가상 기업 자료 · 실존 회사와 무관하며 모든 수치와 사건은 지어낸 것입니다"
PROCESS = ["지원·개인정보 동의", "이력서 제출", "장치·연결 점검", "AI 면접", "코딩테스트(공고 설정 시)", "담당자 검토·결정"]


def st(name, size=11.2, lead=None, color=INK, font="Malgun", **kw):
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=lead or size * 1.72, textColor=color, wordWrap="CJK", **kw)


BODY = st("body", spaceAfter=8)
SMALL = st("small", 8.8, color=MUTED)
CELL = st("cell", 10.2, 15.4)
HEAD_CELL = st("headcell", 10.2, 15.4, colors.white, "MalgunBd")
H3 = st("h3", 12.5, font="MalgunBd", spaceBefore=12, spaceAfter=5)


def esc(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def p(t, style=BODY):
    return Paragraph(esc(t), style)


def bullets(items):
    return [Paragraph(esc(i), st("li", leftIndent=12, bulletIndent=2, spaceAfter=4), bulletText="•") for i in items]


class Book:
    """One company's document: the accent color and the small parts built from it."""

    def __init__(self, fact: dict, prof: dict):
        self.f, self.c = fact, prof
        self.accent = colors.HexColor(prof["accent"])
        self.soft = colors.HexColor(prof["soft"])

    def h2(self, kicker, title):
        return [Paragraph(esc(kicker), st("k", 9, color=self.accent, font="MalgunBd", spaceAfter=2)),
                Paragraph(esc(title), st("h2", 18, 25, font="MalgunBd", spaceAfter=10))]

    def table(self, head, rows, widths):
        data = [[Paragraph(esc(h), HEAD_CELL) for h in head]] + [[Paragraph(esc(x).replace("\n", "<br/>"), CELL) for x in r] for r in rows]
        t = Table(data, colWidths=[w * WIDTH for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), self.accent), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        return t

    def callout(self, label, text):
        t = Table([[[Paragraph(esc(label), st("cl", 8.8, color=self.accent, font="MalgunBd", spaceAfter=3)), p(text, st("cb", 11.2, 18.6))]]], colWidths=[WIDTH])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), self.soft), ("LINEBEFORE", (0, 0), (0, -1), 3, self.accent),
                               ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        return t

    def tiles(self, stats):
        cells = [[Paragraph(esc(v), st("tv", 15, 20, self.accent, "MalgunBd", alignment=TA_CENTER)),
                  Paragraph(esc(k), st("tk", 8.6, 12, MUTED, alignment=TA_CENTER))] for k, v in stats]
        t = Table([cells], colWidths=[WIDTH / len(cells)] * len(cells))
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 11)]))
        return t

    def flow(self, steps, h=66):
        """Boxes joined by arrows; long labels wrap on two lines."""
        n, gap = len(steps), 14
        w = (WIDTH - gap * (n - 1)) / n
        d = Drawing(WIDTH, h)
        for i, s in enumerate(steps):
            x = i * (w + gap)
            d.add(Rect(x, 0, w, h, rx=6, ry=6, fillColor=self.soft, strokeColor=self.accent, strokeWidth=0.8))
            lines = _wrap(s, w - 8, 8.6)
            y = h / 2 + (len(lines) - 1) * 6 - 3
            for ln in lines:
                d.add(String(x + w / 2, y, ln, fontName="Malgun", fontSize=8.6, fillColor=INK, textAnchor="middle"))
                y -= 12
            if i < n - 1:
                ax = x + w + 2
                d.add(Line(ax, h / 2, ax + gap - 7, h / 2, strokeColor=self.accent, strokeWidth=1))
                d.add(Polygon([ax + gap - 7, h / 2 + 3, ax + gap - 3, h / 2, ax + gap - 7, h / 2 - 3], fillColor=self.accent, strokeColor=self.accent))
        return d

    def bars(self, chart):
        rows = chart["rows"]
        top, row_h, label_w = max(v for _, v in rows) or 1, 22, 118
        d = Drawing(WIDTH, row_h * len(rows))
        room = WIDTH - label_w - 62
        for i, (label, v) in enumerate(rows):
            y = row_h * (len(rows) - 1 - i) + 4
            d.add(String(0, y + 3, str(label), fontName="Malgun", fontSize=9, fillColor=INK))
            d.add(Rect(label_w, y, max(2, room * v / top), 13, rx=2, ry=2, fillColor=self.accent, strokeColor=None))
            d.add(String(label_w + room * v / top + 5, y + 3, f"{v:g}{chart['unit']}", fontName="MalgunBd", fontSize=9, fillColor=INK))
        return d

    def _docs(self, kind):
        return [d for d in self.f["docs"] if d["type"] == kind]

    def story(self) -> list:
        c, f = self.c, self.f
        projects, tech = self._docs("PROJECT"), self._docs("TECH")
        posting = f["posting"]
        s = []

        # 1 cover
        s += [Spacer(1, 52 * mm), Paragraph("회사 소개 자료", st("cv", 12, color=self.accent, font="MalgunBd", spaceAfter=8)),
              Paragraph(esc(f["name"]), st("ct", 30, 40, font="MalgunBd", spaceAfter=10)),
              Paragraph(esc(c["tagline"]), st("cs", 13.5, 22, MUTED, spaceAfter=20)), self.tiles(c["stats"]), Spacer(1, 12),
              self.callout("채용 중인 포지션", f"{posting['title']} · {posting['duties'].splitlines()[0]}"), Spacer(1, 12),
              self.callout("이 문서를 읽는 법", "이 자료는 AI 채용 플랫폼의 시연과 시험을 위해 만든 가상 기업 자료입니다. 회사, 인물, 고객, 수치, 사건은 모두 지어낸 것이며 실존 회사와 관계가 없습니다."),
              PageBreak()]
        # 2 overview
        s += self.h2("01 회사 개요", "우리가 하는 일") + [self.callout("미션", c["mission"]), Spacer(1, 10)]
        s += [p(t) for t in c["about"]]
        s += [Paragraph("연혁", H3), self.table(["연도", "주요 사건"], c["history"], [0.14, 0.86]), PageBreak()]
        # 3 services
        s += self.h2("02 서비스와 고객", "무엇을 누구에게 제공하는가")
        s += [self.table(["서비스", "설명"], c["services"], [0.25, 0.75]), Paragraph("주요 고객", H3), p(c["customers"]), Paragraph("수익 구조", H3), p(c["model"]),
              Paragraph("지금 집중하는 문제", H3)] + bullets(c["focus"]) + [PageBreak()]
        # 4 org
        s += self.h2("03 조직과 일하는 방식", "어떻게 일하는가") + [self.table(["팀", "인원", "하는 일"], c["teams"], [0.22, 0.1, 0.68]), Paragraph("일하는 원칙", H3)]
        s += [self.table(["원칙", "실제 모습"], c["culture"], [0.26, 0.74]), Paragraph("근무 환경", H3), p(c["work"]), PageBreak()]
        # 5 tech
        s += self.h2("04 기술", tech[0]["title"]) + [self.callout("요약", tech[0]["text"]), Spacer(1, 12), Paragraph("데이터와 요청의 흐름", H3), self.flow(c["flow"]), Spacer(1, 6), p(c["arch"]),
                                                    Paragraph("영역별 기술", H3), self.table(["영역", "기술", "쓰는 이유"], c["stack"], [0.2, 0.28, 0.52]), PageBreak()]
        # 6-8 projects
        for i, (doc, d) in enumerate(zip(projects, c["projects"], strict=True), start=1):
            s += self.h2(f"05 프로젝트 {i}", doc["title"]) + [self.callout("요약", doc["text"]), Paragraph("배경", H3), p(d["bg"]), Paragraph("접근 방식", H3)] + bullets(d["steps"])
            s += [Paragraph("성과 지표", H3), self.table(["지표", "현재", "목표"], d["metrics"], [0.5, 0.25, 0.25]), Paragraph("남은 과제", H3)] + bullets(d["next"]) + [PageBreak()]
        # 9 operations
        s += self.h2("06 운영 기준", tech[1]["title"]) + [self.callout("요약", tech[1]["text"]), Spacer(1, 8), p(c["ops_note"]), Paragraph("운영 기준표", H3),
                                                       self.table(["항목", "기준"], c["ops"], [0.34, 0.66]),
                                                       KeepTogether([Paragraph(esc(c["chart"]["title"]), H3), self.bars(c["chart"]), Paragraph("설명을 위해 지어낸 수치입니다.", SMALL)]), PageBreak()]
        # 10 posting
        s += self.h2("07 채용 안내", posting["title"]) + [p(c["role"]), self.table(["구분", "내용"], [["주요 업무", posting["duties"]], ["자격 요건", posting["requirements"]], ["우대 사항", posting["preferred"]], ["근무 조건", f"{posting.get('employment_type', '')} · {posting.get('experience_level', '')} · 서울(가상)"]], [0.18, 0.82]),
                                                       Paragraph("입사 후 90일", H3)] + [Paragraph(esc(i), st("li2", 10.2, 15.2, leftIndent=12, bulletIndent=2, spaceAfter=2), bulletText="•") for i in c["days"]] + [Paragraph("채용 절차", H3), self.flow(PROCESS, 44), Paragraph("자주 묻는 질문", H3)]
        s += [self.table(["질문", "답변"], c["faq"], [0.36, 0.64]), Spacer(1, 5), Paragraph("합격 여부는 AI가 아니라 담당자가 결정합니다.", SMALL)]
        return s


def _wrap(text: str, width: float, size: float) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if pdfmetrics.stringWidth(cur + ch, "Malgun", size) > width:
            lines.append(cur)
            cur = ch.strip()
        else:
            cur += ch
    return [*lines, cur]


def build(fact: dict, prof: dict) -> tuple[Path, int]:
    OUT.mkdir(parents=True, exist_ok=True)
    book = Book(fact, prof)
    path = OUT / f"{fact['name']}_회사자료.pdf"

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont("Malgun", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 11 * mm, NOTICE)
        canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, str(doc.page))
        if doc.page > 1:
            canvas.drawString(20 * mm, A4[1] - 12 * mm, fact["name"])
            canvas.setStrokeColor(LINE)
            canvas.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        canvas.restoreState()

    doc = BaseDocTemplate(str(path), pagesize=A4, title=f"{fact['name']} 회사 소개 자료", author="AI 채용 플랫폼 시연 자료", subject="가상 기업 자료")
    doc.addPageTemplates(PageTemplate(id="page", frames=[Frame(20 * mm, 16 * mm, WIDTH, A4[1] - 34 * mm, 0, 0, 0, 0)], onPage=decorate))
    doc.build(book.story())
    return path, doc.page


def main(keys: list[str]) -> int:
    facts = json.loads(FACTS.read_text(encoding="utf-8"))
    bad = 0
    for fact in facts:
        file = PROFILES / f"{fact['key']}.json"
        if (keys and fact["key"] not in keys) or not file.exists():
            continue
        path, pages = build(fact, json.loads(file.read_text(encoding="utf-8")))
        bad += pages != 10
        print(fact["key"], pages, "pages", path.name, flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
