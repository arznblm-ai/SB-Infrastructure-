"""Экспорт сырья чатов из sqlite в vault (`transcripts/telegram/`).

Один md-файл на чат, инкрементальный append. Дата в имени файла —
дата ПЕРВОГО экспорта (хранится в `md_files`), чтобы имя не «плыло».
Медиа не выгружаются: в базе только текст.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db as dbmod
from .config import Settings

FILENAME_PREFIX = "{self} {transcript} TG "
EN_DASH = "–"
MAX_FILENAME_LEN = 80
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\[\]#^\n\r\t]+')


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def sanitize_title(title: str | None, chat_id: int | None = None) -> str:
    """Название чата → безопасный кусок имени файла."""
    raw = (title or "").strip()
    if not raw:
        raw = f"chat {chat_id}" if chat_id is not None else "chat"
    raw = unicodedata.normalize("NFC", raw)
    raw = _BAD_CHARS.sub(" ", raw)
    raw = raw.replace(EN_DASH, "-").replace("—", "-")
    raw = re.sub(r"\s+", " ", raw).strip(" .-")
    if not raw:
        raw = f"chat {chat_id}" if chat_id is not None else "chat"
    return raw


def build_filename(title: str | None, chat_id: int, first_export_date: str) -> str:
    """`{self} {transcript} TG <title> – YYYY-MM-DD.md`, не длиннее 80 символов."""
    suffix = f" {EN_DASH} {first_export_date}.md"
    budget = MAX_FILENAME_LEN - len(FILENAME_PREFIX) - len(suffix)
    clean = sanitize_title(title, chat_id)
    if len(clean) > budget:
        clean = clean[:budget].rstrip(" .-")
    if not clean:
        clean = str(chat_id)[:budget]
    return f"{FILENAME_PREFIX}{clean}{suffix}"


def _display_name(user: sqlite3.Row | None, sender_id: Any) -> str:
    if user is None:
        return f"id:{sender_id}" if sender_id is not None else "неизвестный"
    parts = [user["first_name"] or "", user["last_name"] or ""]
    name = " ".join(p for p in parts if p).strip()
    if not name:
        name = user["username"] or f"id:{user['id']}"
    if user["username"]:
        return f"{name} (@{user['username']})"
    return name


def _fmt_date(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return ""
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime(fmt)
    except ValueError:
        return text[:16]


def _frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for key in (
        "chat_id",
        "chat_type",
        "username",
        "members",
        "period_from",
        "period_to",
        "message_count",
        "source",
        "updated",
    ):
        value = meta.get(key)
        lines.append(f"{key}: {'' if value is None else value}")
    lines.append("---")
    return "\n".join(lines)


def _replace_frontmatter(text: str, frontmatter: str) -> str:
    if not text.startswith("---"):
        return frontmatter + "\n\n" + text
    end = text.find("\n---", 3)
    if end == -1:
        return frontmatter + "\n\n" + text
    tail = text[end + len("\n---") :]
    return frontmatter + tail


def _message_line(row: sqlite3.Row, users: dict[int, sqlite3.Row]) -> str:
    sender_id = row["sender_id"]
    user = users.get(int(sender_id)) if sender_id is not None else None
    stamp = _fmt_date(row["date"])
    text = (row["text"] or "").replace("\r\n", "\n").strip()
    text = text.replace("\n", "\n> ")
    return f"**[{stamp}] {_display_name(user, sender_id)}:** {text}"


def export_md(
    conn: sqlite3.Connection,
    settings: Settings,
    target_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Выгружает все неисключённые чаты с ≥ min_messages_for_md сообщений."""
    out_dir = Path(target_dir) if target_dir else settings.telegram_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    users = {int(u["id"]): u for u in dbmod.iter_users(conn)}
    today = _today()

    written: list[dict[str, Any]] = []
    skipped: list[int] = []

    for chat in list(dbmod.iter_chats(conn)):
        chat_id = int(chat["id"])
        total = dbmod.message_count(conn, chat_id)
        if total < settings.min_messages_for_md:
            skipped.append(chat_id)
            continue

        record = dbmod.get_md_file(conn, chat_id)
        first_export_date = record["first_export_date"] if record else today
        since_id = int(record["last_exported_msg_id"]) if record else 0

        rows = list(dbmod.iter_messages(conn, chat_id, since_id=since_id))
        if record is None and len(rows) > settings.md_tail_limit:
            # первый экспорт большого чата — только хвост, полная история в sqlite
            rows = rows[-settings.md_tail_limit :]

        filename = build_filename(chat["title"], chat_id, first_export_date)
        path = out_dir / filename
        if record and record["path"]:
            existing = Path(record["path"])
            if not existing.is_absolute():
                existing = out_dir / existing.name
            path = existing

        if not rows and path.exists():
            continue

        period_row = conn.execute(
            "SELECT MIN(date) AS a, MAX(date) AS b FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        meta = {
            "chat_id": chat_id,
            "chat_type": chat["type"],
            "username": chat["username"] or "",
            "members": chat["members_count"] if chat["members_count"] is not None else "",
            "period_from": _fmt_date(period_row["a"], "%Y-%m-%d"),
            "period_to": _fmt_date(period_row["b"], "%Y-%m-%d"),
            "message_count": total,
            "source": "pavel",
            "updated": today,
        }
        frontmatter = _frontmatter(meta)
        body = "\n\n".join(_message_line(r, users) for r in rows)

        if path.exists():
            text = path.read_text(encoding="utf-8")
            text = _replace_frontmatter(text, frontmatter)
            if body:
                text = text.rstrip("\n") + "\n\n" + body + "\n"
            path.write_text(text, encoding="utf-8")
        else:
            title = sanitize_title(chat["title"], chat_id)
            header = f"{frontmatter}\n\n# TG {title}\n\n### [[{first_export_date}]]\n\n"
            if len(rows) == settings.md_tail_limit and total > settings.md_tail_limit:
                header += (
                    f"> Хвост последних {settings.md_tail_limit} сообщений; "
                    "полная история — в sqlite Pavel.\n\n"
                )
            path.write_text(header + body + "\n", encoding="utf-8")

        last_id = int(rows[-1]["id"]) if rows else since_id
        dbmod.upsert_md_file(conn, chat_id, str(path), first_export_date, last_id)
        written.append(
            {
                "chat_id": chat_id,
                "path": str(path),
                "appended": len(rows),
                "total": total,
                "title": chat["title"],
                "type": chat["type"],
                "period_from": meta["period_from"],
                "period_to": meta["period_to"],
            }
        )

    index_path = write_index(conn, out_dir)
    return {"written": written, "skipped": skipped, "index": str(index_path)}


def write_index(conn: sqlite3.Connection, out_dir: Path) -> Path:
    """Полностью перегенерирует `transcripts/telegram/index.md`."""
    today = _today()
    lines = [
        "---",
        "tags:",
        "  - type/index",
        "  - topic/telegram",
        f"date: {today}",
        "source: pavel",
        "---",
        "",
        "# TG чаты — индекс",
        "",
        f"### [[{today}]]",
        "",
        "> Сгенерировано Pavel. Руками не править: файл перезаписывается при каждом `export-md`.",
        "",
        "| Чат | Тип | Сообщений | Период | Файл |",
        "|---|---|---|---|---|",
    ]
    rows = list(dbmod.iter_md_files(conn))
    for row in rows:
        chat = dbmod.get_chat(conn, int(row["chat_id"]))
        if chat is None:
            continue
        total = dbmod.message_count(conn, int(row["chat_id"]))
        period = conn.execute(
            "SELECT MIN(date) AS a, MAX(date) AS b FROM messages WHERE chat_id = ?",
            (int(row["chat_id"]),),
        ).fetchone()
        name = Path(row["path"]).stem
        title = (chat["title"] or str(chat["id"])).replace("|", "/")
        lines.append(
            f"| {title} | {chat['type']} | {total} | "
            f"{_fmt_date(period['a'], '%Y-%m-%d')} → {_fmt_date(period['b'], '%Y-%m-%d')} | "
            f"[[{name}]] |"
        )
    if not rows:
        lines.append("| — | — | 0 | — | — |")
    lines.append("")

    index_path = out_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path
