#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from link_digest import (
    build_about,
    build_rich_summary,
    bullets,
    extract_numbered_tools,
    read_text,
    russian_tool_desc,
    section,
    short_path,
    source_fields,
    transcript_excerpt,
    transcript_lines,
)
from link_inbox_common import load_config, load_state, paths


VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
INDEX_FILENAME = "index.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Saved Links external resources index.")
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def rel(path: str | None) -> str:
    if not path:
        return ""
    return short_path(path)


def title_from_transcript(path: str | None) -> str:
    text = read_text(path, max_chars=4000)
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    if path:
        return Path(path).stem
    return ""


def brief_summary(record: dict) -> tuple[list[str], list[str]]:
    brief = read_text(record.get("brief_path"), max_chars=16000)
    transcript = read_text(record.get("transcript_path"), max_chars=12000)
    summary: list[str] = []
    insights: list[str] = []
    meta = source_fields(brief)
    lines = transcript_lines(transcript)
    tools = extract_numbered_tools(lines)
    rich = build_rich_summary(record, meta, transcript, lines, tools)

    if tools:
        summary = [str(rich.get("about") or build_about(tools, lines, meta.get("description", "")))]
        insights = [f"{name}: {russian_tool_desc(name, desc)}" for name, desc in tools[:5]]
        return summary, insights

    if brief:
        core = section(brief, "Core Read") or section(brief, "Краткое содержание")
        summary = bullets(core, limit=3)
        insight_section = (
            section(brief, "Главные инсайты")
            or section(brief, "Remix Angles")
            or section(brief, "Beat Map")
        )
        insights = bullets(insight_section, limit=3)

    if not summary and record.get("excerpt"):
        summary = [str(record["excerpt"]).strip()]
    if not summary and transcript:
        summary = [str(rich.get("about") or transcript_excerpt(transcript, max_chars=360))]
    if not insights and transcript:
        insights = [str(item) for item in list(rich.get("insights") or [])[:3]]
        if not insights:
            excerpt = transcript_excerpt(transcript, max_chars=360)
            if excerpt:
                insights = [excerpt]
    return summary, insights


def clean_cell(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = value.replace("|", "\\|")
    return value


def record_sort_key(item: tuple[str, dict]) -> str:
    _, record = item
    return str(record.get("processed_at") or record.get("created_at") or record.get("date") or "")


def format_resource(uid: str, record: dict) -> str:
    title = (
        record.get("title")
        or title_from_transcript(record.get("transcript_path"))
        or record.get("url")
        or "Saved resource"
    )
    summary, insights = brief_summary(record)
    lines = [
        f"### {clean_cell(title)}",
        "",
        f"- **ID:** `{uid}`",
        f"- **Kind:** `{record.get('kind', 'web')}`",
        f"- **Status:** `{record.get('status', 'unknown')}`",
        f"- **Date:** {record.get('date', 'unknown')}",
        f"- **URL:** {record.get('url', '')}",
    ]
    if record.get("note_path"):
        lines.append(f"- **Card:** `{rel(record.get('note_path'))}`")
    if record.get("summary_path"):
        lines.append(f"- **Summary:** `{rel(record.get('summary_path'))}`")
    if record.get("transcript_path"):
        lines.append(f"- **Transcript:** `{rel(record.get('transcript_path'))}`")
    if record.get("brief_path"):
        lines.append(f"- **Brief:** `{rel(record.get('brief_path'))}`")
    if record.get("video_path"):
        lines.append(f"- **Video:** `{rel(record.get('video_path'))}`")
    if summary:
        lines.extend(["", "**Короткое содержание:**"])
        lines.extend(f"- {clean_cell(item)}" for item in summary[:3] if item)
    if insights:
        lines.extend(["", "**Инсайты / что можно вытащить в контекст:**"])
        lines.extend(f"- {clean_cell(item)}" for item in insights[:3] if item)
    if record.get("error"):
        lines.extend(["", f"**Ошибка обработки:** {record['error']}"])
    return "\n".join(lines).strip()


def orphan_transcripts(config: dict, indexed_paths: set[str]) -> list[Path]:
    transcript_dir = paths(config)["transcripts"]
    files = []
    for path in sorted(transcript_dir.glob("*.md")):
        if path.name.lower() in {"readme.md", INDEX_FILENAME}:
            continue
        if str(path) not in indexed_paths:
            files.append(path)
    return files


def build_index(config: dict, state: dict) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records = sorted(state.get("links", {}).items(), key=record_sort_key, reverse=True)
    processed = [(uid, record) for uid, record in records if record.get("status") == "processed"]
    pending = [
        (uid, record)
        for uid, record in records
        if record.get("status") in {"new", "failed", "needs_manual_processing"}
    ]
    indexed_paths = {str(record.get("transcript_path")) for _, record in records if record.get("transcript_path")}
    orphans = orphan_transcripts(config, indexed_paths)

    lines = [
        "# Saved Links External Resources Index",
        "",
        "> **Thesis:** this index is the first-read map for agents that need context from links Anton saved in Telegram. Start here, then open the linked card, brief, or transcript only when needed.",
        "",
        f"Generated: `{generated_at}`",
        "",
        "## Agent Routing",
        "",
        "- Start with this file when the task mentions saved links, external videos, Instagram/TikTok/YouTube references, or internet posts sent to the Telegram bot.",
        "- Cost-saving retrieval order: `Index -> Summary -> Transcript only if needed`.",
        "- Use `Summary` first for Anton-readable context and agent reuse.",
        "- Open `Transcript` only when exact wording, quotes, timestamps, or missing details are needed.",
        "- Use `Brief` for content mechanics and lower-level extraction details.",
        "- Use `Card` for source URL, status, and original Telegram message.",
        "- Do not scan every transcript before reading this index and the relevant summaries.",
        "- Do not mix these files with personal meetings or course notes; this folder is external-source context only.",
        "",
        "## Current Counts",
        "",
        f"- Processed resources: {len(processed)}",
        f"- Pending / failed resources: {len(pending)}",
        f"- Orphan transcript files not linked from state: {len(orphans)}",
        "",
        "## Processed Resources",
        "",
    ]

    if processed:
        for uid, record in processed:
            lines.extend([format_resource(uid, record), ""])
    else:
        lines.append("_No processed saved links yet._")
        lines.append("")

    lines.extend(["## Pending / Failed Resources", ""])
    if pending:
        lines.append("| Date | Status | Kind | Title / URL | Card |")
        lines.append("|---|---|---|---|---|")
        for _, record in pending:
            title = clean_cell(record.get("title") or record.get("url") or "Saved link")
            card = f"`{rel(record.get('note_path'))}`" if record.get("note_path") else ""
            lines.append(
                f"| {record.get('date', '')} | {record.get('status', '')} | {record.get('kind', '')} | {title} | {card} |"
            )
    else:
        lines.append("_No pending or failed saved links._")
    lines.append("")

    lines.extend(["## Orphan External Transcripts", ""])
    if orphans:
        lines.append("These files exist in `transcripts/external resources/` but are not linked from current Link Inbox state.")
        lines.append("")
        for path in orphans:
            lines.append(f"- `{rel(str(path))}`")
    else:
        lines.append("_No orphan external transcripts._")
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
