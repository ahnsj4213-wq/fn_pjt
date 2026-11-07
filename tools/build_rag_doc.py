"""Build docs/design/llm-rag-orchestration.pdf (ReportLab, Malgun Gothic).

    python tools/build_rag_doc.py
"""
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent.parent / "docs" / "design" / "llm-rag-orchestration.pdf"
TITLE = "면접 질문은 어떻게 만들어지나: AI 모델과 회사 자료 검색 구조"

pdfmetrics.registerFont(TTFont("Malgun", r"C:\Windows\Fonts\malgun.ttf"))
pdfmetrics.registerFont(TTFont("MalgunBd", r"C:\Windows\Fonts\malgunbd.ttf"))
pdfmetrics.registerFontFamily("Malgun", normal="Malgun", bold="MalgunBd")

import math

BG = colors.HexColor("#0b1020")
PANEL = colors.HexColor("#121a30")
PANEL_ALT = colors.HexColor("#161f39")
HEAD = colors.HexColor("#1b2748")
INK = colors.HexColor("#e8edf7")
SOFT_INK = colors.HexColor("#d3dbee")
MUTED = colors.HexColor("#7f8bab")
LINE = colors.HexColor("#27324f")
ACCENT = colors.HexColor("#7db4ff")
TEAL = colors.HexColor("#5eead4")
VIOLET = colors.HexColor("#b69cff")
AMBER = colors.HexColor("#fbbf24")
PINK = colors.HexColor("#f472b6")
SLATE = colors.HexColor("#8b97b5")
SOFT = PANEL
WARN = colors.HexColor("#241d0e")
WARN_LINE = AMBER

BODY = ParagraphStyle("body", fontName="Malgun", fontSize=9.5, leading=15.5, textColor=SOFT_INK, alignment=TA_LEFT, spaceAfter=4)
H1 = ParagraphStyle("h1", parent=BODY, fontName="MalgunBd", fontSize=15, leading=21, textColor=ACCENT, spaceBefore=14, spaceAfter=7, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=BODY, fontName="MalgunBd", fontSize=11, leading=16, textColor=INK, spaceBefore=8, spaceAfter=3, keepWithNext=1)
EYEBROW = ParagraphStyle("eyebrow", parent=BODY, fontName="MalgunBd", fontSize=8.5, leading=12, textColor=TEAL, spaceAfter=4)
TITLE_STYLE = ParagraphStyle("title", parent=BODY, fontName="MalgunBd", fontSize=22, leading=30, textColor=INK, spaceAfter=6)
SUB = ParagraphStyle("sub", parent=BODY, fontSize=8.5, textColor=MUTED, spaceAfter=10)
CAP = ParagraphStyle("cap", parent=BODY, fontSize=8.3, leading=12.5, textColor=MUTED, spaceBefore=3, spaceAfter=9)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.6, leading=13, textColor=SOFT_INK, spaceAfter=0)
CELL_B = ParagraphStyle("cellb", parent=CELL, fontName="MalgunBd", fontSize=8.3, textColor=ACCENT)
CODE = ParagraphStyle("code", parent=BODY, fontSize=8, leading=12.5, textColor=TEAL, backColor=colors.HexColor("#0e1730"),
                      borderColor=LINE, borderWidth=0.6, borderPadding=8, borderRadius=4, spaceBefore=6, spaceAfter=12)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2, bulletColor=ACCENT)


def P(text, style=BODY):
    return Paragraph(text, style)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths, header=True):
    data = [[Paragraph(str(c), CELL_B if (header and i == 0) else CELL) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    start = 1 if header else 0
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("ROWBACKGROUNDS", (0, start), (-1, -1), [PANEL, PANEL_ALT]),
             ("LINEBELOW", (0, start), (-1, -1), 0.3, LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
             ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
             ("ROUNDEDCORNERS", [5, 5, 5, 5])]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), HEAD), ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT)]
    t.setStyle(TableStyle(style))
    return t


def callout(title, text, bg=WARN, edge=WARN_LINE, width=515):
    if bg is SOFT:
        bg, edge = PANEL_ALT, ACCENT
    head = ParagraphStyle("ch", parent=CELL, fontName="MalgunBd", textColor=edge, fontSize=9)
    t = Table([[Paragraph(title, head)], [Paragraph(text, CELL)]], colWidths=[width])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, edge),
                           ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                           ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                           ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                           ("ROUNDEDCORNERS", [5, 5, 5, 5])]))
    return t


def box(d, x, y, w, h, text, tone=ACCENT, size=8.2):
    d.add(Rect(x, y, w, h, rx=6, ry=6, fillColor=PANEL_ALT, strokeColor=tone, strokeWidth=0.9))
    lines = text.split("\n")
    lead = size + 3
    top = y + h / 2 + (len(lines) - 1) * lead / 2 - size * 0.35 - 1
    for i, ln in enumerate(lines):
        d.add(String(x + w / 2, top - i * lead, ln, textAnchor="middle", fontName="MalgunBd",
                     fontSize=size, fillColor=INK))


def seg(d, x1, y1, x2, y2):
    d.add(Line(x1, y1, x2, y2, strokeColor=SLATE, strokeWidth=1.0))


def arrow(d, x1, y1, x2, y2, label=None, via=None):
    pts = [(x1, y1)] + (via or []) + [(x2, y2)]
    for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
        seg(d, ax, ay, bx, by)
    (px, py), (qx, qy) = pts[-2], pts[-1]
    a = math.atan2(qy - py, qx - px)
    s = 5
    d.add(Polygon([qx, qy, qx - s * math.cos(a - 0.42), qy - s * math.sin(a - 0.42), qx - s * math.cos(a + 0.42), qy - s * math.sin(a + 0.42)],
                  fillColor=SLATE, strokeColor=SLATE, strokeWidth=0.4))
    if label:
        d.add(String((x1 + x2) / 2, (y1 + y2) / 2 + 4, label, textAnchor="middle", fontName="Malgun", fontSize=7.5, fillColor=MUTED))


def flow(labels, height=50, width=515, size=8, gap=15):
    n = len(labels)
    w = (width - gap * (n - 1)) / n
    d = Drawing(width, height)
    for i, t in enumerate(labels):
        x = i * (w + gap)
        box(d, x, 3, w, height - 6, t, tone=ACCENT, size=size)
        if i < n - 1:
            arrow(d, x + w + 2, height / 2, x + w + gap - 1, height / 2)
    return d


def fig_overview():
    def card(title, tone, rows):
        data = [[Paragraph(title, ParagraphStyle("ct", parent=CELL, fontName="MalgunBd", fontSize=9.5, textColor=tone)), ""]]
        lab = ParagraphStyle("lab", parent=CELL, textColor=MUTED, fontSize=8.2)
        for k, v in rows:
            data.append([Paragraph(k, lab), Paragraph(v, CELL)])
        t = Table(data, colWidths=[58, 184])
        t.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (-1, -1), PANEL_ALT),
                               ("LINEABOVE", (0, 0), (-1, 0), 0.8, tone), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                               ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                               ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                               ("LINEBELOW", (0, 1), (-1, -2), 0.3, LINE), ("ROUNDEDCORNERS", [5, 5, 5, 5])]))
        return t
    a = card("① 면접 전 · 본질문 10개를 미리 만든다", ACCENT, [
        ("언제", "지원자가 면접 준비를 요청한 직후"),
        ("누가", "백그라운드 작업 서버(워커)"),
        ("AI 작업", "질문 생성, 이력서 질문"),
        ("자료 검색", "회사 자료와 참고 질문"),
        ("결과", "고정된 질문 10개와 미리 만든 음성"),
        ("걸리는 시간", "한 세트에 45~96초<br/>(지원자는 대기 화면을 본다)")])
    b = card("② 면접 중 · 답변마다 꼬리질문을 판단한다", TEAL, [
        ("언제", "지원자가 답변을 확정한 직후"),
        ("누가", "API 서버"),
        ("AI 작업", "꼬리질문 판단"),
        ("자료 검색", "답변과 가까운 회사 자료 2건"),
        ("결과", "본질문당 최대 1개,<br/>면접 전체로는 설정한 한도까지"),
        ("걸리는 시간", "3.1~9.4초<br/>(지원자는 대기 메시지를 본다)")])
    t = Table([[a, b]], colWidths=[259, 256])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return t


def fig_arch():
    W, H = 515, 250
    d = Drawing(W, H)
    box(d, 175, 212, 165, 32, "브라우저\n음성·텍스트로 답변", tone=ACCENT, size=8)
    box(d, 10, 138, 205, 46, "API 서버 (FastAPI)\n면접 진행 · 꼬리질문 판단", tone=ACCENT, size=8.2)
    box(d, 300, 138, 205, 46, "워커 (Celery)\n본질문 만들기 · 이력서 질문", tone=ACCENT, size=8.2)
    arrow(d, 257, 212, 112, 185, via=[(257, 198), (112, 198)])
    arrow(d, 216, 161, 299, 161, label="작업 등록")
    grp = Rect(0, 4, W, 108, rx=8, ry=8, fillColor=None, strokeColor=LINE, strokeWidth=0.8)
    grp.strokeDashArray = [3, 3]
    d.add(grp)
    d.add(String(12, 97, "공용 서버 (API와 워커가 함께 사용)", fontName="MalgunBd", fontSize=7.8, fillColor=MUTED))
    box(d, 12, 14, 152, 70, "AI 모델 서버\nllama.cpp\nqwen3.5:4b · GPU", tone=ACCENT, size=7.8)
    box(d, 182, 14, 152, 70, "음성·임베딩 서버 (ai)\n임베딩 BGE-M3 (CPU)\n음성 인식 Whisper (GPU)\n음성 합성 Supertonic (CPU)", tone=ACCENT, size=7.2)
    box(d, 352, 14, 152, 70, "PostgreSQL + pgvector\n회사 자료 · 참고 질문\n질문은행 · 진행 상태", tone=ACCENT, size=7.6)
    arrow(d, 112, 138, 112, 114)
    arrow(d, 403, 138, 403, 114)
    return d


def fig_compose():
    W, H = 515, 190
    d = Drawing(W, H)
    box(d, 175, 150, 165, 34, "① 계획\n유형별 개수 정하기", tone=ACCENT, size=8)
    cols = [("인성·협업 3개\n질문은행에서 고르기\nAI는 쓰지 않음", 0, ACCENT), ("지원 동기 1개\nAI + 회사 자료", 130, ACCENT),
            ("직무 3개\nAI + 공고 + 회사 자료", 260, ACCENT), ("경험 3개\nAI + 이력서", 390, ACCENT)]
    seg(d, 257, 150, 257, 134)
    seg(d, 62, 134, 452, 134)
    for text, x, tone in cols:
        arrow(d, x + 62, 134, x + 62, 118)
        box(d, x, 62, 125, 56, text, tone=tone, size=7.7)
        seg(d, x + 62, 62, x + 62, 46)
    seg(d, 62, 46, 452, 46)
    arrow(d, 257, 46, 257, 36)
    box(d, 135, 2, 245, 34, "② 개수·중복 검사  →  ③ 한 번에 저장", tone=ACCENT, size=8)
    return d


def fig_followup():
    W, H = 515, 118
    d = Drawing(W, H)
    labels = ["질문 제시", "질문 음성 재생", "답변 대기\n여기서 멈춤", "음성을 글자로\n답변 확정", "꼬리질문 판단"]
    n, gap = len(labels), 16
    w = (W - gap * (n - 1)) / n
    for i, t in enumerate(labels):
        x = i * (w + gap)
        box(d, x, 66, w, 46, t, tone=ACCENT, size=7.8)
        if i < n - 1:
            arrow(d, x + w + 2, 89, x + w + gap - 1, 89)
    cx = (n - 1) * (w + gap) + w / 2
    seg(d, cx, 66, cx, 50)
    seg(d, 175, 50, cx, 50)
    arrow(d, 175, 50, 175, 38)
    arrow(d, cx, 50, cx, 38)
    box(d, 60, 2, 230, 36, "질문이 남았으면\n꼬리질문 또는 다음 본질문을 제시", tone=ACCENT, size=7.6)
    box(d, 315, 2, 200, 36, "모두 끝나면\n코딩 테스트 또는 제출·평가", tone=ACCENT, size=7.6)
    return d


def fig_call():
    return flow(["① 사전 점검\n규칙으로 먼저\n판단", "② 프롬프트\n조립", "③ 호출 대기\n우선순위·기한", "④ AI 모델\n정해진 JSON\n형식으로 답", "⑤ 검증\n인용을 원문과\n대조", "⑥ 결과 사용\n통과한 것만"], height=62, size=7.6)


def fig_rag():
    return flow(["검색할 글\n공고·이력서\n·답변", "숫자로 변환\n앞 1000자\nCPU", "범위 고정\n정확 검색", "유사도 0.5\n미만은 버림", "프롬프트에\n자료 삽입", "AI 인용을\n원문과 대조", "출처 id 기록"], height=62, size=7.2, gap=11)



# ---------------------------------------------------------------- story
S = []
S += [P("AI 채용 플랫폼 · 설계 문서", EYEBROW), P(TITLE, TITLE_STYLE),
      P("AI 채용 플랫폼 · 2026-11-05 · 현재 코드 기준 (ADR-0005, 0016, 0019, 0020) · 커밋 전 작업 트리 기준", SUB)]

S += [P("한눈에 보기", H1),
      P("면접 질문은 <b>두 번</b> 만들어진다. 본질문 10개는 면접 전에 서버가 미리 만들어 고정한다. 꼬리질문은 지원자가 답변을 확정할 때마다 그 자리에서 판단한다."),
      P("질문을 만드는 순서는 <b>코드가 정한다</b>. AI 모델은 주어진 단계 안에서 질문 내용만 쓰고, 다음에 무엇을 할지는 고르지 않는다. "
        "AI가 쓴 인용문은 코드가 원문과 대조해 통과한 것만 쓴다."),
      P("회사 자료는 <b>검색</b>으로 찾아 AI에게 참고 자료로 준다. 검색 범위는 해당 회사와 공고로 고정한다. "
        "모든 AI 처리는 사내 서버에서 실행하며 외부 AI API는 호출하지 않는다."),
      Spacer(1, 4), fig_overview(),
      P("그림 1. 질문이 만들어지는 두 시점. 걸리는 시간은 실제로 잰 값이며 8장에 자세히 있다.", CAP)]

S += [P("먼저 알아둘 말", H2),
      table([["용어", "뜻"],
             ["본질문 / 꼬리질문", "본질문은 면접에서 정해진 10개 질문이다. 꼬리질문은 지원자의 답변을 보고 이어서 묻는 질문이다."],
             ["회사 자료 검색", "회사 소개와 업무 설명 같은 문서에서 지금 질문과 관련된 부분을 찾아 AI에게 참고용으로 주는 방식이다(RAG)."],
             ["임베딩", "글을 숫자 목록으로 바꿔 뜻이 비슷한 글끼리 가깝게 놓이게 하는 방법이다. 검색에 쓴다."],
             ["유사도", "두 글이 얼마나 비슷한지를 0~1로 나타낸 값이다. 1에 가까울수록 비슷하다."],
             ["인용 검증", "AI가 '근거'라며 가져온 문장이 실제 이력서·답변·자료에 글자 그대로 있는지 코드가 확인하는 일이다."],
             ["워커", "화면 요청과 별개로 뒤에서 오래 걸리는 일을 처리하는 서버다."],
             ["LangGraph", "여러 단계를 정해진 순서와 분기로 실행하고 각 단계의 진행 상태를 저장하는 라이브러리다."]],
            [105, 410])]

S += [KeepTogether([P("1. 어떤 서버가 관여하나", H1), Spacer(1, 2), fig_arch(),
                    P("그림 2. 질문 생성에 관여하는 서버(컨테이너). 모두 사내에서 실행한다. API와 워커는 모두 아래 세 서버를 쓴다. "
                      "API의 음성 처리는 실시간 인식·합성이고, 워커의 음성 처리는 질문 음성을 미리 합성하는 것이다.", CAP)]),
      table([["서버", "하는 일", "비고"],
             ["API", "면접 진행 단계 실행, 꼬리질문 판단 호출", "답변을 기다리는 동안 해당 단계에서 멈춰 있는다"],
             ["워커", "본질문 만들기 실행, 질문 생성·이력서 질문 호출, 회사 자료 임베딩 저장", "작업이 실패해 다시 실행되면 멈춘 단계부터 이어서 한다"],
             ["AI 모델 서버", "질문 생성과 판단을 하는 언어 모델 실행(llama.cpp, 문맥 16384, 동시 처리 2개)", "모델은 qwen3.5:4b"],
             ["음성·임베딩 서버", "임베딩, 음성 인식, 음성 합성", "임베딩 BGE-M3(CPU, 1024차원), 음성 인식 Whisper large-v3-turbo(GPU), 음성 합성 Supertonic(CPU). 기본 설정 기준"],
             ["PostgreSQL", "질문과 근거 저장, 유사 문서 검색(pgvector), 진행 상태 저장", "회사 자료·참고 질문·질문은행이 같은 DB에 있다"]],
            [85, 250, 180])]

S += [P("2. 본질문 10개 만들기 (면접 전)", H1), fig_compose(),
      P("그림 3. 본질문 만들기 단계. 파일: app/questions/graph.py, compose.py", CAP),
      P("지원자가 면접 준비를 요청하면 '질문 만들기' 작업이 등록되고, 워커가 위 단계를 한 번 실행한다. "
        "단계마다 결과를 저장하므로 작업이 다시 실행돼도 <b>멈춘 단계부터 이어가며 AI를 다시 부르지 않는다</b>. "
        "면접을 시작할 때 공고 설정이 바뀌어 있으면 질문을 다시 만든다. 질문 저장이 끝나면 질문 음성을 미리 만드는 작업을 등록한다."),
      P("기본 구성은 인성 2, 지원 동기 1, 직무 3, 경험 3, 협업 1이다. 공고 설정에서 슬롯 구성을 바꿀 수 있다. "
        "담당자가 문장을 직접 쓴 슬롯은 AI와 질문은행을 거치지 않고 그대로 묻는다. 슬롯마다 어떤 출처를 먼저 쓸지는 코드가 정한다."),
      table([["슬롯", "개수", "1순위 출처", "AI를 쓸 수 없을 때", "AI가 하는 일"],
             ["인성·협업", "3", "질문은행 (첫 인성 질문은 고정 자기소개)", "대체 없음. 질문이 부족하면 만들기 실패", "쓰지 않음"],
             ["지원 동기", "1", "AI + 회사 자료", "자료가 없으면 공고 기반 직무 질문으로 바꾼다. AI 생성이 실패하면 회사 질문은행, 그다음 공용 기본 질문", "어떤 문서를 물을지 고른다. 문장은 코드가 정해 둔 틀을 쓴다"],
             ["직무", "3", "AI + 공고 + 회사 자료", "직무 질문은행, 그다음 공용 기본 질문", "문장을 쓴다. 코드가 검증한다"],
             ["경험", "3", "AI + 이력서 스냅샷", "직무 질문은행(관련도 순), 그다음 공용 기본 질문", "묻고 싶은 이력서 항목을 고른다. 문장은 코드가 조립한다"]],
            [52, 32, 110, 175, 146]),
      Spacer(1, 6),
      callout("왜 문장을 코드가 만드나",
              "작은 모델이 쓴 문장은 '예·아니오'로 답하는 질문이 되거나 이력서에 없는 사실을 덧붙이는 일이 있었다. "
              "그래서 동기와 경험 슬롯에서는 AI가 <b>무엇을 물을지만 고르고</b>, 그 선택을 검증한 뒤 코드가 정해 둔 문장 틀에 넣는다. "
              "예: “이 회사의 ‘정산 배치 재설계’ 업무에서 어떤 점에 관심이 있고, 본인의 어떤 경험을 살릴 수 있다고 보십니까?”", bg=SOFT, edge=ACCENT),
      Spacer(1, 6),
      callout("변경 이력",
              "지원 동기와 직무 슬롯은 원래 질문은행을 먼저 썼다(요구사항 QST-01). 회사 자료 기반 개인화를 필수로 확정하면서 "
              "<b>AI 생성을 먼저 쓰는 순서로 바꿨고</b>, 이 변경은 ADR-0020에 기록했다. "
              "생성한 질문 중 공고나 회사 소개가 근거인 질문만 질문은행에 저장한다. 회사 자료가 근거인 질문은 저장하지 않는다. "
              "한 공고에만 있는 자료가 질문은행을 거쳐 다른 공고에 새는 것을 막기 위해서다.")]

S += [P("3. 꼬리질문 판단하기 (면접 중)", H1), fig_followup(),
      P("그림 4. 면접 진행 단계. 파일: app/interview/graph.py, turns.py", CAP),
      P("진행은 '답변 대기' 단계에서 멈추고, 그때의 상태가 저장된다. 연결이 끊겼다가 다시 이어지면 답변 중이던 질문을 <b>처음부터 다시</b> 보여 준다. "
        "음성 모드와 텍스트 모드는 같은 단계를 쓰고, 입출력의 차이만 따로 처리한다. "
        "음성 모드는 한 번의 실행이 다음 질문까지 이어진다. 텍스트 모드는 꼬리질문 판단까지 실행하고 멈춘 뒤, 화면이 다음 질문을 따로 요청한다."),
      P("본질문의 답변이 확정되면 꼬리질문 판단 AI를 부른다. 꼬리질문에 대한 답변 뒤에는 부르지 않는다. "
        "AI를 부르기 전에 코드 규칙으로 먼저 걸러낸다."),
      P("<b>AI를 부르지 않고 건너뛰는 경우</b>", BODY)]
S += bullets(["이미 꼬리질문을 한 본질문", "면접 전체 꼬리질문 한도를 다 쓴 경우 (공고 설정 0~4개, 기본 2개)", "답변이 없거나 내용이 없는 경우"])
S += [P("<b>AI에게 주는 정보</b>", BODY)]
S += bullets(["본질문과 지금 답변", "이력서 근거 문장 (이력서 기반 본질문일 때)", "공고 요약과, 공고와 이력서에 공통으로 나오는 항목",
              "직전 답변 2개 (각 200자까지)", "이미 한 꼬리질문", "답변과 가까운 회사 자료 2건"])
S += [P("<b>시간 제한과 결과</b>", BODY)]
S += bullets(["제한 시간은 답변 확정 후 <b>30초</b>다. 모델이 멈췄을 때만 쓰는 안전장치다(ADR-0019). PRD 6.2의 '5초'와 충돌하며 변경 근거를 ADR에 기록했다.",
              "AI가 꼬리질문을 하라고 하면 질문을 저장하고 그 질문을 낸다.",
              "하지 않기로 하면 사유를 기록하고 다음 본질문으로 간다.",
              "AI 호출이 실패하거나 시간이 지나도 면접은 멈추지 않고 꼬리질문 없이 넘어간다."])

S += [P("4. AI 호출은 이렇게 처리한다", H1), fig_call(),
      P("그림 5. AI 호출 한 건의 처리 순서. 검증에 실패하면 실패 사유를 덧붙여 다시 요청한다(작업별 횟수 제한). "
        "파일: app/ai/tasks/base.py, app/ai/direct.py, app/aisvc/gateway.py", CAP),
      P("AI 호출은 모두 '작업' 단위다. 작업마다 프롬프트, 출력 형식, 검증 방법, 재시도 횟수가 정해져 있다. "
        "질문 생성과 이력서 질문에는 프롬프트 버전을 함께 저장한다. 꼬리질문에는 버전 없이 작업 이름(followup_decide)만 저장한다.")]
S += bullets(["사전 점검에서 끝나면 AI를 부르지 않는다. (예: 꼬리질문 한도 소진, 내용 없는 답변)",
              "재시도까지 실패하면 '검증 실패'나 빈 결과를 돌려주고, 호출한 쪽이 대체 경로를 탄다. <b>검증을 거치지 않은 AI 출력은 쓰지 않는다.</b>",
              "이력서·답변 같은 입력 원문은 무작위 구분자로 감싼다. 그 안에 들어 있는 지시문을 AI가 명령으로 오해하지 않게 하기 위해서다."])
S += [Spacer(1, 4), table([
    ["AI 작업", "하는 일", "우선순위·기한", "재시도", "코드가 확인하는 것"],
    ["질문 생성<br/>(question_generate)", "지원 동기·직무 질문을 쓴다", "면접 준비용 중간 순위<br/>120초", "형식 오류 2회<br/>검증 실패 시 다시 생성 1회",
     "인용이 출처에 실제로 있음 · 질문이 인용과 내용을 공유함 · 출처에 없는 기술명·숫자 금지 · 지원 동기는 회사 자료 인용 필수 · 회사 자료 기반 질문은 지원자의 판단·경험을 물을 것 · 참고 질문 베끼기 금지"],
    ["이력서 질문<br/>(resume_question)", "이력서 항목을 골라 경험 질문을 만든다", "면접 준비용 중간 순위<br/>120초", "형식 오류 2회<br/>검증 실패 시 다시 생성 1회",
     "인용이 이력서 값에 있음 · 질문이 인용과 내용을 공유함 · 사적인 질문 금지 · 예·아니오 질문은 다시 요청(마지막 시도에서는 통과시키되 문장은 코드가 조립)"],
    ["꼬리질문 판단<br/>(followup_decide)", "꼬리질문을 할지, 한다면 무엇을 물을지 정한다", "실시간(가장 높음)<br/>답변 확정 후 30초", "형식 오류 1회<br/>인용 재요청 1회",
     "앵커(질문의 근거 문장)가 답변·이력서·앞선 답변에 글자 그대로 있음 · 새 주제 금지 · 출처에 없는 기술명·숫자 금지 · 이미 한 꼬리질문 반복 금지"]],
    [110, 80, 72, 78, 175]),
    Spacer(1, 4),
    P("생성 설정(무작위성 · 최대 길이): 질문 생성 0.7 · 400토큰, 이력서 질문 0.5 · 500토큰, 꼬리질문 판단 0.3 · 160토큰.", CAP),
    P("<b>우선순위</b>", H2),
    P("호출 대기열은 우선순위가 있다. 높은 순위 호출이 오면 낮은 순위 호출을 중간에 끊고 대기열 앞으로 돌려놓는다. "
      "지금은 대기열을 두 개로 나눠 두었다. 하나는 실시간·면접 준비용이고 다른 하나는 평가용이다. 그래서 끊는 동작은 실시간(꼬리질문)과 면접 준비용(본질문) 사이에서만 일어난다. "
      "답변 평가는 평가용 대기열에 있어서 꼬리질문 호출이 끊지 않는다."),
    callout("구조상의 한계",
            "대기열은 호출한 프로세스 안에 있다. 그래서 우선순위 조정은 <b>같은 프로세스 안에서만</b> 적용된다. "
            "워커의 질문 생성·이력서 질문과 API의 꼬리질문 판단은 서로를 끊지 못하고 AI 서버의 동시 처리 칸 2개를 나눠 쓴다. "
            "여러 면접이 동시에 진행될 때 꼬리질문 판단이 얼마나 느려지는지는 측정하지 않았다.")]

S += [P("5. 회사 자료는 이렇게 검색한다", H1), fig_rag(),
      P("그림 6. 회사 자료 검색 한 번의 흐름. 파일: app/questions/knowledge.py", CAP),
      P("<b>자료가 0건일 때</b>: 자료 없이 그대로 진행한다. 임베딩 서버 장애도 같은 방식으로 처리하며 면접은 막지 않는다. "
        "지원 동기 슬롯이면 회사 소개도 쓰지 않고 공고 기반 직무 질문으로 바꾼다."),
      P("검색 대상은 두 가지이고 용도가 다르다.", BODY),
      table([["", "회사 자료 (company_knowledge)", "참고 질문 (question_examples)"],
             ["용도", "질문의 사실 근거. 인용할 수 있다", "말투와 난이도 예시. 사실 근거로 쓰면 안 된다"],
             ["검색 범위", "회사 + 공고 (SQL 조건으로 강제)", "질문 종류 (직무·회사·이력서)"],
             ["규모", "가상 기업 12곳, 문서 60개", "5,859건"],
             ["검색 방식", "정확 검색 (회사별로 후보를 줄인 뒤 거리 계산)", "근사 검색(HNSW 색인)"],
             ["검증", "인용이 원문에 있어야 통과", "코사인 유사도 0.9 이상이거나 단어가 80% 이상 겹치면 베낀 것으로 보고 탈락"]],
            [70, 225, 220]),
      Spacer(1, 6),
      P("유사도는 BGE-M3로 잰 코사인 값이다. 다른 임베딩 모델과 비교해 본 적은 없다.", CAP),
      table([["시점", "AI 작업", "검색할 글", "회사 자료 개수", "참고 질문 개수", "이번 측정의 유사도 중앙값"],
             ["면접 전", "질문 생성 (지원 동기)", "공고 제목 + 회사 소개 + 업무", "4", "3", "0.578"],
             ["면접 전", "질문 생성 (직무)", "공고 요약", "4", "3", "0.613"],
             ["면접 전", "이력서 질문", "이력서 표준 항목", "4 (공통 항목 계산용)", "3", "0.490"],
             ["면접 중", "꼬리질문 판단", "지금 답변", "2", "없음", "0.484"]],
            [48, 100, 125, 90, 60, 92]),
      Spacer(1, 6)]
S += bullets(["<b>회사·공고 범위 고정</b>: 검색 함수는 회사와 공고 없이는 실행되지 않는다. 다른 회사의 공고를 가리키는 문서는 DB의 복합 외래키가 거부한다.",
              "<b>색인을 쓰지 않는 이유</b>: 근사 색인은 범위 조건을 나중에 적용하기 때문에 해당 회사의 문서를 놓칠 수 있다. 회사별 문서가 적어 정확 검색으로 충분하다.",
              "<b>이력서 질문에서 회사 자료의 역할</b>: 프롬프트에 자료 원문을 넣지 않는다. 공고·자료와 이력서에 공통으로 나오는 항목만 계산해 AI가 그 항목을 먼저 고르게 한다.",
              "<b>로그</b>: 검색어와 본문은 남기지 않고 id, 건수, 유사도만 남긴다(SEC-08)."])

S += [P("6. 질문의 근거를 남기는 방법", H1),
      P("모든 질문의 근거는 interview_questions.grounding에 저장한다. <b>sources</b>는 질문에 실제로 쓴 근거이고, <b>retrieved</b>는 검색되어 AI에게 전달된 문서다. "
        "둘을 나누는 이유는 검색됐다고 해서 질문에 쓰인 것은 아니기 때문이다. 아래는 질문 생성 결과의 예이고, 이력서 질문에서는 evidence, 꼬리질문에서는 anchor에 인용이 들어간다. "
        "질문은행 질문과 담당자가 직접 쓴 질문에는 sources만 있다."),
      P('{ "basis":     { "start": 0, "end": 11, "text": "가맹점별 파티션 처리" },   // 원문과 대조를 통과한 인용<br/>'
        '&nbsp;&nbsp;"sources":   [ { "type": "KNOWLEDGE", "id": "&lt;문서 조각 id&gt;" } ],   // 실제로 쓴 근거<br/>'
        '&nbsp;&nbsp;"retrieved": [ "&lt;조각 id&gt;", "&lt;조각 id&gt;", "&lt;조각 id&gt;", "&lt;조각 id&gt;" ] }   // 검색된 문서', CODE),
      table([["type", "id가 가리키는 것", "쓰이는 곳"],
             ["KNOWLEDGE", "회사 자료 조각", "질문 생성, 꼬리질문 판단"],
             ["POSTING / COMPANY_INTRO", "공고 / 회사 소개", "질문 생성"],
             ["RESUME_SNAPSHOT", "지원 건 (이력서 질문은 field로 이력서 항목을 지정)", "이력서 질문, 꼬리질문 판단"],
             ["ANSWER", "면접 답변", "꼬리질문 판단"],
             ["BANK", "질문은행의 질문", "은행에서 가져온 질문"],
             ["POSTING_SETTINGS", "공고 설정 버전", "회사가 직접 쓴 질문"]],
            [140, 245, 130]),
      Spacer(1, 4),
      P("문서를 지워도 이미 만든 질문은 그대로이고 인용 문장도 남는다. 질문 스냅샷은 바꿀 수 없다. 근거 정보는 지원자용 API로 내보내지 않는다.")]

S += [P("7. 답변 평가와의 경계", H1)]
S += bullets(["회사 자료는 질문 생성에만 쓴다. 답변 평가 프롬프트에는 들어가지 않는다.",
              "이번 작업에서 평가 루브릭, 인용 검증, 서버 측 점수 계산은 바꾸지 않았다. AI는 합격·불합격을 결정하거나 추천하지 않는다.",
              "이번 작업에서 음성 인식·음성 합성·AI 모델 처리 구조는 바꾸지 않았다."])

S += [P("8. 실제로 재 본 결과", H1),
      P("실제 모델과 실행 중인 서비스로 측정했다(임베딩 BGE-M3, AI 모델 qwen3.5:4b). 도구는 tools/eval_rag.py다. "
        "답변은 미리 준비한 고정 문장이고, 면접은 텍스트 모드로 진행했다."),
      table([["항목", "결과", "조건과 해석"],
             ["회사 안 검색 정확도", "1순위 적중 100% (36/36)", "평가용 질의를 문서 작성자가 만들어 쉬운 조건이다. 상한에 가까운 값으로 봐야 한다"],
             ["범위 위반", "0건", "12개 세트와 면접의 모든 근거 id를 회사·공고 범위와 대조했다"],
             ["지원 동기·직무 질문의 회사 자료 근거", "46/48", "유사도 하한을 0.5로 올리기 전의 12개 세트"],
             ["경험 질문의 이력서 근거", "24/36", "같은 12개 세트. 0개인 세트가 2건. 이전 실행은 33/36"],
             ["자료 없는 회사", "공고 기반으로 대체됨 (1건)", "지어낸 회사 사실은 없었다(사람이 읽고 확인)"],
             ["본질문 한 세트 생성 시간", "45~96초", "12개 회사, 2회 실행"],
             ["질문 생성 / 이력서 질문 호출 시간", "중앙값 6.9초 / 19.0초", "각각 32건 / 16건. 재생성 포함"],
             ["꼬리질문 판단 호출 시간", "3.1~9.4초", "마지막 코드 면접 3건, 호출 10건"],
             ["꼬리질문 판단 결과", "질문함 6, 검증 실패 4", "같은 호출 10건"],
             ["꼬리질문의 회사 자료 연결", "1/6", "나머지 5건은 답변만 근거로 삼았다"]],
            [150, 130, 235]),
      Spacer(1, 6), P("확인된 문제", H2)]
S += bullets(["<b>꼬리질문 판단의 검증 실패 비율이 높다</b>(10건 중 4건). 대부분 첫 내용 답변에서 났고 재요청까지 실패했다. 고정 답변이 질문과 맞지 않은 탓일 수 있으나 원인은 확인하지 못했다.",
              "<b>이력서 질문이 검증을 통과하지 못하는 경우가 있다.</b> AI 응답 상태는 모두 정상이었으므로 검증 단계에서 탈락한 것이다. 2개 세트는 경험 질문이 전부 질문은행 질문으로 대체됐다. 원인은 확인하지 못했다.",
              "<b>꼬리질문이 잘못된 전제를 둘 수 있다.</b> “재순위화를 … 결제 정산 시스템에 적용할 때 어떤 어려움이 있었나요?”는 지원자가 하지 않은 일을 전제로 한다. 코드 검증으로 걸러지지 않았다.",
              "<b>직무 질문의 문장 품질이 고르지 않다.</b> “사진 유사도 분석을 위해 OpenSearch 텍스트 검색을 어떻게…”처럼 자료의 단어를 어색하게 엮은 질문이 있다. 지나치게 길거나 숫자 사이에 공백이 생기는 경우도 있다.",
              "<b>지연 목표 미달.</b> 꼬리질문 판단 호출만 최대 9.4초가 걸려 PRD 8.2의 '답변 종료 후 다음 질문까지 5초'를 맞추지 못한다."])
S += [P("아직 확인하지 않은 것", H2)]
S += bullets(["음성 경로(WebRTC·음성 인식·음성 합성)로 진행한 전체 면접과 브라우저 화면",
              "실제 기업 문서에서의 검색 정확도. 지금은 가상 문서만 있다.",
              "여러 면접이 동시에 진행될 때의 지연",
              "질문 품질의 합격 기준. 지금은 사람이 읽고 판단한 기록뿐이다."])
S += [P("이번 범위에서 제외한 것", H2)]
S += bullets(["지식베이스(회사 자료) 관리 화면. 담당자 API만 있다(/api/recruiter/knowledge).",
              "실제 기업 자료의 이용 허락과 기밀 등급(OQ-41)."])


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG)
    canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, A4[1] - 3, A4[0], 3, stroke=0, fill=1)
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(40, 36, A4[0] - 40, 36)
    canvas.setFont("Malgun", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(40, 24, TITLE)
    canvas.drawRightString(A4[0] - 40, 24, str(doc.page))
    canvas.restoreState()


def main():
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=42,
                            title=TITLE, author="AI 채용 플랫폼 팀")
    doc.build(S, onFirstPage=footer, onLaterPages=footer)
    print(OUT)


if __name__ == "__main__":
    main()
