#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

from link_digest import build_record_digest
from link_inbox_common import load_config, load_state

SCRIPT_DIR = Path(__file__).resolve().parent
LOCK_FILE = Path.home() / ".config" / "link-inbox" / "process.lock"
LOCK_POLL_SECONDS = 1


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


def acquire_lock(timeout_seconds: int = 1800):
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "a+", encoding="utf-8")
    started = time.time()
    while True:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.seek(0)
            lock_fd.truncate()
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
            os.fchmod(lock_fd.fileno(), 0o600)
            return lock_fd
        except BlockingIOError:
            pass
        if time.time() - started > timeout_seconds:
            lock_fd.close()
            return None
        time.sleep(LOCK_POLL_SECONDS)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    token = bot_token(config)
    lock_fd = acquire_lock()
    if lock_fd is None:
        send_message(token, args.chat_id, "Ссылка сохранена, но обработка занята слишком долго. Напиши /process позже.")
        return 1

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
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            last = tail[-1] if tail else "unknown error"
            send_message(token, args.chat_id, f"⚠️ Не удалось обработать ссылку автоматически: {last}\nЧто удалось сохранить — ниже.")

        state = load_state(config)
        digests = []
        for uid in args.ids:
            record = state.get("links", {}).get(uid)
            if record:
                digests.append(build_record_digest(record))
        if digests:
            send_message(token, args.chat_id, "\n\n---\n\n".join(digests))
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
