#!/usr/bin/env python3
"""Telegram scout for Research Department."""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from research_common import (
    build_markdown_note,
    build_note_path,
    build_source_index,
    canonicalize_url,
    configure_logging,
    detect_language_code,
    get_output_dir,
    load_last_run_state,
    load_runtime_config,
    parse_datetime,
    render_content_markdown,
    save_last_run_state,
    title_from_text,
    tldr_from_text,
    topic_from_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Telegram channel posts into the vault.")
    parser.add_argument("--config", help="Path to config.json", default=None)
    parser.add_argument("--authorize-only", action="store_true", help="Only authorize Telethon.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def build_message_url(entity: Any, message_id: int, fallback_channel: str) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"

    channel_name = fallback_channel.lstrip("@")
    if channel_name:
        return f"https://t.me/{channel_name}/{message_id}"

    entity_id = str(getattr(entity, "id", "")).lstrip("-")
    if entity_id.startswith("100"):
        entity_id = entity_id[3:]
    return f"https://t.me/c/{entity_id}/{message_id}"


def get_forwarded_from_label(message: Any) -> str | None:
    forward = getattr(message, "forward", None)
    if forward is not None:
        chat = getattr(forward, "chat", None)
        if getattr(chat, "username", None):
            return f"@{chat.username}"
        if getattr(chat, "title", None):
            return str(chat.title)

        sender_name = getattr(forward, "sender_name", None)
        if sender_name:
            return str(sender_name)

    fwd_from = getattr(message, "fwd_from", None)
    if getattr(fwd_from, "from_name", None):
        return str(fwd_from.from_name)

    return None


async def authorize_telegram_session(config: dict[str, Any], logger: logging.Logger) -> None:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "Telethon is not installed. Run `python3 Scripts/setup.py` first."
        ) from exc

    telegram_config = config["telegram"]
    api_id = str(telegram_config.get("api_id", "")).strip()
    api_hash = str(telegram_config.get("api_hash", "")).strip()
    if not api_id or api_id.startswith("YOUR_") or not api_hash or api_hash.startswith("YOUR_"):
        raise RuntimeError(
            "Telegram credentials are missing. Fill `~/.config/research-department/telegram_config.json` first."
        )
    if not api_id.isdigit():
        raise RuntimeError("Telegram api_id must be numeric.")

    session_file = telegram_config["session_file"]
    session_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Authorizing Telegram session: {session_file}")
    client = TelegramClient(str(session_file), int(api_id), api_hash)
    async with client:
        await client.start()
    logger.info("Telegram session is ready.")


async def collect_channel(
    client: Any,
    channel_ref: str,
    config: dict[str, Any],
    source_index: set[str],
    state: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, int | str]:
    entity = await client.get_entity(channel_ref)
    telegram_config = config["telegram"]
    output_dir = get_output_dir(config)

    channel_name = f"@{entity.username}" if getattr(entity, "username", None) else channel_ref
    channel_state = state.setdefault("telegram", {}).setdefault(channel_name, {})
    last_message_id = int(channel_state.get("last_message_id", 0) or 0)
    last_run_at = parse_datetime(channel_state.get("last_run_at"))

    now = datetime.now(timezone.utc)
    default_cutoff = now - timedelta(hours=24)
    cutoff = max(default_cutoff, last_run_at) if last_run_at else default_cutoff

    max_posts = int(telegram_config.get("max_posts_per_channel", 20) or 20)
    allowed_languages = set(telegram_config.get("language_filter", []))

    inspected = 0
    saved = 0
    skipped = 0
    latest_seen_id = last_message_id

    logger.info(f"Scanning {channel_name} from message id {last_message_id}")

    async for message in client.iter_messages(entity, min_id=last_message_id, reverse=True):
        latest_seen_id = max(latest_seen_id, int(message.id))
        inspected += 1

        message_date = getattr(message, "date", None)
        if message_date is not None:
            message_date = message_date.astimezone(timezone.utc)

        if message_date and message_date < cutoff:
            skipped += 1
        else:
            text = (getattr(message, "raw_text", None) or getattr(message, "message", "") or "").strip()
            has_media = getattr(message, "media", None) is not None

            if not text:
                skipped += 1
            else:
                language = detect_language_code(text)
                if allowed_languages and language not in allowed_languages:
                    skipped += 1
                else:
                    source_url = canonicalize_url(build_message_url(entity, int(message.id), channel_name))
                    if source_url in source_index:
                        skipped += 1
                    else:
                        title = title_from_text(text)
                        note_date = (
                            message_date.date().isoformat() if message_date else now.date().isoformat()
                        )
                        topic = topic_from_text(f"{channel_name}\n{text}")
                        content_markdown = render_content_markdown(text, full_text_label="Полный текст поста")
                        if has_media:
                            content_markdown += "\n\n[изображение в оригинале]"

                        forwarded_from = get_forwarded_from_label(message)
                        note = build_markdown_note(
                            source_tag="telegram",
                            topic=topic,
                            note_date=note_date,
                            source_url=source_url,
                            source_type="telegram-post",
                            title=title,
                            tldr=tldr_from_text(text),
                            content_markdown=content_markdown,
                            source_lines=[
                                f"[Открыть пост]({source_url})",
                                f"Канал: `{channel_name}`",
                                *([f"Original channel: `{forwarded_from}`"] if forwarded_from else []),
                            ],
                            extra_fields={
                                "source_channel": channel_name,
                                "forwarded_from": forwarded_from,
                            },
                        )

                        note_path = build_note_path(
                            output_dir=output_dir,
                            description=title,
                            date_str=note_date,
                            unique_hint=str(message.id),
                        )
                        note_path.write_text(note, encoding="utf-8")
                        source_index.add(source_url)
                        saved += 1
                        logger.info(f"  + {note_path.name}")

        if inspected >= max_posts:
            break

    channel_state["last_message_id"] = latest_seen_id
    channel_state["last_run_at"] = now.isoformat()

    return {
        "channel": channel_name,
        "inspected": inspected,
        "saved": saved,
        "skipped": skipped,
        "last_message_id": latest_seen_id,
    }


async def run_async(
    config_path: str | None = None,
    *,
    verbose: bool = False,
    authorize_only: bool = False,
) -> dict[str, Any]:
    config = load_runtime_config(config_path)
    logger = configure_logging(config["log_file"], verbose=verbose).getChild("telegram")

    if authorize_only:
        await authorize_telegram_session(config, logger)
        return {"component": "telegram", "authorized": True}

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "Telethon is not installed. Run `python3 Scripts/setup.py` first."
        ) from exc

    telegram_config = config["telegram"]
    channels = [
        str(channel).strip()
        for channel in telegram_config.get("channels", [])
        if str(channel).strip() and "channel_name" not in str(channel)
    ]
    if not channels:
        logger.info("No Telegram channels configured. Nothing to do.")
        return {"component": "telegram", "channels": 0, "saved": 0, "skipped": 0}

    await authorize_telegram_session(config, logger)

    session_file = telegram_config["session_file"]
    api_id = int(str(telegram_config["api_id"]).strip())
    api_hash = str(telegram_config["api_hash"]).strip()

    source_index = build_source_index(get_output_dir(config))
    state = load_last_run_state(config)
    channel_summaries: list[dict[str, Any]] = []

    client = TelegramClient(str(session_file), api_id, api_hash)
    async with client:
        for channel_ref in channels:
            try:
                summary = await collect_channel(client, channel_ref, config, source_index, state, logger)
                channel_summaries.append(summary)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                logger.exception(f"Failed to process {channel_ref}: {exc}")
                channel_summaries.append(
                    {"channel": channel_ref, "inspected": 0, "saved": 0, "skipped": 0, "error": str(exc)}
                )

    save_last_run_state(config, state)

    total_saved = sum(int(item.get("saved", 0)) for item in channel_summaries)
    total_skipped = sum(int(item.get("skipped", 0)) for item in channel_summaries)
    total_inspected = sum(int(item.get("inspected", 0)) for item in channel_summaries)

    logger.info(
        f"Telegram scout finished: inspected={total_inspected}, saved={total_saved}, skipped={total_skipped}"
    )
    return {
        "component": "telegram",
        "channels": len(channel_summaries),
        "inspected": total_inspected,
        "saved": total_saved,
        "skipped": total_skipped,
        "details": channel_summaries,
    }


def run(config_path: str | None = None, *, verbose: bool = False, authorize_only: bool = False) -> dict[str, Any]:
    return asyncio.run(run_async(config_path, verbose=verbose, authorize_only=authorize_only))


def main() -> int:
    args = parse_args()
    run(args.config, verbose=args.verbose, authorize_only=args.authorize_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
