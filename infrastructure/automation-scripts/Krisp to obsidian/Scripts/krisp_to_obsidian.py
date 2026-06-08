#!/usr/bin/env python3
"""
Krisp → Obsidian
Читает callinfo.json из Krisp, создаёт заметки в Obsidian vault,
затем подтягивает транскрипт из Krisp MCP (ждёт обработки до 10 минут).
Формат имени: {meeting} название – YYYY-MM-DD.md
"""

import base64
import json
import os
import re
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Настройки ────────────────────────────────────────────────────────────────

KRISP_CALLINFO    = Path.home() / "Library/Application Support/Krisp/callinfo.json"
VAULT_MEETINGS    = Path("/Users/anton/AI AGENT FOLDER/Second Brain/transcripts")
TOKEN_FILE        = Path.home() / ".claude/krisp_token.json"

OAUTH_CLIENT_ID     = os.environ.get("KRISP_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.environ.get("KRISP_CLIENT_SECRET", "")
TOKEN_ENDPOINT      = "https://api.krisp.ai/platform/v1/oauth2/token"
MCP_URL             = "https://mcp.krisp.ai/mcp"

# Звонки с такими именами пропускать (автогенерированные Krisp без реального названия)
SKIP_NAME_PATTERNS = [
    r"^\d{1,2}:\d{2} [AP]M - .+ meeting ",
]

# Сколько раз пытаться получить транскрипт из MCP (раз в 60 сек)
MCP_RETRY_COUNT    = 10
MCP_RETRY_INTERVAL = 60  # секунд
HTTP_TIMEOUT       = 30  # секунд
STALE_PENDING_RETRY_DAYS = 3
CALLINFO_LOOKBACK_DAYS = 7

TRANSCRIPT_LOADING_PLACEHOLDER = "_Транскрипт загружается из Krisp..._"
TRANSCRIPT_MISSING_PLACEHOLDER = "_Транскрипт не получен. Откройте Krisp и скопируйте вручную._"

NOTE_FILENAME_RE = re.compile(r"^\{meeting\} (.+) – \d{4}-\d{2}-\d{2}$")
NOTE_BOLD_KRISP_ID_RE = re.compile(r"^\*\*Krisp ID:\*\* `([^`]+)`", re.MULTILINE)
NOTE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ── SSL контекст ──────────────────────────────────────────────────────────────

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations("/etc/ssl/cert.pem")
    return ctx


# ── OAuth токен ───────────────────────────────────────────────────────────────

def load_token() -> dict:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_token(token: dict):
    TOKEN_FILE.write_text(json.dumps(token, indent=2), encoding="utf-8")


def refresh_access_token(token: dict) -> dict:
    """Обновляет access_token через refresh_token."""
    if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
        raise RuntimeError("Set KRISP_CLIENT_ID and KRISP_CLIENT_SECRET before refreshing Krisp tokens.")
    data = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id":     OAUTH_CLIENT_ID,
    }).encode()
    credentials = base64.b64encode(f"{OAUTH_CLIENT_ID}:{OAUTH_CLIENT_SECRET}".encode()).decode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=HTTP_TIMEOUT) as resp:
        new_token = json.loads(resp.read())
    if "refresh_token" not in new_token and token.get("refresh_token"):
        new_token["refresh_token"] = token["refresh_token"]
    if "refresh_token_expires_at" not in new_token and token.get("refresh_token_expires_at"):
        new_token["refresh_token_expires_at"] = token["refresh_token_expires_at"]
    save_token(new_token)
    print("  [MCP] токен обновлён")
    return new_token


def get_access_token() -> Optional[str]:
    token = load_token()
    if not token:
        print("  [MCP] токен не найден — запустите krisp_oauth.py")
        return None
    # Проверяем срок действия с запасом 5 минут
    expires_at = token.get("expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if (exp - datetime.now(tz=timezone.utc)).total_seconds() < 300:
                token = refresh_access_token(token)
        except Exception:
            pass
    return token.get("access_token")


# ── MCP запросы ───────────────────────────────────────────────────────────────

def mcp_call(method: str, arguments: dict) -> Optional[dict]:
    access_token = get_access_token()
    if not access_token:
        return None

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "tools/call",
        "params":  {"name": method, "arguments": arguments},
    }).encode()

    req = urllib.request.Request(MCP_URL, data=payload, method="POST")
    req.add_header("Authorization",  f"Bearer {access_token}")
    req.add_header("Content-Type",   "application/json")
    req.add_header("Accept",         "application/json, text/event-stream")
    req.add_header("User-Agent",     "curl/8.7.1")

    try:
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        print(f"  [MCP] HTTP ошибка {e.code}")
        return None
    except Exception as e:
        print(f"  [MCP] ошибка соединения: {e}")
        return None

    # SSE формат: ищем строку "data: {...}"
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return None


def generate_meeting_title(mcp_name: str, key_points: list[str]) -> Optional[str]:
    """Возвращает локально очищенное MCP-название без внешних LLM/API."""
    if not mcp_name:
        return None
    title = re.sub(r'[<>:"/\\|?*\n\r\t]', " ", mcp_name)
    title = re.sub(r"\s+", " ", title).strip()
    return title if title else None


def note_is_pending(content: str) -> bool:
    return (
        TRANSCRIPT_LOADING_PLACEHOLDER in content
        or TRANSCRIPT_MISSING_PLACEHOLDER in content
    )


def extract_frontmatter_value(content: str, field: str) -> Optional[str]:
    match = re.search(rf"(?m)^{re.escape(field)}:\s*(.+)$", content)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value or None


def extract_note_ids(content: str) -> tuple[Optional[str], Optional[str]]:
    call_id = extract_frontmatter_value(content, "krisp_call_id")
    mcp_id = (
        extract_frontmatter_value(content, "krisp_mcp_id")
        or extract_frontmatter_value(content, "krisp_id")
    )

    if not call_id:
        match = NOTE_BOLD_KRISP_ID_RE.search(content)
        if match:
            call_id = match.group(1).strip()

    return call_id, mcp_id


def extract_note_title_from_path(filepath: Path) -> str:
    match = NOTE_FILENAME_RE.match(filepath.stem)
    return match.group(1) if match else filepath.stem


def note_score(filepath: Path, content: str) -> tuple[int, int, str]:
    score = 0
    if "## Ключевые моменты" in content:
        score += 60
    if "## Action Items" in content:
        score += 20
    if "## Транскрипт" in content and not note_is_pending(content):
        score += 40
    elif TRANSCRIPT_MISSING_PLACEHOLDER in content:
        score += 10
    elif TRANSCRIPT_LOADING_PLACEHOLDER in content:
        score += 5

    if not should_skip(extract_note_title_from_path(filepath)):
        score += 15

    return score, -len(filepath.name), filepath.name


def choose_best_note_path(paths: list[Path]) -> Optional[Path]:
    unique_paths = sorted({path for path in paths}, key=lambda p: p.name)
    if not unique_paths:
        return None

    best_path = unique_paths[0]
    best_key = (-1, 0, "")
    for path in unique_paths:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = ""
        key = note_score(path, content)
        if key > best_key:
            best_path = path
            best_key = key
    return best_path


def build_note_index(notes_dir: Path) -> dict[str, dict[str, list[Path]]]:
    index = {"call_ids": {}, "mcp_ids": {}}
    for filepath in notes_dir.glob("*.md"):
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            continue

        call_id, mcp_id = extract_note_ids(content)
        if call_id:
            index["call_ids"].setdefault(call_id, []).append(filepath)
        if mcp_id:
            index["mcp_ids"].setdefault(mcp_id, []).append(filepath)
    return index


def find_existing_note_path(
    call: dict,
    filepath: Path,
    note_index: dict[str, dict[str, list[Path]]],
) -> Path:
    candidates = []

    call_id = call.get("call_id")
    if call_id:
        candidates.extend(note_index["call_ids"].get(call_id, []))

    mcp_id = call.get("_mcp_id")
    if mcp_id:
        candidates.extend(note_index["mcp_ids"].get(mcp_id, []))

    if filepath.exists():
        candidates.append(filepath)

    return choose_best_note_path(candidates) or filepath


def extract_key_points(doc_text: str) -> list[str]:
    """Извлекает пункты из секции Key Points / Ключевые моменты."""
    points = []
    in_section = False
    for line in doc_text.split("\n"):
        if re.search(r"##\s*(Key Points|Ключевые моменты)", line):
            in_section = True
            continue
        if in_section:
            if line.startswith("#"):
                break
            stripped = line.strip().lstrip("- ").strip("*").strip()
            if stripped:
                points.append(stripped)
    return points


def mcp_search_meetings(date_str: str) -> list[dict]:
    """Возвращает список встреч за указанную дату с временем начала."""
    result = mcp_call("search_meetings", {
        "query": date_str,
        "limit": 50,
    })
    if not result:
        return []

    meetings = []
    for item in result.get("result", {}).get("content", []):
        text = item.get("text", "")
        if "meeting_id:" not in text:
            continue
        lines = text.split("\n")
        # Имя и время из строки "## Name (2026-03-27T12:06:05+03:00)"
        header = lines[0].lstrip("# ")
        name = header.split(" (")[0].strip()
        start_time = None
        m = re.search(r"\((\d{4}-\d{2}-\d{2}T[\d:+\-.]+)\)", header)
        if m:
            try:
                start_time = datetime.fromisoformat(m.group(1))
            except ValueError:
                pass
        mid   = next((l.split(": ")[1].strip() for l in lines if "meeting_id:" in l), None)
        ready = "uploaded" in text
        if mid:
            if start_time is not None and start_time.date().isoformat() != date_str:
                continue
            meetings.append({"name": name, "id": mid, "transcript_ready": ready, "start_time": start_time})
    return meetings


def mcp_get_document(meeting_id: str) -> Optional[str]:
    """Возвращает полный текст документа встречи."""
    result = mcp_call("get_multiple_documents", {"ids": [meeting_id]})
    if not result:
        return None
    for item in result.get("result", {}).get("content", []):
        text = item.get("text", "")
        if text.strip():
            return text
    return None


# ── Парсинг документа MCP ─────────────────────────────────────────────────────

def extract_section(text: str, header: str) -> str:
    """Извлекает содержимое секции (H1 или H2)."""
    for prefix in ["# ", "## "]:
        pattern = rf"{re.escape(prefix)}{re.escape(header)}\n(.*?)(?=\n#{1,2} |\Z)"
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


def build_mcp_transcript_section(doc_text: str) -> str:
    transcript = extract_section(doc_text, "Transcript")
    key_points = extract_section(doc_text, "Key Points")
    action_items = extract_section(doc_text, "Action Items")

    parts = []
    if key_points:
        parts.append(f"## Ключевые моменты\n\n{key_points}")
    if action_items and "No Action Item" not in action_items:
        parts.append(f"## Action Items\n\n{action_items}")
    if transcript:
        parts.append(f"## Транскрипт\n\n{transcript}")
    return "\n\n".join(parts)


def upsert_frontmatter_field(content: str, field: str, value: str) -> str:
    if not value:
        return content

    trailing_newline = content.endswith("\n")
    lines = content.splitlines()
    if lines and lines[0] == "---":
        try:
            end_idx = lines.index("---", 1)
        except ValueError:
            end_idx = -1

        if end_idx != -1:
            for idx in range(1, end_idx):
                if lines[idx].startswith(f"{field}:"):
                    lines[idx] = f"{field}: {value}"
                    break
            else:
                lines.insert(end_idx, f"{field}: {value}")

            updated = "\n".join(lines)
            return updated + ("\n" if trailing_newline else "")

    base = content.lstrip("\n")
    updated = f"---\n{field}: {value}\n---\n\n{base}"
    if trailing_newline and not updated.endswith("\n"):
        updated += "\n"
    return updated


def ensure_note_metadata(filepath: Path, call_id: str = "", mcp_id: str = ""):
    if not filepath.exists():
        return

    content = filepath.read_text(encoding="utf-8")
    updated = content
    if call_id:
        updated = upsert_frontmatter_field(updated, "krisp_call_id", call_id)
    if mcp_id:
        updated = upsert_frontmatter_field(updated, "krisp_mcp_id", mcp_id)

    if updated != content:
        filepath.write_text(updated, encoding="utf-8")
        sync_note_timestamp(filepath)


def pick_ai_title_filepath(filepath: Path, ai_title: str, date_str: str) -> Path:
    filename = f"{{meeting}} {clean_name_for_filename(ai_title)} – {date_str}.md"
    return filepath.parent / filename


def move_note_if_needed(filepath: Path, target_filepath: Path) -> Path:
    if target_filepath == filepath:
        return filepath
    if target_filepath.exists():
        return target_filepath
    if filepath.exists():
        filepath.rename(target_filepath)
    return target_filepath


def extract_note_date(path: Path) -> Optional[str]:
    match = NOTE_DATE_RE.search(path.name)
    return match.group(1) if match else None


def should_retry_pending_note(filepath: Path, content: str) -> bool:
    if not note_is_pending(content):
        return False

    date_str = (
        extract_frontmatter_value(content, "date")
        or extract_note_date(filepath)
    )
    if not date_str:
        return True

    try:
        note_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return True

    age_days = (datetime.now().date() - note_date).days
    return age_days <= STALE_PENDING_RETRY_DAYS


def sync_note_timestamp(filepath: Path, explicit_date: Optional[str] = None):
    if filepath.parent.name == "transcripts":
        now = datetime.now()
        os.utime(filepath, (now.timestamp(), now.timestamp()))
        timestamp = now.strftime("%m/%d/%Y %H:%M:%S")
        subprocess.run(
            ["/usr/bin/SetFile", "-d", timestamp, str(filepath)],
            check=False,
            capture_output=True,
        )
        return
    date_str = explicit_date or extract_note_date(filepath)
    if not date_str or not filepath.exists():
        return
    timestamp = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=12, minute=0, second=0, microsecond=0
    ).timestamp()
    os.utime(filepath, (timestamp, timestamp))


# ── Сопоставление встречи ─────────────────────────────────────────────────────

def best_match(call_time: Optional[datetime], call_name: str, meetings: list[dict]) -> Optional[dict]:
    """
    Сопоставляет встречу по времени начала (приоритет) или по имени.
    Допуск по времени: ±30 минут.
    """
    if not meetings:
        return None

    # 1. Сопоставление по времени — самый надёжный способ
    if call_time is not None:
        timed = [(m, abs((m["start_time"] - call_time).total_seconds()))
                 for m in meetings if m["start_time"] is not None]
        if timed:
            timed.sort(key=lambda x: x[1])
            closest, delta = timed[0]
            if delta <= 1800:  # в пределах 30 минут
                return closest

    # 2. Fallback: одна встреча за день
    if len(meetings) == 1:
        return meetings[0]

    # 3. Fallback: совпадение по словам (≥20%)
    call_words = set(re.sub(r"[^\w\s]", "", call_name.lower()).split())
    best, best_score = None, 0
    for m in meetings:
        mcp_words = set(re.sub(r"[^\w\s]", "", m["name"].lower()).split())
        overlap = len(call_words & mcp_words) / max(len(call_words | mcp_words), 1)
        if overlap > best_score:
            best, best_score = m, overlap
    if best and best_score >= 0.2:
        return best

    return None


# ── Обновление заметки транскриптом из MCP ────────────────────────────────────

def inject_mcp_content(filepath: Path, mcp_section: str):
    """Заменяет блок от ## Ключевые моменты / ## Транскрипт до конца файла."""
    if not filepath.exists():
        filepath.write_text(mcp_section + "\n", encoding="utf-8")
        return
    content = filepath.read_text(encoding="utf-8")

    # Ищем где начинается старый транскрипт/summary блок
    cut_markers = ["## Ключевые моменты", "## Action Items", "## Транскрипт", "## Summary"]
    cut_pos = len(content)
    for marker in cut_markers:
        idx = content.find(marker)
        if idx != -1 and idx < cut_pos:
            cut_pos = idx

    header = content[:cut_pos].rstrip()
    new_content = header + "\n\n" + mcp_section + "\n"
    filepath.write_text(new_content, encoding="utf-8")
    sync_note_timestamp(filepath)


# ── Основная логика ───────────────────────────────────────────────────────────

def parse_krisp_date(date_obj) -> Optional[datetime]:
    if isinstance(date_obj, dict) and "$$date" in date_obj:
        return datetime.fromtimestamp(date_obj["$$date"] / 1000, tz=timezone.utc).astimezone()
    return None


def clean_name_for_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("{", "").replace("}", "")
    if len(name) > 60:
        name = name[:60].rstrip()
    return name


def should_skip(call_name: str) -> bool:
    return any(re.search(p, call_name) for p in SKIP_NAME_PATTERNS)


def format_duration(seconds: int) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}ч {m}мин" if h else f"{m}мин"


def read_callinfo() -> list[dict]:
    if not KRISP_CALLINFO.exists():
        return []

    calls_by_key = {}
    with open(KRISP_CALLINFO, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            data      = record.get("data") or {}
            call_name = data.get("callName")
            if not call_name:
                continue
            date = parse_krisp_date(record.get("date"))
            if date is not None:
                age_days = (datetime.now().date() - date.date()).days
                if age_days > CALLINFO_LOOKBACK_DAYS:
                    continue
            date_str = date.strftime("%Y-%m-%d") if date else "unknown"
            call_id = data.get("callId", "")
            key = call_id or f"{call_name}|{date.isoformat() if date else date_str}"
            transcript = data.get("transcript") or {}
            calls_by_key[key] = {
                "call_name":         call_name,
                "date":              date,
                "date_str":          date_str,
                "call_id":           call_id,
                "app_name":          data.get("appName", ""),
                "duration_sec":      data.get("meetingDuration", 0),
                "transcript_status": transcript.get("status", ""),
                "transcript_lines":  transcript.get("transcriptContent") or [],
                "meeting_notes":     (transcript.get("meetingNotes") or {}).get("notes"),
            }
    return list(calls_by_key.values())


def build_note_text(call: dict) -> str:
    date_str  = call["date_str"]
    time_str  = call["date"].strftime("%H:%M") if call["date"] else ""
    duration  = format_duration(call["duration_sec"])
    app       = call["app_name"].replace(".us", "").replace(".app", "").capitalize()

    meta = [f"**Дата:** {date_str} {time_str}".strip()]
    if duration:
        meta.append(f"**Длительность:** {duration}")
    if app:
        meta.append(f"**Приложение:** {app}")
    if call["call_id"]:
        meta.append(f"**Krisp ID:** `{call['call_id']}`")

    frontmatter = [
        "---",
        "tags:",
        "  - type/meeting",
        f"date: {date_str}",
        "status: active",
        "source: krisp",
    ]
    if call["call_id"]:
        frontmatter.append(f"krisp_call_id: {call['call_id']}")
    if call.get("_mcp_id"):
        frontmatter.append(f"krisp_mcp_id: {call['_mcp_id']}")
    frontmatter.append("---")

    return (
        "\n".join(frontmatter)
        + "\n\n"
        f"# {call['call_name']}\n\n"
        + "\n".join(meta)
        + f"\n\n## Транскрипт\n\n{TRANSCRIPT_LOADING_PLACEHOLDER}\n"
    )


def fetch_and_inject_transcript(call: dict, filepath: Path) -> tuple[bool, Path]:
    """
    Ищет встречу в MCP и вставляет транскрипт в заметку.
    Повторяет попытки MCP_RETRY_COUNT раз с интервалом MCP_RETRY_INTERVAL сек.
    Возвращает True если транскрипт успешно получен.
    """
    date_str = call["date_str"]

    for attempt in range(1, MCP_RETRY_COUNT + 1):
        print(f"  [MCP] попытка {attempt}/{MCP_RETRY_COUNT} — ищу «{call['call_name']}»...")

        meetings = mcp_search_meetings(date_str)
        match    = best_match(call["date"], call["call_name"], meetings)

        if not match:
            print(f"  [MCP] встреча не найдена в облаке Krisp, жду {MCP_RETRY_INTERVAL}с...")
            time.sleep(MCP_RETRY_INTERVAL)
            continue

        if not match["transcript_ready"]:
            print(f"  [MCP] найдена «{match['name']}», транскрипт ещё обрабатывается...")
            time.sleep(MCP_RETRY_INTERVAL)
            continue

        doc_text = mcp_get_document(match["id"])
        if not doc_text:
            print("  [MCP] не удалось получить документ")
            time.sleep(MCP_RETRY_INTERVAL)
            continue

        mcp_section = build_mcp_transcript_section(doc_text)
        if not mcp_section:
            print("  [MCP] документ пустой")
            time.sleep(MCP_RETRY_INTERVAL)
            continue

        # Используем локально очищенное MCP-название без внешних LLM/API.
        key_points = extract_key_points(doc_text)
        ai_title = generate_meeting_title(match["name"], key_points)
        if ai_title:
            new_filepath = pick_ai_title_filepath(filepath, ai_title, call["date_str"])
            old_filepath = filepath
            filepath = move_note_if_needed(filepath, new_filepath)
            if filepath != old_filepath:
                if filepath.exists() and filepath == new_filepath and old_filepath.exists():
                    print(f"  [MCP] использую существующую заметку → «{ai_title}»")
                else:
                    print(f"  [MCP] переименовано → «{ai_title}»")

        ensure_note_metadata(filepath, call.get("call_id", ""), match["id"])
        inject_mcp_content(filepath, mcp_section)
        print(f"  [MCP] транскрипт вставлен из «{match['name']}»")
        return True, filepath

    # Все попытки исчерпаны
    ensure_note_metadata(filepath, call.get("call_id", ""), call.get("_mcp_id", ""))
    inject_mcp_content(
        filepath,
        f"## Транскрипт\n\n{TRANSCRIPT_MISSING_PLACEHOLDER}"
    )
    return False, filepath


def main():
    VAULT_MEETINGS.mkdir(parents=True, exist_ok=True)

    calls = read_callinfo()
    if not calls:
        print("Звонков в Krisp не найдено.")
        return

    created = skipped = skipped_auto = 0
    # Кэш MCP-встреч по дате и уже обработанные MCP ID (против дублей)
    mcp_cache: dict = {}
    processed_mcp_ids: set = set()
    note_index = build_note_index(VAULT_MEETINGS)

    for call in calls:
        if should_skip(call["call_name"]):
            # Generic-имя: пробуем найти встречу в MCP по времени
            if call["date"] is not None:
                if call["date_str"] not in mcp_cache:
                    mcp_cache[call["date_str"]] = mcp_search_meetings(call["date_str"])
                mcp_match = best_match(call["date"], call["call_name"], mcp_cache[call["date_str"]])
                if mcp_match:
                    if mcp_match["id"] in processed_mcp_ids:
                        skipped += 1
                        continue
                    call = dict(call, call_name=mcp_match["name"], _mcp_id=mcp_match["id"])
                else:
                    skipped_auto += 1
                    continue
            else:
                skipped_auto += 1
                continue

        # Для не-generic имён тоже ищем MCP — берём MCP-имя и ID для дедупликации
        if not call.get("_mcp_id") and call["date"] is not None:
            if call["date_str"] not in mcp_cache:
                mcp_cache[call["date_str"]] = mcp_search_meetings(call["date_str"])
            mcp_match = best_match(call["date"], call["call_name"], mcp_cache[call["date_str"]])
            if mcp_match:
                if mcp_match["id"] in processed_mcp_ids:
                    skipped += 1
                    continue
                call = dict(call, call_name=mcp_match["name"], _mcp_id=mcp_match["id"])

        clean    = clean_name_for_filename(call["call_name"])
        filename = f"{{meeting}} {clean} – {call['date_str']}.md"
        filepath = find_existing_note_path(call, VAULT_MEETINGS / filename, note_index)

        # Если файл существует и транскрипт уже получен — пропускаем
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            if not note_is_pending(content):
                skipped += 1
                continue
            if not should_retry_pending_note(filepath, content):
                skipped += 1
                continue
            # Транскрипт ещё не получен — пробуем снова
            print(f"↻ повтор MCP для: {filepath.name}")
        else:
            # 1. Создаём заметку сразу (с плейсхолдером транскрипта)
            filepath.write_text(build_note_text(call), encoding="utf-8")
            sync_note_timestamp(filepath, call["date_str"])
            print(f"✓ создано: {filepath.name}")
            created += 1
            note_index = build_note_index(VAULT_MEETINGS)

        # Отмечаем MCP ID как обработанный (защита от дублей)
        if call.get("_mcp_id"):
            processed_mcp_ids.add(call["_mcp_id"])

        # 2. Тянем транскрипт из MCP
        success, filepath = fetch_and_inject_transcript(call, filepath)
        note_index = build_note_index(VAULT_MEETINGS)
        if not success:
            print(f"  ⚠ транскрипт не получен за отведённое время")

    print(
        f"\nГотово: создано {created}, уже существует {skipped}"
        + (f", пропущено авто-имён {skipped_auto}" if skipped_auto else "")
    )


if __name__ == "__main__":
    main()
