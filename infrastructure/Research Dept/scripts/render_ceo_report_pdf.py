#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


TITLE_RE = re.compile(r"^\s*#\s+(.*)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
UNORDERED_RE = re.compile(r"^-\s+(.*)")
TABLE_RE = re.compile(r"^\|")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
SOURCE_REF_RE = re.compile(r"\[(SRC-[A-Z0-9-]+)\]")

ARIAL = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("ArialUnicode", ARIAL))
    pdfmetrics.registerFont(TTFont("ArialBold", ARIAL_BOLD))


def escape_text(text: str, source_map: dict[str, tuple[str, str]]) -> str:
    parts: list[str] = []
    code_tokens: list[str] = []
    cursor = 0

    for match in INLINE_CODE_RE.finditer(text):
        plain = text[cursor:match.start()]
        parts.append(_escape_noncode_text(plain, source_map))
        token = f"__CODE_TOKEN_{len(code_tokens)}__"
        code_content = (
            match.group(1)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        code_tokens.append(f"<font face='Courier'>{code_content}</font>")
        parts.append(token)
        cursor = match.end()

    parts.append(_escape_noncode_text(text[cursor:], source_map))
    result = "".join(parts)
    for idx, replacement in enumerate(code_tokens):
        result = result.replace(f"__CODE_TOKEN_{idx}__", replacement)
    return result


def _escape_noncode_text(text: str, source_map: dict[str, tuple[str, str]]) -> str:
    parts: list[str] = []
    cursor = 0
    combined_re = re.compile(r"\[(SRC-[A-Z0-9-]+)\]|\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s<)]+)")

    for match in combined_re.finditer(text):
        if match.start() > cursor:
            parts.append(
                text[cursor:match.start()]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        if match.group(1):
            src_id = match.group(1)
            if src_id in source_map:
                label_raw, url_raw = source_map[src_id]
                label = (
                    label_raw
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                url = (
                    url_raw
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                parts.append(f"<link href='{url}' color='#8c4f2b'><u>{label}</u></link>")
            else:
                parts.append(
                    match.group(0)
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
        elif match.group(2) and match.group(3):
            label = (
                match.group(2)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            url = (
                match.group(3)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            parts.append(f"<link href='{url}' color='#8c4f2b'><u>{label}</u></link>")
        else:
            url_raw = match.group(4)
            url = (
                url_raw
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            label = url
            parts.append(f"<link href='{url}' color='#8c4f2b'><u>{label}</u></link>")

        cursor = match.end()

    if cursor < len(text):
        parts.append(
            text[cursor:]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    return "".join(parts)


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="ArialBold",
            fontSize=22,
            leading=28,
            textColor=colors.HexColor("#1f1c17"),
            alignment=TA_LEFT,
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubTitle",
            parent=styles["Normal"],
            fontName="ArialUnicode",
            fontSize=11,
            leading=15,
            textColor=colors.HexColor("#6b6258"),
            spaceAfter=18,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            fontName="ArialBold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#1b140e"),
            spaceBefore=14,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SubSection",
            parent=styles["Heading3"],
            fontName="ArialBold",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#8c4f2b"),
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Body",
            parent=styles["BodyText"],
            fontName="ArialUnicode",
            fontSize=10.4,
            leading=14,
            textColor=colors.HexColor("#1f1c17"),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportBullet",
            parent=styles["BodyText"],
            fontName="ArialUnicode",
            fontSize=10.2,
            leading=14,
            leftIndent=14,
            firstLineIndent=-8,
            bulletIndent=4,
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontName="ArialUnicode",
            fontSize=8.6,
            leading=11,
            textColor=colors.HexColor("#6b6258"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Callout",
            parent=styles["BodyText"],
            fontName="ArialUnicode",
            fontSize=10.4,
            leading=15,
            textColor=colors.HexColor("#1f1c17"),
            backColor=colors.HexColor("#f7efe4"),
            borderColor=colors.HexColor("#d8c7b0"),
            borderWidth=0.7,
            borderPadding=10,
            borderRadius=6,
            spaceBefore=4,
            spaceAfter=12,
        )
    )
    return styles


def page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("ArialUnicode", 8)
    canvas.setFillColor(colors.HexColor("#6b6258"))
    canvas.drawString(doc.leftMargin, 10 * mm, "UGC / Content Factory Platform — CEO Report")
    canvas.drawRightString(A4[0] - doc.rightMargin, 10 * mm, f"Страница {doc.page}")
    canvas.restoreState()


def make_source_stats_table(styles):
    data = [
        ["Показатель", "Значение"],
        ["Всего просмотренных источников", "40"],
        ["Tier 1 primary sources", "38"],
        ["Tier 2 secondary sources", "2"],
        ["Deep-dive competitors", "5"],
        ["Категорий рынка", "6"],
        ["Ключевых platform surfaces", "5+"],
    ]
    table = Table(data, colWidths=[80 * mm, 32 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4e6da")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1b140e")),
                ("FONTNAME", (0, 0), (-1, 0), "ArialBold"),
                ("FONTNAME", (0, 1), (-1, -1), "ArialUnicode"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#ded4c4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fffcf7")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    caption = Paragraph("Исследовательская статистика по текущему run", styles["Small"])
    return KeepTogether([caption, Spacer(1, 3), table, Spacer(1, 10)])


def make_source_tier_chart(styles):
    drawing = Drawing(170 * mm, 52 * mm)
    drawing.add(String(0, 46 * mm, "Распределение источников по tier", fontName="ArialBold", fontSize=11, fillColor=colors.HexColor("#1b140e")))
    chart = HorizontalBarChart()
    chart.x = 42 * mm
    chart.y = 6 * mm
    chart.height = 28 * mm
    chart.width = 100 * mm
    chart.data = [[38, 2, 0, 0]]
    chart.categoryAxis.categoryNames = ["Tier 1", "Tier 2", "Tier 3", "Tier 4"]
    chart.categoryAxis.labels.fontName = "ArialUnicode"
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 40
    chart.valueAxis.valueStep = 10
    chart.valueAxis.labels.fontName = "ArialUnicode"
    chart.valueAxis.labels.fontSize = 8
    chart.bars[0].fillColor = colors.HexColor("#8c4f2b")
    chart.bars[0].strokeColor = colors.HexColor("#8c4f2b")
    drawing.add(chart)
    drawing.add(String(0, 0, "Факт: основной корпус доказательств опирается на primary sources.", fontName="ArialUnicode", fontSize=8, fillColor=colors.HexColor("#6b6258")))
    return KeepTogether([drawing, Spacer(1, 12)])


def make_wedge_chart():
    labels = [
        ("Pure AI generator", 2),
        ("Marketplace only", 3),
        ("Factory OS", 5),
        ("Scheduling only", 1),
        ("Analytics only", 3),
    ]
    drawing = Drawing(180 * mm, 70 * mm)
    drawing.add(String(0, 64 * mm, "Качественная оценка wedge attractiveness", fontName="ArialBold", fontSize=11, fillColor=colors.HexColor("#1b140e")))
    chart = HorizontalBarChart()
    chart.x = 48 * mm
    chart.y = 10 * mm
    chart.height = 42 * mm
    chart.width = 105 * mm
    chart.data = [[score for _, score in labels]]
    chart.categoryAxis.categoryNames = [label for label, _ in labels]
    chart.categoryAxis.labels.fontName = "ArialUnicode"
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 5
    chart.valueAxis.valueStep = 1
    chart.valueAxis.labels.fontName = "ArialUnicode"
    chart.valueAxis.labels.fontSize = 8
    chart.bars[0].fillColor = colors.HexColor("#356d5a")
    chart.bars[0].strokeColor = colors.HexColor("#356d5a")
    drawing.add(chart)
    drawing.add(String(0, 2 * mm, "Это аналитическая шкала по исследованию, а не рыночная доля.", fontName="ArialUnicode", fontSize=8, fillColor=colors.HexColor("#6b6258")))
    return KeepTogether([drawing, Spacer(1, 10)])


def make_operating_loop_diagram():
    drawing = Drawing(180 * mm, 42 * mm)
    drawing.add(String(0, 36 * mm, "Canonical operating loop", fontName="ArialBold", fontSize=11, fillColor=colors.HexColor("#1b140e")))
    labels = ["Strategy", "Production", "Distribution", "Analytics", "Remix / Scale"]
    x_positions = [0, 36 * mm, 74 * mm, 112 * mm, 148 * mm]
    widths = [30 * mm, 32 * mm, 32 * mm, 28 * mm, 30 * mm]
    y = 10 * mm
    for idx, (label, x, width) in enumerate(zip(labels, x_positions, widths)):
        drawing.add(Rect(x, y, width, 12 * mm, rx=3, ry=3, fillColor=colors.HexColor("#f7efe4"), strokeColor=colors.HexColor("#d8c7b0")))
        drawing.add(String(x + 3 * mm, y + 4.3 * mm, label, fontName="ArialUnicode", fontSize=9, fillColor=colors.HexColor("#1f1c17")))
        if idx < len(labels) - 1:
            x1 = x + width
            x2 = x_positions[idx + 1]
            drawing.add(Line(x1 + 2, y + 6 * mm, x2 - 2, y + 6 * mm, strokeColor=colors.HexColor("#8c4f2b"), strokeWidth=1.2))
            drawing.add(Line(x2 - 5, y + 7 * mm, x2 - 2, y + 6 * mm, strokeColor=colors.HexColor("#8c4f2b"), strokeWidth=1.2))
            drawing.add(Line(x2 - 5, y + 5 * mm, x2 - 2, y + 6 * mm, strokeColor=colors.HexColor("#8c4f2b"), strokeWidth=1.2))
    return KeepTogether([drawing, Spacer(1, 12)])


def build_table(rows, styles):
    split_rows = []
    max_cols = max(len(r.strip().strip("|").split("|")) for r in rows)
    for row in rows:
        parts = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(parts) < max_cols:
            parts += [""] * (max_cols - len(parts))
        split_rows.append(parts)

    header = split_rows[0]
    body = split_rows[2:]
    wrapped = [[Paragraph(escape_text(cell, SOURCE_MAP), styles["Small"] if i == 0 else styles["Body"]) for i, cell in enumerate(row)] for row in [header] + body]

    page_width = A4[0] - 32 * mm
    col_width = page_width / len(header)
    table = Table(wrapped, colWidths=[col_width] * len(header), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4e6da")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1b140e")),
                ("FONTNAME", (0, 0), (-1, 0), "ArialBold"),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#ded4c4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fffcf7")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def extract_source_map(md_text: str) -> dict[str, tuple[str, str]]:
    source_map: dict[str, tuple[str, str]] = {}
    link_re = re.compile(r"\[(SRC-[A-Z0-9-]+)(?:\s+[—-]\s+([^\]]+))?\]\((https?://[^)\s]+)\)")
    for match in link_re.finditer(md_text):
        src_id = match.group(1)
        title = match.group(2).strip() if match.group(2) else src_id
        url = match.group(3)
        source_map[src_id] = (title, url)
    return source_map


SOURCE_MAP: dict[str, tuple[str, str]] = {}


def parse_markdown_to_story(md_text: str, styles):
    story = []
    lines = md_text.splitlines()
    i = 0
    first_title_done = False

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            text = escape_text(heading.group(2), SOURCE_MAP)
            if level == 1 and not first_title_done:
                story.append(Paragraph(text, styles["ReportTitle"]))
                story.append(Paragraph("Русская CEO-версия исследования для спокойного чтения и product decision making", styles["ReportSubTitle"]))
                story.append(make_source_stats_table(styles))
                story.append(make_source_tier_chart(styles))
                first_title_done = True
            elif level == 2:
                story.append(Paragraph(text, styles["Section"]))
                if text == "Models / frameworks":
                    story.append(make_operating_loop_diagram())
            else:
                story.append(Paragraph(text, styles["SubSection"]))
                if text == "2. Где strongest wedge":
                    story.append(make_wedge_chart())
            i += 1
            continue

        if TABLE_RE.match(stripped):
            rows = []
            while i < len(lines) and TABLE_RE.match(lines[i].strip()):
                rows.append(lines[i].rstrip())
                i += 1
            story.append(build_table(rows, styles))
            story.append(Spacer(1, 10))
            continue

        bullet = UNORDERED_RE.match(stripped)
        if bullet:
            bullets = []
            while i < len(lines):
                m = UNORDERED_RE.match(lines[i].strip())
                if not m:
                    break
                bullets.append(m.group(1))
                i += 1
            for item in bullets:
                story.append(Paragraph(f"• {escape_text(item, SOURCE_MAP)}", styles["ReportBullet"]))
            story.append(Spacer(1, 4))
            continue

        # paragraph block
        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate or HEADING_RE.match(candidate) or TABLE_RE.match(candidate) or UNORDERED_RE.match(candidate):
                break
            paragraph_lines.append(candidate)
            i += 1

        paragraph_text = " ".join(paragraph_lines)
        style = styles["Callout"] if paragraph_text.startswith("Лучший стартовый wedge") else styles["Body"]
        story.append(Paragraph(escape_text(paragraph_text, SOURCE_MAP), style))

    return story


def build_pdf(input_md: Path, output_pdf: Path) -> None:
    register_fonts()
    styles = build_styles()
    md_text = input_md.read_text(encoding="utf-8")
    global SOURCE_MAP
    SOURCE_MAP = extract_source_map(md_text)
    story = parse_markdown_to_story(md_text, styles)

    doc = SimpleDocTemplate(
        str(output_pdf),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="UGC / Content Factory Platform — CEO Report",
        author="Codex / Deep Research Department",
    )
    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: render_ceo_report_pdf.py <input.md> <output.pdf>", file=sys.stderr)
        return 1

    input_md = Path(sys.argv[1])
    output_pdf = Path(sys.argv[2])
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(input_md, output_pdf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
