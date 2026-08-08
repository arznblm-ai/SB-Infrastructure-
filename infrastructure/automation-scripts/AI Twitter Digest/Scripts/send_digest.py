#!/usr/bin/env python3
"""
send_digest.py — отправка готового дайджеста в Telegram-бота AI Twitter Digest.

Отправка через curl (паттерн daily focus / meeting_notes.py): --data-urlencode,
нарезка по 3500 символов, disable_web_page_preview, БЕЗ parse_mode
(чтобы спецсимволы из твитов не ломали разбор сущностей).

Секреты — вне vault: ~/.config/ai-twitter-digest/env
    AI_DIGEST_BOT_TOKEN="..."
    AI_DIGEST_CHAT_ID="..."

Использование:
    send_digest.py --file /tmp/digest.txt
    echo "текст" | send_digest.py
    send_digest.py --text "❌ алерт"
    send_digest.py --get-chat-id      # напиши боту любое сообщение и запусти
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "ai-twitter-digest" / "env"
LOG_FILE = Path.home() / "Library" / "Logs" / "ai-twitter-digest.log"
CHUNK_CHARS = 3500


def redact_secrets(message: str) -> str:
    message = re.sub(r"https://api\.telegram\.org/bot[^/'\"\s]+", "https://api.telegram.org/bot<redacted>", message)
    message = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<redacted>", message)
    return message


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] send: {redact_secrets(message)}\n")
    print(f"send: {redact_secrets(message)}", file=sys.stderr, flush=True)


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        raise RuntimeError(f"env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = shlex.split(value.strip())[0] if value.strip() else ""
    return env


def archive_to_vault(text: str) -> None:
    """Копилка выпуска в vault после успешной отправки — строго best-effort.

    Алерты и служебные сообщения не сохраняются: копилку получает только текст
    с заголовком выпуска. Любая ошибка — строка в лог, отправка и state не страдают.
    """
    try:
        script_dir = str(Path(__file__).resolve().parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        from vault_archive import archive_digest_safe, is_digest

        if not is_digest(text):
            return
        archive_digest_safe(text)
    except Exception as exc:  # noqa: BLE001 — копилка не имеет права ронять выпуск
        log(f"копилка vault недоступна ({type(exc).__name__}: {exc})")


def send_message(token: str, chat_id: str, text: str) -> None:
    remaining = text.strip() or "(пустое сообщение)"
    while remaining:
        chunk, remaining = remaining[:CHUNK_CHARS], remaining[CHUNK_CHARS:]
        command = [
            "curl", "-fsS", "--max-time", "30",
            "--data-urlencode", f"chat_id={chat_id}",
            "--data-urlencode", f"text={chunk}",
            "--data-urlencode", "disable_web_page_preview=true",
            f"https://api.telegram.org/bot{token}/sendMessage",
        ]
        subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def get_chat_id(token: str) -> int:
    command = [
        "curl", "-fsS", "--max-time", "30",
        f"https://api.telegram.org/bot{token}/getUpdates",
    ]
    payload = json.loads(subprocess.check_output(command, text=True, stderr=subprocess.STDOUT))
    updates = payload.get("result") or []
    if not updates:
        print(
            "Telegram не вернул ни одного апдейта.\n"
            "Напиши боту любое сообщение в Telegram и запусти команду ещё раз.\n"
            "(если бот уже опрашивается другим процессом, апдейты мог забрать он)"
        )
        return 1
    for update in reversed(updates):
        for key in ("message", "edited_message", "channel_post"):
            container = update.get(key)
            if isinstance(container, dict):
                chat = container.get("chat") or {}
                chat_id = chat.get("id")
                if chat_id is not None:
                    print(f"chat_id: {chat_id}")
                    print(f"от: {chat.get('username') or chat.get('title') or chat.get('first_name') or '?'}")
                    print(f"\nВпиши в {ENV_FILE}:\n  AI_DIGEST_CHAT_ID=\"{chat_id}\"")
                    return 0
    print("В апдейтах нет chat_id — напиши боту сообщение и повтори.")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Отправка AI Twitter дайджеста в Telegram")
    parser.add_argument("--file", help="файл с текстом дайджеста (по умолчанию stdin)")
    parser.add_argument("--text", help="отправить эту строку вместо файла/stdin")
    parser.add_argument("--get-chat-id", action="store_true",
                        help="показать chat_id последнего написавшего боту (для заполнения env)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        env = load_env_file(ENV_FILE)
    except RuntimeError as exc:
        log(str(exc))
        print(f"Нет env-файла: {ENV_FILE}. Запусти Scripts/setup.sh", file=sys.stderr)
        return 1

    token = env.get("AI_DIGEST_BOT_TOKEN", "")
    if not token or token.startswith("PASTE"):
        log("AI_DIGEST_BOT_TOKEN не заполнен")
        return 1

    if args.get_chat_id:
        return get_chat_id(token)

    chat_id = env.get("AI_DIGEST_CHAT_ID", "")
    if not chat_id or chat_id.startswith("PASTE"):
        log("AI_DIGEST_CHAT_ID не заполнен (см. --get-chat-id)")
        return 1

    if args.text is not None:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    try:
        send_message(token, chat_id, text)
    except subprocess.CalledProcessError as exc:
        log(f"Telegram отдал ошибку: {(exc.output or '')[:300]}")
        return 1
    except Exception as exc:
        log(f"отправка не удалась: {type(exc).__name__}: {exc}")
        return 1
    log(f"отправлено {len(text)} симв.")
    archive_to_vault(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
