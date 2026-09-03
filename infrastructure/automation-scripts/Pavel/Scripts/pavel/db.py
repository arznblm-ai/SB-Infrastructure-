"""Состояние Pavel: sqlite вне vault (`$PAVEL_STATE_DIR/pavel.db`).

Схема — раздел D плана (контракт для T2, менять нельзя). Единственное
разрешённое расширение — таблица `md_files` для инкрементального экспорта md.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT,
        finished_at TEXT,
        cmd TEXT,
        stats_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY,
        type TEXT,
        title TEXT,
        username TEXT,
        members_count INTEGER,
        is_contact_dialog INTEGER,
        last_message_at TEXT,
        first_seen_run INTEGER,
        last_seen_run INTEGER,
        excluded INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        phone_hash TEXT,
        bio TEXT,
        is_contact INTEGER,
        is_bot INTEGER,
        last_seen_run INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memberships (
        chat_id INTEGER,
        user_id INTEGER,
        role TEXT,
        seen_run INTEGER,
        PRIMARY KEY (chat_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS common_chats (
        user_id INTEGER,
        chat_id INTEGER,
        seen_run INTEGER,
        PRIMARY KEY (user_id, chat_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER,
        chat_id INTEGER,
        sender_id INTEGER,
        date TEXT,
        text TEXT,
        reply_to INTEGER,
        PRIMARY KEY (chat_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        summary TEXT,
        activity_tags_json TEXT,
        category TEXT,
        collab_direction TEXT,
        confidence REAL,
        evidence_json TEXT,
        model TEXT,
        profiled_at TEXT,
        source_msg_count INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER,
        user_id INTEGER,
        chat_id INTEGER,
        kind TEXT,
        detail_json TEXT,
        created_at TEXT
    )
    """,
    # Единственное расширение схемы (T1): состояние md-экспорта.
    """
    CREATE TABLE IF NOT EXISTS md_files (
        chat_id INTEGER PRIMARY KEY,
        path TEXT,
        first_export_date TEXT,
        last_exported_msg_id INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id)",
    "CREATE INDEX IF NOT EXISTS idx_changes_run ON changes(run_id)",
)

CHANGE_KINDS = (
    "new_contact",
    "bio_changed",
    "new_common_chat",
    "new_chat",
    "left_chat",
    "profile_changed",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    """Открывает БД (создавая каталог) и инициализирует схему."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()


# --------------------------------------------------------------------------- runs


def start_run(conn: sqlite3.Connection, cmd: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, finished_at, cmd, stats_json) VALUES (?, NULL, ?, NULL)",
        (utcnow(), cmd),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, stats: dict[str, Any] | None = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, stats_json = ? WHERE id = ?",
        (utcnow(), json.dumps(stats or {}, ensure_ascii=False), run_id),
    )
    conn.commit()


def last_finished_run(conn: sqlite3.Connection, before_run_id: int | None = None) -> int | None:
    sql = "SELECT id FROM runs WHERE finished_at IS NOT NULL"
    params: list[Any] = []
    if before_run_id is not None:
        sql += " AND id < ?"
        params.append(before_run_id)
    sql += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return int(row["id"]) if row else None


# -------------------------------------------------------------------------- upsert


def upsert_chat(conn: sqlite3.Connection, run_id: int, chat: dict[str, Any]) -> dict[str, Any] | None:
    """Пишет чат, возвращает предыдущую строку (None — чат новый)."""
    previous = get_chat(conn, int(chat["id"]))
    first_seen = previous["first_seen_run"] if previous else run_id
    conn.execute(
        """
        INSERT INTO chats (id, type, title, username, members_count, is_contact_dialog,
                           last_message_at, first_seen_run, last_seen_run, excluded)
        VALUES (:id, :type, :title, :username, :members_count, :is_contact_dialog,
                :last_message_at, :first_seen_run, :last_seen_run, :excluded)
        ON CONFLICT(id) DO UPDATE SET
            type = excluded.type,
            title = excluded.title,
            username = excluded.username,
            members_count = excluded.members_count,
            is_contact_dialog = excluded.is_contact_dialog,
            last_message_at = excluded.last_message_at,
            last_seen_run = excluded.last_seen_run,
            excluded = excluded.excluded
        """,
        {
            "id": int(chat["id"]),
            "type": chat.get("type"),
            "title": chat.get("title"),
            "username": chat.get("username"),
            "members_count": chat.get("members_count"),
            "is_contact_dialog": int(bool(chat.get("is_contact_dialog"))),
            "last_message_at": chat.get("last_message_at"),
            "first_seen_run": first_seen,
            "last_seen_run": run_id,
            "excluded": int(bool(chat.get("excluded"))),
        },
    )
    conn.commit()
    return dict(previous) if previous else None


def upsert_user(conn: sqlite3.Connection, run_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
    previous = get_user(conn, int(user["id"]))
    conn.execute(
        """
        INSERT INTO users (id, username, first_name, last_name, phone_hash, bio,
                           is_contact, is_bot, last_seen_run)
        VALUES (:id, :username, :first_name, :last_name, :phone_hash, :bio,
                :is_contact, :is_bot, :last_seen_run)
        ON CONFLICT(id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            phone_hash = COALESCE(excluded.phone_hash, users.phone_hash),
            bio = COALESCE(excluded.bio, users.bio),
            is_contact = excluded.is_contact,
            is_bot = excluded.is_bot,
            last_seen_run = excluded.last_seen_run
        """,
        {
            "id": int(user["id"]),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "phone_hash": user.get("phone_hash"),
            "bio": user.get("bio"),
            "is_contact": int(bool(user.get("is_contact"))),
            "is_bot": int(bool(user.get("is_bot"))),
            "last_seen_run": run_id,
        },
    )
    conn.commit()
    return dict(previous) if previous else None


def upsert_membership(
    conn: sqlite3.Connection, run_id: int, chat_id: int, user_id: int, role: str | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO memberships (chat_id, user_id, role, seen_run)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            role = excluded.role, seen_run = excluded.seen_run
        """,
        (int(chat_id), int(user_id), role, run_id),
    )
    conn.commit()


def upsert_common_chat(conn: sqlite3.Connection, run_id: int, user_id: int, chat_id: int) -> bool:
    """Возвращает True, если пара user↔chat встречена впервые."""
    is_new = (
        conn.execute(
            "SELECT 1 FROM common_chats WHERE user_id = ? AND chat_id = ?",
            (int(user_id), int(chat_id)),
        ).fetchone()
        is None
    )
    conn.execute(
        """
        INSERT INTO common_chats (user_id, chat_id, seen_run)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, chat_id) DO UPDATE SET seen_run = excluded.seen_run
        """,
        (int(user_id), int(chat_id), run_id),
    )
    conn.commit()
    return is_new


def insert_messages(conn: sqlite3.Connection, chat_id: int, messages: Iterable[dict[str, Any]]) -> int:
    """Пишет сообщения пачкой (idempotent). Возвращает число новых строк."""
    rows: list[tuple[Any, ...]] = []
    for msg in messages:
        text = msg.get("text")
        if not text:  # пустые/сервисные пропускаем
            continue
        rows.append(
            (
                int(msg["id"]),
                int(chat_id),
                msg.get("sender_id"),
                msg.get("date"),
                text,
                msg.get("reply_to"),
            )
        )
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO messages (id, chat_id, sender_id, date, text, reply_to)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


# ----------------------------------------------------------------------- readers


def get_chat(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM chats WHERE id = ?", (int(chat_id),)).fetchone()


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()


def iter_chats(
    conn: sqlite3.Connection, include_excluded: bool = False
) -> Iterator[sqlite3.Row]:
    sql = "SELECT * FROM chats"
    if not include_excluded:
        sql += " WHERE excluded = 0"
    sql += " ORDER BY id"
    yield from conn.execute(sql)


def iter_users(conn: sqlite3.Connection, contacts_only: bool = False) -> Iterator[sqlite3.Row]:
    sql = "SELECT * FROM users"
    if contacts_only:
        sql += " WHERE is_contact = 1"
    sql += " ORDER BY id"
    yield from conn.execute(sql)


def iter_messages(
    conn: sqlite3.Connection, chat_id: int, since_id: int = 0
) -> Iterator[sqlite3.Row]:
    yield from conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? AND id > ? ORDER BY id",
        (int(chat_id), int(since_id)),
    )


def last_message_id(conn: sqlite3.Connection, chat_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(id) AS max_id FROM messages WHERE chat_id = ?", (int(chat_id),)
    ).fetchone()
    return int(row["max_id"]) if row and row["max_id"] is not None else 0


def message_count(conn: sqlite3.Connection, chat_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ?", (int(chat_id),)
    ).fetchone()
    return int(row["n"]) if row else 0


def active_chat_ids(conn: sqlite3.Connection) -> set[int]:
    """Чаты, по которым уже есть текст — «активные» для GetCommonChats."""
    return {int(r["chat_id"]) for r in conn.execute("SELECT DISTINCT chat_id FROM messages")}


# ------------------------------------------------------------------- md_files


def get_md_file(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM md_files WHERE chat_id = ?", (int(chat_id),)).fetchone()


def upsert_md_file(
    conn: sqlite3.Connection,
    chat_id: int,
    path: str,
    first_export_date: str,
    last_exported_msg_id: int,
) -> None:
    conn.execute(
        """
        INSERT INTO md_files (chat_id, path, first_export_date, last_exported_msg_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            path = excluded.path,
            last_exported_msg_id = excluded.last_exported_msg_id
        """,
        (int(chat_id), path, first_export_date, int(last_exported_msg_id)),
    )
    conn.commit()


def iter_md_files(conn: sqlite3.Connection) -> Iterator[sqlite3.Row]:
    yield from conn.execute("SELECT * FROM md_files ORDER BY chat_id")


# ------------------------------------------------------------------- changes


def record_change(
    conn: sqlite3.Connection,
    run_id: int,
    kind: str,
    user_id: int | None = None,
    chat_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    if kind not in CHANGE_KINDS:
        raise ValueError(f"Неизвестный kind изменения: {kind!r} (ожидается один из {CHANGE_KINDS})")
    cur = conn.execute(
        """
        INSERT INTO changes (run_id, user_id, chat_id, kind, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            user_id,
            chat_id,
            kind,
            json.dumps(detail or {}, ensure_ascii=False),
            utcnow(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def iter_changes(conn: sqlite3.Connection, run_id: int | None = None) -> Iterator[sqlite3.Row]:
    if run_id is None:
        yield from conn.execute("SELECT * FROM changes ORDER BY id")
    else:
        yield from conn.execute("SELECT * FROM changes WHERE run_id = ? ORDER BY id", (run_id,))


# diff-хелперы: сравнивают новое состояние с предыдущей строкой и пишут changes


def diff_new_chat(
    conn: sqlite3.Connection, run_id: int, chat: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    if previous is not None:
        return False
    record_change(
        conn,
        run_id,
        "new_chat",
        chat_id=int(chat["id"]),
        detail={"title": chat.get("title"), "type": chat.get("type")},
    )
    return True


def diff_new_contact(
    conn: sqlite3.Connection, run_id: int, user: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    was_contact = bool(previous and previous.get("is_contact"))
    if not user.get("is_contact") or was_contact:
        return False
    record_change(
        conn,
        run_id,
        "new_contact",
        user_id=int(user["id"]),
        detail={"username": user.get("username"), "first_name": user.get("first_name")},
    )
    return True


def diff_bio_changed(
    conn: sqlite3.Connection, run_id: int, user: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    if previous is None:
        return False
    old = (previous.get("bio") or "").strip()
    new = (user.get("bio") or "").strip()
    if not new or old == new:
        return False
    record_change(
        conn, run_id, "bio_changed", user_id=int(user["id"]), detail={"old": old, "new": new}
    )
    return True


def diff_new_common_chat(
    conn: sqlite3.Connection, run_id: int, user_id: int, chat_id: int, is_new: bool
) -> bool:
    if not is_new:
        return False
    chat = get_chat(conn, chat_id)
    record_change(
        conn,
        run_id,
        "new_common_chat",
        user_id=int(user_id),
        chat_id=int(chat_id),
        detail={"title": chat["title"] if chat else None},
    )
    return True


def diff_left_chats(conn: sqlite3.Connection, run_id: int, previous_run_id: int | None) -> int:
    """Чаты, которые были в прошлом прогоне и пропали в этом."""
    if previous_run_id is None:
        return 0
    rows = conn.execute(
        """
        SELECT * FROM chats
        WHERE excluded = 0 AND last_seen_run = ? AND last_seen_run < ?
        """,
        (previous_run_id, run_id),
    ).fetchall()
    for row in rows:
        record_change(
            conn,
            run_id,
            "left_chat",
            chat_id=int(row["id"]),
            detail={"title": row["title"], "last_seen_run": previous_run_id},
        )
    return len(rows)


def stats_snapshot(conn: sqlite3.Connection) -> dict[str, int]:
    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    return {name: count(name) for name in ("chats", "users", "memberships", "common_chats", "messages")}


__all__: Sequence[str] = [
    "SCHEMA_STATEMENTS",
    "CHANGE_KINDS",
    "connect",
    "init_schema",
    "start_run",
    "finish_run",
    "last_finished_run",
    "upsert_chat",
    "upsert_user",
    "upsert_membership",
    "upsert_common_chat",
    "insert_messages",
    "get_chat",
    "get_user",
    "iter_chats",
    "iter_users",
    "iter_messages",
    "last_message_id",
    "message_count",
    "active_chat_ids",
    "get_md_file",
    "upsert_md_file",
    "iter_md_files",
    "record_change",
    "iter_changes",
    "diff_new_chat",
    "diff_new_contact",
    "diff_bio_changed",
    "diff_new_common_chat",
    "diff_left_chats",
    "stats_snapshot",
]
