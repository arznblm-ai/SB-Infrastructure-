#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from link_digest import (
    build_about,
    extract_numbered_tools,
    extract_urls,
    human_title,
    read_text,
    russian_tool_desc,
    short_path,
    source_fields,
    transcript_excerpt,
    transcript_lines,
)
from link_inbox_common import clean_filename_part, load_config, load_state, paths, save_state, write_link_note


def summary_note_path(config: dict, record: dict) -> Path:
    title = clean_filename_part(human_title(record, source_fields(read_text(record.get("brief_path")))), max_len=72)
    return paths(config)["summaries"] / f"{{link}} {{summary}} {title} – {record.get('date')}.md"


def md_link(path: str | None) -> str:
    if not path:
        return "`not available`"
    return f"`{short_path(path)}`"


def build_summary_note(record: dict) -> str:
    brief = read_text(record.get("brief_path"), max_chars=18000)
    transcript = read_text(record.get("transcript_path"), max_chars=16000)
    meta = source_fields(brief)
    lines = transcript_lines(transcript)
    tools = extract_numbered_tools(lines)
    title = human_title(record, meta)
    source_url = record.get("url", "")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    about = build_about(tools, lines, meta.get("description", "") or record.get("excerpt", ""))

    tool_lines = []
    for name, desc in tools[:8]:
        tool_lines.append(f"- **{name}:** {russian_tool_desc(name, desc)}")
    if not tool_lines:
        tool_lines.append("- Инструменты явно не извлечены. Смотри transcript/brief для деталей.")

    system_lines = []
    if tools:
        system_lines.extend(
            [
                "- Автор собирает workflow из готовых референсов: UI-компоненты, дизайн-DNA сайтов, каталоги экранов, галереи сильных сайтов.",
                "- Эти ресурсы используются как input для AI/vibe coding, чтобы не начинать интерфейс и продуктовую механику с пустого листа.",
            ]
        )
        if any(name.lower() in {"10x", "10 x"} for name, _ in tools):
            system_lines.append("- Финальный слой: инструмент, который обещает превратить app idea в native iOS app / reusable UI code / assets за минуты.")
        system_lines.append("- Практическая польза: быстрее собрать убедительный прототип и повысить визуальное качество результата.")
    else:
        system_lines.append("- Система автора не выделена автоматически. Нужен ручной review transcript/brief.")

    urls = extract_urls(brief, record.get("message_text", ""))
    if source_url and source_url not in urls:
        urls.insert(0, source_url)
    url_lines = [f"- {url}" for url in urls[:8]] or ["- not found"]

    excerpt = transcript_excerpt(transcript, max_chars=900) if transcript else ""

    return "\n".join(
        [
            "---",
            "tags:",
            "  - type/summary",
            "  - source/link-inbox",
            "  - topic/external-resource",
            f"date: {record.get('date')}",
            "status: active",
            f"source_url: \"{source_url}\"",
            f"kind: {record.get('kind', 'web')}",
            f"generated_at: \"{generated_at}\"",
            f"transcript: \"{short_path(record.get('transcript_path'))}\"",
            f"brief: \"{short_path(record.get('brief_path'))}\"",
            f"card: \"{short_path(record.get('note_path'))}\"",
            "---",
            "",
            f"# {title}",
            "",
            f"> **Thesis:** {about}",
            "",
            "## Краткое содержание",
            "",
            f"- {about}",
            "",
            "## Инструменты / ресурсы",
            "",
            *tool_lines,
            "",
            "## Система автора",
            "",
            *system_lines,
            "",
            "## Полезные ссылки",
            "",
            *url_lines,
            "",
            "## Где лежит сырьё",
            "",
            f"- Card: {md_link(record.get('note_path'))}",
            f"- Transcript: {md_link(record.get('transcript_path'))}",
            f"- Brief: {md_link(record.get('brief_path'))}",
            f"- Video: {md_link(record.get('video_path'))}",
            "",
            "## Transcript excerpt",
            "",
            excerpt or "_Transcript unavailable._",
            "",
        ]
    )


def write_summary_note(config: dict, record: dict) -> Path:
    path = summary_note_path(config, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_summary_note(record), encoding="utf-8")
    record["summary_path"] = str(path)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create summary notes for processed Link Inbox records.")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    state = load_state(config)
    target_ids = set(args.ids or [])
    written = []
    for uid, record in state.get("links", {}).items():
        if target_ids and uid not in target_ids:
            continue
        if record.get("status") != "processed":
            continue
        path = write_summary_note(config, record)
        write_link_note(config, record)
        written.append(path)
    save_state(config, state)
    for path in written:
        print(f"SUMMARY_PATH={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
