#!/usr/bin/env python3
"""Todoist Janitor — ежедневная гигиена доски задач.

Что делает (раз в сутки, по таймеру на VPS):
  1. Закрывает задачи в секции intake старше `intake_stale_days` (по `added_at`).
     Это ЕДИНСТВЕННОЕ автодействие — всё остальное только попадает в отчёт.
  2. Предупреждает, если в секции week задач больше `week_limit`.
  3. Показывает похожие пары среди открытых задач (нечёткий матч заголовков).
  4. По понедельникам — сводка «N задач с меткой "без области"».
  5. Шлёт отчёт одним сообщением в Telegram. Пустой отчёт = сообщение не шлём.

Контракт надёжности: best-effort. Любая ошибка (нет токена, нет сети, ошибка API)
→ строка в лог и выход с кодом 0. Наружу исключений не отдаём, ретраев нет.

Запуск:
    python3 todoist_janitor.py            # боевой прогон
    python3 todoist_janitor.py --dry-run  # только печать плана, ничего не менять
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import todoist_client as tc  # noqa: E402

# ── Дефолты (используются, только если ключа нет в todoist-ids.json) ───────
DEFAULT_WEEK_LIMIT = 10
DEFAULT_INTAKE_STALE_DAYS = 14
DEFAULT_NO_AREA_LABEL = "без области"

DUPLICATE_RATIO = 0.85
MAX_DUPLICATE_PAIRS = 10  # отчёт в TG должен оставаться читаемым

TELEGRAM_ENV_FILE = Path.home() / ".config" / "second-brain" / "daily-focus.env"


# ── Лог ───────────────────────────────────────────────────────────────────

def _log_path() -> Path:
    """~/Library/Logs на маке, ~/.local/share на Linux (VPS)."""
    mac_logs = Path.home() / "Library" / "Logs"
    if mac_logs.is_dir():
        return mac_logs / "todoist-janitor.log"
    linux_logs = Path.home() / ".local" / "share"
    try:
        linux_logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path("/tmp/todoist-janitor.log")
    return linux_logs / "todoist-janitor.log"


LOG_FILE = _log_path()


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass  # лог не должен ронять прогон


# ── Конфиг ────────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    raw = tc.load_ids_config(log_fn=log)
    if not isinstance(raw, dict):
        raw = {}

    def _int(key: str, default: int) -> int:
        try:
            value = int(raw.get(key, default))
            return value if value > 0 else default
        except Exception:
            return default

    no_area = raw.get("no_area_label")
    return {
        "week_limit": _int("week_limit", DEFAULT_WEEK_LIMIT),
        "intake_stale_days": _int("intake_stale_days", DEFAULT_INTAKE_STALE_DAYS),
        "no_area_label": str(no_area).strip() if no_area else DEFAULT_NO_AREA_LABEL,
    }


# ── Помощники ─────────────────────────────────────────────────────────────

def parse_added_at(value: Any) -> Optional[datetime]:
    """`added_at` Todoist (ISO8601 с Z) → aware datetime в UTC, или None."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except Exception:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def task_title(task: dict) -> str:
    return str(task.get("content") or "").strip()


def short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Проверки ──────────────────────────────────────────────────────────────

def find_stale_intake(tasks: list[dict], intake_id: Optional[str], stale_days: int) -> list[dict]:
    if not intake_id:
        log("intake section id не найден — шаг протухших задач пропущен")
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    stale: list[dict] = []
    for task in tasks:
        if str(task.get("section_id") or "") != str(intake_id):
            continue
        added = parse_added_at(task.get("added_at"))
        if added is None:
            continue  # без даты создания не рискуем закрывать
        if added < cutoff:
            stale.append(task)
    stale.sort(key=lambda t: parse_added_at(t.get("added_at")) or datetime.now(timezone.utc))
    return stale


def count_week(tasks: list[dict], week_id: Optional[str]) -> int:
    if not week_id:
        return 0
    return sum(1 for t in tasks if str(t.get("section_id") or "") == str(week_id))


def find_duplicate_pairs(tasks: list[dict]) -> list[tuple[dict, dict, float]]:
    """Похожие пары среди открытых задач: normalize_task_title + difflib >= 0.85."""
    prepared = [(task, tc.normalize_task_title(task_title(task))) for task in tasks]
    prepared = [(task, norm) for task, norm in prepared if norm]
    pairs: list[tuple[dict, dict, float]] = []
    for i in range(len(prepared)):
        for j in range(i + 1, len(prepared)):
            try:
                ratio = difflib.SequenceMatcher(None, prepared[i][1], prepared[j][1]).ratio()
            except Exception:
                continue
            if ratio >= DUPLICATE_RATIO:
                pairs.append((prepared[i][0], prepared[j][0], ratio))
    pairs.sort(key=lambda item: item[2], reverse=True)
    return pairs


def count_no_area(tasks: list[dict], label: str) -> int:
    target = label.strip()
    total = 0
    for task in tasks:
        labels = task.get("labels")
        labels = labels if isinstance(labels, list) else []
        if target in {str(x).strip() for x in labels}:
            total += 1
    return total


# ── Telegram ──────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    try:
        env = tc.load_env_file(TELEGRAM_ENV_FILE)
    except Exception as exc:
        log(f"telegram: env unreadable: {type(exc).__name__}: {exc}")
        return False
    token = (env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        log("telegram: нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID — отчёт не отправлен")
        return False
    try:
        result = subprocess.run(
            [
                "curl", "-sS", "--max-time", "30",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "--data-urlencode", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}",
                "--data-urlencode", "parse_mode=HTML",
                "--data-urlencode", "disable_web_page_preview=true",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except Exception as exc:
        log(f"telegram: send failed: {type(exc).__name__}: {exc}")
        return False
    if result.returncode != 0 or '"ok":true' not in (result.stdout or ""):
        log(f"telegram: send failed rc={result.returncode} {short(result.stdout or result.stderr, 200)}")
        return False
    log("telegram: отчёт отправлен")
    return True


# ── Основной прогон ───────────────────────────────────────────────────────

def run(dry_run: bool) -> None:
    config = load_config()
    sections = tc.load_section_ids(log_fn=log)
    intake_id = sections.get("intake")
    week_id = sections.get("week")

    scope = tc.work_scope_project_ids(log_fn=log)
    tasks = tc.list_open_tasks(scope, log_fn=log)
    if not tasks:
        log("открытых задач не найдено (или API недоступен) — отчёт не формируется")
        return
    log(f"открытых задач: {len(tasks)}")

    lines: list[str] = []

    # 1. Протухший intake — единственное автодействие
    stale = find_stale_intake(tasks, intake_id, config["intake_stale_days"])
    if stale:
        closed = 0
        detail: list[str] = []
        for task in stale:
            title = task_title(task)
            added = parse_added_at(task.get("added_at"))
            age = (datetime.now(timezone.utc) - added).days if added else "?"
            if dry_run:
                log(f"[dry-run] закрыл бы {task.get('id')} «{short(title)}» (возраст {age} дн.)")
                closed += 1
            elif tc.close_task(str(task.get("id")), log_fn=log):
                closed += 1
            else:
                continue
            detail.append(f"• {escape_html(short(title))} ({age} дн.)")
        if closed:
            head = "🧹 Закрыл протухшее в «Новое»" if not dry_run else "🧹 [dry-run] закрыл бы"
            lines.append(f"{head} ({closed} шт., старше {config['intake_stale_days']} дн.):")
            lines.extend(detail)
    else:
        log(f"протухших задач в intake нет (порог {config['intake_stale_days']} дн.)")

    # 2. Перегруз недели
    week_count = count_week(tasks, week_id)
    if week_id and week_count > config["week_limit"]:
        lines.append(
            f"⚠️ В «Эта неделя» {week_count} задач при лимите {config['week_limit']} — "
            f"перенеси лишнее в «Потом»."
        )
    log(f"в week: {week_count} (лимит {config['week_limit']})")

    # 3. Дубли — только показать
    pairs = find_duplicate_pairs(tasks)
    if pairs:
        lines.append(f"👯 Похожие задачи ({len(pairs)} пар, не трогал):")
        for left, right, ratio in pairs[:MAX_DUPLICATE_PAIRS]:
            lines.append(
                f"• {escape_html(short(task_title(left), 50))}\n"
                f"  ↔ {escape_html(short(task_title(right), 50))} ({ratio:.0%})"
            )
        if len(pairs) > MAX_DUPLICATE_PAIRS:
            lines.append(f"  …и ещё {len(pairs) - MAX_DUPLICATE_PAIRS} пар")
    log(f"похожих пар: {len(pairs)}")

    # 4. Понедельничная сводка «без области»
    no_area_count = count_no_area(tasks, config["no_area_label"])
    if datetime.now().weekday() == 0 and no_area_count:
        lines.append(f"🏷 {no_area_count} задач с меткой «{escape_html(config['no_area_label'])}» — разложи по областям.")
    log(f"без области: {no_area_count} (понедельник: {datetime.now().weekday() == 0})")

    # 5. Отчёт
    if not lines:
        log("отчёт пуст — Telegram не трогаем")
        return
    report = "🧽 <b>Уборка задач</b>\n\n" + "\n".join(lines)
    if dry_run:
        print("\n--- отчёт, который был бы отправлен в Telegram ---")
        print(report)
        print("--- конец отчёта (dry-run: ничего не отправлено) ---")
        return
    send_telegram(report)


def main() -> int:
    parser = argparse.ArgumentParser(description="Todoist Janitor — ежедневная гигиена задач")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="печатать план действий, не трогая Todoist и Telegram",
    )
    args = parser.parse_args()
    mode = "dry-run" if args.dry_run else "боевой"
    log(f"=== todoist janitor старт ({mode}) ===")
    try:
        run(args.dry_run)
    except Exception as exc:  # best-effort: наружу исключений не отдаём
        log(f"janitor: прогон упал: {type(exc).__name__}: {exc}")
    log("=== todoist janitor конец ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
