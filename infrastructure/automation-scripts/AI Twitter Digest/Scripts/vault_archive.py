#!/usr/bin/env python3
"""
vault_archive.py — копилка выпусков AI Twitter Digest в vault.

После успешной отправки в Telegram тот же текст дополнительно кладётся в
`<vault>/infrastructure/AI Twitter Digest/digests/`, чтобы Гермес и другие
агенты могли читать прошлые выпуски. Telegram-бот остаётся как есть.

Правила:
- best-effort: любая ошибка сохранения пишется в лог и НЕ влияет на отправку
  и на двухфазный state (`last_seen_id` коммитится независимо);
- имя файла по конвенции vault: `{automation} {summary} AI дайджест – YYYY-MM-DD.md`
  (дата отправки, перед датой EN DASH, ≤80 символов);
- накопительный режим: выпуск покрывает несколько суток, поэтому «утро/вечер»
  в имени больше нет, а охват периода живёт во frontmatter
  (`period_start` / `period_end`) — из заголовка выпуска или из CLI-аргументов;
- старый заголовок («🤖 AI Twitter — утро 2026-08-07») по-прежнему разбирается:
  часть суток попадает во frontmatter, дата — в имя файла;
- строка выпуска дозаписывается в `digests/index.md`.

Резолв vault (скрипт исполняется ВНЕ vault, в /opt — по расположению файла не определить):
    1) env SECOND_BRAIN_VAULT
    2) /root/second-brain, если существует (VPS)
    3) /Users/anton/AI AGENT FOLDER/Second Brain (мак)

CLI (смоук/ручное сохранение):
    vault_archive.py --file /tmp/digest.txt
    vault_archive.py --file /tmp/digest.txt --period-start 2026-08-18 --period-end 2026-08-21
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

# Старый однодневный заголовок: «🤖 AI Twitter — утро 2026-08-07»
HEADER_RE = re.compile(
    r"AI\s+Twitter\s*[—–-]?\s*(утро|вечер)\s+(\d{4}-\d{2}-\d{2})"
)

# Накопительный заголовок: «🤖 AI Twitter — дайджест за 3 дн., 19.08–21.08.2026»
PERIOD_HEADER_RE = re.compile(
    r"AI\s+Twitter\s*[—–-]?\s*дайджест\s+за\s+\d+\s*дн\.,\s*"
    r"(\d{2})\.(\d{2})\s*[–—-]\s*(\d{2})\.(\d{2})\.(\d{4})"
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


def parse_header(text: str, now: dt.datetime) -> tuple[str, str | None]:
    """(дата выпуска, часть суток или None) из старого заголовка; fallback — сейчас.

    У накопительного заголовка части суток нет и даты отправки в нём нет —
    в этом случае датой файла становится дата отправки (сейчас).
    """
    match = HEADER_RE.search(text or "")
    if match:
        return match.group(2), match.group(1)
    return now.strftime("%Y-%m-%d"), None


def parse_period(text: str) -> tuple[str, str] | None:
    """(period_start, period_end) YYYY-MM-DD из накопительного заголовка."""
    match = PERIOD_HEADER_RE.search(text or "")
    if not match:
        return None
    start_day, start_month, end_day, end_month, year = match.groups()
    end_year = int(year)
    # Период может пересекать Новый год: 30.12–02.01.2027 → начало в 2026.
    start_year = end_year - 1 if int(start_month) > int(end_month) else end_year
    return f"{start_year}-{start_month}-{start_day}", f"{end_year}-{end_month}-{end_day}"


def note_name(date_str: str) -> str:
    """`{automation} {summary} AI дайджест – YYYY-MM-DD` (без .md), дата отправки."""
    name = f"{{automation}} {{summary}} AI дайджест {EN_DASH} {date_str}"
    if len(name) + len(".md") > FILENAME_MAX_CHARS:
        log(f"имя длиннее {FILENAME_MAX_CHARS} символов: {name}.md")
    return name


def note_body(
    text: str,
    date_str: str,
    part: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    lines = ["---", f"date: {date_str}"]
    if part:
        lines.append(f"part: {part}")
    lines.append(f"period_start: {period_start or date_str}")
    lines.append(f"period_end: {period_end or date_str}")
    lines.append("source: ai-twitter-digest")
    lines.append("---")
    front = "\n".join(lines) + "\n\n"
    return front + (text or "").strip() + "\n"


def index_line(
    name: str,
    date_str: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> str:
    if period_start and period_end and period_start != period_end:
        return f"- [[{name}]] — {date_str} (период {period_start} … {period_end})"
    return f"- [[{name}]] — {date_str}"


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
    period_start: str | None = None,
    period_end: str | None = None,
) -> Path:
    """Сохранить выпуск в vault и дозаписать строку индекса. Бросает исключение при ошибке."""
    now = now or dt.datetime.now()
    parsed_date, parsed_part = parse_header(text, now)
    date_str = date_str or parsed_date
    part = part or parsed_part
    if not (period_start and period_end):
        parsed_period = parse_period(text)
        if parsed_period:
            period_start = period_start or parsed_period[0]
            period_end = period_end or parsed_period[1]

    target_dir = digests_dir(vault)
    target_dir.mkdir(parents=True, exist_ok=True)

    name = note_name(date_str)
    note_path = target_dir / f"{name}.md"
    note_path.write_text(
        note_body(text, date_str, part, period_start, period_end), encoding="utf-8"
    )
    append_index(
        target_dir / INDEX_NAME, index_line(name, date_str, period_start, period_end)
    )
    return note_path


def archive_digest_safe(
    text: str,
    now: dt.datetime | None = None,
    vault: Path | None = None,
    part: str | None = None,
    date_str: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> Path | None:
    """Best-effort обёртка: ошибка → строка в лог, None; вызывающий код не страдает."""
    try:
        path = archive_digest(
            text, now=now, vault=vault, part=part, date_str=date_str,
            period_start=period_start, period_end=period_end,
        )
    except Exception as exc:  # noqa: BLE001 — копилка не имеет права ронять выпуск
        log(f"копилка не сохранила выпуск ({type(exc).__name__}: {exc})")
        return None
    log(f"выпуск сохранён в vault: {path}")
    return path


def is_digest(text: str) -> bool:
    """Это выпуск (а не ❌-алерт)? Отличаем по заголовку из digest_builder.header().

    Понимаем оба вида заголовка: накопительный («дайджест за N дн., …») и старый
    однодневный («утро|вечер YYYY-MM-DD»).
    """
    body = text or ""
    return bool(PERIOD_HEADER_RE.search(body) or HEADER_RE.search(body))


# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сохранение выпуска AI Twitter Digest в vault")
    parser.add_argument("--file", help="файл с текстом выпуска (по умолчанию stdin)")
    parser.add_argument("--text", help="сохранить эту строку вместо файла/stdin")
    parser.add_argument("--part", choices=[PART_MORNING, PART_EVENING],
                        help="часть суток (по умолчанию из заголовка выпуска / по часу)")
    parser.add_argument("--date", help="дата YYYY-MM-DD (по умолчанию из заголовка / сегодня)")
    parser.add_argument("--period-start", help="начало охвата выпуска YYYY-MM-DD (frontmatter)")
    parser.add_argument("--period-end", help="конец охвата выпуска YYYY-MM-DD (frontmatter)")
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

    for flag, value in (("--date", args.date), ("--period-start", args.period_start),
                        ("--period-end", args.period_end)):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            print(f"{flag} ожидает формат YYYY-MM-DD", file=sys.stderr)
            return 2

    path = archive_digest_safe(
        text, part=args.part, date_str=args.date,
        period_start=args.period_start, period_end=args.period_end,
    )
    if path is None:
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
