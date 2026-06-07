#!/usr/bin/env python3
"""
claude_to_obsidian.py — Запись транскрипта Claude-сессии в Obsidian vault.

Скрипт вызывается Claude-агентом (scheduled task), который передаёт данные сессии.
Агент читает транскрипты через MCP-инструменты (list_sessions / read_transcript),
скрипт берёт на себя именование по конвенции и запись файлов.

Использование (агент вызывает через bash):
    python3 claude_to_obsidian.py \\
        --session-id "local_abc123" \\
        --date "2026-03-28" \\
        --topic "организация vault" \\
        --transcript-file /tmp/transcript.txt

    # Или через stdin:
    echo "текст..." | python3 claude_to_obsidian.py \\
        --session-id "local_abc123" \\
        --date "2026-03-28" \\
        --topic "организация vault"
"""

import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

# ── Настройки ──────────────────────────────────────────────────────────────────

VAULT_ROOT = Path.home() / "AI AGENT FOLDER" / "Second Brain"
SESSIONS_DIR = VAULT_ROOT / "sessions"
EN_DASH = "–"
MAX_FILENAME_LEN = 80

# ── Именование по конвенции ────────────────────────────────────────────────────

def build_filename(topic: str, date: str) -> str:
    """
    Формирует имя файла по naming convention:
    {self} {transcript} Claude [тема] – YYYY-MM-DD.md
    """
    suffix = f" {EN_DASH} {date}.md"
    prefix = "{self} {transcript} Claude "
    max_topic = MAX_FILENAME_LEN - len(prefix) - len(suffix)
    if len(topic) > max_topic:
        topic = topic[:max_topic].rstrip()
    return f"{prefix}{topic}{suffix}"


def unique_path(directory: Path, filename: str) -> Path:
    """Возвращает уникальный путь, добавляя _2, _3 при коллизии."""
    stem = filename[:-3]  # убираем .md
    path = directory / filename
    counter = 2
    while path.exists():
        path = directory / f"{stem}_{counter}.md"
        counter += 1
    return path

# ── Форматирование файла ───────────────────────────────────────────────────────

def build_content(topic: str, date: str, session_id: str, transcript: str) -> str:
    return f"""---
tags:
  - type/transcript
  - project/self
  - source/claude
date: {date}
status: archive
session_id: {session_id}
---

# Claude сессия: {topic}
**Дата:** {date}

{transcript.strip()}
"""

# ── Точка входа ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Сохраняет транскрипт Claude-сессии в Obsidian vault"
    )
    parser.add_argument("--session-id", required=True,
                        help="ID сессии Claude (например, local_abc123)")
    parser.add_argument("--date",
                        default=datetime.today().strftime("%Y-%m-%d"),
                        help="Дата сессии в формате YYYY-MM-DD (по умолчанию сегодня)")
    parser.add_argument("--topic", required=True,
                        help="Краткая тема на русском, 2-4 слова (например, 'организация vault')")
    parser.add_argument("--transcript-file",
                        help="Путь к файлу с транскриптом (иначе читается из stdin)")
    parser.add_argument("--vault-root",
                        help="Путь к vault (переопределяет VAULT_ROOT из скрипта)")
    args = parser.parse_args()

    # Vault root можно переопределить флагом или переменной окружения
    vault_root = Path(args.vault_root) if args.vault_root else \
                 Path(os.environ.get("OBSIDIAN_VAULT", VAULT_ROOT))
    sessions_dir = vault_root / "sessions"

    # Читаем транскрипт
    if args.transcript_file:
        transcript_path = Path(args.transcript_file)
        if not transcript_path.exists():
            print(f"Ошибка: файл не найден: {transcript_path}", file=sys.stderr)
            sys.exit(1)
        transcript = transcript_path.read_text(encoding="utf-8")
    else:
        transcript = sys.stdin.read()

    if not transcript.strip():
        print("Ошибка: пустой транскрипт", file=sys.stderr)
        sys.exit(1)

    # Создаём папку sessions/ если нет
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Строим путь и пишем файл
    filename = build_filename(args.topic, args.date)
    output_path = unique_path(sessions_dir, filename)
    content = build_content(args.topic, args.date, args.session_id, transcript)

    output_path.write_text(content, encoding="utf-8")
    print(f"✓ Сохранено: {output_path.name}")


if __name__ == "__main__":
    main()
