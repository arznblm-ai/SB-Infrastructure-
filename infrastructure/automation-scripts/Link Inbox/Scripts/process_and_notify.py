#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from link_digest import build_record_digest
from link_inbox_common import load_config, load_state

SCRIPT_DIR = Path(__file__).resolve().parent
LOCK_FILE = Path.home() / ".config" / "link-inbox" / "process.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process specific Link Inbox records and notify Telegram.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


def bot_token(config: dict) -> str:
    env_name = str(config["telegram"].get("bot_token_env") or "LINK_INBOX_BOT_TOKEN")
    token = os.environ.get(env_name, "").strip()
    if not token:
        raise RuntimeError(f"Bot token env is missing: {env_name}")
    return token


def send_message(token: str, chat_id: str, text: str) -> None:
    chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)] or ["(empty)"]
    for chunk in chunks:
        command = ["curl", "-fsS", "--max-time", "30"]
        for key, value in {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"}.items():
            command.extend(["--data-urlencode", f"{key}={value}"])
        command.append(f"https://api.telegram.org/bot{token}/sendMessage")
        subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


def lock_is_active() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        LOCK_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        LOCK_FILE.unlink(missing_ok=True)
        return False


def wait_for_lock(timeout_seconds: int = 1800) -> bool:
    started = time.time()
    while lock_is_active():
        if time.time() - started > timeout_seconds:
            return False
        time.sleep(5)
    return True


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    token = bot_token(config)
    if not wait_for_lock():
        send_message(token, args.chat_id, "Ссылка сохранена, но обработка занята слишком долго. Напиши /process позже.")
        return 1

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(LOCK_FILE, 0o600)
    try:
        command = [
            "python3",
            str(SCRIPT_DIR / "process_links.py"),
            "--ids",
            *args.ids,
            "--limit",
            str(args.limit),
        ]
        result = subprocess.run(command, cwd=str(SCRIPT_DIR.parent), capture_output=True, text=True, check=False)
        if result.returncode != 0:
            send_message(token, args.chat_id, "Обработка ссылки завершилась с ошибкой. Покажу что удалось сохранить.\n\n" + result.stdout[-1200:] + result.stderr[-1200:])

        state = load_state(config)
        digests = []
        for uid in args.ids:
            record = state.get("links", {}).get(uid)
            if record:
                digests.append(build_record_digest(record))
        if digests:
            send_message(token, args.chat_id, "\n\n---\n\n".join(digests))
    finally:
        LOCK_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
