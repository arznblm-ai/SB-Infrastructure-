#!/usr/bin/env python3
"""migrate_todo_owners.py — one-off миграция стора meeting todo/ на owner-поле.

Проставляет `owner: me|other|unknown` (по classify_owner от `who:`) в каждый
блок без owner во ВСЕХ файлах meeting todo/. После миграции /todo показывает
только задачи Антона (owner: me).

Запуск:
  python3 migrate_todo_owners.py            # dry-run: только отчёт
  python3 migrate_todo_owners.py --apply    # записать owner в файлы
  python3 migrate_todo_owners.py --apply --notify  # + отчёт в Telegram
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_owner import classify_owner
from meeting_notes import ENV_FILE, TODO_DIR, load_env_file, send_message


def migrate_file(path: Path, apply: bool) -> list[dict]:
    """Возвращает список задач, получивших owner: {id, task, who, owner, status}."""
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    migrated: list[dict] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        if not line.strip().startswith("- id:"):
            output.append(line)
            index += 1
            continue
        block = [line]
        index += 1
        while index < len(lines) and not lines[index].strip().startswith("- id:"):
            block.append(lines[index])
            index += 1
        fields = {}
        for b in block:
            stripped = b.strip().lstrip("- ")
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                fields[key.strip()] = value.strip()
        if "owner" not in fields:
            owner = classify_owner(fields.get("who", ""))
            new_block = []
            for b in block:
                new_block.append(b)
                if b.strip().startswith("who:"):
                    indent = b[:len(b) - len(b.lstrip())]
                    new_block.append(f"{indent}owner: {owner}")
            block = new_block
            changed = True
            migrated.append({
                "id": fields.get("id", "?"),
                "task": fields.get("task", ""),
                "who": fields.get("who", ""),
                "owner": owner,
                "status": fields.get("status", ""),
            })
        output.extend(block)
    if changed and apply:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return migrated


def build_report(migrated: list[dict], apply: bool) -> str:
    counts = {"me": 0, "other": 0, "unknown": 0}
    for item in migrated:
        counts[item["owner"]] += 1
    removed_open = [i for i in migrated if i["owner"] == "other" and i["status"] == "open"]
    unknown_open = [i for i in migrated if i["owner"] == "unknown" and i["status"] == "open"]
    mode = "Применено" if apply else "DRY-RUN (ничего не записано)"
    lines = [
        f"Миграция meeting todo → owner-поле. {mode}.",
        f"Размечено задач: {len(migrated)} (мои: {counts['me']}, чужие: {counts['other']}, без исполнителя: {counts['unknown']})",
    ]
    if removed_open:
        lines.append("")
        lines.append("Убраны из /todo (чужие, остаются в сторе):")
        for i in removed_open:
            task = i["task"] if len(i["task"]) <= 70 else i["task"][:67].rstrip() + "..."
            lines.append(f"• {i['id']} — {i['who']} — {task}")
    if unknown_open:
        lines.append("")
        lines.append("Без исполнителя (не в /todo; вернуть себе: /todo_claim T<n>):")
        for i in unknown_open:
            task = i["task"] if len(i["task"]) <= 70 else i["task"][:67].rstrip() + "..."
            lines.append(f"• {i['id']} — {task}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="записать изменения (без флага — dry-run)")
    parser.add_argument("--notify", action="store_true", help="отправить отчёт в Telegram (только с --apply)")
    args = parser.parse_args()

    files = sorted(p for p in TODO_DIR.iterdir() if p.is_file() and p.suffix == ".md")
    migrated: list[dict] = []
    for path in files:
        migrated.extend(migrate_file(path, apply=args.apply))

    report = build_report(migrated, apply=args.apply)
    print(report)

    if args.apply and args.notify:
        env = load_env_file(ENV_FILE)
        token, chat_id = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            send_message(token, chat_id, report)
            print("\n[отчёт отправлен в Telegram]")
        else:
            print("\n[Telegram env не найден — отчёт не отправлен]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
