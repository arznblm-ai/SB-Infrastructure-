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
from link_inbox_common import (
    configure_logging,
    load_config,
    load_hermes_delivery,
    load_state,
    send_telegram_message,
)

SCRIPT_DIR = Path(__file__).resolve().parent
LOCK_FILE = Path.home() / ".config" / "link-inbox" / "process.lock"
LOCK_POLL_SECONDS = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process specific Link Inbox records and notify Telegram.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--chat-id", default=None)
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


def bot_token(config: dict) -> str:
    env_name = str(config["telegram"].get("bot_token_env") or "LINK_INBOX_BOT_TOKEN")
    token = os.environ.get(env_name, "").strip()
    if not token:
        raise RuntimeError(f"Bot token env is missing: {env_name}")
    return token


def send_message(token: str, chat_id: str, text: str, thread_id: str | None = None) -> None:
    send_telegram_message(token, chat_id, text, thread_id=thread_id)


def wants_hermes_delivery(config: dict, ids: list[str]) -> bool:
    """Хоть одна из записей пришла из топика Saved → доставка токеном Гермеса."""
    try:
        links = load_state(config).get("links", {})
    except Exception:
        return False
    return any((links.get(uid) or {}).get("delivery") == "hermes" for uid in ids)


def make_notifier(config: dict, args: argparse.Namespace, logger):
    """Возвращает send(text): hermes-топик, иначе старый путь (бот-токен + --chat-id)."""
    channel = load_hermes_delivery() if wants_hermes_delivery(config, args.ids) else None

    def notify(text: str) -> None:
        if channel and channel.ok:
            try:
                send_telegram_message(channel.token, channel.chat_id, text, thread_id=channel.thread_id)
                return
            except Exception as exc:
                logger.warning(f"hermes delivery failed ({exc}) → пробую старый канал")
        if not args.chat_id:
            logger.warning("нет ни hermes-канала, ни --chat-id: дайджест никуда не отправлен")
            return
        try:
            send_message(bot_token(config), str(args.chat_id), text)
        except Exception as exc:
            logger.warning(f"старый канал доставки тоже не сработал: {exc}")

    return notify


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
    logger = configure_logging(config)
    notify = make_notifier(config, args, logger)
    lock_fd = acquire_lock()
    if lock_fd is None:
        notify("Ссылка сохранена, но обработка занята слишком долго. Напиши /process позже.")
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
            notify(f"⚠️ Не удалось обработать ссылку автоматически: {last}\nЧто удалось сохранить — ниже.")

        state = load_state(config)
        digests = []
        for uid in args.ids:
            record = state.get("links", {}).get(uid)
            if record:
                digests.append(build_record_digest(record))
        if digests:
            notify("\n\n---\n\n".join(digests))
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
