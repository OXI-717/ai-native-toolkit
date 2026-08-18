#!/usr/bin/env python3
"""
Generate a PDF research report from synthesis.md and supporting files.
Usage: python3 generate-pdf-report.py <output_dir> <topic> <level> <domain> [language]

Requires: reportlab (pip install reportlab)
Fonts: Uses Arial (macOS system font) for broad Unicode support.
"""

import sys
import os
import re
import textwrap
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Font Registration ---
# macOS system fonts with broad Unicode support
FONT_DIR = "/System/Library/Fonts/Supplemental"
try:
    pdfmetrics.registerFont(TTFont("Arial", f"{FONT_DIR}/Arial.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold", f"{FONT_DIR}/Arial Bold.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Italic", f"{FONT_DIR}/Arial Italic.ttf"))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic")
except Exception as e:
    sys.stderr.write(f"Error: Arial fonts not found at {FONT_DIR}: {e}\n")
    sys.stderr.write("This script requires macOS with Arial fonts for Unicode support.\n")
    sys.exit(1)

# --- Colors ---
PRIMARY = HexColor("#1a1a2e")
ACCENT = HexColor("#4a6fa5")
LIGHT_BG = HexColor("#f0f4f8")
BORDER = HexColor("#d0d8e0")
TEXT_COLOR = HexColor("#2c3e50")
MUTED = HexColor("#7f8c8d")

# --- Styles ---
STYLES = {
    "title": ParagraphStyle(
        "Title", fontName="Arial-Bold", fontSize=22, leading=28,
        textColor=PRIMARY, alignment=TA_CENTER, spaceAfter=6*mm,
    ),
    "subtitle": ParagraphStyle(
        "Subtitle", fontName="Arial", fontSize=11, leading=14,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=10*mm,
    ),
    "h1": ParagraphStyle(
        "H1", fontName="Arial-Bold", fontSize=16, leading=22,
        textColor=PRIMARY, spaceBefore=8*mm, spaceAfter=4*mm,
    ),
    "h2": ParagraphStyle(
        "H2", fontName="Arial-Bold", fontSize=13, leading=18,
        textColor=ACCENT, spaceBefore=6*mm, spaceAfter=3*mm,
    ),
    "h3": ParagraphStyle(
        "H3", fontName="Arial-Bold", fontSize=11, leading=15,
        textColor=TEXT_COLOR, spaceBefore=4*mm, spaceAfter=2*mm,
    ),
    "body": ParagraphStyle(
        "Body", fontName="Arial", fontSize=10, leading=15,
        textColor=TEXT_COLOR, alignment=TA_JUSTIFY, spaceAfter=2*mm,
    ),
    "bullet": ParagraphStyle(
        "Bullet", fontName="Arial", fontSize=10, leading=15,
        textColor=TEXT_COLOR, leftIndent=8*mm, bulletIndent=3*mm,
        spaceAfter=1.5*mm,
    ),
    "tldr": ParagraphStyle(
        "TLDR", fontName="Arial", fontSize=10.5, leading=16,
        textColor=PRIMARY, leftIndent=4*mm, rightIndent=4*mm,
        spaceBefore=3*mm, spaceAfter=3*mm, backColor=LIGHT_BG,
        borderPadding=(3*mm, 3*mm, 3*mm, 3*mm),
    ),
    "footer": ParagraphStyle(
        "Footer", fontName="Arial-Italic", fontSize=8, leading=10,
        textColor=MUTED, alignment=TA_CENTER,
    ),
}


LABELS = {
    "en": {
        "report": "RESEARCH REPORT", "level": "Level", "domain": "Domain", "date": "Date",
        "method": "Method: Multi-Agent Adversarial Ensemble (Exa AI)", "contents": "Contents",
        "takeaways": "Key Takeaways", "main_report": "Main Report", "fact_check": "Fact Check",
        "critic_review": "Critic Review", "open_questions": "Open Questions and Next Steps",
        "tldr_missing": "TL;DR section not found in synthesis.md", "synthesis_missing": "synthesis.md not found",
        "fact_check_missing": "_fact_check.md not found", "critic_missing": "_critic_review.md not found",
        "unknowns_missing": "unknowns_and_next.md not found", "footer": "Deep Research",
        "tldr_headings": ("TL;?DR", "Executive Summary", "Key Takeaways"),
    },
    "ru": {
        "report": "ОТЧЁТ ОБ ИССЛЕДОВАНИИ", "level": "Уровень", "domain": "Домен", "date": "Дата",
        "method": "Метод: мультиагентный состязательный анализ (Exa AI)", "contents": "Содержание",
        "takeaways": "Ключевые выводы", "main_report": "Основной отчёт", "fact_check": "Проверка фактов",
        "critic_review": "Критический обзор", "open_questions": "Открытые вопросы и следующие шаги",
        "tldr_missing": "Раздел с ключевыми выводами не найден в synthesis.md", "synthesis_missing": "synthesis.md не найден",
        "fact_check_missing": "_fact_check.md не найден", "critic_missing": "_critic_review.md не найден",
        "unknowns_missing": "unknowns_and_next.md не найден", "footer": "Глубокое исследование",
        "tldr_headings": ("TL;?DR", "Ключевые выводы", "Краткие выводы", "Резюме"),
    },
    "es": {
        "report": "INFORME DE INVESTIGACIÓN", "level": "Nivel", "domain": "Dominio", "date": "Fecha",
        "method": "Método: conjunto adversarial multiagente (Exa AI)", "contents": "Contenido",
        "takeaways": "Conclusiones clave", "main_report": "Informe principal", "fact_check": "Verificación de datos",
        "critic_review": "Revisión crítica", "open_questions": "Preguntas abiertas y próximos pasos",
        "tldr_missing": "No se encontró la sección de conclusiones en synthesis.md", "synthesis_missing": "No se encontró synthesis.md",
        "fact_check_missing": "No se encontró _fact_check.md", "critic_missing": "No se encontró _critic_review.md",
        "unknowns_missing": "No se encontró unknowns_and_next.md", "footer": "Investigación profunda",
        "tldr_headings": ("TL;?DR", "Resumen ejecutivo", "Conclusiones clave"),
    },
}


def language_key(language):
    """Normalize the selected report language to a supported label set."""
    normalized = language.strip().lower().replace("_", "-")
    if normalized.startswith(("ru", "russian", "рус")):
        return "ru"
    if normalized.startswith(("es", "spanish", "español")):
        return "es"
    return "en"


def read_file(path):
    """Read file content, return empty string if not found."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def escape_xml(text):
    """Escape XML special characters for ReportLab Paragraph."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def md_inline_to_rl(text):
    """Convert inline markdown (bold, italic, code) to ReportLab XML tags."""
    text = escape_xml(text)
    # Bold: **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    # Italic: *text* or _text_
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)
    # Inline code: `text` — use Arial for Cyrillic support (Courier has no Cyrillic glyphs)
    text = re.sub(r'`(.+?)`', r'<font face="Arial" size="9" color="#c0392b">\1</font>', text)
    # Links: [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    return text


def parse_markdown_to_flowables(md_text):
    """Parse markdown text into ReportLab flowables."""
    flowables = []
    lines = md_text.split("\n")
    i = 0
    buffer = []

    def flush_buffer():
        if buffer:
            text = " ".join(buffer)
            flowables.append(Paragraph(md_inline_to_rl(text), STYLES["body"]))
            buffer.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines (flush buffer)
        if not stripped:
            flush_buffer()
            i += 1
            continue

        # Headings
        if stripped.startswith("### "):
            flush_buffer()
            flowables.append(Paragraph(md_inline_to_rl(stripped[4:]), STYLES["h3"]))
            i += 1
            continue
        if stripped.startswith("## "):
            flush_buffer()
            flowables.append(Paragraph(md_inline_to_rl(stripped[3:]), STYLES["h2"]))
            i += 1
            continue
        if stripped.startswith("# "):
            flush_buffer()
            flowables.append(Paragraph(md_inline_to_rl(stripped[2:]), STYLES["h1"]))
            i += 1
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            flush_buffer()
            flowables.append(Spacer(1, 2*mm))
            flowables.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
            flowables.append(Spacer(1, 2*mm))
            i += 1
            continue

        # Bullet points
        if re.match(r'^[-*+]\s', stripped):
            flush_buffer()
            bullet_text = re.sub(r'^[-*+]\s+', '', stripped)
            flowables.append(Paragraph(
                f"&bull;&nbsp;&nbsp;{md_inline_to_rl(bullet_text)}", STYLES["bullet"]
            ))
            i += 1
            continue

        # Numbered list
        m = re.match(r'^(\d+)\.\s+(.+)', stripped)
        if m:
            flush_buffer()
            num, text = m.group(1), m.group(2)
            flowables.append(Paragraph(
                f"{num}.&nbsp;&nbsp;{md_inline_to_rl(text)}", STYLES["bullet"]
            ))
            i += 1
            continue

        # Code blocks — render as preformatted
        if stripped.startswith("```"):
            flush_buffer()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(escape_xml(lines[i]))
                i += 1
            i += 1  # skip closing ```
            code_text = "<br/>".join(code_lines) if code_lines else "&nbsp;"
            code_style = ParagraphStyle(
                "Code", fontName="Arial", fontSize=8.5, leading=12,
                textColor=TEXT_COLOR, backColor=LIGHT_BG,
                leftIndent=4*mm, rightIndent=4*mm,
                spaceBefore=2*mm, spaceAfter=2*mm,
                borderPadding=(2*mm, 2*mm, 2*mm, 2*mm),
            )
            flowables.append(Paragraph(code_text, code_style))
            continue

        # Markdown table
        if "|" in stripped and stripped.startswith("|"):
            flush_buffer()
            table_rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                # Skip separator rows (|---|---|)
                if re.match(r'^\|[\s\-:|]+$', row):
                    i += 1
                    continue
                cells = [c.strip() for c in row.split("|")[1:-1]]
                table_rows.append(cells)
                i += 1
            if table_rows:
                # Convert to Paragraphs for wrapping
                data = []
                for ri, row in enumerate(table_rows):
                    style = STYLES["body"] if ri > 0 else ParagraphStyle(
                        "TableHeader", parent=STYLES["body"],
                        fontName="Arial-Bold", fontSize=9,
                    )
                    data.append([Paragraph(md_inline_to_rl(c), style) for c in row])
                ncols = max(len(r) for r in data) if data else 1
                col_width = (A4[0] - 40*mm) / ncols
                t = Table(data, colWidths=[col_width]*ncols)
                t_style = [
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2*mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2*mm),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2*mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2*mm),
                ]
                t.setStyle(TableStyle(t_style))
                flowables.append(Spacer(1, 2*mm))
                flowables.append(t)
                flowables.append(Spacer(1, 2*mm))
            continue

        # Regular paragraph text — accumulate
        buffer.append(stripped)
        i += 1

    flush_buffer()
    return flowables


def slugify_filename(text, max_len=80):
    """Create a safe filename from text, preserving non-ASCII characters."""
    # Remove characters unsafe for filenames
    safe = re.sub(r'[<>:"/\\|?*]', '', text)
    # Collapse whitespace
    safe = re.sub(r'\s+', ' ', safe).strip()
    return safe[:max_len]


def build_pdf(output_dir, topic, level, domain, language="English"):
    """Build PDF report from research output files."""
    labels = LABELS[language_key(language)]
    date_str = datetime.now().strftime("%Y-%m-%d")
    # Prefix is deployment-specific (vault slug and the like), so it comes from
    # the environment instead of being hardcoded here.
    prefix = slugify_filename(os.environ.get("DEEP_RESEARCH_FILENAME_PREFIX", "").strip())
    prefix = f"{prefix} " if prefix else ""
    filename = f"{prefix}{{research}} {slugify_filename(topic)} – {date_str} – DeepResearch.pdf"
    pdf_path = os.path.join(output_dir, filename)
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        topMargin=20*mm,
        bottomMargin=20*mm,
        leftMargin=20*mm,
        rightMargin=20*mm,
        title=f"{labels['report']}: {topic}",
        author="Deep Research — Adversarial Ensemble",
    )

    story = []

    # --- Cover Page ---
    story.append(Spacer(1, 30*mm))
    story.append(Paragraph(labels["report"], STYLES["title"]))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(md_inline_to_rl(topic), ParagraphStyle(
        "TopicTitle", fontName="Arial-Bold", fontSize=18, leading=24,
        textColor=ACCENT, alignment=TA_CENTER, spaceAfter=8*mm,
    )))
    story.append(HRFlowable(width="60%", thickness=1, color=ACCENT))
    story.append(Spacer(1, 6*mm))

    meta_lines = [
        f"{labels['level']}: {level} &nbsp;|&nbsp; {labels['domain']}: {domain}",
        f"{labels['date']}: {timestamp}",
        labels["method"],
    ]
    for line in meta_lines:
        story.append(Paragraph(line, STYLES["subtitle"]))

    story.append(PageBreak())

    # --- Table of Contents (lightweight) ---
    story.append(Paragraph(labels["contents"], STYLES["h1"]))
    story.append(Spacer(1, 4*mm))

    toc_items = [
        f"1. {labels['takeaways']} (TL;DR)",
        f"2. {labels['main_report']}",
        f"3. {labels['fact_check']}",
        f"4. {labels['critic_review']}",
        f"5. {labels['open_questions']}",
    ]
    for item in toc_items:
        story.append(Paragraph(item, ParagraphStyle(
            "TOC", fontName="Arial", fontSize=11, leading=18,
            textColor=ACCENT, leftIndent=10*mm, spaceAfter=2*mm,
        )))

    story.append(PageBreak())

    # --- 1. TL;DR ---
    synthesis = read_file(os.path.join(output_dir, "synthesis.md"))
    tldr_text = ""
    if synthesis:
        # Extract TL;DR section
        tldr_pattern = "|".join(labels["tldr_headings"])
        tldr_match = re.search(
            rf'(?:^|\n)##?\s*(?:\d+\.\s*)?(?:{tldr_pattern})[^\n]*\n(.*?)(?=\n##?\s|\Z)',
            synthesis, re.DOTALL | re.IGNORECASE
        )
        if tldr_match:
            tldr_text = tldr_match.group(1).strip()

    story.append(Paragraph(f"1. {labels['takeaways']}", STYLES["h1"]))
    if tldr_text:
        story.extend(parse_markdown_to_flowables(tldr_text))
    else:
        story.append(Paragraph(
            f"<i>{labels['tldr_missing']}</i>", STYLES["body"]
        ))

    story.append(PageBreak())

    # --- 2. Main Report ---
    story.append(Paragraph(f"2. {labels['main_report']}", STYLES["h1"]))
    if synthesis:
        # Remove TL;DR section to avoid duplication, and frontmatter
        body = re.sub(
            rf'(?:^|\n)##?\s*(?:\d+\.\s*)?(?:{tldr_pattern})[^\n]*\n.*?(?=\n##?\s|\Z)',
            '', synthesis, flags=re.DOTALL | re.IGNORECASE
        )
        # Remove YAML frontmatter
        body = re.sub(r'^---\n.*?\n---\n', '', body, flags=re.DOTALL)
        # Remove top-level title (first # line)
        body = re.sub(r'^#\s+[^\n]+\n', '', body.strip())
        story.extend(parse_markdown_to_flowables(body.strip()))
    else:
        story.append(Paragraph(
            f"<i>{labels['synthesis_missing']}</i>", STYLES["body"]
        ))

    story.append(PageBreak())

    # --- 3. Fact Check ---
    fact_check = read_file(os.path.join(output_dir, "reviews", "_fact_check.md"))
    story.append(Paragraph(f"3. {labels['fact_check']}", STYLES["h1"]))
    if fact_check:
        # Remove top-level title
        fact_check = re.sub(r'^#\s+[^\n]+\n', '', fact_check.strip())
        story.extend(parse_markdown_to_flowables(fact_check.strip()))
    else:
        story.append(Paragraph(
            f"<i>{labels['fact_check_missing']}</i>", STYLES["body"]
        ))

    story.append(PageBreak())

    # --- 4. Critic Review ---
    critic = read_file(os.path.join(output_dir, "reviews", "_critic_review.md"))
    story.append(Paragraph(f"4. {labels['critic_review']}", STYLES["h1"]))
    if critic:
        critic = re.sub(r'^#\s+[^\n]+\n', '', critic.strip())
        story.extend(parse_markdown_to_flowables(critic.strip()))
    else:
        story.append(Paragraph(
            f"<i>{labels['critic_missing']}</i>", STYLES["body"]
        ))

    story.append(PageBreak())

    # --- 5. Open Questions ---
    unknowns = read_file(os.path.join(output_dir, "unknowns_and_next.md"))
    story.append(Paragraph(f"5. {labels['open_questions']}", STYLES["h1"]))
    if unknowns:
        unknowns = re.sub(r'^#\s+[^\n]+\n', '', unknowns.strip())
        story.extend(parse_markdown_to_flowables(unknowns.strip()))
    else:
        story.append(Paragraph(
            f"<i>{labels['unknowns_missing']}</i>", STYLES["body"]
        ))

    # --- Footer on every page ---
    def add_page_number(canvas_obj, doc_obj):
        canvas_obj.saveState()
        canvas_obj.setFont("Arial", 8)
        canvas_obj.setFillColor(MUTED)
        page_num = canvas_obj.getPageNumber()
        canvas_obj.drawCentredString(
            A4[0] / 2, 12*mm,
            f"{labels['footer']} — {topic} — p. {page_num}"
        )
        canvas_obj.restoreState()

    try:
        doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    except Exception as e:
        sys.stderr.write(f"PDF build failed: {e}\n")
        sys.exit(1)
    return os.path.abspath(pdf_path)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(f"Usage: {sys.argv[0]} <output_dir> <topic> <level> <domain> [language]")
        sys.exit(1)

    output_dir = sys.argv[1]
    topic = sys.argv[2]
    level = sys.argv[3]
    domain = sys.argv[4]
    language = sys.argv[5] if len(sys.argv) > 5 else "English"

    pdf_path = build_pdf(output_dir, topic, level, domain, language)
    print(pdf_path)
