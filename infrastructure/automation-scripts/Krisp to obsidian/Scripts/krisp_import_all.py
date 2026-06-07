#!/usr/bin/env python3
"""
Mass import recent Krisp meetings with ready transcripts into the vault.

Uses the current Krisp MCP integration from krisp_to_obsidian.py, dedupes by
krisp_mcp_id, and writes notes into Second Brain/transcripts.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HELPER_PATH = SCRIPT_DIR / "krisp_to_obsidian.py"


def load_helper_module():
    spec = importlib.util.spec_from_file_location("krisp_to_obsidian", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


helper = load_helper_module()
VAULT_MEETINGS = helper.VAULT_MEETINGS
DEFAULT_LOOKBACK_DAYS = 45


def dedupe_meetings(meetings: list[dict]) -> list[dict]:
    unique = []
    seen_ids = set()
    for meeting in meetings:
        meeting_id = meeting.get("id")
        if not meeting_id or meeting_id in seen_ids:
            continue
        seen_ids.add(meeting_id)
        unique.append(meeting)
    return unique


def make_base_note(meeting: dict) -> str:
    date_str = (
        meeting["start_time"].strftime("%Y-%m-%d")
        if meeting.get("start_time")
        else "unknown"
    )
    time_str = (
        meeting["start_time"].strftime("%H:%M")
        if meeting.get("start_time")
        else ""
    )
    meta_lines = []
    if time_str:
        meta_lines.append(f"**Дата:** {date_str} {time_str}")
    else:
        meta_lines.append(f"**Дата:** {date_str}")

    return (
        "---\n"
        "tags:\n"
        "  - type/meeting\n"
        f"date: {date_str}\n"
        "status: active\n"
        "source: krisp\n"
        f"krisp_mcp_id: {meeting['id']}\n"
        "---\n\n"
        f"# {meeting['name']}\n\n"
        + "\n".join(meta_lines)
        + "\n\n"
    )


def choose_target_path(meeting: dict, note_index: dict) -> Path:
    date_str = (
        meeting["start_time"].strftime("%Y-%m-%d")
        if meeting.get("start_time")
        else "unknown"
    )
    clean_name = helper.clean_name_for_filename(meeting["name"])
    desired = VAULT_MEETINGS / f"{{meeting}} {clean_name} – {date_str}.md"

    candidates = list(note_index["mcp_ids"].get(meeting["id"], []))
    if desired.exists():
        candidates.append(desired)

    best_existing = helper.choose_best_note_path(candidates)
    return best_existing or desired


def collect_recent_meetings(lookback_days: int) -> list[dict]:
    """Collect recent meetings by querying each date to avoid hardcoded cutoffs."""
    today = datetime.now().date()
    cutoff = today - timedelta(days=lookback_days - 1)
    meetings = []
    for offset in range(lookback_days):
        query_date = (today - timedelta(days=offset)).isoformat()
        for meeting in helper.mcp_search_meetings(query_date):
            start_time = meeting.get("start_time")
            if start_time is None:
                continue
            if cutoff <= start_time.date() <= today:
                meetings.append(meeting)
    return dedupe_meetings(meetings)


def import_recent_meetings(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> int:
    meetings = collect_recent_meetings(lookback_days)
    if not meetings:
        print("Не удалось получить список встреч из Krisp MCP.")
        return 1

    meetings.sort(
        key=lambda item: item["start_time"].isoformat() if item.get("start_time") else ""
    )
    VAULT_MEETINGS.mkdir(parents=True, exist_ok=True)

    note_index = helper.build_note_index(VAULT_MEETINGS)
    created = 0
    updated = 0
    skipped = 0

    for meeting in meetings:
        if not meeting.get("transcript_ready"):
            print(f"- пропущено, транскрипт ещё не готов: {meeting['name']}")
            skipped += 1
            continue

        doc_text = helper.mcp_get_document(meeting["id"])
        if not doc_text:
            print(f"- пропущено, документ пустой: {meeting['name']}")
            skipped += 1
            continue

        mcp_section = helper.build_mcp_transcript_section(doc_text)
        if not mcp_section:
            print(f"- пропущено, не удалось собрать секции: {meeting['name']}")
            skipped += 1
            continue

        filepath = choose_target_path(meeting, note_index)
        is_new = not filepath.exists()

        if is_new:
            filepath.write_text(make_base_note(meeting), encoding="utf-8")

        helper.ensure_note_metadata(filepath, "", meeting["id"])
        helper.inject_mcp_content(filepath, mcp_section)

        if is_new:
            created += 1
            print(f"+ создано: {filepath.name}")
        else:
            updated += 1
            print(f"↻ обновлено: {filepath.name}")

        note_index = helper.build_note_index(VAULT_MEETINGS)

    print(f"\nИтого: создано {created}, обновлено {updated}, пропущено {skipped}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"How many days back to query Krisp MCP (default: {DEFAULT_LOOKBACK_DAYS}).",
    )
    args = parser.parse_args()
    raise SystemExit(import_recent_meetings(args.lookback_days))
