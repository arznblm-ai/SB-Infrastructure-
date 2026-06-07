#!/usr/bin/env python3
from __future__ import annotations

import html
import re
import sys
from pathlib import Path


TITLE_RE = re.compile(r"^\s*#\s+(.*)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
ORDERED_RE = re.compile(r"^\d+\.\s+(.*)")
UNORDERED_RE = re.compile(r"^-\s+(.*)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
SOURCE_REF_RE = re.compile(r"\[(SRC-[A-Z0-9-]+)\]")


def inline_format(text: str, source_map: dict[str, tuple[str, str]]) -> str:
    parts: list[str] = []
    code_tokens: list[str] = []
    cursor = 0

    for match in INLINE_CODE_RE.finditer(text):
        plain = text[cursor:match.start()]
        parts.append(_inline_format_without_code(plain, source_map))
        token = f"__CODE_TOKEN_{len(code_tokens)}__"
        code_tokens.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        parts.append(token)
        cursor = match.end()

    parts.append(_inline_format_without_code(text[cursor:], source_map))
    result = "".join(parts)
    for idx, replacement in enumerate(code_tokens):
        result = result.replace(f"__CODE_TOKEN_{idx}__", replacement)
    return result


def _inline_format_without_code(text: str, source_map: dict[str, tuple[str, str]]) -> str:
    parts: list[str] = []
    cursor = 0
    combined_re = re.compile(r"\[(SRC-[A-Z0-9-]+)\]|\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s<)]+)")

    for match in combined_re.finditer(text):
        if match.start() > cursor:
            parts.append(html.escape(text[cursor:match.start()], quote=False))

        if match.group(1):
            src_id = match.group(1)
            if src_id in source_map:
                label_raw, url_raw = source_map[src_id]
                label = html.escape(label_raw, quote=False)
                url = html.escape(url_raw, quote=True)
                parts.append(
                    f'<a class="source-ref" href="{url}" target="_blank" rel="noopener noreferrer" title="{label}">{label}</a>'
                )
            else:
                parts.append(html.escape(match.group(0), quote=False))
        elif match.group(2) and match.group(3):
            label = html.escape(match.group(2), quote=False)
            url = html.escape(match.group(3), quote=True)
            parts.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')
        else:
            url_raw = match.group(4)
            url = html.escape(url_raw, quote=True)
            label = html.escape(url_raw, quote=False)
            parts.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>')

        cursor = match.end()

    if cursor < len(text):
        parts.append(html.escape(text[cursor:], quote=False))

    return "".join(parts)


def parse_table(lines: list[str], start: int, source_map: dict[str, tuple[str, str]]) -> tuple[str, int]:
    rows = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append(lines[i].rstrip())
        i += 1

    if len(rows) < 2:
        return f"<p>{inline_format(rows[0], source_map)}</p>", i

    header = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
    body_rows = []
    for row in rows[2:]:
        body_rows.append([cell.strip() for cell in row.strip().strip("|").split("|")])

    parts = ["<div class='table-wrap'><table>"]
    parts.append("<thead><tr>" + "".join(f"<th>{inline_format(cell, source_map)}</th>" for cell in header) + "</tr></thead>")
    parts.append("<tbody>")
    for row in body_rows:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        parts.append("<tr>" + "".join(f"<td>{inline_format(cell, source_map)}</td>" for cell in row[: len(header)]) + "</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts), i


def extract_source_map(md: str) -> dict[str, tuple[str, str]]:
    source_map: dict[str, tuple[str, str]] = {}
    link_re = re.compile(r"\[(SRC-[A-Z0-9-]+)(?:\s+[—-]\s+([^\]]+))?\]\((https?://[^)\s]+)\)")
    for match in link_re.finditer(md):
        src_id = match.group(1)
        title = match.group(2).strip() if match.group(2) else src_id
        url = match.group(3)
        source_map[src_id] = (title, url)
    return source_map


def render_markdown(md: str, source_map: dict[str, tuple[str, str]]) -> str:
    lines = md.splitlines()
    output: list[str] = []
    i = 0
    in_paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal in_paragraph
        if in_paragraph:
            text = " ".join(part.strip() for part in in_paragraph if part.strip())
            output.append(f"<p>{inline_format(text, source_map)}</p>")
            in_paragraph = []

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if stripped.startswith("|"):
            flush_paragraph()
            table_html, i = parse_table(lines, i, source_map)
            output.append(table_html)
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            text = inline_format(heading.group(2), source_map)
            output.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        unordered = UNORDERED_RE.match(stripped)
        if unordered:
            flush_paragraph()
            items = []
            while i < len(lines):
                candidate = lines[i].strip()
                match = UNORDERED_RE.match(candidate)
                if not match:
                    break
                items.append(match.group(1))
                i += 1
            output.append("<ul>" + "".join(f"<li>{inline_format(item, source_map)}</li>" for item in items) + "</ul>")
            continue

        ordered = ORDERED_RE.match(stripped)
        if ordered:
            flush_paragraph()
            items = []
            while i < len(lines):
                candidate = lines[i].strip()
                match = ORDERED_RE.match(candidate)
                if not match:
                    break
                items.append(match.group(1))
                i += 1
            output.append("<ol>" + "".join(f"<li>{inline_format(item, source_map)}</li>" for item in items) + "</ol>")
            continue

        in_paragraph.append(stripped)
        i += 1

    flush_paragraph()
    return "\n".join(output)


def build_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --paper: #fffdf8;
      --text: #1f1c17;
      --muted: #6b6258;
      --line: #ded4c4;
      --accent: #8c4f2b;
      --accent-soft: #f4e6da;
      --code-bg: #f3eee6;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
      background:
        radial-gradient(circle at top left, #efe5d5 0, transparent 28rem),
        linear-gradient(180deg, #efe9df 0%, var(--bg) 100%);
      color: var(--text);
      line-height: 1.65;
    }}
    .page {{
      max-width: 1100px;
      margin: 40px auto;
      padding: 56px 64px 72px;
      background: var(--paper);
      border: 1px solid rgba(140, 79, 43, 0.14);
      box-shadow: 0 24px 80px rgba(64, 45, 28, 0.12);
    }}
    h1, h2, h3, h4, h5, h6 {{
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      line-height: 1.15;
      letter-spacing: -0.02em;
      margin: 1.4em 0 0.5em;
      color: #1b140e;
    }}
    h1 {{ font-size: 2.4rem; margin-top: 0; }}
    h2 {{
      font-size: 1.55rem;
      padding-top: 0.7rem;
      border-top: 1px solid var(--line);
    }}
    h3 {{ font-size: 1.2rem; color: var(--accent); }}
    p, li {{ font-size: 1.02rem; }}
    p {{ margin: 0.5rem 0 1rem; }}
    ul, ol {{ padding-left: 1.4rem; margin: 0.4rem 0 1rem; }}
    li {{ margin: 0.2rem 0; }}
    code {{
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      background: var(--code-bg);
      border: 1px solid #eadfce;
      border-radius: 6px;
      padding: 0.08rem 0.35rem;
      font-size: 0.92em;
    }}
    a {{
      color: var(--accent);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    a:hover {{
      color: #6d381b;
    }}
    a.source-ref {{
      display: inline-block;
      margin: 0 0.1rem;
      padding: 0.02rem 0.3rem;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid #ead7c7;
      font-size: 0.88em;
      white-space: nowrap;
    }}
    .table-wrap {{
      overflow-x: auto;
      margin: 1.1rem 0 1.5rem;
      border: 1px solid var(--line);
      border-radius: 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      background: #fff;
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 12px 14px;
      border-bottom: 1px solid #ece3d6;
      font-size: 0.95rem;
    }}
    th {{
      position: sticky;
      top: 0;
      background: var(--accent-soft);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      font-weight: 700;
    }}
    tr:nth-child(even) td {{
      background: #fffcf7;
    }}
    @media print {{
      body {{ background: white; }}
      .page {{
        box-shadow: none;
        border: none;
        margin: 0;
        max-width: none;
        padding: 0;
      }}
      .table-wrap {{
        overflow: visible;
        border-radius: 0;
      }}
    }}
    @media (max-width: 900px) {{
      .page {{ margin: 0; padding: 28px 18px 40px; border: none; box-shadow: none; }}
      h1 {{ font-size: 2rem; }}
      h2 {{ font-size: 1.35rem; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    {body}
  </main>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: render_markdown_report.py <input.md> <output.html>", file=sys.stderr)
        return 1

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    markdown_text = input_path.read_text(encoding="utf-8")
    source_map = extract_source_map(markdown_text)

    title_match = TITLE_RE.search(markdown_text)
    title = title_match.group(1).strip() if title_match else input_path.stem
    body = render_markdown(markdown_text, source_map)
    html_text = build_html(title, body)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
