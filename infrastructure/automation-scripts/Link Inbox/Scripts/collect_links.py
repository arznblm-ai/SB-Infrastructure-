#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from link_inbox_common import (
    configure_logging,
    extract_urls,
    link_kind,
    load_config,
    load_state,
    now_iso,
    save_state,
    today,
    url_id,
    write_link_note,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect links from a Telegram channel into Link Inbox.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--authorize-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def authorize(config: dict[str, Any], logger) -> None:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Telethon is not installed. Run `pip install -r requirements.txt`.") from exc

    tg = config["telegram"]
    api_id = str(tg.get("api_id", "")).strip()
    api_hash = str(tg.get("api_hash", "")).strip()
    if not api_id or not api_hash or not api_id.isdigit():
        raise RuntimeError("Fill api_id/api_hash in ~/.config/link-inbox/config.json first.")
    tg["session_file"].parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(tg["session_file"]), int(api_id), api_hash)
    async with client:
        await client.start()
    logger.info("Telegram session is ready.")


def message_url(entity: Any, message_id: int, fallback: str) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    channel_name = fallback.lstrip("@")
    if channel_name:
        return f"https://t.me/{channel_name}/{message_id}"
    entity_id = str(getattr(entity, "id", "")).lstrip("-")
    if entity_id.startswith("100"):
        entity_id = entity_id[3:]
    return f"https://t.me/c/{entity_id}/{message_id}"


async def run_async(config_path: str | None = None, *, verbose: bool = False, authorize_only: bool = False) -> dict:
    config = load_config(config_path)
    logger = configure_logging(config, verbose=verbose)
    if authorize_only:
        await authorize(config, logger)
        return {"authorized": True}

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Telethon is not installed. Run `pip install -r requirements.txt`.") from exc

    tg = config["telegram"]
    source_channel = str(tg.get("source_channel", "")).strip()
    if not source_channel:
        raise RuntimeError("Set telegram.source_channel in ~/.config/link-inbox/config.json.")

    await authorize(config, logger)
    state = load_state(config)
    last_message_id = int(state.get("telegram", {}).get("last_message_id", 0) or 0)
    max_messages = int(tg.get("max_messages", 100) or 100)

    client = TelegramClient(str(tg["session_file"]), int(str(tg["api_id"])), str(tg["api_hash"]))
    inspected = 0
    saved = 0
    skipped = 0
    latest_seen = last_message_id

    async with client:
        entity = await client.get_entity(source_channel)
        channel_label = f"@{entity.username}" if getattr(entity, "username", None) else source_channel
        logger.info(f"Scanning {channel_label} from message id {last_message_id}")
        async for message in client.iter_messages(entity, min_id=last_message_id, reverse=True):
            inspected += 1
            latest_seen = max(latest_seen, int(message.id))
            text = (getattr(message, "raw_text", None) or getattr(message, "message", "") or "").strip()
            urls = extract_urls(text)
            if not urls:
                skipped += 1
            for url in urls:
                uid = url_id(url)
                if uid in state["links"]:
                    skipped += 1
                    continue
                record = {
                    "id": uid,
                    "url": url,
                    "kind": link_kind(url),
                    "title": url,
                    "status": "new",
                    "date": (getattr(message, "date", None).date().isoformat() if getattr(message, "date", None) else today()),
                    "created_at": now_iso(),
                    "message_id": int(message.id),
                    "message_url": message_url(entity, int(message.id), channel_label),
                    "channel": channel_label,
                    "message_text": text,
                }
                write_link_note(config, record)
                state["links"][uid] = record
                saved += 1
                logger.info(f"  + {record['kind']}: {url}")
            if inspected >= max_messages:
                break

    state["telegram"]["last_message_id"] = latest_seen
    save_state(config, state)
    logger.info(f"Collect done: inspected={inspected}, saved={saved}, skipped={skipped}")
    return {"inspected": inspected, "saved": saved, "skipped": skipped}


def main() -> int:
    args = parse_args()
    asyncio.run(run_async(args.config, verbose=args.verbose, authorize_only=args.authorize_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
