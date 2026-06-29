#!/usr/bin/env python3
"""Unified external-resource note builder.

Single source of truth for the ONE rich note produced for every saved external
resource (Instagram, TikTok, YouTube, X, web), regardless of whether it was
captured by the Telegram bot (Link Inbox) or analyzed manually by an agent
(the former `instagram-reel-analyzer` skill).

The note shape follows the schema documented in
`transcripts/external resources/README.md`.

Two tiers of intelligence:

- ``auto`` — everything derivable WITHOUT an LLM: frontmatter, transcript,
  caption, regex-extracted links, heuristic tool list, heuristic thesis.
  The LLM-grade sections are written with an explicit ``PENDING`` marker.
- ``enrich`` — an agent supplies summary / essence / insights / solutions /
  strategic analysis / verified links; those overwrite the pending markers and
  flip ``enrichment: done`` in the frontmatter.

Both the bot pipeline and manual agent runs call into this module so a reel
saved via Telegram and a reel analyzed by hand produce an IDENTICAL note in the
SAME canonical folder.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from link_digest import (
    build_about,
    extract_numbered_tools,
    extract_urls,
    read_text,
    russian_tool_desc,
    source_fields,
    transcript_lines,
)
from link_inbox_common import (
    EXTERNAL_TRANSCRIPTS_DIR,
    canonicalize_url,
    clean_filename_part,
    link_kind,
)

PENDING = "_Ожидает разбора агентом (enrich)._"

# Canonical section order. Headings are matched exactly on load/enrich.
SECTION_ORDER = [
    "Краткое содержание",
    "Суть",
    "Полезные ссылки",
    "Упомянутые инструменты и skills",
    "Главные инсайты",
    "Готовые решения / как применить",
    "Оценка применимости для Антона",
    "Strategic Board analysis",
    "Транскрипт",
    "Raw caption / metadata",
]

# Sections an agent can fill during enrich (transcript / caption are preserved).
ENRICHABLE = {
    "Краткое содержание",
    "Суть",
    "Полезные ссылки",
    "Упомянутые инструменты и skills",
    "Главные инсайты",
    "Готовые решения / как применить",
    "Оценка применимости для Антона",
    "Strategic Board analysis",
}

DATE_SUFFIX_RE = re.compile(r"–\s*(\d{4}-\d{2}-\d{2})\s*$")
AUTHOR_RE = re.compile(r"Video by\s+([A-Za-z0-9_.]+)", re.IGNORECASE)
NAME_RE = re.compile(r"UGC\s+(?:instagram|tiktok)\s+(.+?)\s+-\s+Video by", re.IGNORECASE)
SHORTCODE_RE = re.compile(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def yaml_scalar(value: str | None) -> str:
    value = " ".join(str(value or "").split()).replace('"', '\\"')
    return value or "unknown"


def yaml_list(items: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = " ".join(str(item or "").split())
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item.replace('"', '\\"'))
    if not out:
        return "[]"
    return "[" + ", ".join(f'"{x}"' for x in out) + "]"


def bullet_block(items: list[str], fallback: str) -> str:
    clean = [" ".join(str(i).split()) for i in items if str(i).strip()]
    if not clean:
        return fallback
    return "\n".join(f"- {i}" for i in clean)


# --------------------------------------------------------------------------- #
# Raw transcript parsing (handles legacy whisper dumps and YouTube outputs)
# --------------------------------------------------------------------------- #


def parse_raw_transcript(text: str) -> dict:
    """Split a raw transcript file into title / source meta / transcript body."""
    title = ""
    title_match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.M)
    if title_match:
        title = title_match.group(1).strip()

    meta: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^-\s*(Source|Model|Language|Language probability)\s*:\s*(.+)$", line, flags=re.I)
        if m:
            meta[m.group(1).strip().lower()] = m.group(2).strip()

    body_match = re.search(r"^##\s+(?:Transcript|Транскрипт)\s*$", text, flags=re.M)
    if body_match:
        body = text[body_match.end():].strip()
    else:
        # No explicit transcript header: keep everything after the frontmatter/title.
        body = re.sub(r"^#\s+.+?$", "", text, count=1, flags=re.M).strip()
    return {"title": title, "meta": meta, "body": body}


# --------------------------------------------------------------------------- #
# Note rendering
# --------------------------------------------------------------------------- #


def derive_identity(record: dict, title: str, source_url: str) -> dict:
    kind = record.get("kind") or (link_kind(source_url) if source_url else "web")
    author_user = ""
    author_name = ""
    am = AUTHOR_RE.search(title)
    if am:
        author_user = am.group(1)
    nm = NAME_RE.search(title)
    if nm:
        author_name = nm.group(1).strip()
    shortcode = ""
    sm = SHORTCODE_RE.search(source_url or "")
    if sm:
        shortcode = sm.group(1)
    return {
        "platform": kind,
        "author_username": author_user,
        "author_name": author_name,
        "shortcode": shortcode,
    }


def build_frontmatter(
    *,
    title: str,
    source_url: str,
    identity: dict,
    published_at: str,
    language: str,
    model: str,
    tools: list[str],
    links: list[str],
    tags: list[str],
    enrichment: str,
) -> str:
    base_tags = ["external-resource", identity.get("platform", "web")]
    return "\n".join(
        [
            "---",
            "type: external-resource",
            f'title: "{yaml_scalar(title)}"',
            f'source_url: "{yaml_scalar(source_url)}"',
            f'platform: "{yaml_scalar(identity.get("platform"))}"',
            f'author_username: "{yaml_scalar(identity.get("author_username"))}"',
            f'author_name: "{yaml_scalar(identity.get("author_name"))}"',
            f'shortcode: "{yaml_scalar(identity.get("shortcode"))}"',
            f'published_at: "{yaml_scalar(published_at)}"',
            f'captured_at: "{now_iso()}"',
            f'language: "{yaml_scalar(language)}"',
            f'transcription_model: "{yaml_scalar(model)}"',
            f"enrichment: {enrichment}",
            f"tools: {yaml_list(tools)}",
            f"links: {yaml_list(links)}",
            f"tags: {yaml_list(base_tags + tags)}",
            "---",
        ]
    )


def render_note(title: str, frontmatter: str, sections: dict[str, str]) -> str:
    parts = [frontmatter, "", f"# {title}", ""]
    for heading in SECTION_ORDER:
        parts.append(f"## {heading}")
        parts.append("")
        parts.append(sections.get(heading, PENDING).rstrip() or PENDING)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_auto_sections(
    *,
    transcript_body: str,
    caption: str,
    links: list[str],
    tools: list[tuple[str, str]],
    about: str,
) -> dict[str, str]:
    link_lines = []
    for url in links:
        link_lines.append(f"- {url}")
    tool_lines = []
    for name, desc in tools[:8]:
        readable = russian_tool_desc(name, desc)
        tool_lines.append(f"- **{name}:** {readable}" if readable else f"- **{name}**")

    return {
        "Краткое содержание": f"- {about}" if about else PENDING,
        "Суть": PENDING,
        "Полезные ссылки": "\n".join(link_lines) if link_lines else "- not found",
        "Упомянутые инструменты и skills": "\n".join(tool_lines)
        if tool_lines
        else "_Инструменты автоматически не выделены._",
        "Главные инсайты": PENDING,
        "Готовые решения / как применить": PENDING,
        "Оценка применимости для Антона": PENDING,
        "Strategic Board analysis": PENDING,
        "Транскрипт": transcript_body.strip() or "_Transcript unavailable._",
        "Raw caption / metadata": caption.strip() or "unknown",
    }


def build_auto_note(record: dict, raw_text: str, caption: str = "") -> str:
    parsed = parse_raw_transcript(raw_text)
    title = parsed["title"] or record.get("title") or record.get("url") or "External resource"
    title = re.sub(r"^\{?self\}?\s+\{?transcript\}?\s+", "", title, flags=re.I).strip()
    source_url = canonicalize_url(record.get("url", "")) if record.get("url") else ""
    identity = derive_identity(record, title, source_url)

    # Prefer the content date embedded in the title/filename ("– YYYY-MM-DD"),
    # falling back to the record's save date.
    dm = DATE_SUFFIX_RE.search(title)
    published_at = (dm.group(1) if dm else "") or record.get("date") or ""

    meta = parsed["meta"]
    language = meta.get("language", "")
    model = meta.get("model", "")

    body = parsed["body"]
    lines = transcript_lines("## Транскрипт\n" + body) or transcript_lines("## Transcript\n" + body)
    tools = extract_numbered_tools(lines)
    brief_meta = source_fields(caption) if caption else {}
    description = brief_meta.get("description", "") or record.get("excerpt", "")
    about = build_about(tools, lines, description)

    links = extract_urls(body, caption, record.get("message_text", ""))
    if source_url and source_url not in links:
        links.insert(0, source_url)

    tool_names = [name for name, _ in tools]
    frontmatter = build_frontmatter(
        title=title,
        source_url=source_url,
        identity=identity,
        published_at=published_at,
        language=language,
        model=model,
        tools=tool_names,
        links=links,
        tags=[],
        enrichment="pending",
    )
    sections = build_auto_sections(
        transcript_body=body,
        caption=caption or "unknown",
        links=links,
        tools=tools,
        about=about,
    )
    return render_note(title, frontmatter, sections)


# --------------------------------------------------------------------------- #
# Note loading / enrich
# --------------------------------------------------------------------------- #


def load_note(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[: end + 4]
            body = text[end + 4 :]
    title = ""
    tm = re.search(r"^#\s+(.+?)\s*$", body, flags=re.M)
    if tm:
        title = tm.group(1).strip()
    sections: dict[str, str] = {}
    for match in re.finditer(r"^##\s+(.+?)\s*\n(.*?)(?=^##\s+|\Z)", body, flags=re.M | re.S):
        sections[match.group(1).strip()] = match.group(2).strip()
    return {"frontmatter": fm, "title": title, "sections": sections}


def set_frontmatter_enriched(fm: str) -> str:
    if "enrichment:" in fm:
        return re.sub(r"enrichment:\s*\w+", "enrichment: done", fm)
    return fm.replace("---\n", "---\nenrichment: done\n", 1)


def update_frontmatter_list(fm: str, field: str, items: list[str]) -> str:
    """Merge new items into an existing ``field: [...]`` frontmatter line."""
    if not items:
        return fm
    match = re.search(rf"^{field}:\s*\[(.*)\]\s*$", fm, flags=re.M)
    existing = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)) if match else []
    merged = yaml_list([e.replace('\\"', '"') for e in existing] + items)
    if match:
        return fm[: match.start()] + f"{field}: {merged}" + fm[match.end():]
    return fm.replace("---\n", f"---\n{field}: {merged}\n", 1)


def enrich_note(
    path: Path,
    updates: dict[str, str],
    fm_tools: list[str] | None = None,
    fm_links: list[str] | None = None,
) -> Path:
    note = load_note(path)
    sections = note["sections"]
    for heading, content in updates.items():
        if heading in ENRICHABLE and content and content.strip():
            sections[heading] = content.strip()
    fm = set_frontmatter_enriched(note["frontmatter"]) or note["frontmatter"]
    fm = update_frontmatter_list(fm, "tools", fm_tools or [])
    fm = update_frontmatter_list(fm, "links", fm_links or [])
    path.write_text(render_note(note["title"], fm.rstrip(), sections), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Pipeline entry point
# --------------------------------------------------------------------------- #


def web_note_path(record: dict) -> Path:
    title = clean_filename_part(record.get("title") or record.get("url") or "external resource")
    date = record.get("date") or datetime.now().date().isoformat()
    return EXTERNAL_TRANSCRIPTS_DIR / f"{{link}} {title} – {date}.md"


def write_external_resource_note(config: dict, record: dict) -> Path:
    """Build (or refresh) the single rich note for a processed record.

    For video/transcript records the note REPLACES the raw transcript file at
    the same path. For web records the note is created from metadata.
    Existing enriched notes are not clobbered.
    """
    caption = read_text(record.get("brief_path"), max_chars=18000)
    transcript_path = record.get("transcript_path")

    if transcript_path and Path(transcript_path).exists():
        note_path = Path(transcript_path)
        existing = note_path.read_text(encoding="utf-8", errors="replace")
        # Already a rich note that an agent enriched? Leave the smart sections.
        if "enrichment: done" in existing[:600]:
            record["summary_path"] = str(note_path)
            return note_path
        raw_text = existing
    else:
        note_path = web_note_path(record)
        raw_text = f"# {record.get('title') or record.get('url') or 'External resource'}\n\n## Транскрипт\n\n_No transcript (web resource)._\n"

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(build_auto_note(record, raw_text, caption), encoding="utf-8")
    record["transcript_path"] = str(note_path)
    record["summary_path"] = str(note_path)
    record["note_md_path"] = str(note_path)
    return note_path


# --------------------------------------------------------------------------- #
# CLI: enrich an existing note (used by agents) + rebuild one note from raw
# --------------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or enrich a unified external-resource note.")
    parser.add_argument("--path", required=True, help="Path to the note to enrich/rebuild.")
    parser.add_argument("--rebuild-auto", action="store_true", help="Rebuild the auto note from a raw transcript file at --path.")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--essence", default="")
    parser.add_argument("--link", action="append", default=[], help="label=url or url")
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--insight", action="append", default=[])
    parser.add_argument("--solution", action="append", default=[])
    parser.add_argument("--anton-relevance", default="")
    parser.add_argument("--strategic", action="append", default=[])
    return parser.parse_args()


def _links_block(items: list[str]) -> str:
    rows = []
    for item in items:
        if "=" in item:
            label, url = item.split("=", 1)
            rows.append(f"- [{label.strip()}]({url.strip()})")
        else:
            rows.append(f"- {item.strip()}")
    return "\n".join(rows)


def main() -> int:
    args = parse_args()
    path = Path(args.path).expanduser()
    if args.rebuild_auto:
        record = {"url": args.source_url} if args.source_url else {}
        path.write_text(build_auto_note(record, path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        print(f"NOTE_REBUILT={path}")
        return 0

    updates: dict[str, str] = {}
    if args.summary:
        updates["Краткое содержание"] = bullet_block(args.summary, PENDING)
    if args.essence:
        updates["Суть"] = args.essence.strip()
    if args.link:
        updates["Полезные ссылки"] = _links_block(args.link)
    if args.tool:
        updates["Упомянутые инструменты и skills"] = bullet_block(args.tool, PENDING)
    if args.insight:
        updates["Главные инсайты"] = bullet_block(args.insight, PENDING)
    if args.solution:
        updates["Готовые решения / как применить"] = bullet_block(args.solution, PENDING)
    if args.anton_relevance:
        updates["Оценка применимости для Антона"] = args.anton_relevance.strip()
    if args.strategic:
        updates["Strategic Board analysis"] = bullet_block(args.strategic, "not applicable")
    fm_links = [item.split("=", 1)[1].strip() if "=" in item else item.strip() for item in args.link]
    enrich_note(path, updates, fm_tools=args.tool, fm_links=fm_links)
    print(f"NOTE_ENRICHED={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
