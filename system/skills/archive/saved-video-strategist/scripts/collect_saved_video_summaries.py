#!/usr/bin/env python3
"""Collect compact Link Inbox summary packets for strategic review."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


SECOND_BRAIN = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
INDEX_PATH = SECOND_BRAIN / "transcripts/external resources/index.md"
SUMMARIES_DIR = SECOND_BRAIN / "resources/link-inbox/summaries"
STATE_PATH = Path("/Users/anton/.config/link-inbox/state.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect saved video summaries from Link Inbox.")
    parser.add_argument("--date", help="Only include items created/processed on YYYY-MM-DD.")
    parser.add_argument("--days", type=int, default=1, help="Look back this many days from today when --date is omitted.")
    parser.add_argument("--max-chars", type=int, default=9000, help="Maximum summary text characters per item.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    return parser.parse_args()


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"records": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"records": {}}


def as_date(value: str | None) -> date | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def target_dates(args: argparse.Namespace) -> set[date]:
    if args.date:
        return {datetime.strptime(args.date, "%Y-%m-%d").date()}
    today = date.today()
    return {today - timedelta(days=offset) for offset in range(max(args.days, 1))}


def find_summary(record: dict[str, Any]) -> Path | None:
    explicit = record.get("summary_path")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path

    record_id = record.get("id")
    title = (record.get("title") or "").lower()
    candidates = sorted(SUMMARIES_DIR.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        name = path.name.lower()
        if record_id and record_id in path.read_text(encoding="utf-8", errors="ignore")[:2000]:
            return path
        if title and any(part for part in title.split()[:4] if len(part) > 4 and part in name):
            return path
    return None


def weak_markers(text: str) -> list[str]:
    markers: list[str] = []
    lowered = text.lower()
    if "low confidence" in lowered or "низкая уверенность" in lowered:
        markers.append("low-confidence summary")
    if "инструменты явно не извлечены" in lowered:
        markers.append("tools not extracted")
    if "transcript quality" in lowered or "asr" in lowered:
        markers.append("transcript-quality warning")
    if len(text.strip()) < 800:
        markers.append("short summary")
    return markers


def record_date(record: dict[str, Any]) -> date | None:
    for key in ("date", "processed_at", "created_at", "received_at", "updated_at"):
        value = as_date(record.get(key))
        if value:
            return value
    return None


def collect(args: argparse.Namespace) -> list[dict[str, Any]]:
    state = load_state()
    dates = target_dates(args)
    items: list[dict[str, Any]] = []
    records = state.get("records") or state.get("links") or {}

    for record_id, record in records.items():
        if not isinstance(record, dict):
            continue
        item_date = record_date(record)
        if item_date not in dates:
            continue
        if record.get("status") not in (None, "processed", "done"):
            continue

        summary_path = find_summary(record)
        summary_text = ""
        if summary_path and summary_path.exists():
            summary_text = summary_path.read_text(encoding="utf-8", errors="ignore")[: args.max_chars]

        items.append(
            {
                "id": record.get("id") or record_id,
                "date": item_date.isoformat() if item_date else "",
                "title": record.get("title") or record.get("source_title") or "Untitled saved resource",
                "source_url": record.get("source_url") or record.get("url") or "",
                "summary_path": str(summary_path) if summary_path else "",
                "transcript_path": record.get("transcript_path") or "",
                "brief_path": record.get("brief_path") or "",
                "weak_markers": weak_markers(summary_text),
                "summary": summary_text.strip(),
            }
        )

    items.sort(key=lambda item: (item["date"], item["title"]))
    return items


def emit_markdown(items: list[dict[str, Any]]) -> str:
    lines: list[str] = [
        "# Saved Video Strategy Packet",
        "",
        f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"- index: {INDEX_PATH}",
        f"- items: {len(items)}",
        "",
    ]
    for item in items:
        lines.extend(
            [
                f"## {item['title']}",
                "",
                f"- id: {item['id']}",
                f"- date: {item['date']}",
                f"- source: {item['source_url']}",
                f"- summary: {item['summary_path'] or 'missing'}",
                f"- transcript: {item['transcript_path'] or 'missing'}",
                f"- brief: {item['brief_path'] or 'missing'}",
                f"- quality_flags: {', '.join(item['weak_markers']) if item['weak_markers'] else 'ok'}",
                "",
                item["summary"] or "_No summary text found._",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    items = collect(args)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        print(emit_markdown(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
