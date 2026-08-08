#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from link_inbox_common import (
    configure_logging,
    extract_urls,
    link_kind,
    load_config,
    load_state,
    now_iso,
    paths,
    save_state,
    today,
    url_id,
    write_link_note,
)
from link_digest import build_record_digest
from send_review import build_review

SCRIPT_DIR = Path(__file__).resolve().parent
PROCESS_LOCK = Path.home() / ".config" / "link-inbox" / "process.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram bot collector for Link Inbox.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--init-offset", action="store_true")
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def bot_token(config: dict) -> str:
    env_name = str(config["telegram"].get("bot_token_env") or "LINK_INBOX_BOT_TOKEN")
    token = os.environ.get(env_name, "").strip()
    if not token:
        raise RuntimeError(f"Bot token env is missing: {env_name}")
    return token


def telegram_request(token: str, method: str, data: dict[str, str] | None = None, timeout: int = 90) -> dict:
    command = ["curl", "-fsS", "--max-time", str(timeout)]
    for key, value in (data or {}).items():
        command.extend(["--data-urlencode", f"{key}={value}"])
    command.append(f"https://api.telegram.org/bot{token}/{method}")
    try:
        payload = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        output = (exc.output or "").strip()
        if token:
            output = output.replace(token, "<redacted-token>")
        detail = f": {output[-500:]}" if output else ""
        raise RuntimeError(f"Telegram API {method} failed with curl exit {exc.returncode}{detail}") from exc
    return json.loads(payload)


def send_message(token: str, chat_id: str, text: str) -> None:
    chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)] or ["(empty)"]
    for chunk in chunks:
        telegram_request(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": "true",
            },
            timeout=30,
        )


def get_updates(token: str, offset: int | None, timeout: int = 50) -> list[dict]:
    data = {"timeout": str(timeout)}
    if offset is not None:
        data["offset"] = str(offset)
    payload = telegram_request(token, "getUpdates", data, timeout=timeout + 15)
    return payload.get("result", [])


def allowed(config: dict, chat_id: str) -> bool:
    allowed_ids = [str(item).strip() for item in config["telegram"].get("allowed_chat_ids", []) if str(item).strip()]
    if not allowed_ids:
        # fail-closed: пустой allowlist = никому нельзя (аудит 2026-08-07)
        return False
    return str(chat_id) in allowed_ids


def chat_label(message: dict) -> str:
    chat = message.get("chat") or {}
    username = chat.get("username")
    if username:
        return "@" + str(username)
    first = chat.get("first_name") or ""
    last = chat.get("last_name") or ""
    title = chat.get("title") or ""
    return " ".join(str(part) for part in (title or first, last) if part).strip() or str(chat.get("id", "telegram"))


def message_urls(message: dict) -> list[str]:
    text = (message.get("text") or message.get("caption") or "").strip()
    urls = extract_urls(text)
    seen = set(urls)
    entities = message.get("entities") or message.get("caption_entities") or []
    for entity in entities:
        entity_type = entity.get("type")
        if entity_type == "text_link" and entity.get("url"):
            for url in extract_urls(str(entity["url"])):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        elif entity_type == "url":
            try:
                offset = int(entity.get("offset", 0))
                length = int(entity.get("length", 0))
                raw_url = text[offset : offset + length]
            except Exception:
                raw_url = ""
            for url in extract_urls(raw_url):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def make_record(url: str, message: dict) -> dict:
    date_raw = message.get("date")
    message_date = today()
    if date_raw:
        try:
            import datetime as dt

            message_date = dt.datetime.fromtimestamp(int(date_raw)).date().isoformat()
        except Exception:
            pass
    return {
        "id": url_id(url),
        "url": url,
        "kind": link_kind(url),
        "title": url,
        "status": "new",
        "date": message_date,
        "created_at": now_iso(),
        "message_id": int(message.get("message_id", 0) or 0),
        "message_url": "",
        "channel": chat_label(message),
        "message_text": (message.get("text") or message.get("caption") or "").strip(),
        "ingest_source": "telegram_bot_dm",
    }


def save_links_from_message(config: dict, state: dict, message: dict, logger) -> tuple[int, int, list[dict]]:
    urls = message_urls(message)
    saved = 0
    duplicates = 0
    records = []
    for url in urls:
        uid = url_id(url)
        if uid in state["links"]:
            duplicates += 1
            records.append(state["links"][uid])
            continue
        record = make_record(url, message)
        write_link_note(config, record)
        state["links"][uid] = record
        saved += 1
        records.append(record)
        logger.info(f"bot saved {record['kind']}: {url}")
    return saved, duplicates, records


def process_is_running() -> bool:
    if not PROCESS_LOCK.exists():
        return False
    try:
        pid = int(PROCESS_LOCK.read_text(encoding="utf-8").strip())
    except Exception:
        PROCESS_LOCK.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        PROCESS_LOCK.unlink(missing_ok=True)
        return False


def start_processing_background(limit: int = 3, chat_id: str | None = None, record_ids: list[str] | None = None) -> bool:
    log_path = Path.home() / "Library" / "Logs" / "link-inbox.processor.log"
    if chat_id and record_ids:
        command = [
            "/bin/zsh",
            "-lc",
            (
                f"source {str(Path.home() / '.config' / 'link-inbox' / 'env')!r} 2>/dev/null || true; "
                f"python3 {str(SCRIPT_DIR / 'process_and_notify.py')!r} "
                f"--chat-id {chat_id!r} --limit {int(limit)} "
                + " ".join(["--ids", *[repr(item) for item in record_ids]])
                + f" >> {str(log_path)!r} 2>&1"
            ),
        ]
        subprocess.Popen(command, cwd=str(SCRIPT_DIR.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    if process_is_running():
        return False
    PROCESS_LOCK.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "/bin/zsh",
        "-lc",
        (
            f"source {str(Path.home() / '.config' / 'link-inbox' / 'env')!r} 2>/dev/null || true; "
            f"python3 {str(SCRIPT_DIR / 'process_links.py')!r} --limit {int(limit)} "
            f">> {str(log_path)!r} 2>&1; "
            f"rm -f {str(PROCESS_LOCK)!r}"
        ),
    ]
    proc = subprocess.Popen(command, cwd=str(SCRIPT_DIR.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PROCESS_LOCK.write_text(str(proc.pid), encoding="utf-8")
    os.chmod(PROCESS_LOCK, 0o600)
    return True


def status_text(config: dict, state: dict) -> str:
    counts: dict[str, int] = {}
    for record in state.get("links", {}).values():
        status = record.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    unreviewed = sum(
        1
        for record in state.get("links", {}).values()
        if not record.get("reviewed_at") and record.get("status") in {"processed", "needs_manual_processing", "failed"}
    )
    lines = [
        "Saved Links status",
        f"Total links: {len(state.get('links', {}))}",
        f"New: {counts.get('new', 0)}",
        f"Processed: {counts.get('processed', 0)}",
        f"Needs manual: {counts.get('needs_manual_processing', 0)}",
        f"Failed: {counts.get('failed', 0)}",
        f"Unreviewed: {unreviewed}",
        f"Processor running: {'yes' if process_is_running() else 'no'}",
        "",
        f"Links folder: {config['output_root'] / 'links'}",
        f"Transcripts folder: {paths(config)['transcripts']}",
    ]
    return "\n".join(lines)


def help_text() -> str:
    return "\n".join(
        [
            "Saved Links bot",
            "",
            "Пришли сюда ссылку на YouTube, статью, лекцию или пост.",
            "Я сохраню source card в Second Brain и запущу обработку в фоне.",
            "",
            "Команды:",
            "/status — состояние inbox",
            "/process — обработать pending ссылки сейчас",
            "/review — прислать обзор обработанных ссылок",
            "/help — эта справка",
            "",
            "После обработки пришлю короткое содержание, инсайты и пути сохранения.",
            "YouTube, Instagram и TikTok транскрибируются автоматически, если платформа отдаёт видео.",
        ]
    )


def handle_command(token: str, chat_id: str, text: str, config: dict, state: dict) -> bool:
    normalized = text.strip()
    if normalized in {"/start", "/help"}:
        send_message(token, chat_id, help_text())
        return True
    if normalized == "/status":
        send_message(token, chat_id, status_text(config, state))
        return True
    if normalized == "/process":
        started = start_processing_background(limit=5)
        send_message(token, chat_id, "Запустил обработку pending ссылок." if started else "Обработка уже идёт.")
        return True
    if normalized == "/review":
        text_out, reviewed_ids = build_review(config, state)
        send_message(token, chat_id, text_out)
        now = now_iso()
        for uid in reviewed_ids:
            state["links"][uid]["reviewed_at"] = now
        state["reviews"]["last_reviewed_at"] = now
        save_state(config, state)
        return True
    return False


def handle_message(token: str, config: dict, state: dict, message: dict, logger) -> None:
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return
    if not allowed(config, chat_id):
        logger.warning(f"ignored unauthorized chat_id={chat_id}")
        return

    text = (message.get("text") or message.get("caption") or "").strip()
    if text.startswith("/") and handle_command(token, chat_id, text, config, state):
        return
    saved, duplicates, records = save_links_from_message(config, state, message, logger)
    save_state(config, state)
    if not records:
        send_message(token, chat_id, "Ссылок не нашёл. Пришли URL на YouTube, статью, лекцию или пост.")
        return
    record_ids = [record["id"] for record in records if record.get("id")]
    already_processed = [record for record in records if record.get("status") == "processed"]
    started = False
    if saved or any(record.get("status") in {"new", "failed", "needs_manual_processing"} for record in records):
        started = start_processing_background(limit=max(3, len(record_ids)), chat_id=chat_id, record_ids=record_ids)
    kinds = ", ".join(sorted({record.get("kind", "web") for record in records}))
    if saved:
        send_message(
            token,
            chat_id,
            "\n".join(
                [
                    f"Сохранил ссылок: {saved}. Дубликатов: {duplicates}.",
                    f"Типы: {kinds}.",
                    "Карточки уже в Second Brain.",
                    "Сейчас обработаю и пришлю короткое содержание, инсайты и пути сохранения." if started else "Обработка уже идёт, digest пришлю после очереди.",
                ]
            ),
        )
    elif already_processed:
        send_message(token, chat_id, "\n\n---\n\n".join(build_record_digest(record) for record in already_processed))
    else:
        send_message(
            token,
            chat_id,
            "Ссылка уже была сохранена. Запустил повторную обработку и пришлю digest после очереди." if started else "Ссылка уже была сохранена. Обработка уже идёт.",
        )


def init_offset(token: str, config: dict, state: dict) -> None:
    updates = get_updates(token, offset=None, timeout=1)
    next_offset = 0
    if updates:
        next_offset = max(int(item["update_id"]) for item in updates) + 1
    state["telegram_bot"]["offset"] = next_offset
    save_state(config, state)
    print(next_offset)


def run_loop(config: dict, *, once: bool = False, verbose: bool = False) -> None:
    logger = configure_logging(config, verbose=verbose)
    token = bot_token(config)
    state = load_state(config)
    offset = state.get("telegram_bot", {}).get("offset")
    logger.info(f"Saved Links bot loop started; offset={offset}")
    while True:
        try:
            updates = get_updates(token, offset=offset, timeout=50)
        except Exception as exc:
            logger.warning(f"getUpdates warning: {exc}")
            if once:
                return
            time.sleep(10)
            continue
        for update in updates:
            update_id = int(update["update_id"])
            offset = update_id + 1
            state["telegram_bot"]["offset"] = offset
            save_state(config, state)
            message = update.get("message") or update.get("edited_message") or update.get("channel_post") or {}
            if not message:
                continue
            handle_message(token, config, state, message, logger)
            state = load_state(config)
        if once:
            return


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    token = bot_token(config)
    state = load_state(config)
    if args.init_offset:
        init_offset(token, config, state)
        return 0
    if args.send_test:
        chat_ids = [str(item).strip() for item in config["telegram"].get("allowed_chat_ids", []) if str(item).strip()]
        if not chat_ids:
            raise RuntimeError("Set telegram.allowed_chat_ids first or send /start and run without --send-test.")
        send_message(token, chat_ids[0], "Saved Links bot готов. Пришли ссылку.")
        return 0
    run_loop(config, once=args.once, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise
