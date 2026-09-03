"""Отчёт прогона и снапшот нетворка (команда `report`).

Два артефакта в `outputs/`:
  - `{automation} {report} Pavel прогон – YYYY-MM-DD.md` — статистика run
    и список изменений человекочитаемо (EN DASH перед датой, конвенция vault);
  - `network.json` — слой для скилла `/pavel`: люди, профили, общие чаты,
    членства, счётчики. **Текстов сообщений в нём нет и быть не должно.**
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUTS_DIR = PROJECT_ROOT / "outputs"
NETWORK_JSON = "network.json"

CHANGE_LABELS = {
    "new_contact": "Новые контакты",
    "bio_changed": "Сменилось bio",
    "new_common_chat": "Новые общие чаты",
    "new_chat": "Новые чаты",
    "left_chat": "Ушёл из чата",
    "profile_changed": "Изменился профиль",
}

_RECENT = "((typeof(m.date) = 'integer' AND m.date >= :unix) OR (typeof(m.date) <> 'integer' AND m.date >= :iso))"


def report_filename(date: str) -> str:
    """EN DASH (–) перед датой — конвенция имён vault."""
    return f"{{automation}} {{report}} Pavel прогон – {date}.md"


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


def _name(first: Any, last: Any, username: Any, uid: Any) -> str:
    parts = [str(p) for p in (first, last) if p]
    if parts:
        return " ".join(parts)
    return f"@{username}" if username else str(uid)


# ── сбор данных ─────────────────────────────────────────────────────────────

def run_stats(conn, run_id: int | None) -> dict:
    row = None
    if run_id is not None:
        row = conn.execute(
            "SELECT id, started_at, finished_at, cmd, stats_json FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return {"run_id": run_id, "cmd": "", "started_at": "", "finished_at": "", "stats": {}}
    rid, started, finished, cmd, stats_json = tuple(row)[:5]
    stats = stats_json
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except (ValueError, TypeError):
            stats = {"raw": stats}
    return {
        "run_id": rid,
        "cmd": cmd or "",
        "started_at": str(started or ""),
        "finished_at": str(finished or ""),
        "stats": stats if isinstance(stats, dict) else {},
    }


def changes_for_run(conn, run_id: int | None) -> list[dict]:
    sql = """SELECT ch.id, ch.kind, ch.user_id, ch.chat_id, ch.detail_json, ch.created_at,
                    u.username, u.first_name, u.last_name, c.title
               FROM changes ch
               LEFT JOIN users u ON u.id = ch.user_id
               LEFT JOIN chats c ON c.id = ch.chat_id"""
    params: tuple = ()
    if run_id is not None:
        sql += " WHERE ch.run_id = ?"
        params = (run_id,)
    sql += " ORDER BY ch.kind, ch.id"

    out = []
    for row in conn.execute(sql, params).fetchall():
        cid, kind, uid, chat_id, detail, created, username, first, last, title = tuple(row)[:10]
        out.append(
            {
                "id": cid,
                "kind": kind or "",
                "who": _name(first, last, username, uid) if uid else "",
                "chat": title or (str(chat_id) if chat_id else ""),
                "detail": detail if isinstance(detail, str) else json.dumps(detail or {}, ensure_ascii=False),
                "created_at": str(created or ""),
            }
        )
    return out


def build_network(conn, *, months: int = 12) -> dict:
    """Снапшот нетворка без текстов сообщений."""
    params = _window(months)
    people = []
    rows = conn.execute(
        f"""SELECT u.id, u.username, u.first_name, u.last_name, u.bio, u.is_contact,
                   p.summary, p.activity_tags_json, p.category, p.collab_direction,
                   p.confidence, p.profiled_at, p.source_msg_count,
                   (SELECT COUNT(*) FROM messages m
                     WHERE m.sender_id = u.id AND {_RECENT}) AS msg_cnt
              FROM users u
              LEFT JOIN profiles p ON p.user_id = u.id
             WHERE COALESCE(u.is_bot, 0) = 0
               AND (COALESCE(u.is_contact, 0) = 1 OR p.user_id IS NOT NULL)
          ORDER BY msg_cnt DESC, u.id""",
        params,
    ).fetchall()

    for row in rows:
        (uid, username, first, last, bio, is_contact, summary, tags, category,
         direction, confidence, profiled_at, source_cnt, msg_cnt) = tuple(row)[:14]
        common = [
            {"chat_id": r[0], "title": r[1]}
            for r in conn.execute(
                """SELECT c.id, c.title FROM common_chats cc JOIN chats c ON c.id = cc.chat_id
                    WHERE cc.user_id = ? AND COALESCE(c.excluded, 0) = 0 ORDER BY c.title""",
                (uid,),
            ).fetchall()
        ]
        memberships = [
            {"chat_id": r[0], "title": r[1], "role": r[2]}
            for r in conn.execute(
                """SELECT c.id, c.title, mm.role FROM memberships mm JOIN chats c ON c.id = mm.chat_id
                    WHERE mm.user_id = ? AND COALESCE(c.excluded, 0) = 0 ORDER BY c.title""",
                (uid,),
            ).fetchall()
        ]
        person = {
            "user_id": uid,
            "username": username,
            "name": _name(first, last, username, uid),
            "bio": bio,
            "is_contact": bool(is_contact),
            "messages_12m": int(msg_cnt or 0),
            "common_chats": common,
            "memberships": memberships,
            "profile": None,
        }
        if category is not None or summary is not None:
            person["profile"] = {
                "summary": summary,
                "activity_tags": _json_list(tags),
                "category": category,
                "collab_direction": direction,
                "confidence": confidence,
                "profiled_at": profiled_at,
                "source_msg_count": source_cnt,
            }
        people.append(person)

    chats = [
        {
            "chat_id": r[0],
            "title": r[1],
            "type": r[2],
            "username": r[3],
            "members_count": r[4],
            "last_message_at": str(r[5] or ""),
        }
        for r in conn.execute(
            """SELECT id, title, type, username, members_count, last_message_at
                 FROM chats WHERE COALESCE(excluded, 0) = 0 ORDER BY id"""
        ).fetchall()
    ]

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "pavel",
        "note": "Снапшот нетворка без текстов сообщений. Сырьё чатов — transcripts/telegram/.",
        "users": people,
        "chats": chats,
    }


# ── рендер ──────────────────────────────────────────────────────────────────

def render_report(info: dict, changes: list[dict], network: dict, date: str) -> str:
    lines = [
        "---",
        "tags:",
        "  - type/report",
        "  - project/automation",
        "  - topic/telegram",
        f"date: {date}",
        "source: pavel",
        "track: none",
        "---",
        "",
        "# Pavel — отчёт о прогоне",
        "",
        f"### [[{date}]]",
        "",
        "## 1.0 Прогон",
        "",
        "| Поле | Значение |",
        "|---|---|",
        f"| run_id | {info.get('run_id') if info.get('run_id') is not None else '—'} |",
        f"| Команда | {info.get('cmd') or '—'} |",
        f"| Начат | {info.get('started_at') or '—'} |",
        f"| Завершён | {info.get('finished_at') or '—'} |",
    ]
    for key, value in (info.get("stats") or {}).items():
        lines.append(f"| {key} | {value} |")

    lines += [
        "",
        "## 2.0 Нетворк на сейчас",
        "",
        f"- Людей в снапшоте: {len(network.get('users', []))}",
        f"- С профилем: {sum(1 for u in network.get('users', []) if u.get('profile'))}",
        f"- Чатов: {len(network.get('chats', []))}",
        "",
        "## 3.0 Изменения",
        "",
    ]

    if not changes:
        lines.append("Изменений за прогон нет.")
    else:
        grouped: dict[str, list[dict]] = {}
        for change in changes:
            grouped.setdefault(change["kind"], []).append(change)
        for kind, items in grouped.items():
            lines.append(f"### {CHANGE_LABELS.get(kind, kind)} ({len(items)})")
            lines.append("")
            for item in items:
                subject = item["who"] or item["chat"] or "—"
                detail = f" — {item['detail']}" if item["detail"] and item["detail"] != "{}" else ""
                lines.append(f"- {subject}{detail}")
            lines.append("")

    lines += [
        "",
        "## 4.0 Связи",
        "",
        "- Снапшот для скилла: `outputs/network.json`",
        "- Сырьё чатов: `transcripts/telegram/index.md`",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_report(db, run_id: int | None = None, outputs_dir: str | Path | None = None,
                 *, months: int = 12, date: str | None = None) -> tuple[Path, Path]:
    """Пишет md-отчёт и network.json. Возвращает (путь к md, путь к json)."""
    out_dir = Path(outputs_dir or DEFAULT_OUTPUTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = date or datetime.now().strftime("%Y-%m-%d")

    info = run_stats(db, run_id)
    changes = changes_for_run(db, run_id)
    network = build_network(db, months=months)
    network["run_id"] = run_id

    md_path = out_dir / report_filename(day)
    md_path.write_text(render_report(info, changes, network, day), encoding="utf-8")

    json_path = out_dir / NETWORK_JSON
    json_path.write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path
