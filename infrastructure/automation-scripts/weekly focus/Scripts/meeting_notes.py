#!/usr/bin/env python3
"""
meeting_notes.py — Meeting Notes Assistant.

Следит за transcripts/: когда Krisp-транскрипт встречи готов, делает короткое
shareable summary (claude haiku), шлёт его в Telegram (Daily Focus бот) и
складывает таски встречи в "meeting todo/" для команд /todo в боте.

LaunchAgent: com.user.meeting-notes (WatchPaths на transcripts/ + StartInterval 600)
Лог: ~/Library/Logs/meeting-notes.log
State: ~/.config/second-brain/meeting-notes-state.json (ключ = krisp id, exactly-once)
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from task_owner import classify_owner

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
TRANSCRIPTS_DIR = VAULT / "transcripts"
DAILY_DIR = VAULT / "infrastructure" / "daily focus"
TODO_DIR = DAILY_DIR / "meeting todo"
ENV_FILE = Path("/Users/anton/.config/second-brain/daily-focus.env")
STATE_FILE = Path("/Users/anton/.config/second-brain/meeting-notes-state.json")
LOCK_FILE = Path("/Users/anton/.config/second-brain/meeting-notes.lock")
LOG_FILE = Path("/Users/anton/Library/Logs/meeting-notes.log")
CLAUDE_BIN = "/Users/anton/.local/bin/claude"
MODEL = "haiku"
MAX_AGE_DAYS = 3
MAX_LLM_ATTEMPT_RUNS = 3
ABANDON_AFTER_HOURS = 24
# Через сколько часов ожидания транскрипта прислать в TG предупреждение (один раз на встречу)
WARN_PENDING_AFTER_HOURS = 3
LOCK_STALE_SECONDS = 15 * 60
TRANSCRIPT_MIN_CHARS = 500
TRANSCRIPT_HEAD_CHARS = 30_000
TRANSCRIPT_TAIL_CHARS = 10_000

TRANSCRIPT_LOADING_PLACEHOLDER = "_Транскрипт загружается из Krisp..._"
TRANSCRIPT_MISSING_PLACEHOLDER = "_Транскрипт не получен. Откройте Krisp и скопируйте вручную._"

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary_lines": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 8,
        },
        "tasks": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string"},
                    "what": {"type": "string"},
                    "deadline": {"type": "string"},
                },
                "required": ["who", "what"],
            },
        },
        "detailed_lines": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 8,
            "maxItems": 25,
        },
    },
    "required": ["title", "summary_lines", "tasks", "detailed_lines"],
}

PROMPT_TEMPLATE = """Ты готовишь короткую заметку по итогам встречи. Участник встречи поделится этой заметкой с коллегами, поэтому она должна быть самодостаточной и безопасной для пересылки.

Правила:
- Только русский язык.
- Строго по транскрипту: ничего не выдумывай, не додумывай имена, даты и договорённости.
- summary_lines: 3-6 коротких строк (каждая до 90 символов) — суть обсуждения, решения и договорённости. Без воды, без вступлений вроде "на встрече обсудили".
- НИКАКОЙ личной информации об Антоне: ни финансов, ни здоровья, ни планов, ни других его проектов и бизнесов, если они не были предметом именно этой встречи.
- tasks: только явные договорённости и обещания из встречи. who — имя исполнителя из транскрипта. Владелец этих заметок — Антон Розенблюм: если исполнитель — участник, подписанный как Speaker_N, но по контексту транскрипта понятно, кто это (например, к нему обращаются по имени), пиши реальное имя. Если транскрипт без меток спикеров (диктофонная запись) и обещание сформулировано от первого лица («я сделаю», «я напишу»), исполнитель — Антон. "unknown" — только если исполнителя действительно нельзя определить. what — короткая формулировка задачи. deadline — только если срок явно назван (формат как в транскрипте), иначе пустая строка.
- title: короткое название встречи по её содержанию (2-5 слов).
- detailed_lines: развёрнутое саммари, 10-25 строк (каждая до 200 символов) — тоже безопасное для пересылки коллегам, те же запреты на личную информацию. Покрой: контекст и цель встречи; участники и их роли; ход обсуждения с позициями и аргументами сторон; названные цифры, факты, названия; принятые решения; договорённости; открытые вопросы и на чём остановились. Пиши плотно и конкретно, без воды, каждая строка — законченная мысль.

Название файла встречи: {title}
Дата встречи: {date}

Материалы встречи:

{body}"""


# ── Логирование ──────────────────────────────────────────────────────────

def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")
    print(message, flush=True)


# ── Env / Telegram (копия паттерна telegram_codex_bot.py, без импорта) ────

def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        raise RuntimeError(f"env file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = shlex.split(value.strip())[0] if value.strip() else ""
    return env


def send_message(token: str, chat_id: str, text: str, reply_markup: Optional[dict] = None) -> None:
    remaining = text.strip() or "(empty message)"
    while remaining:
        chunk, remaining = remaining[:3500], remaining[3500:]
        command = [
            "curl", "-fsS", "--max-time", "30",
            "--data-urlencode", f"chat_id={chat_id}",
            "--data-urlencode", f"text={chunk}",
            "--data-urlencode", "disable_web_page_preview=true",
        ]
        if reply_markup and not remaining:  # клавиатура только на последнем чанке
            command.extend(["--data-urlencode", f"reply_markup={json.dumps(reply_markup, ensure_ascii=False)}"])
        command.append(f"https://api.telegram.org/bot{token}/sendMessage")
        subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


# ── Парсинг транскрипт-заметок (тот же формат, что пишет krisp_to_obsidian) ─

def extract_frontmatter_value(content: str, field: str) -> Optional[str]:
    match = re.search(rf"(?m)^{re.escape(field)}:\s*(.+)$", content)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def extract_section(text: str, header: str) -> str:
    for prefix in ["# ", "## "]:
        pattern = rf"{re.escape(prefix)}{re.escape(header)}\n(.*?)(?=\n#{{1,2}} |\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def note_key(content: str) -> Optional[str]:
    call_id = extract_frontmatter_value(content, "krisp_call_id")
    if call_id:
        return call_id
    mcp_id = extract_frontmatter_value(content, "krisp_mcp_id") or extract_frontmatter_value(content, "krisp_id")
    if mcp_id:
        return f"mcp:{mcp_id}"
    voice_id = extract_frontmatter_value(content, "voice_memo_id")
    if voice_id:
        return f"voice:{voice_id}"
    return None


def note_date(content: str, path: Path) -> Optional[dt.date]:
    raw = extract_frontmatter_value(content, "date")
    if not raw:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
        raw = m.group(1) if m else None
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def note_title(content: str, path: Path) -> str:
    m = re.search(r"(?m)^# (.+)$", content)
    if m:
        return m.group(1).strip()
    stem = path.stem
    stem = re.sub(r"^\{meeting\}\s*", "", stem)
    stem = re.sub(r"\s+[–-]\s+\d{4}-\d{2}-\d{2}$", "", stem)
    return stem.strip()


def is_ready(content: str) -> bool:
    if TRANSCRIPT_LOADING_PLACEHOLDER in content or TRANSCRIPT_MISSING_PLACEHOLDER in content:
        return False
    return len(extract_section(content, "Транскрипт")) >= TRANSCRIPT_MIN_CHARS


def build_llm_body(content: str) -> str:
    parts = []
    key_points = extract_section(content, "Ключевые моменты")
    action_items = extract_section(content, "Action Items")
    transcript = extract_section(content, "Транскрипт")
    if key_points:
        parts.append(f"## Ключевые моменты\n{key_points}")
    if action_items:
        parts.append(f"## Action Items\n{action_items}")
    if len(transcript) > TRANSCRIPT_HEAD_CHARS + TRANSCRIPT_TAIL_CHARS:
        transcript = (
            transcript[:TRANSCRIPT_HEAD_CHARS]
            + "\n\n[... середина транскрипта опущена ...]\n\n"
            + transcript[-TRANSCRIPT_TAIL_CHARS:]
        )
    parts.append(f"## Транскрипт\n{transcript}")
    return "\n\n".join(parts)


# ── Claude CLI ────────────────────────────────────────────────────────────

def run_claude(prompt: str) -> Optional[dict]:
    cmd = [
        CLAUDE_BIN, "-p",
        "--model", MODEL,
        "--output-format", "json",
        "--json-schema", json.dumps(SUMMARY_SCHEMA, ensure_ascii=False),
        "--max-turns", "4",
        "--no-session-persistence",
        "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "Read", "Glob", "Grep",
    ]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=240,
        )
    except subprocess.TimeoutExpired:
        log("claude: timeout after 240s")
        return None
    if proc.returncode != 0:
        log(f"claude: exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log(f"claude: stdout is not JSON: {proc.stdout[:200]}")
        return None
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            log(f"claude: is_error=true: {str(envelope.get('result'))[:200]}")
            return None
        for field in ("structured_output", "result"):
            payload = envelope.get(field)
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
    log("claude: could not extract structured payload")
    return None


def validate_summary(data: dict) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    title = str(data.get("title", "")).strip()
    lines = data.get("summary_lines")
    tasks = data.get("tasks")
    if not title or not isinstance(lines, list) or not isinstance(tasks, list):
        return None
    clean_lines = []
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        # промпт просит <=90; жёсткая обрезка — только страховка от простыни
        if len(text) > 160:
            text = text[:157].rstrip() + "..."
        clean_lines.append(text)
    if len(clean_lines) < 2:
        return None
    clean_tasks = []
    for task in tasks[:10]:
        if not isinstance(task, dict):
            continue
        what = str(task.get("what", "")).strip()
        if not what:
            continue
        clean_tasks.append({
            "who": str(task.get("who", "")).strip() or "unknown",
            "what": what,
            "deadline": str(task.get("deadline", "")).strip(),
        })
    detailed = []
    for line in data.get("detailed_lines") or []:
        text = str(line).strip()
        if not text:
            continue
        if len(text) > 250:
            text = text[:247].rstrip() + "..."
        detailed.append(text)
    return {
        "title": title,
        "summary_lines": clean_lines[:8],
        "tasks": clean_tasks,
        "detailed_lines": detailed[:25],
    }


def summarize_llm(content: str, title: str, date: str) -> Optional[dict]:
    prompt = PROMPT_TEMPLATE.format(title=title, date=date, body=build_llm_body(content))
    for attempt in (1, 2):
        data = run_claude(prompt)
        summary = validate_summary(data) if data else None
        if summary:
            summary["engine"] = "llm"
            return summary
        log(f"claude: attempt {attempt} failed")
    return None


def summarize_fallback(content: str, title: str) -> Optional[dict]:
    """Детерминированная выжимка из готовых Krisp-секций, без LLM."""
    key_points = extract_section(content, "Ключевые моменты")
    action_items = extract_section(content, "Action Items")
    lines = []
    for raw in key_points.splitlines():
        text = re.sub(r"^[-*•]\s*", "", raw.strip())
        if not text or text.startswith("#"):
            continue
        if len(text) > 90:
            text = text[:87].rstrip() + "..."
        lines.append(text)
        if len(lines) >= 6:
            break
    tasks = []
    for raw in action_items.splitlines():
        text = re.sub(r"^[-*•]\s*(\[.\]\s*)?", "", raw.strip())
        if not text or text.startswith("#") or "No Action Item" in text:
            continue
        # Krisp-хвост "- _Имя_, _Jul, 17_" (или только имя / только дата)
        who, deadline = "unknown", ""
        m = re.search(r"\s+-\s+_([^_]+)_(?:\s*,\s*_([^_]+)_)?\s*$", text)
        if m:
            first, second = m.group(1).strip(), (m.group(2) or "").strip()
            if re.match(r"^[A-Za-z]{3},?\s*\d", first):
                deadline = first  # одинокая дата без исполнителя
            else:
                who, deadline = first, second
            text = text[:m.start()].rstrip(" .")
        if not text:
            continue
        tasks.append({"who": who, "what": text[:200], "deadline": deadline})
        if len(tasks) >= 10:
            break
    if len(lines) < 2:
        return None
    return {"title": title, "summary_lines": lines, "tasks": tasks, "engine": "fallback"}


# ── Todo store ────────────────────────────────────────────────────────────

def task_key(task: str) -> str:
    lowered = task.lower().replace("ё", "е")
    lowered = re.sub(r"[()]", "", lowered)
    lowered = re.sub(r"[^a-zа-я0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def recent_todo_files(limit: int = 7) -> list[Path]:
    if not TODO_DIR.exists():
        return []
    files = [p for p in TODO_DIR.iterdir() if p.is_file() and p.suffix == ".md"]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def existing_todo_state() -> tuple[set[str], int]:
    """Возвращает (ключи существующих задач за 7 файлов, максимальный T-номер)."""
    keys: set[str] = set()
    max_id = 0
    for path in recent_todo_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in re.finditer(r"(?m)^\s*task:\s*(.+)$", text):
            keys.add(task_key(m.group(1)))
        for m in re.finditer(r"(?m)^- id:\s*T(\d+)\s*$", text):
            max_id = max(max_id, int(m.group(1)))
    return keys, max_id


def append_todos(summary: dict, note_path: str, meeting_date: str, dry_run: bool = False) -> list[dict]:
    """Пишет задачи в стор. Возвращает список {id, what, who, owner};
    owner: me — в /todo, other — только в саммари, unknown — кнопка ➕."""
    tasks = summary.get("tasks", [])
    if not tasks:
        return []
    seen_keys, max_id = existing_todo_state()
    today = dt.date.today().isoformat()
    todo_path = TODO_DIR / f"{{todo}} meeting tasks – {today}.md"
    blocks = []
    items: list[dict] = []
    now_stamp = time.strftime("%Y-%m-%d %H:%M")
    source_meeting = f"{summary.get('title', 'встреча')} – {meeting_date}"
    for task in tasks:
        key = task_key(task["what"])
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        max_id += 1
        todo_id = f"T{max_id}"
        owner = classify_owner(task["who"])
        items.append({"id": todo_id, "what": task["what"], "who": task["who"], "owner": owner})
        blocks.append(
            f"- id: {todo_id}\n"
            f"  task: {task['what']}\n"
            f"  who: {task['who']}\n"
            f"  owner: {owner}\n"
            f"  deadline: {task['deadline'] or 'unknown'}\n"
            f"  source_meeting: {source_meeting}\n"
            f"  source_path: {note_path}\n"
            f"  status: open\n"
            f"  created: {now_stamp}\n"
        )
    if not blocks:
        return []
    if dry_run:
        print(f"--- todo blocks (would append to {todo_path}) ---")
        print("\n".join(blocks))
        return items
    TODO_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not todo_path.exists()
    with todo_path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Meeting TODO — {today}\n\n")
        f.write("\n".join(blocks) + "\n")
    return items


# ── Сообщения ─────────────────────────────────────────────────────────────

def format_shareable_message(summary: dict, meeting_date: str) -> str:
    try:
        date_label = dt.datetime.strptime(meeting_date, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        date_label = meeting_date
    lines = [f"📝 {summary['title']} — {date_label}", ""]
    lines.extend(summary["summary_lines"])
    tasks = summary.get("tasks", [])
    if tasks:
        lines.append("")
        lines.append("План действий:")
        for task in tasks:
            item = f"• {task['who']} — {task['what']}" if task["who"] != "unknown" else f"• {task['what']}"
            deadline = task["deadline"]
            if deadline:
                prefix = "" if re.match(r"^(до|к|в|на|по)\s", deadline.lower()) else "до "
                item += f" ({prefix}{deadline})"
            lines.append(item)
    return "\n".join(lines)


def _short_task(text: str, limit: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def format_private_message(items: list[dict], engine: str) -> str:
    mine = [i for i in items if i["owner"] == "me"]
    others = [i for i in items if i["owner"] == "other"]
    unclear = [i for i in items if i["owner"] == "unknown"]
    sections: list[str] = []
    if mine:
        lines = ["Мои задачи → /todo:"]
        lines += [f"• {i['id']} — {_short_task(i['what'])}" for i in mine]
        sections.append("\n".join(lines))
    if others:
        lines = ["Задачи других:"]
        lines += [f"• {i['id']} — {i['who']}: {_short_task(i['what'])}" for i in others]
        sections.append("\n".join(lines))
    if unclear:
        lines = ["Без исполнителя (➕ — взять себе):"]
        lines += [f"• {i['id']} — {_short_task(i['what'])}" for i in unclear]
        sections.append("\n".join(lines))
    if sections:
        text = "\n\n".join(sections) + "\n\nСписок: /todo, детали встречи: /meeting"
    else:
        text = "Новых задач из встречи не добавил (нет явных договорённостей или дубли)."
    if engine == "fallback":
        text += "\n(авто-выжимка без LLM)"
    return text


def todo_keyboard(items: list[dict]) -> Optional[dict]:
    """Inline-кнопки: ✔ для задач Антона, ➕ (взять себе) для unknown;
    callback обрабатывает telegram_codex_bot."""
    buttons = [
        {"text": f"✔ {i['id']}", "callback_data": f"mnote_done:{i['id']}"}
        for i in items if i["owner"] == "me"
    ] + [
        {"text": f"➕ {i['id']}", "callback_data": f"todo_claim:{i['id']}"}
        for i in items if i["owner"] == "unknown"
    ]
    if not buttons:
        return None
    buttons = buttons[:30]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    return {"inline_keyboard": rows}


def format_detail_message(summary: dict, meeting_date: str) -> str:
    try:
        date_label = dt.datetime.strptime(meeting_date, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        date_label = meeting_date
    lines = [f"📋 {summary.get('title', 'Встреча')} — {date_label}", ""]
    lines.extend(summary.get("detailed_lines") or [])
    return "\n".join(lines)


# ── State ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("meetings"), dict):
                return data
        except Exception as exc:
            log(f"state: corrupted, starting fresh: {exc}")
    return {"version": 1, "meetings": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


def acquire_lock() -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
        except FileNotFoundError:
            return acquire_lock()
        if age > LOCK_STALE_SECONDS:
            log(f"lock: stale ({int(age)}s), taking over")
            LOCK_FILE.unlink(missing_ok=True)
            return acquire_lock()
        return False


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


# ── Основной проход ───────────────────────────────────────────────────────

def eligible_notes(only_file: Optional[Path] = None) -> list[tuple[Path, str, str, dt.date]]:
    """[(path, content, key, date)] для krisp-заметок в окне давности."""
    result = []
    cutoff = dt.date.today() - dt.timedelta(days=MAX_AGE_DAYS)
    paths = [only_file] if only_file else sorted(TRANSCRIPTS_DIR.glob("*.md"))
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        key = note_key(content)
        if not key:
            continue
        date = note_date(content, path)
        if date is None:
            continue
        if only_file is None and date < cutoff:
            continue
        result.append((path, content, key, date))
    return result


def process_meeting(
    entry: dict,
    path: Path,
    content: str,
    date: dt.date,
    token: str,
    chat_id: str,
    dry_run: bool,
) -> None:
    title = note_title(content, path)
    entry["note_path"] = str(path)
    entry["title"] = title
    entry["date"] = date.isoformat()

    if entry.get("status") in {"sent", "failed"}:
        return

    if not is_ready(content):
        age_hours = (dt.datetime.now() - dt.datetime.combine(date, dt.time(12))).total_seconds() / 3600
        if TRANSCRIPT_MISSING_PLACEHOLDER in content and age_hours > ABANDON_AFTER_HOURS:
            if entry.get("status") != "abandoned":
                entry["status"] = "abandoned"
                log(f"abandoned (no transcript >24h): {path.name}")
                if not dry_run:
                    try:
                        send_message(
                            token, chat_id,
                            f"❌ «{title}» ({date.isoformat()}): Krisp так и не выгрузил транскрипт за 24 ч. "
                            f"Открой Krisp и вставь транскрипт в заметку вручную — тогда пришлю саммари.",
                        )
                    except Exception as exc:
                        log(f"abandon alert send failed: {path.name}: {exc}")
        else:
            entry["status"] = "pending"
            now = dt.datetime.now()
            since_raw = entry.get("pending_since")
            if since_raw is None:
                entry["pending_since"] = now.strftime("%Y-%m-%d %H:%M:%S")
            elif not entry.get("pending_warned"):
                try:
                    since = dt.datetime.strptime(since_raw, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    since = now
                    entry["pending_since"] = now.strftime("%Y-%m-%d %H:%M:%S")
                if (now - since).total_seconds() / 3600 > WARN_PENDING_AFTER_HOURS and not dry_run:
                    entry["pending_warned"] = True
                    try:
                        send_message(
                            token, chat_id,
                            f"⏳ «{title}» ({date.isoformat()}): жду транскрипт из облака Krisp дольше обычного "
                            f"(>{WARN_PENDING_AFTER_HOURS} ч). Продолжаю проверять; если за 24 ч не появится — пришлю alert.",
                        )
                    except Exception as exc:
                        entry["pending_warned"] = False
                        log(f"pending warn send failed: {path.name}: {exc}")
        return

    # транскрипт готов
    summary = entry.get("summary")
    if not summary:
        log(f"summarizing: {path.name}")
        summary = summarize_llm(content, title, date.isoformat())
        if not summary:
            summary = summarize_fallback(content, title)
            if summary:
                log(f"fallback extract used: {path.name}")
        if not summary:
            entry["attempts"] = entry.get("attempts", 0) + 1
            if entry["attempts"] >= MAX_LLM_ATTEMPT_RUNS:
                entry["status"] = "failed"
                log(f"failed after {entry['attempts']} runs: {path.name}")
            else:
                entry["status"] = "ready"
            return
        entry["summary"] = summary
        entry["status"] = "summarized"

    # todos (кэшируем items, чтобы ретрай TG не дублировал задачи)
    if "todo_items" not in entry:
        if "todo_ids" in entry:
            # in-flight запись старого формата (до owner-фильтрации): ids без
            # структуры — считаем задачами Антона, как вело себя старое поведение
            entry["todo_items"] = [
                {"id": tid, "what": "", "who": "", "owner": "me"} for tid in entry["todo_ids"]
            ]
        else:
            entry["todo_items"] = append_todos(summary, str(path), entry["date"], dry_run=dry_run)
            entry["todo_ids"] = [i["id"] for i in entry["todo_items"]]

    shareable = format_shareable_message(summary, entry["date"])
    private = f"Встреча: {title}\n\n" + format_private_message(entry["todo_items"], summary.get("engine", "llm"))

    if dry_run:
        print("--- message 1 (shareable) ---")
        print(shareable)
        print("--- message 2 (private) ---")
        print(private)
        print("--- detail (/meeting) ---")
        print(format_detail_message(summary, entry["date"]))
        return

    try:
        send_message(token, chat_id, shareable)
        send_message(token, chat_id, private, reply_markup=todo_keyboard(entry["todo_items"]))
    except Exception as exc:
        log(f"telegram send failed (will retry): {path.name}: {exc}")
        return
    entry["status"] = "sent"
    entry["sent_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log(f"sent: {path.name} ({len(entry['todo_ids'])} todos)")


def run_detail(key: str) -> int:
    """Печатает развёрнутое саммари встречи (для /meeting в боте); генерирует и кэширует при отсутствии."""
    state = load_state()
    entry = state["meetings"].get(key)
    if not entry:
        print(f"Встреча с ключом {key} не найдена в state.")
        return 1
    summary = entry.get("summary") or {}
    if summary.get("detailed_lines"):
        print(format_detail_message(summary, entry.get("date", "")))
        return 0
    note_path = Path(entry.get("note_path", ""))
    if not note_path.exists():
        print(f"Транскрипт не найден: {note_path}")
        return 1
    content = note_path.read_text(encoding="utf-8")
    title = entry.get("title") or note_title(content, note_path)
    log(f"detail: generating for {note_path.name}")
    fresh = summarize_llm(content, title, entry.get("date", ""))
    if not fresh or not fresh.get("detailed_lines"):
        print("Не удалось сгенерировать детальное саммари (ошибка LLM). Лог: ~/Library/Logs/meeting-notes.log")
        return 1
    if summary:
        summary["detailed_lines"] = fresh["detailed_lines"]
    else:
        summary = fresh
    entry["summary"] = summary
    save_state(state)
    print(format_detail_message(summary, entry.get("date", "")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Meeting Notes Assistant watcher")
    parser.add_argument("--dry-run", action="store_true", help="печать сообщений без отправки и записи")
    parser.add_argument("--bootstrap", action="store_true", help="пометить текущие встречи sent без отправки")
    parser.add_argument("--file", type=str, help="обработать только этот транскрипт (без фильтра давности)")
    parser.add_argument("--force", type=str, help="сбросить state для ключа и обработать заново")
    parser.add_argument("--detail", type=str, help="напечатать детальное саммари встречи по ключу (генерирует при отсутствии)")
    args = parser.parse_args()

    # --detail вызывается ботом синхронно: ждём lock до 60 сек вместо мгновенного выхода
    lock_deadline = time.time() + (60 if args.detail else 0)
    while not acquire_lock():
        if time.time() >= lock_deadline:
            log("lock held by another run, exiting")
            return 0 if not args.detail else 1
        time.sleep(2)

    if args.detail:
        try:
            return run_detail(args.detail)
        finally:
            release_lock()

    try:
        state = load_state()
        meetings = state["meetings"]

        if args.force and args.force in meetings:
            del meetings[args.force]
            log(f"force: reset state for {args.force}")

        only_file = Path(args.file).expanduser() if args.file else None
        notes = eligible_notes(only_file)

        if args.bootstrap:
            for path, content, key, date in notes:
                entry = meetings.setdefault(key, {})
                if entry.get("status") != "sent":
                    entry.update({
                        "status": "sent",
                        "note_path": str(path),
                        "title": note_title(content, path),
                        "date": date.isoformat(),
                        "sent_at": "bootstrap",
                    })
                    log(f"bootstrap: marked sent: {path.name}")
            save_state(state)
            return 0

        token = chat_id = ""
        if not args.dry_run:
            env = load_env_file(ENV_FILE)
            token = env.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = env.get("TELEGRAM_CHAT_ID", "")
            if not token or not chat_id:
                log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing in env file")
                return 1

        # защита: два файла с одним krisp/voice id — признак сбоя матчинга
        # (реальный транскрипт мог попасть в чужую заметку и остаться необработанным)
        paths_by_key: dict[str, list[Path]] = {}
        for path, _content, key, _date in notes:
            paths_by_key.setdefault(key, []).append(path)
        for key, dup_paths in paths_by_key.items():
            if len(dup_paths) < 2:
                continue
            entry = meetings.setdefault(key, {})
            if entry.get("dup_warned"):
                continue
            entry["dup_warned"] = True
            names = ", ".join(p.name for p in dup_paths)
            log(f"WARNING: duplicate key {key} in files: {names}")
            if not args.dry_run:
                try:
                    send_message(
                        token, chat_id,
                        f"⚠️ Два транскрипта с одним Krisp ID ({key[:13]}…): {names}. "
                        f"Возможен сбой матчинга krisp-логгера — саммари одной из встреч могло не прийти. Проверь файлы.",
                    )
                except Exception as exc:
                    entry["dup_warned"] = False
                    log(f"dup warn send failed: {exc}")

        for path, content, key, date in notes:
            if args.dry_run and only_file is not None:
                entry = {}  # свежая обработка без учёта state
            else:
                entry = meetings.setdefault(key, {})
                if args.dry_run and entry.get("status") in {"sent", "failed", "abandoned"}:
                    continue
            try:
                process_meeting(entry, path, content, date, token, chat_id, args.dry_run)
            except Exception as exc:
                log(f"error processing {path.name}: {exc}")

        if not args.dry_run:
            save_state(state)
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
