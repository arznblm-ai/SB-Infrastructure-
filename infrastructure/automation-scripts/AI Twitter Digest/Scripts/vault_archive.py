#!/usr/bin/env python3
"""
vault_archive.py — копилка выпусков AI Twitter Digest в vault.

После успешной отправки в Telegram тот же текст дополнительно кладётся в
`<vault>/infrastructure/AI Twitter Digest/digests/`, чтобы Гермес и другие
агенты могли читать прошлые выпуски. Telegram-бот остаётся как есть.

Правила:
- best-effort: любая ошибка сохранения пишется в лог и НЕ влияет на отправку
  и на двухфазный state (`last_seen_id` коммитится независимо);
- имя файла по конвенции vault: `{automation} {summary} AI дайджест утро – YYYY-MM-DD.md`
  (перед датой EN DASH, ≤80 символов);
- часть суток и дата берутся из заголовка выпуска («🤖 AI Twitter — утро 2026-08-07»),
  fallback — текущее время (утро, если час < 15);
- строка выпуска дозаписывается в `digests/index.md`.

Резолв vault (скрипт исполняется ВНЕ vault, в /opt — по расположению файла не определить):
    1) env SECOND_BRAIN_VAULT
    2) /root/second-brain, если существует (VPS)
    3) /Users/anton/AI AGENT FOLDER/Second Brain (мак)

CLI (смоук/ручное сохранение):
    vault_archive.py --file /tmp/digest.txt
    vault_archive.py --text "smoke test" --part утро --date 1970-01-01
    vault_archive.py --where          # показать, куда будет писать
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "ai-twitter-digest.log"

VAULT_ENV = "SECOND_BRAIN_VAULT"
VPS_VAULT = Path("/root/second-brain")
MAC_VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
PROJECT_SUBDIR = Path("infrastructure") / "AI Twitter Digest"
DIGESTS_DIRNAME = "digests"
INDEX_NAME = "index.md"

MORNING_BEFORE_HOUR = 15
EN_DASH = "–"
FILENAME_MAX_CHARS = 80

PART_MORNING = "утро"
PART_EVENING = "вечер"

# «🤖 AI Twitter — утро 2026-08-07» (тире может быть любым, часть/дата — обязательны)
HEADER_RE = re.compile(
    r"AI\s+Twitter\s*[—–-]?\s*(утро|вечер)\s+(\d{4}-\d{2}-\d{2})"
)

INDEX_HEADER = (
    "# AI Twitter Digest — выпуски\n"
    "\n"
    "Копилка отправленных дайджестов: каждый выпуск после успешной отправки в Telegram\n"
    "сохраняется в эту папку автоматически (`Scripts/vault_archive.py`).\n"
    "\n"
)


def log(message: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] vault: {message}\n")
    except OSError:
        pass
    print(f"vault: {message}", file=sys.stderr, flush=True)


# ── Пути ─────────────────────────────────────────────────────────────────

def resolve_vault() -> Path:
    """Корень vault: env → /root/second-brain (VPS) → мак-путь."""
    raw = os.environ.get(VAULT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    if VPS_VAULT.is_dir():
        return VPS_VAULT
    return MAC_VAULT


def digests_dir(vault: Path | None = None) -> Path:
    return (vault or resolve_vault()) / PROJECT_SUBDIR / DIGESTS_DIRNAME


# ── Имя и содержимое заметки ─────────────────────────────────────────────

def part_for_hour(hour: int) -> str:
    return PART_MORNING if hour < MORNING_BEFORE_HOUR else PART_EVENING


def parse_header(text: str, now: dt.datetime) -> tuple[str, str]:
    """(дата, часть суток) из заголовка выпуска; fallback — текущее время."""
    match = HEADER_RE.search(text or "")
    if match:
        return match.group(2), match.group(1)
    return now.strftime("%Y-%m-%d"), part_for_hour(now.hour)


def note_name(date_str: str, part: str) -> str:
    """`{automation} {summary} AI дайджест утро – YYYY-MM-DD` (без .md)."""
    name = f"{{automation}} {{summary}} AI дайджест {part} {EN_DASH} {date_str}"
    if len(name) + len(".md") > FILENAME_MAX_CHARS:
        log(f"имя длиннее {FILENAME_MAX_CHARS} символов: {name}.md")
    return name


def note_body(text: str, date_str: str, part: str) -> str:
    front = (
        "---\n"
        f"date: {date_str}\n"
        f"part: {part}\n"
        "source: ai-twitter-digest\n"
        "---\n\n"
    )
    return front + (text or "").strip() + "\n"


def index_line(name: str, date_str: str, part: str) -> str:
    return f"- [[{name}]] — {date_str} {part}"


def append_index(index_path: Path, line: str) -> bool:
    """Дозапись строки в index.md. Повторный вызов с той же строкой ничего не делает."""
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if not existing.strip():
        existing = INDEX_HEADER
    elif line in existing.splitlines():
        return False
    if not existing.endswith("\n"):
        existing += "\n"
    index_path.write_text(existing + line + "\n", encoding="utf-8")
    return True


# ── Основная операция ────────────────────────────────────────────────────

def archive_digest(
    text: str,
    now: dt.datetime | None = None,
    vault: Path | None = None,
    part: str | None = None,
    date_str: str | None = None,
) -> Path:
    """Сохранить выпуск в vault и дозаписать строку индекса. Бросает исключение при ошибке."""
    now = now or dt.datetime.now()
    parsed_date, parsed_part = parse_header(text, now)
    date_str = date_str or parsed_date
    part = part or parsed_part

    target_dir = digests_dir(vault)
    target_dir.mkdir(parents=True, exist_ok=True)

    name = note_name(date_str, part)
    note_path = target_dir / f"{name}.md"
    note_path.write_text(note_body(text, date_str, part), encoding="utf-8")
    append_index(target_dir / INDEX_NAME, index_line(name, date_str, part))
    return note_path


def archive_digest_safe(
    text: str,
    now: dt.datetime | None = None,
    vault: Path | None = None,
    part: str | None = None,
    date_str: str | None = None,
) -> Path | None:
    """Best-effort обёртка: ошибка → строка в лог, None; вызывающий код не страдает."""
    try:
        path = archive_digest(text, now=now, vault=vault, part=part, date_str=date_str)
    except Exception as exc:  # noqa: BLE001 — копилка не имеет права ронять выпуск
        log(f"копилка не сохранила выпуск ({type(exc).__name__}: {exc})")
        return None
    log(f"выпуск сохранён в vault: {path}")
    return path


def is_digest(text: str) -> bool:
    """Это выпуск (а не ❌-алерт)? Отличаем по заголовку из digest_builder.header()."""
    return bool(HEADER_RE.search(text or ""))


# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сохранение выпуска AI Twitter Digest в vault")
    parser.add_argument("--file", help="файл с текстом выпуска (по умолчанию stdin)")
    parser.add_argument("--text", help="сохранить эту строку вместо файла/stdin")
    parser.add_argument("--part", choices=[PART_MORNING, PART_EVENING],
                        help="часть суток (по умолчанию из заголовка выпуска / по часу)")
    parser.add_argument("--date", help="дата YYYY-MM-DD (по умолчанию из заголовка / сегодня)")
    parser.add_argument("--where", action="store_true",
                        help="показать каталог копилки и выйти")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.where:
        print(digests_dir())
        return 0

    if args.text is not None:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print("--date ожидает формат YYYY-MM-DD", file=sys.stderr)
        return 2

    path = archive_digest_safe(text, part=args.part, date_str=args.date)
    if path is None:
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
