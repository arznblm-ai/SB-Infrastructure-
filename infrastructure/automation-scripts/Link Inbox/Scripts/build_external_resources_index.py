#!/usr/bin/env python3
"""Build the Saved Links external resources index (note-centric).

After the pipeline merge there is ONE rich note per external resource in
`transcripts/external resources/`. This index scans those unified notes
(reading their frontmatter + `Краткое содержание`) and lists them as the
first-read map for agents, cross-referenced with Link Inbox state for
pending/failed links.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from link_inbox_common import load_config, load_state, paths

INDEX_FILENAME = "index.md"
SKIP_NAMES = {"index.md", "readme.md"}
VAULT_PREFIX = "/Users/anton/AI AGENT FOLDER/Second Brain/"


def short_path(path: str | None) -> str:
    return (path or "").replace(VAULT_PREFIX, "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Saved Links external resources index.")
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().replace("|", "\\|")


def read_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r'^([a-z_]+):\s*(.*)$', line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        fm[key] = value
    return fm


def section_text(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def short_summary(text: str, max_chars: int = 320) -> str:
    body = section_text(text, "Краткое содержание")
    body = re.sub(r"^[-*]\s*", "", body, flags=re.M)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:max_chars].rstrip()


def scan_notes(transcript_dir: Path) -> list[dict]:
    notes = []
    for path in sorted(transcript_dir.glob("*.md")):
        if path.name.lower() in SKIP_NAMES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "type: external-resource" not in text[:200]:
            continue
        fm = read_frontmatter(text)
        notes.append(
            {
                "path": path,
                "title": fm.get("title") or path.stem,
                "platform": fm.get("platform", "web"),
                "source_url": fm.get("source_url", ""),
                "published_at": fm.get("published_at", ""),
                "captured_at": fm.get("captured_at", ""),
                "enrichment": fm.get("enrichment", "pending"),
                "summary": short_summary(text),
            }
        )
    notes.sort(key=lambda n: (n["published_at"] or n["captured_at"] or ""), reverse=True)
    return notes


def format_note(note: dict) -> str:
    badge = "✅ enriched" if note["enrichment"] == "done" else "🟡 pending"
    lines = [
        f"### {clean_cell(note['title'])}",
        "",
        f"- **Platform:** `{note['platform']}`  ·  **Enrichment:** {badge}  ·  **Published:** {note['published_at'] or 'unknown'}",
    ]
    if note["source_url"]:
        lines.append(f"- **URL:** {note['source_url']}")
    lines.append(f"- **Note:** `{short_path(str(note['path']))}`")
    if note["summary"]:
        lines.extend(["", f"- {clean_cell(note['summary'])}"])
    return "\n".join(lines).strip()


def build_index(config: dict, state: dict) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    transcript_dir = paths(config)["transcripts"]
    notes = scan_notes(transcript_dir)
    enriched = sum(1 for n in notes if n["enrichment"] == "done")
    pending_notes = len(notes) - enriched

    records = sorted(
        state.get("links", {}).items(),
        key=lambda kv: str(kv[1].get("processed_at") or kv[1].get("created_at") or kv[1].get("date") or ""),
        reverse=True,
    )
    unprocessed = [
        (uid, rec)
        for uid, rec in records
        if rec.get("status") in {"new", "failed", "needs_manual_processing"}
    ]

    lines = [
        "# Saved Links External Resources Index",
        "",
        "> **Thesis:** first-read map for agents that need context from links Anton saved via the Telegram bot. One rich note per resource lives here; start with this index, then open the single note.",
        "",
        f"Generated: `{generated_at}`",
        "",
        "## Agent Routing",
        "",
        "- Start here when the task mentions saved links, external videos, Instagram/TikTok/YouTube/X references, or internet posts sent to the Telegram bot.",
        "- Retrieval order: `Index -> Note`. Each note is self-contained: краткое содержание, суть, полезные ссылки, инструменты, инсайты, готовые решения, Strategic Board, транскрипт.",
        "- `enrichment: pending` = smart sections not yet filled by an agent (auto tier only). `done` = analyzed. Run the enrich step to upgrade a pending note.",
        "- `Card` in `resources/link-inbox/links/` holds source URL, status and the original Telegram message.",
        "- Do not mix these notes with personal meetings (`meetings/`) or course notes (`education/`); this folder is external-source context only.",
        "",
        "## Current Counts",
        "",
        f"- Unified notes: {len(notes)}  (✅ enriched: {enriched}, 🟡 pending: {pending_notes})",
        f"- Pending / failed links not yet processed: {len(unprocessed)}",
        "",
        "## Resources",
        "",
    ]

    if notes:
        for note in notes:
            lines.extend([format_note(note), ""])
    else:
        lines.extend(["_No unified notes yet._", ""])

    lines.extend(["## Pending / Failed Links", ""])
    if unprocessed:
        lines.append("| Date | Status | Kind | Title / URL |")
        lines.append("|---|---|---|---|")
        for _, rec in unprocessed:
            title = clean_cell(rec.get("title") or rec.get("url") or "Saved link")
            lines.append(f"| {rec.get('date', '')} | {rec.get('status', '')} | {rec.get('kind', '')} | {title} |")
    else:
        lines.append("_No pending or failed links._")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    state = load_state(config)
    index_path = paths(config)["transcripts"] / INDEX_FILENAME
    index_path.write_text(build_index(config, state), encoding="utf-8")
    print(f"EXTERNAL_RESOURCES_INDEX={index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
