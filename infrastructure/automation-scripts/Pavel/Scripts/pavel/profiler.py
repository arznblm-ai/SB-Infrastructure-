"""LLM-профилирование активных контактов (команда `profile`).

Один вызов `claude -p` на человека, без инструментов; JSON валидируется кодом.
Текст сообщений — недоверенные данные (ADR-009): барьер прописан в промпте
`config/profile_prompt.md`, инструменты у модели отключены флагом.

Владелец схемы sqlite — T1 (`pavel/db.py`), здесь только прямые SQL-запросы
по схеме D плана.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "config" / "profile_prompt.md"

CLAUDE_BIN = os.environ.get("PAVEL_CLAUDE_BIN") or os.path.expanduser("~/.local/bin/claude")
CLAUDE_MODEL = os.environ.get("PAVEL_PROFILE_MODEL", "sonnet")
CLAUDE_TIMEOUT_SECONDS = int(os.environ.get("PAVEL_PROFILE_TIMEOUT", "180"))

# Все инструменты запрещены — паттерн digest_builder.py (AI Twitter Digest).
DISALLOWED_TOOLS = ["Bash", "Edit", "Write", "WebFetch", "WebSearch", "Read", "Glob", "Grep"]

DEFAULT_MIN_MSGS = 20
DEFAULT_PROFILE_MONTHS = 12
MAX_MESSAGES_IN_PROMPT = 150
MAX_MESSAGE_CHARS = 500
REGROW_RATIO = 1.30  # «источник вырос ≥ на 30%» → перепрофилировать

CATEGORIES = {"client", "executor", "collab", "none"}
DIRECTIONS = {"design", "ai", "startups"}

FALLBACK_PROFILE: dict[str, Any] = {
    "summary": "(не удалось профилировать)",
    "activity_tags": [],
    "category": "none",
    "collab_direction": None,
    "confidence": 0.0,
    "evidence": [],
}


# ── настройки ───────────────────────────────────────────────────────────────

def setting(settings: Any, name: str, default: Any) -> Any:
    """Читает порог из Settings-дата-класса T1 или из обычного dict."""
    if settings is None:
        return default
    if isinstance(settings, dict):
        value = settings.get(name, default)
    else:
        value = getattr(settings, name, default)
    return default if value is None else value


# ── отбор кандидатов ────────────────────────────────────────────────────────

@dataclass
class Candidate:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    bio: str | None
    is_contact: int
    msg_count: int

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or (f"@{self.username}" if self.username else str(self.user_id))


def _cutoff(months: int) -> tuple[str, int]:
    """Граница окна в двух форматах — ISO и unix (тип поля `date` не зафиксирован)."""
    moment = datetime.now(timezone.utc) - timedelta(days=30 * months)
    return moment.isoformat(), int(moment.timestamp())


_RECENT_CLAUSE = "((typeof(m.date) = 'integer' AND m.date >= :unix) OR (typeof(m.date) <> 'integer' AND m.date >= :iso))"


def select_candidates(
    conn,
    *,
    min_msgs: int = DEFAULT_MIN_MSGS,
    only_new: bool = False,
    limit: int | None = None,
    months: int = DEFAULT_PROFILE_MONTHS,
) -> list[Candidate]:
    """Активные люди: ≥ min_msgs сообщений за окно; при --only-new — новые/подросшие."""
    iso, unix = _cutoff(months)
    sql = f"""
        SELECT u.id, u.username, u.first_name, u.last_name, u.bio, u.is_contact,
               COUNT(m.id) AS msg_count,
               p.source_msg_count AS prev_count
          FROM users u
          JOIN messages m ON m.sender_id = u.id AND {_RECENT_CLAUSE}
          LEFT JOIN profiles p ON p.user_id = u.id
         WHERE COALESCE(u.is_bot, 0) = 0
      GROUP BY u.id
        HAVING msg_count >= :min_msgs
      ORDER BY msg_count DESC
    """
    rows = conn.execute(sql, {"iso": iso, "unix": unix, "min_msgs": int(min_msgs)}).fetchall()

    out: list[Candidate] = []
    for row in rows:
        (uid, username, first, last, bio, is_contact, msg_count, prev_count) = tuple(row)[:8]
        if only_new:
            if prev_count is not None and msg_count < prev_count * REGROW_RATIO:
                continue
        out.append(
            Candidate(
                user_id=uid,
                username=username,
                first_name=first,
                last_name=last,
                bio=bio,
                is_contact=int(is_contact or 0),
                msg_count=int(msg_count),
            )
        )
        if limit and len(out) >= int(limit):
            break
    return out


# ── сбор входа для промпта ──────────────────────────────────────────────────

def _titles(conn, sql: str, params: dict) -> list[str]:
    return [str(r[0]) for r in conn.execute(sql, params).fetchall() if r[0]]


def collect_person_data(conn, cand: Candidate, *, months: int = DEFAULT_PROFILE_MONTHS) -> dict:
    iso, unix = _cutoff(months)
    common = _titles(
        conn,
        """SELECT c.title FROM common_chats cc JOIN chats c ON c.id = cc.chat_id
            WHERE cc.user_id = :uid AND COALESCE(c.excluded, 0) = 0 LIMIT 60""",
        {"uid": cand.user_id},
    )
    groups = _titles(
        conn,
        """SELECT c.title FROM memberships mm JOIN chats c ON c.id = mm.chat_id
            WHERE mm.user_id = :uid AND COALESCE(c.excluded, 0) = 0 LIMIT 60""",
        {"uid": cand.user_id},
    )
    msg_rows = conn.execute(
        f"""SELECT m.date, c.title, m.text
              FROM messages m LEFT JOIN chats c ON c.id = m.chat_id
             WHERE m.sender_id = :uid AND {_RECENT_CLAUSE}
               AND m.text IS NOT NULL AND TRIM(m.text) <> ''
          ORDER BY m.date DESC LIMIT :lim""",
        {"uid": cand.user_id, "iso": iso, "unix": unix, "lim": MAX_MESSAGES_IN_PROMPT},
    ).fetchall()

    messages = []
    for date, title, text in (tuple(r)[:3] for r in msg_rows):
        clean = " ".join(str(text).split())[:MAX_MESSAGE_CHARS]
        messages.append({"date": str(date), "chat": title or "", "text": clean})

    return {
        "user_id": cand.user_id,
        "name": cand.display_name,
        "username": cand.username,
        "bio": cand.bio,
        "is_contact": bool(cand.is_contact),
        "common_chats": common,
        "groups": groups,
        "messages_12m": cand.msg_count,
        "messages": messages,
    }


def render_data_block(person: dict) -> str:
    lines = [
        f"Имя: {person['name']}",
        f"Username: @{person['username']}" if person.get("username") else "Username: —",
        f"В контактах: {'да' if person.get('is_contact') else 'нет'}",
        f"Bio: {person.get('bio') or '—'}",
        f"Сообщений за 12 мес: {person.get('messages_12m', 0)}",
        "",
        "Общие чаты: " + (", ".join(person.get("common_chats") or []) or "—"),
        "Группы: " + (", ".join(person.get("groups") or []) or "—"),
        "",
        f"Последние сообщения (до {MAX_MESSAGES_IN_PROMPT}, каждое обрезано до {MAX_MESSAGE_CHARS} симв.):",
    ]
    for msg in person.get("messages") or []:
        chat = f" [{msg['chat']}]" if msg.get("chat") else ""
        lines.append(f"- {msg.get('date', '')}{chat}: {msg.get('text', '')}")
    if not person.get("messages"):
        lines.append("- (нет текстовых сообщений в окне)")
    return "\n".join(lines)


def load_prompt_template(path: str | os.PathLike[str] | None = None) -> str:
    return Path(path or DEFAULT_PROMPT_PATH).read_text(encoding="utf-8")


def build_prompt(template: str, person: dict) -> str:
    if "{{DATA}}" not in template:
        raise ValueError("В шаблоне промпта нет плейсхолдера {{DATA}}")
    return template.replace("{{DATA}}", render_data_block(person))


# ── вызов модели ────────────────────────────────────────────────────────────

def call_llm(prompt: str) -> str:
    """Один вызов `claude -p` без инструментов. Возвращает stdout (сырой)."""
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--model",
        CLAUDE_MODEL,
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "--no-session-persistence",
        "--disallowedTools",
        *DISALLOWED_TOOLS,
    ]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_SECONDS
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
    return (proc.stdout or "").strip()


_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def extract_json(raw: str) -> dict | None:
    """Достаёт объект профиля из ответа: обёртка `--output-format json` → fence → голый JSON."""
    if not raw:
        return None
    text = raw.strip()
    try:
        outer = json.loads(text)
        if isinstance(outer, dict):
            if "result" in outer and isinstance(outer["result"], str):
                text = outer["result"].strip()
            elif "category" in outer or "summary" in outer:
                return outer
    except (ValueError, TypeError):
        pass

    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _strings(value: Any, limit: int, max_chars: int = 300) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(" ".join(item.split())[:max_chars])
        if len(out) >= limit:
            break
    return out


def validate_profile(payload: Any) -> dict | None:
    """Строгая валидация JSON-профиля. Невалид → None (решение о fallback — выше)."""
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    category = payload.get("category")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(category, str) or category not in CATEGORIES:
        return None

    direction = payload.get("collab_direction")
    if isinstance(direction, str) and direction.strip().lower() in {"null", "none", ""}:
        direction = None
    if direction is not None and (not isinstance(direction, str) or direction not in DIRECTIONS):
        return None
    if category != "collab":
        direction = None

    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None

    return {
        "summary": " ".join(summary.split())[:2000],
        "activity_tags": _strings(payload.get("activity_tags"), 8, 80),
        "category": category,
        "collab_direction": direction,
        "confidence": confidence,
        "evidence": _strings(payload.get("evidence"), 3, 300),
    }


def profile_one(prompt: str, llm: Callable[[str], str]) -> tuple[dict, bool]:
    """Вызов + валидация с одним ретраем. Возвращает (профиль, ok)."""
    for attempt in range(2):
        try:
            raw = llm(prompt)
        except Exception:  # noqa: BLE001 — один упавший человек не валит прогон
            continue
        result = validate_profile(extract_json(raw))
        if result is not None:
            return result, True
    return dict(FALLBACK_PROFILE), False


# ── запись в sqlite ─────────────────────────────────────────────────────────

def upsert_profile(conn, user_id: int, data: dict, *, model: str, source_msg_count: int) -> str | None:
    """Пишет профиль. Возвращает описание изменения, если category/summary поменялись."""
    row = conn.execute(
        "SELECT category, summary FROM profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    old_category, old_summary = (tuple(row)[:2] if row else (None, None))

    conn.execute(
        """INSERT INTO profiles
             (user_id, summary, activity_tags_json, category, collab_direction,
              confidence, evidence_json, model, profiled_at, source_msg_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             summary=excluded.summary,
             activity_tags_json=excluded.activity_tags_json,
             category=excluded.category,
             collab_direction=excluded.collab_direction,
             confidence=excluded.confidence,
             evidence_json=excluded.evidence_json,
             model=excluded.model,
             profiled_at=excluded.profiled_at,
             source_msg_count=excluded.source_msg_count""",
        (
            user_id,
            data["summary"],
            json.dumps(data["activity_tags"], ensure_ascii=False),
            data["category"],
            data["collab_direction"],
            data["confidence"],
            json.dumps(data["evidence"], ensure_ascii=False),
            model,
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            int(source_msg_count),
        ),
    )
    conn.commit()

    if row is None:
        return f"новый профиль: {data['category']}"
    if old_category != data["category"] or (old_summary or "") != data["summary"]:
        return f"{old_category or '—'} → {data['category']}"
    return None


# ── оркестрация команды ─────────────────────────────────────────────────────

def run_profile(
    conn,
    *,
    settings: Any = None,
    run_id: int | None = None,
    min_msgs: int | None = None,
    only_new: bool = False,
    limit: int | None = None,
    months: int | None = None,
    prompt_path: str | os.PathLike[str] | None = None,
    llm: Callable[[str], str] = call_llm,
    record_change: Callable[..., Any] | None = None,
    model: str | None = None,
) -> dict:
    if min_msgs is None:
        min_msgs = int(setting(settings, "profile_min_msgs", DEFAULT_MIN_MSGS))
    if months is None:
        months = int(setting(settings, "profile_months", DEFAULT_PROFILE_MONTHS))

    template = load_prompt_template(prompt_path)
    candidates = select_candidates(
        conn, min_msgs=min_msgs, only_new=only_new, limit=limit, months=months
    )

    stats = {"candidates": len(candidates), "profiled": 0, "failed": 0, "changed": 0}
    for cand in candidates:
        person = collect_person_data(conn, cand, months=months)
        data, ok = profile_one(build_prompt(template, person), llm)
        change = upsert_profile(
            conn,
            cand.user_id,
            data,
            model=model or CLAUDE_MODEL,
            source_msg_count=cand.msg_count,
        )
        stats["profiled" if ok else "failed"] += 1
        if change and record_change:
            stats["changed"] += 1
            record_change(
                run_id=run_id,
                kind="profile_changed",
                user_id=cand.user_id,
                detail={"change": change, "confidence": data["confidence"]},
            )
        elif change:
            stats["changed"] += 1
    return stats


def add_arguments(parser) -> None:
    parser.add_argument("--min-msgs", type=int, default=None, help="порог активности (default 20)")
    parser.add_argument("--only-new", action="store_true", help="только новые/подросшие")
    parser.add_argument("--limit", type=int, default=None, help="максимум людей за прогон")
    parser.add_argument("--months", type=int, default=None, help="окно, месяцев (default 12)")
