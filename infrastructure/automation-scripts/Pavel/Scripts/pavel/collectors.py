"""Коллекторы Pavel: метаданные нетворка и текст сообщений.

Все сетевые вызовы идут через `ReadOnlyTelegramClient` — allowlist проверяется
на транспорте, а не «обещанием» этого модуля. Исключённые чаты отсекаются ДО
любого запроса по ним. Медиа не скачиваются никогда.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import db as dbmod
from .config import Config, Settings
from .readonly_client import ReadOnlyTelegramClient

log = logging.getLogger("pavel.collectors")

MESSAGE_BATCH = 500


def normalize_chat_id(value: Any) -> int:
    """`-1001234567890`, `1001234567890`, `1234567890` → один и тот же ключ."""
    n = abs(int(value))
    s = str(n)
    if s.startswith("100") and len(s) > 10:
        n = int(s[3:])
    return n


def exclude_set(exclude_ids: Iterable[int]) -> set[int]:
    return {normalize_chat_id(x) for x in exclude_ids}


def make_client(config: Config) -> ReadOnlyTelegramClient:
    api_id, api_hash = config.require_api()
    client = ReadOnlyTelegramClient(config.session, api_id, api_hash)
    client.flood_sleep_threshold = config.settings.flood_sleep_threshold
    return client


def _entity_type(entity: Any) -> str:
    cls = type(entity).__name__
    if cls == "User":
        return "user"
    if cls == "Chat":
        return "group"
    if cls == "Channel":
        return "supergroup" if getattr(entity, "megagroup", False) else "channel"
    return cls.lower()


def _members_count(entity: Any) -> int | None:
    for attr in ("participants_count", "participants"):
        value = getattr(entity, attr, None)
        if isinstance(value, int):
            return value
    return None


async def scan_meta(
    client: ReadOnlyTelegramClient,
    conn: sqlite3.Connection,
    settings: Settings,
    exclude_ids: Iterable[int],
) -> dict[str, Any]:
    """Диалоги → chats/users/memberships/common_chats + changes. Без LLM."""
    from telethon.tl.functions.contacts import GetContactsRequest
    from telethon.tl.functions.messages import GetCommonChatsRequest
    from telethon.tl.functions.users import GetFullUserRequest

    excluded = exclude_set(exclude_ids)
    run_id = dbmod.start_run(conn, "scan-meta")
    previous_run = dbmod.last_finished_run(conn, before_run_id=run_id)
    stats = {"dialogs": 0, "excluded": 0, "users": 0, "memberships": 0, "common_chats": 0}

    contact_ids: set[int] = set()
    try:
        contacts = await client(GetContactsRequest(hash=0))
        contact_ids = {int(u.id) for u in getattr(contacts, "users", [])}
    except Exception as exc:  # контакты не критичны для остального прогона
        log.warning("GetContacts не удался: %s", exc)

    active_chats = dbmod.active_chat_ids(conn)
    common_chat_targets: list[int] = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        chat_id = normalize_chat_id(getattr(entity, "id", dialog.id))

        if chat_id in excluded:
            # ни метаданных, ни текста — только пометка в состоянии
            dbmod.upsert_chat(
                conn,
                run_id,
                {"id": chat_id, "type": _entity_type(entity), "title": None, "excluded": True},
            )
            stats["excluded"] += 1
            continue

        ctype = _entity_type(entity)
        title = getattr(entity, "title", None) or " ".join(
            p for p in [getattr(entity, "first_name", "") or "", getattr(entity, "last_name", "") or ""] if p
        ).strip()
        chat_row = {
            "id": chat_id,
            "type": ctype,
            "title": title or None,
            "username": getattr(entity, "username", None),
            "members_count": _members_count(entity),
            "is_contact_dialog": ctype == "user" and int(getattr(entity, "id", 0)) in contact_ids,
            "last_message_at": dialog.date.isoformat() if getattr(dialog, "date", None) else None,
            "excluded": False,
        }
        previous_chat = dbmod.upsert_chat(conn, run_id, chat_row)
        dbmod.diff_new_chat(conn, run_id, chat_row, previous_chat)
        stats["dialogs"] += 1

        if ctype == "user":
            user_id = int(entity.id)
            bio = None
            try:
                full = await client(GetFullUserRequest(entity))
                bio = getattr(getattr(full, "full_user", None), "about", None)
            except Exception as exc:
                log.warning("GetFullUser(%s) не удался: %s", user_id, exc)
            user_row = {
                "id": user_id,
                "username": getattr(entity, "username", None),
                "first_name": getattr(entity, "first_name", None),
                "last_name": getattr(entity, "last_name", None),
                "bio": bio,
                "is_contact": user_id in contact_ids,
                "is_bot": bool(getattr(entity, "bot", False)),
            }
            previous_user = dbmod.upsert_user(conn, run_id, user_row)
            dbmod.diff_new_contact(conn, run_id, user_row, previous_user)
            dbmod.diff_bio_changed(conn, run_id, user_row, previous_user)
            stats["users"] += 1
            if user_id in contact_ids or chat_id in active_chats:
                common_chat_targets.append(user_id)

        elif ctype in {"group", "supergroup"}:
            members = chat_row["members_count"]
            if members is not None and members <= settings.max_participants_fetch:
                try:
                    async for participant in client.iter_participants(entity):
                        pid = int(participant.id)
                        dbmod.upsert_user(
                            conn,
                            run_id,
                            {
                                "id": pid,
                                "username": getattr(participant, "username", None),
                                "first_name": getattr(participant, "first_name", None),
                                "last_name": getattr(participant, "last_name", None),
                                "is_contact": pid in contact_ids,
                                "is_bot": bool(getattr(participant, "bot", False)),
                            },
                        )
                        dbmod.upsert_membership(conn, run_id, chat_id, pid)
                        stats["memberships"] += 1
                except Exception as exc:
                    log.warning("iter_participants(%s) не удался: %s", chat_id, exc)
            else:
                log.info("Чат %s пропущен по порогу участников (%s)", chat_id, members)

        await asyncio.sleep(settings.chat_pause_seconds)

    for user_id in common_chat_targets:
        try:
            result = await client(GetCommonChatsRequest(user_id=user_id, max_id=0, limit=100))
        except Exception as exc:
            log.warning("GetCommonChats(%s) не удался: %s", user_id, exc)
            continue
        for chat in getattr(result, "chats", []):
            common_id = normalize_chat_id(chat.id)
            if common_id in excluded:
                continue
            is_new = dbmod.upsert_common_chat(conn, run_id, user_id, common_id)
            dbmod.diff_new_common_chat(conn, run_id, user_id, common_id, is_new)
            stats["common_chats"] += 1
        await asyncio.sleep(settings.chat_pause_seconds)

    stats["left_chats"] = dbmod.diff_left_chats(conn, run_id, previous_run)
    stats.update(dbmod.stats_snapshot(conn))
    dbmod.finish_run(conn, run_id, stats)
    return stats


async def backfill(
    client: ReadOnlyTelegramClient,
    conn: sqlite3.Connection,
    settings: Settings,
    exclude_ids: Iterable[int],
    months: int = 12,
    full: bool = False,
) -> dict[str, Any]:
    """Текст сообщений по всем неисключённым чатам, инкрементально по `messages.id`."""
    excluded = exclude_set(exclude_ids)
    run_id = dbmod.start_run(conn, f"backfill months={months} full={full}")
    stats = {"chats": 0, "messages": 0, "skipped_excluded": 0, "errors": 0}

    chats = [c for c in dbmod.iter_chats(conn)]
    if not chats:
        dbmod.finish_run(conn, run_id, stats)
        raise RuntimeError("В базе нет чатов: сначала выполни `scan-meta`.")

    cutoff = None if full else datetime.now(timezone.utc) - timedelta(days=30 * months)

    for chat in chats:
        chat_id = int(chat["id"])
        if chat_id in excluded or chat["excluded"]:
            stats["skipped_excluded"] += 1
            continue

        since_id = dbmod.last_message_id(conn, chat_id)
        kwargs: dict[str, Any] = {"reverse": True}
        if since_id:
            kwargs["min_id"] = since_id
        elif cutoff is not None:
            kwargs["offset_date"] = cutoff

        batch: list[dict[str, Any]] = []
        try:
            entity = await client.get_input_entity(chat_id)
            async for message in client.iter_messages(entity, **kwargs):
                text = getattr(message, "message", None)
                if not text:  # пустые и сервисные сообщения не храним
                    continue
                if cutoff is not None and getattr(message, "date", None) and message.date < cutoff:
                    continue
                batch.append(
                    {
                        "id": int(message.id),
                        "sender_id": message.sender_id,
                        "date": message.date.isoformat() if message.date else None,
                        "text": text,
                        "reply_to": getattr(message, "reply_to_msg_id", None),
                    }
                )
                if len(batch) >= MESSAGE_BATCH:
                    stats["messages"] += dbmod.insert_messages(conn, chat_id, batch)
                    batch = []
        except Exception as exc:
            stats["errors"] += 1
            log.warning("backfill(%s) прерван: %s", chat_id, exc)

        if batch:
            stats["messages"] += dbmod.insert_messages(conn, chat_id, batch)
        stats["chats"] += 1
        await asyncio.sleep(settings.chat_pause_seconds)

    stats.update(dbmod.stats_snapshot(conn))
    dbmod.finish_run(conn, run_id, stats)
    return stats


async def resolve_candidates(
    client: ReadOnlyTelegramClient, query: str
) -> list[dict[str, Any]]:
    """Кандидаты для `PAVEL_EXCLUDE_CHAT_IDS`: id, type, title, username."""
    needle = query.strip().lower().lstrip("@")
    found: list[dict[str, Any]] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        title = getattr(entity, "title", None) or " ".join(
            p for p in [getattr(entity, "first_name", "") or "", getattr(entity, "last_name", "") or ""] if p
        ).strip()
        username = getattr(entity, "username", None) or ""
        haystack = f"{title} {username}".lower()
        if needle and needle not in haystack:
            continue
        found.append(
            {
                "id": normalize_chat_id(getattr(entity, "id", dialog.id)),
                "raw_id": getattr(entity, "id", dialog.id),
                "type": _entity_type(entity),
                "title": title,
                "username": username,
            }
        )
    return found
