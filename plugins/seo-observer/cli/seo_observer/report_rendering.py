from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReportPdfError(RuntimeError):
    """Raised when HTML was created but PDF rendering failed."""


@dataclass(frozen=True)
class RenderedReportArtifacts:
    html_path: Path
    pdf_path: Path | None
    pdf_error: str | None = None


def write_polished_report_artifacts(
    *,
    markdown_text: str,
    output_dir: Path,
    basename: str,
    title: str,
    subtitle: str | None = None,
    render_pdf: bool = True,
) -> RenderedReportArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_text = render_markdown_report_html(markdown_text, title=title, subtitle=subtitle)
    html_path = output_dir / f"{basename}.html"
    html_path.write_text(html_text, encoding="utf-8")
    if not render_pdf:
        return RenderedReportArtifacts(html_path=html_path, pdf_path=None)

    pdf_path = output_dir / f"{basename}.pdf"
    try:
        render_pdf_from_html(html_path, pdf_path)
    except Exception as exc:  # pragma: no cover - exact renderer failures are environment-specific
        return RenderedReportArtifacts(
            html_path=html_path,
            pdf_path=None,
            pdf_error=f"{exc.__class__.__name__}: {exc}",
        )
    return RenderedReportArtifacts(html_path=html_path, pdf_path=pdf_path)


def render_pdf_from_html(html_path: Path, pdf_path: Path) -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - covered by callers as structured error
        raise ReportPdfError("Playwright is not installed; HTML report was still created.") from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1800}, device_scale_factor=1)
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "12mm", "right": "10mm", "bottom": "14mm", "left": "10mm"},
            )
            browser.close()
    except PlaywrightError as exc:  # pragma: no cover - covered by callers as structured error
        raise ReportPdfError("Playwright could not render PDF; install Chromium with `playwright install chromium`.") from exc


def render_markdown_report_html(markdown_text: str, *, title: str, subtitle: str | None = None) -> str:
    body, toc = _render_blocks(markdown_text)
    subtitle_html = f"<p class=\"report-subtitle\">{html.escape(subtitle)}</p>" if subtitle else ""
    toc_html = _render_toc(toc)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
{_REPORT_CSS}
  </style>
</head>
<body>
  <main class="report-shell">
    <section class="report-cover">
      <div class="eyebrow">SEO Observer</div>
      <h1>{html.escape(title)}</h1>
      {subtitle_html}
    </section>
    {toc_html}
    <article class="report-content">
      {body}
    </article>
  </main>
</body>
</html>
"""


def _render_toc(items: list[tuple[int, str, str]]) -> str:
    visible = [(level, text, anchor) for level, text, anchor in items if level in (2, 3)]
    if not visible:
        return ""
    links = "\n".join(
        f'<a class="toc-link toc-level-{level}" href="#{anchor}">{html.escape(text)}</a>' for level, text, anchor in visible[:36]
    )
    return f'<nav class="toc" aria-label="Оглавление"><h2>Оглавление</h2><div class="toc-grid">{links}</div></nav>'


def _render_blocks(markdown_text: str) -> tuple[str, list[tuple[int, str, str]]]:
    lines = markdown_text.splitlines()
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if _is_table_start(lines, i):
            html_table, i = _render_table(lines, i)
            out.append(html_table)
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            text = _strip_inline_markdown(heading.group(2).strip())
            anchor = _anchor(text, used={item[2] for item in toc})
            toc.append((level, text, anchor))
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items: list[str] = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]).strip())
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]).strip())
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ol>")
            continue
        paragraph = [line.strip()]
        i += 1
        while i < len(lines):
            current = lines[i].rstrip()
            if not current.strip() or _is_table_start(lines, i) or re.match(r"^(#{1,4})\s+(.+)$", current):
                break
            if re.match(r"^\s*[-*]\s+", current) or re.match(r"^\s*\d+\.\s+", current):
                break
            paragraph.append(current.strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out), toc


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    header = lines[index].strip()
    divider = lines[index + 1].strip()
    return header.startswith("|") and header.endswith("|") and re.fullmatch(r"\|[\s:\-|\u2014]+\|", divider) is not None


def _render_table(lines: list[str], index: int) -> tuple[str, int]:
    headers = _split_table_row(lines[index])
    index += 2
    rows: list[list[str]] = []
    while index < len(lines):
        line = lines[index].strip()
        if not (line.startswith("|") and line.endswith("|")):
            break
        rows.append(_split_table_row(line))
        index += 1
    head = "".join(f"<th>{_inline(cell)}</th>" for cell in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in _pad(row, len(headers))) + "</tr>" for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>', index


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _pad(row: list[str], size: int) -> list[str]:
    return row + [""] * max(0, size - len(row))


def _strip_inline_markdown(value: str) -> str:
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return value.strip()


def _inline(value: Any) -> str:
    text = html.escape(str(value))
    text = re.sub(r"`([^`]*)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", text)
    text = re.sub(
        r"(https?://[^\s<]+)",
        lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>',
        text,
    )
    return text


def _anchor(text: str, *, used: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", text.lower()).strip("-") or "section"
    anchor = base
    index = 2
    while anchor in used:
        anchor = f"{base}-{index}"
        index += 1
    return anchor


_REPORT_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7fb;
  --paper: #ffffff;
  --ink: #172033;
  --muted: #637083;
  --line: #d9e0ea;
  --soft: #eef3f8;
  --accent: #0f766e;
  --accent-2: #1d4ed8;
  --danger: #b42318;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  font-size: 15px;
  line-height: 1.55;
}
.report-shell {
  width: min(1180px, calc(100vw - 32px));
  margin: 24px auto 56px;
  background: var(--paper);
  border: 1px solid var(--line);
  box-shadow: 0 18px 60px rgba(20, 33, 61, 0.10);
}
.report-cover {
  padding: 42px 48px 34px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(135deg, #f9fbff 0%, #eef7f4 100%);
}
.eyebrow {
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .08em;
  text-transform: uppercase;
}
h1, h2, h3, h4 { margin: 0; line-height: 1.18; letter-spacing: 0; }
h1 { margin-top: 12px; font-size: 34px; max-width: 880px; }
.report-subtitle { margin: 14px 0 0; max-width: 900px; color: var(--muted); font-size: 17px; }
.toc {
  padding: 26px 48px;
  border-bottom: 1px solid var(--line);
  background: #fbfcfe;
}
.toc h2 { font-size: 18px; margin-bottom: 14px; }
.toc-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 18px;
}
.toc-link {
  color: var(--accent-2);
  text-decoration: none;
  overflow-wrap: anywhere;
}
.toc-level-3 { padding-left: 14px; color: var(--muted); }
.report-content { padding: 34px 48px 46px; }
.report-content > h1:first-child { display: none; }
h2 {
  margin-top: 34px;
  padding-top: 22px;
  border-top: 1px solid var(--line);
  font-size: 23px;
}
h3 { margin-top: 28px; font-size: 18px; color: #22314d; }
h4 { margin-top: 20px; font-size: 15px; color: var(--muted); }
p { margin: 12px 0; }
ul, ol { margin: 12px 0 18px 22px; padding: 0; }
li { margin: 7px 0; }
code {
  padding: 1px 5px;
  border: 1px solid #d8dee9;
  border-radius: 5px;
  background: #f5f7fb;
  color: #0f172a;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .92em;
}
a { color: var(--accent-2); overflow-wrap: anywhere; }
.table-wrap {
  width: 100%;
  margin: 14px 0 24px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: white;
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 680px;
}
th, td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}
th {
  background: var(--soft);
  color: #334155;
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
tbody tr:nth-child(even) td { background: #fafcff; }
tbody tr:last-child td { border-bottom: 0; }
@media (max-width: 760px) {
  .report-shell { width: 100%; margin: 0; border: 0; box-shadow: none; }
  .report-cover, .toc, .report-content { padding-left: 20px; padding-right: 20px; }
  .toc-grid { grid-template-columns: 1fr; }
  h1 { font-size: 28px; }
}
@media print {
  @page { size: A4; margin: 12mm 10mm 14mm; }
  html, body { background: white; font-size: 10.5pt; }
  .report-shell { width: 100%; margin: 0; border: 0; box-shadow: none; }
  .report-cover, .toc, .report-content { padding-left: 0; padding-right: 0; }
  .report-cover { padding-top: 0; }
  a { color: #1d4ed8; text-decoration: none; }
  h2 { break-after: avoid; page-break-after: avoid; }
  h3, h4 { break-after: avoid; page-break-after: avoid; }
  .table-wrap { overflow: visible; break-inside: avoid; page-break-inside: avoid; }
  table { min-width: 0; font-size: 8.8pt; }
  th, td { padding: 6px 7px; }
  tr { break-inside: avoid; page-break-inside: avoid; }
}
"""
