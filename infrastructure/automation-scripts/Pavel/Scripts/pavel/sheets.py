"""Детерминированная выгрузка в Google-таблицу «Anton Telegram» (команда `push-sheets`).

Пишет код, а не модель: строки собираются SQL-запросами по схеме D и уходят
POST'ом на Apps Script веб-хук (`Scripts/apps_script_webhook.gs`) батчами.
Upsert по ключу: перезаписываются только machine-колонки, ручные колонки
Антона («Решение Антона», «Комментарий») веб-хук не трогает.

Только stdlib (urllib) — паттерн `Travel Agent/Scripts/price_radar.py`.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

BATCH_SIZE = 200
WEBHOOK_TIMEOUT = 60

# Колонки людей. Последние две — ручные, в machine_columns не входят.
PEOPLE_COLUMNS: list[str] = [
    "user_id",
    "Имя",
    "@username",
    "Bio",
    "Категория",
    "Направление",
    "Уверенность",
    "Чем занимается",
    "Теги",
    "Общие чаты (кол-во)",
    "Общие чаты (список ≤10)",
    "Последний контакт",
    "Сообщений (12 мес)",
    "Обновлено",
    "Решение Антона",
    "Комментарий",
]
MANUAL_COLUMNS = ["Решение Антона", "Комментарий"]
PEOPLE_MACHINE_COLUMNS = [c for c in PEOPLE_COLUMNS if c not in MANUAL_COLUMNS]

GROUP_COLUMNS = [
    "chat_id",
    "Название",
    "Тип",
    "Участников",
    "Сообщений (12 мес)",
    "Последнее сообщение",
]
CHANGE_COLUMNS = ["id", "Прогон", "Дата", "Вид", "Кто", "Чат", "Детали"]

CATEGORY_LABELS = {
    "client": "клиент",
    "executor": "исполнитель",
    "collab": "коллаб",
    "none": "—",
}
DIRECTION_LABELS = {"design": "дизайн", "ai": "ИИ", "startups": "стартапы"}

SHEET_CLIENTS = "Клиенты"
SHEET_EXECUTORS = "Исполнители"
SHEET_COLLABS = "Коллабы"
SHEET_ALL = "Все контакты"
SHEET_GROUPS = "Группы"
SHEET_CHANGES = "Изменения"

_SSL_CTX = ssl.create_default_context()

_RECENT = "((typeof({col}) = 'integer' AND {col} >= :unix) OR (typeof({col}) <> 'integer' AND {col} >= :iso))"


def _window(months: int = 12) -> dict[str, Any]:
    moment = datetime.now(timezone.utc) - timedelta(days=30 * months)
    return {"iso": moment.strftime("%Y-%m-%d %H:%M:%S"), "unix": int(moment.timestamp())}


def _json_list(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _name(first: Any, last: Any, username: Any, user_id: Any) -> str:
    parts = [str(p) for p in (first, last) if p]
    if parts:
        return " ".join(parts)
    return f"@{username}" if username else str(user_id)


# ── формирование строк ──────────────────────────────────────────────────────

def build_people_rows(conn, *, months: int = 12) -> list[dict]:
    """По одной строке-словарю на человека; ключ — user_id."""
    params = _window(months)
    rows = conn.execute(
        f"""
        SELECT u.id, u.username, u.first_name, u.last_name, u.bio, u.is_contact,
               p.category, p.collab_direction, p.confidence, p.summary,
               p.activity_tags_json, p.profiled_at,
               (SELECT COUNT(*) FROM common_chats cc WHERE cc.user_id = u.id) AS common_cnt,
               (SELECT COUNT(*) FROM messages m
                 WHERE m.sender_id = u.id AND {_RECENT.format(col='m.date')}) AS msg_cnt,
               (SELECT MAX(m2.date) FROM messages m2 WHERE m2.sender_id = u.id) AS last_contact
          FROM users u
          LEFT JOIN profiles p ON p.user_id = u.id
         WHERE COALESCE(u.is_bot, 0) = 0
           AND (COALESCE(u.is_contact, 0) = 1 OR p.user_id IS NOT NULL)
      ORDER BY msg_cnt DESC, u.id
        """,
        params,
    ).fetchall()

    out: list[dict] = []
    for row in rows:
        (
            uid, username, first, last, bio, _is_contact,
            category, direction, confidence, summary,
            tags_json, profiled_at, common_cnt, msg_cnt, last_contact,
        ) = tuple(row)[:15]

        titles = [
            str(r[0])
            for r in conn.execute(
                """SELECT c.title FROM common_chats cc JOIN chats c ON c.id = cc.chat_id
                    WHERE cc.user_id = ? AND COALESCE(c.excluded, 0) = 0
                 ORDER BY c.title LIMIT 10""",
                (uid,),
            ).fetchall()
            if r[0]
        ]
        out.append(
            {
                "user_id": uid,
                "Имя": _name(first, last, username, uid),
                "@username": f"@{username}" if username else "",
                "Bio": bio or "",
                "Категория": CATEGORY_LABELS.get(category or "", ""),
                "Направление": DIRECTION_LABELS.get(direction or "", ""),
                "Уверенность": round(float(confidence), 2) if confidence is not None else "",
                "Чем занимается": summary or "",
                "Теги": ", ".join(str(t) for t in _json_list(tags_json)),
                "Общие чаты (кол-во)": int(common_cnt or 0),
                "Общие чаты (список ≤10)": ", ".join(titles),
                "Последний контакт": str(last_contact or ""),
                "Сообщений (12 мес)": int(msg_cnt or 0),
                "Обновлено": str(profiled_at or ""),
                "_category": category or "none",
            }
        )
    return out


def _by_category(people: Iterable[dict], category: str) -> list[dict]:
    return [{k: v for k, v in p.items() if k != "_category"} for p in people if p["_category"] == category]


def build_group_rows(conn, *, months: int = 12) -> list[dict]:
    params = _window(months)
    rows = conn.execute(
        f"""
        SELECT c.id, c.title, c.type, c.members_count, c.last_message_at,
               (SELECT COUNT(*) FROM messages m
                 WHERE m.chat_id = c.id AND {_RECENT.format(col='m.date')}) AS msg_cnt
          FROM chats c
         WHERE COALESCE(c.excluded, 0) = 0 AND c.type IN ('group', 'supergroup', 'channel')
      ORDER BY msg_cnt DESC, c.id
        """,
        params,
    ).fetchall()
    return [
        {
            "chat_id": cid,
            "Название": title or "",
            "Тип": ctype or "",
            "Участников": int(members or 0),
            "Сообщений (12 мес)": int(msg_cnt or 0),
            "Последнее сообщение": str(last_at or ""),
        }
        for cid, title, ctype, members, last_at, msg_cnt in (tuple(r)[:6] for r in rows)
    ]


def build_change_rows(conn, *, run_id: int | None = None) -> list[dict]:
    sql = """SELECT ch.id, ch.run_id, ch.created_at, ch.kind, ch.user_id, ch.chat_id, ch.detail_json,
                    u.username, u.first_name, u.last_name, c.title
               FROM changes ch
               LEFT JOIN users u ON u.id = ch.user_id
               LEFT JOIN chats c ON c.id = ch.chat_id"""
    params: tuple = ()
    if run_id is not None:
        sql += " WHERE ch.run_id = ?"
        params = (run_id,)
    sql += " ORDER BY ch.id"

    out = []
    for row in conn.execute(sql, params).fetchall():
        (cid, rid, created, kind, uid, chat_id, detail, username, first, last, title) = tuple(row)[:11]
        out.append(
            {
                "id": cid,
                "Прогон": rid if rid is not None else "",
                "Дата": str(created or ""),
                "Вид": kind or "",
                "Кто": _name(first, last, username, uid) if uid else "",
                "Чат": title or (chat_id if chat_id else ""),
                "Детали": detail if isinstance(detail, str) else json.dumps(detail or {}, ensure_ascii=False),
            }
        )
    return out


# ── транспорт ───────────────────────────────────────────────────────────────

def _webhook(env: Any) -> tuple[str | None, str | None]:
    def get(key: str, attr: str):
        if env is None:
            return None
        if isinstance(env, dict):
            return env.get(key)
        return getattr(env, attr, None)

    return (
        get("PAVEL_SHEETS_WEBHOOK_URL", "sheets_webhook_url"),
        get("PAVEL_SHEETS_WEBHOOK_SECRET", "sheets_webhook_secret"),
    )


def post_rows(
    url: str,
    secret: str,
    sheet: str,
    key: str,
    rows: Sequence[dict],
    machine_columns: Sequence[str],
    *,
    action: str = "upsert",
    batch_size: int = BATCH_SIZE,
) -> int:
    """POST батчами по batch_size. Возвращает число отправленных строк."""
    sent = 0
    for start in range(0, len(rows), batch_size):
        chunk = list(rows[start : start + batch_size])
        payload = {
            "secret": secret,
            "action": action,
            "sheet": sheet,
            "key": key,
            "rows": chunk,
            "machine_columns": list(machine_columns),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        result = json.loads(body) if body.strip() else {}
        if not (isinstance(result, dict) and result.get("ok") is True):
            raise RuntimeError(f"веб-хук не подтвердил запись в «{sheet}»: {body[:300]}")
        sent += len(chunk)
    return sent


def build_payloads(conn, *, run_id: int | None = None, months: int = 12) -> list[dict]:
    """Все вкладки одним списком — удобно и для отправки, и для --dry-run."""
    people = build_people_rows(conn, months=months)
    all_people = [{k: v for k, v in p.items() if k != "_category"} for p in people]
    return [
        {"sheet": SHEET_CLIENTS, "key": "user_id", "rows": _by_category(people, "client"),
         "machine_columns": PEOPLE_MACHINE_COLUMNS},
        {"sheet": SHEET_EXECUTORS, "key": "user_id", "rows": _by_category(people, "executor"),
         "machine_columns": PEOPLE_MACHINE_COLUMNS},
        {"sheet": SHEET_COLLABS, "key": "user_id", "rows": _by_category(people, "collab"),
         "machine_columns": PEOPLE_MACHINE_COLUMNS},
        {"sheet": SHEET_ALL, "key": "user_id", "rows": all_people,
         "machine_columns": PEOPLE_MACHINE_COLUMNS},
        {"sheet": SHEET_GROUPS, "key": "chat_id", "rows": build_group_rows(conn, months=months),
         "machine_columns": GROUP_COLUMNS},
        {"sheet": SHEET_CHANGES, "key": "id", "rows": build_change_rows(conn, run_id=run_id),
         "machine_columns": CHANGE_COLUMNS},
    ]


def push_sheets(db, settings: Any = None, env: Any = None, *, run_id: int | None = None,
                dry_run: bool = False) -> dict:
    """Главная точка команды `push-sheets`. `db` — соединение из db.connect()."""
    if isinstance(settings, dict):
        months = int(settings.get("profile_months") or 12)
    else:
        months = int(getattr(settings, "profile_months", None) or 12)
    payloads = build_payloads(db, run_id=run_id, months=months)
    stats = {p["sheet"]: len(p["rows"]) for p in payloads}

    if dry_run:
        stats["_dry_run"] = True
        return stats

    url, secret = _webhook(env)
    if not url or not secret:
        raise RuntimeError(
            "Веб-хук не настроен: нужны PAVEL_SHEETS_WEBHOOK_URL и PAVEL_SHEETS_WEBHOOK_SECRET "
            "в ~/.config/second-brain/pavel.env (лист «Anton Telegram» разворачивает Антон, H2)."
        )
    for payload in payloads:
        if not payload["rows"]:
            continue
        post_rows(url, secret, payload["sheet"], payload["key"], payload["rows"],
                  payload["machine_columns"])
    return stats
