#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from task_owner import classify_owner

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
DAILY_DIR = VAULT / "infrastructure" / "daily focus"
ENV_FILE = Path("/Users/anton/.config/second-brain/daily-focus.env")
STATE_DIR = Path("/Users/anton/.config/second-brain")
STATE_FILE = STATE_DIR / "telegram-codex-bot.offset"
PLAN_PENDING_FILE = STATE_DIR / "telegram-codex-bot.plan-pending"
CALENDAR_PENDING_FILE = STATE_DIR / "telegram-codex-bot.calendar-pending.json"
CALENDAR_LEDGER_FILE = STATE_DIR / "telegram-codex-bot.calendar-ledger.json"
STRATEGY_REVIEW_LOCK_FILE = STATE_DIR / "telegram-codex-bot.strategy-review.lock"
COMPLETED_TASKS_FILE = STATE_DIR / "telegram-codex-bot.completed-tasks.json"
GOOGLE_CALENDAR_CREDENTIALS_FILE = STATE_DIR / "google-calendar-credentials.json"
GOOGLE_CALENDAR_TOKEN_FILE = STATE_DIR / "google-calendar-token.json"
LOG_FILE = Path("/Users/anton/Library/Logs/telegram-codex-bot.log")
CODEX_BIN = Path(os.environ.get("CODEX_BIN", "/Applications/Codex.app/Contents/Resources/codex"))
RUN_DAILY_FOCUS = DAILY_DIR / "Scripts" / "run_daily_focus.sh"
DAILY_MESSAGES_DIR = DAILY_DIR / "daily focus messages"
PLANNING_INBOX_DIR = DAILY_DIR / "planning inbox"
PLANNING_MEMORY_DIR = DAILY_DIR / "planning memory"
MEETING_ACTION_CANDIDATES_DIR = DAILY_DIR / "meeting action candidates"
MEETING_TODO_DIR = DAILY_DIR / "meeting todo"
MEETING_NOTES_STATE_FILE = STATE_DIR / "meeting-notes-state.json"
MEETING_NOTES_SCRIPT = DAILY_DIR / "Scripts" / "meeting_notes.py"
STRATEGIC_REVIEW_DIR = DAILY_DIR / "strategic planning reviews"
STRATEGIC_BOARD_SKILL = Path("/Users/anton/.codex/skills/strategic-board/SKILL.md")
DEFAULT_CALENDAR_NAME = "a.rznblm@gmail.com"
GOOGLE_WRITE_CALENDAR_ID = os.environ.get("GOOGLE_WRITE_CALENDAR_ID", "primary")
BUSY_CALENDAR_NAMES = ["primary", "tony@portalcg.us"]
CALENDAR_READ_TIMEOUT_SECONDS = 20
CALENDAR_PLANNING_DAYS = 7
FOCUS_EVENT_BUFFER_MINUTES = 30
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]
ACTIVITY_COLOR_IDS = {
    "Deep Work": "3",        # purple
    "Health": "10",          # green
    "Education": "1",        # blue
    "Admin": "8",            # gray
    "Outreach": "6",         # orange
    "Infrastructure": "7",   # teal
}
DEFAULT_FOCUS_EVENT_COLOR_ID = ACTIVITY_COLOR_IDS["Deep Work"]
TODO_ONLY_MODE = os.environ.get("TELEGRAM_CODEX_TODO_ONLY", "1") != "0"
AUTO_CODEX_FOR_QUESTIONS = os.environ.get("TELEGRAM_CODEX_ASK_AUTO", "0") == "1"
TODO_ONLY_DISABLED_COMMANDS = {
    "/focus",
    "/actions",
    "/actions_confirm",
    "/actions_reject",
    "/actions_clear",
    "/prioritize",
    "/calendar",
    "/calendar_new",
    "/calendar_status",
    "/calendar_review",
    "/calendar_clear_done",
    "/calendar_confirm",
    "/calendar_clear",
    "/clear_calendar",
    "/daily",
    "/daily_focus",
    "/daily_planning",
    "/360brief",
    "/fullfocus",
    "/daily_rerun",
    "/ask",
    "/done",
}


def redact_secrets(message: str) -> str:
    message = re.sub(r"https://api\.telegram\.org/bot[^/'\"\s]+", "https://api.telegram.org/bot<redacted>", message)
    message = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<redacted>", message)
    return message


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {redact_secrets(message)}\n")


def log_route(route: str, codex: bool, detail: str = "") -> None:
    suffix = f"; {detail}" if detail else ""
    log(f"route={route}; codex={'yes' if codex else 'no'}{suffix}")


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


def api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def telegram_request(token: str, method: str, data: dict[str, str] | None = None, timeout: int = 90) -> dict:
    command = ["curl", "-fsS", "--max-time", str(timeout)]
    for key, value in (data or {}).items():
        command.extend(["--data-urlencode", f"{key}={value}"])
    command.append(api_url(token, method))
    payload = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)
    return json.loads(payload)


class TelegramPollTimeout(RuntimeError):
    pass


def get_updates(token: str, offset: int | None, timeout: int = 50) -> list[dict]:
    data = {"timeout": str(timeout)}
    if offset is not None:
        data["offset"] = str(offset)
    try:
        payload = telegram_request(token, "getUpdates", data=data, timeout=timeout + 15)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 28:
            raise TelegramPollTimeout("Telegram long poll timed out without updates") from exc
        raise
    return payload.get("result", [])


def send_message(token: str, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    chunks = []
    remaining = text.strip() or "(empty response)"
    while remaining:
        chunks.append(remaining[:3500])
        remaining = remaining[3500:]
    for index, chunk in enumerate(chunks):
        data = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
        }
        if reply_markup and index == len(chunks) - 1:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        telegram_request(token, "sendMessage", data=data, timeout=30)


def answer_callback_query(token: str, callback_id: str, text: str = "") -> None:
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    try:
        telegram_request(token, "answerCallbackQuery", data=data, timeout=15)
    except Exception as exc:
        log(f"answerCallbackQuery warning: {exc}")


def edit_message_text(token: str, chat_id: str, message_id: str, text: str, reply_markup: dict | None = None) -> None:
    data = {
        "chat_id": chat_id,
        "message_id": str(message_id),
        "text": text[:3500],
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    telegram_request(token, "editMessageText", data=data, timeout=15)


def send_typing(token: str, chat_id: str) -> None:
    try:
        telegram_request(token, "sendChatAction", data={"chat_id": chat_id, "action": "typing"}, timeout=10)
    except Exception as exc:
        log(f"typing warning: {exc}")


def read_offset() -> int | None:
    if not STATE_FILE.exists():
        return None
    try:
        return int(STATE_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_offset(offset: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str(offset), encoding="utf-8")
    os.chmod(STATE_FILE, 0o600)


def set_plan_pending(chat_id: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_PENDING_FILE.write_text(str(chat_id), encoding="utf-8")
    os.chmod(PLAN_PENDING_FILE, 0o600)


def is_plan_pending(chat_id: str) -> bool:
    if not PLAN_PENDING_FILE.exists():
        return False
    return PLAN_PENDING_FILE.read_text(encoding="utf-8").strip() == str(chat_id)


def clear_plan_pending() -> None:
    PLAN_PENDING_FILE.unlink(missing_ok=True)


def write_calendar_pending(slots: list[dict[str, str]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CALENDAR_PENDING_FILE.write_text(json.dumps(slots, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CALENDAR_PENDING_FILE, 0o600)


def read_calendar_pending() -> list[dict[str, str]]:
    if not CALENDAR_PENDING_FILE.exists():
        return []
    return json.loads(CALENDAR_PENDING_FILE.read_text(encoding="utf-8"))


def clear_calendar_pending() -> None:
    CALENDAR_PENDING_FILE.unlink(missing_ok=True)


def read_calendar_ledger() -> list[dict[str, str]]:
    if not CALENDAR_LEDGER_FILE.exists():
        return []
    try:
        return json.loads(CALENDAR_LEDGER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def write_calendar_ledger(entries: list[dict[str, str]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CALENDAR_LEDGER_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CALENDAR_LEDGER_FILE, 0o600)


def append_calendar_ledger(entry: dict[str, str]) -> None:
    entries = read_calendar_ledger()
    entries.append(entry)
    write_calendar_ledger(entries)


def read_completed_tasks() -> list[dict[str, str]]:
    if not COMPLETED_TASKS_FILE.exists():
        return []
    try:
        return json.loads(COMPLETED_TASKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def write_completed_tasks(entries: list[dict[str, str]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COMPLETED_TASKS_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(COMPLETED_TASKS_FILE, 0o600)


def completed_task_keys() -> set[str]:
    return {entry.get("task_key", "") for entry in read_completed_tasks() if entry.get("task_key")}


def task_is_completed(task_name: str) -> bool:
    return task_key(task_name) in completed_task_keys_effective()


def mark_task_done(raw_text: str) -> tuple[bool, str]:
    query = raw_text.strip()
    if not query:
        return False, "Напиши так: /done название задачи"
    tasks = current_planning_tasks(include_completed=True)
    query_key = task_key(query)
    exact = [task for task in tasks if task_key(task.get("task", "")) == query_key]
    if exact:
        task_name = exact[0].get("task", "")
    else:
        contains = [
            task for task in tasks
            if query_key and (query_key in task_key(task.get("task", "")) or task_key(task.get("task", "")) in query_key)
        ]
        if len(contains) == 1:
            task_name = contains[0].get("task", "")
        elif len(contains) > 1:
            options = "\n".join(f"- {task.get('task', '')}" for task in contains[:6])
            return False, "Нашёл несколько похожих задач. Уточни название:\n" + options
        else:
            task_name = query

    key = task_key(task_name)
    entries = read_completed_tasks()
    if any(entry.get("task_key") == key for entry in entries):
        return True, f"Уже было отмечено как выполненное: {task_name}"
    entries.append({
        "task": task_name,
        "task_key": key,
        "completed_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "telegram_done",
    })
    write_completed_tasks(entries)
    return True, f"Отметил выполненным: {task_name}"


def google_calendar_service(interactive: bool = False):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except Exception as exc:
        raise RuntimeError("Google Calendar Python packages are not installed") from exc

    creds = None
    if GOOGLE_CALENDAR_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_CALENDAR_TOKEN_FILE), GOOGLE_CALENDAR_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("Google Calendar auth is not configured. Run telegram_codex_bot.py --calendar-auth")
        if not GOOGLE_CALENDAR_CREDENTIALS_FILE.exists():
            raise RuntimeError(f"Google Calendar credentials file not found: {GOOGLE_CALENDAR_CREDENTIALS_FILE}")
        flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CALENDAR_CREDENTIALS_FILE), GOOGLE_CALENDAR_SCOPES)
        creds = flow.run_local_server(port=0)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    GOOGLE_CALENDAR_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(GOOGLE_CALENDAR_TOKEN_FILE, 0o600)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def calendar_auth() -> str:
    service = google_calendar_service(interactive=True)
    calendar_list = service.calendarList().list().execute().get("items", [])
    visible = [item.get("id", "") for item in calendar_list if item.get("id")]
    return "Google Calendar auth ok. Visible calendars: " + ", ".join(visible[:12])


def read_google_calendar_events(days: int = CALENDAR_PLANNING_DAYS, start_date: dt.date | None = None) -> list[dict[str, str]]:
    service = google_calendar_service(interactive=False)
    start = dt.datetime.combine(start_date or dt.date.today(), dt.time.min).astimezone()
    end = start + dt.timedelta(days=days)
    body = {
        "timeMin": start.isoformat(timespec="seconds"),
        "timeMax": end.isoformat(timespec="seconds"),
        "items": [{"id": calendar_id} for calendar_id in BUSY_CALENDAR_NAMES],
    }
    response = service.freebusy().query(body=body).execute()
    events: list[dict[str, str]] = []
    errors = []
    for calendar_id, payload in response.get("calendars", {}).items():
        if payload.get("errors"):
            errors.append(f"{calendar_id}: {payload['errors']}")
            continue
        for index, busy in enumerate(payload.get("busy", []), start=1):
            events.append({
                "calendar": calendar_id,
                "uid": f"busy-{calendar_id}-{index}",
                "summary": "Busy",
                "start": dt.datetime.fromisoformat(busy["start"]).replace(tzinfo=None).isoformat(timespec="minutes"),
                "end": dt.datetime.fromisoformat(busy["end"]).replace(tzinfo=None).isoformat(timespec="minutes"),
            })

    try:
        existing = service.events().list(
            calendarId=GOOGLE_WRITE_CALENDAR_ID,
            timeMin=start.isoformat(timespec="seconds"),
            timeMax=end.isoformat(timespec="seconds"),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()
        for item in existing.get("items", []):
            start_raw = item.get("start", {}).get("dateTime")
            end_raw = item.get("end", {}).get("dateTime")
            if not start_raw or not end_raw:
                continue
            events.append({
                "calendar": GOOGLE_WRITE_CALENDAR_ID,
                "uid": item.get("id", ""),
                "summary": item.get("summary", ""),
                "start": dt.datetime.fromisoformat(start_raw).replace(tzinfo=None).isoformat(timespec="minutes"),
                "end": dt.datetime.fromisoformat(end_raw).replace(tzinfo=None).isoformat(timespec="minutes"),
            })
    except Exception as exc:
        errors.append(f"{GOOGLE_WRITE_CALENDAR_ID} events.list: {exc}")

    if errors and not events:
        raise RuntimeError("; ".join(errors))
    if errors:
        log("google calendar partial read warnings: " + "; ".join(errors))
    return events


def delete_calendar_event_by_id(calendar_name: str, event_id: str) -> bool:
    if calendar_name == GOOGLE_WRITE_CALENDAR_ID:
        try:
            google_calendar_service(interactive=False).events().delete(calendarId=calendar_name, eventId=event_id).execute()
            return True
        except Exception as exc:
            log(f"google calendar delete failed: {exc}")
            return False
    script = """
on run argv
  set calendarName to item 1 of argv
  set targetUid to item 2 of argv
  set didDelete to false
  tell application "Calendar"
    tell calendar calendarName
      repeat with calendarEvent in events
        if uid of calendarEvent is targetUid then
          delete calendarEvent
          set didDelete to true
          exit repeat
        end if
      end repeat
    end tell
  end tell
  return didDelete
end run
"""
    completed = subprocess.run(
        [
            "osascript", "-e", script,
            calendar_name,
            event_id,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return completed.stdout.strip().lower() == "true"


def delete_recent_bot_calendar_events(days: int = 7) -> int:
    entries = read_calendar_ledger()
    cutoff = dt.datetime.now() - dt.timedelta(days=days)
    kept: list[dict[str, str]] = []
    deleted = 0
    for entry in entries:
        created_raw = entry.get("created_at", "")
        try:
            created_at = dt.datetime.fromisoformat(created_raw)
        except Exception:
            kept.append(entry)
            continue
        if created_at < cutoff:
            kept.append(entry)
            continue
        event_id = entry.get("event_id", "")
        calendar_name = entry.get("calendar", DEFAULT_CALENDAR_NAME)
        if event_id and delete_calendar_event_by_id(calendar_name, event_id):
            deleted += 1
        else:
            kept.append(entry)
    write_calendar_ledger(kept)
    return deleted


def delete_future_completed_focus_events(events: list[dict[str, str]]) -> int:
    now = dt.datetime.now()
    completed = completed_task_keys_from_calendar(events, mode="all_slots_past") | completed_task_keys()
    deleted = 0
    for event in events:
        task_name = focus_task_name(event.get("summary", ""))
        if not task_name or task_key(task_name) not in completed:
            continue
        try:
            start = dt.datetime.fromisoformat(event["start"])
        except Exception:
            continue
        if start <= now:
            continue
        event_id = event.get("uid", "")
        calendar_name = event.get("calendar", GOOGLE_WRITE_CALENDAR_ID)
        if event_id and delete_calendar_event_by_id(calendar_name, event_id):
            deleted += 1
    return deleted


def init_offset(token: str) -> None:
    updates = get_updates(token, offset=None, timeout=1)
    next_offset = 0
    if updates:
        next_offset = max(int(item["update_id"]) for item in updates) + 1
    write_offset(next_offset)
    log(f"initialized offset: {next_offset}")
    print(next_offset)


def run_codex(user_text: str) -> tuple[int, str]:
    if not CODEX_BIN.exists():
        return 127, f"Codex binary not found: {CODEX_BIN}"
    prompt = f"""User sent this message via Telegram bot.

Respond to Anton in Russian by default, unless he asks for another language.
Be concise in Telegram, but complete the requested local work when possible.
If you edit files or run checks, mention the important result and paths.

Telegram message:
{user_text}
"""
    command = [
        str(CODEX_BIN),
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "-C",
        str(VAULT),
        prompt,
    ]
    completed = subprocess.run(
        command,
        cwd=str(VAULT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )
    output = completed.stdout.strip()
    if completed.returncode == 0:
        output = extract_codex_answer(output)
    return completed.returncode, output


def extract_codex_answer(output: str) -> str:
    lines = output.splitlines()
    answer_start = None
    answer_end = None

    for index, line in enumerate(lines):
        if line.strip() == "codex":
            answer_start = index + 1
        elif answer_start is not None and line.strip() == "tokens used":
            answer_end = index
            break

    if answer_start is not None:
        answer_lines = lines[answer_start:answer_end]
        answer = "\n".join(answer_lines).strip()
        if answer:
            return answer

    # Fallback for quieter future Codex output: drop known CLI framing lines.
    ignored_prefixes = (
        "Reading additional input from stdin",
        "OpenAI Codex",
        "--------",
        "workdir:",
        "model:",
        "provider:",
        "approval:",
        "sandbox:",
        "reasoning ",
        "session id:",
        "user",
        "warning:",
        "tokens used",
    )
    filtered = [
        line
        for line in lines
        if line.strip() and not line.startswith(ignored_prefixes)
    ]
    return "\n".join(filtered).strip() or output


def run_daily_focus() -> tuple[int, str]:
    env = os.environ.copy()
    env["DAILY_FOCUS_SKIP_TELEGRAM_DELIVERY"] = "1"
    completed = subprocess.run(
        [str(RUN_DAILY_FOCUS)],
        cwd=str(VAULT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1200,
    )
    return completed.returncode, completed.stdout.strip()


def today_daily_briefs() -> list[Path]:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    if not DAILY_MESSAGES_DIR.exists():
        return []
    files = list(DAILY_MESSAGES_DIR.glob(f"{{focus}} daily planning brief – {today}*.md"))
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def cached_daily_summary(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return f"Сегодняшний Daily Planning Brief уже есть:\n{path}"

    sections = [
        "## Planning Snapshot",
        "## Task Inbox Normalization",
        "## Candidate Plan: next 2-3 days",
        "## Dependencies and Blockers",
        "## Meeting Memory Layer",
        "## Carry-over Review",
        "## Questions for Anton",
    ]
    lines = [
        "Сегодняшний Daily Planning Brief уже есть.",
        f"Файл: {path}",
        "",
        "Короткая выжимка:",
    ]
    for section in sections:
        match = re.search(rf"^{re.escape(section)}\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
        if not match:
            continue
        body = "\n".join(line for line in match.group(1).strip().splitlines() if line.strip())
        if not body:
            continue
        lines.extend(["", section.replace("## ", "") + ":"])
        lines.extend(body.splitlines()[:8])
        if len(body.splitlines()) > 8:
            lines.append("...")
    lines.extend(["", "Rerun не делал. Напиши /daily_rerun если нужен новый прогон."])
    return "\n".join(lines)


def is_ignored_message(text: str) -> bool:
    normalized = text.strip().lower()
    normalized = normalized.replace("👍🏻", "👍").replace("👍🏼", "👍").replace("👍🏽", "👍").replace("👍🏾", "👍").replace("👍🏿", "👍")
    return normalized in {"ок", "окей", "понял", "поняла", "спасибо", "спс", "+", "👍"}


def ask_payload(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered == "/ask":
        return ""
    if lowered.startswith("/ask "):
        return stripped[5:].strip()
    if lowered.startswith("/ask\n"):
        return stripped[5:].strip()
    return ""


def build_help_message() -> str:
    mode = "capture-only" if TODO_ONLY_MODE else "planner"
    auto_ask = "on" if AUTO_CODEX_FOR_QUESTIONS else "off"
    return "\n".join([
        f"Daily Focus bot — mode: {mode}",
        "",
        "Быстро, без Codex:",
        "/plan — добавить список дел в Planning Inbox",
        "/focus — быстрый план по текущим задачам",
        "/actions — кандидаты задач из встреч на подтверждение",
        "/todo — мои задачи из встреч, кнопка ✔ закрывает",
        "/todo_add текст — добавить задачу вручную (несколько строк — несколько задач)",
        "/todo_done T1 T2 — закрыть текстом, /todo_drop T3 — убрать",
        "/todo_claim T16 — взять себе задачу без исполнителя (кнопка ➕ в саммари встречи)",
        "/meetings — список последних встреч",
        "/meeting N — детальное саммари встречи (без N — последняя)",
        "/calendar_status — что уже стоит в календаре",
        "/calendar_review — что закрыто/в процессе по Focus-слотам",
        "/done задача — ручной override выполнения",
        "",
        "Календарь:",
        "/calendar_new — предложить слоты только для новых задач",
        "/calendar — предложить слоты для активных задач",
        "/calendar_confirm — создать предложенные слоты",
        "/calendar_clear_done — убрать будущие слоты закрытых задач",
        "",
        "Дорогие режимы, запускают Codex:",
        "/daily_rerun — полный Daily Planning Brief",
        "/prioritize — Strategic Board review",
        "/ask вопрос — задать вопрос Codex по vault",
        "",
        f"Auto-Codex на вопросы без /ask: {auto_ask}. Обычные сообщения сохраняются как задачи.",
    ])


def build_bot_status() -> str:
    tasks = current_planning_tasks()
    pending_actions = pending_meeting_action_candidates()
    pending_slots = read_calendar_pending()
    latest_brief = today_daily_briefs()
    lines = [
        "Daily Focus status",
        f"Mode: {'capture-only' if TODO_ONLY_MODE else 'planner'}",
        f"Auto-Codex questions: {'on' if AUTO_CODEX_FOR_QUESTIONS else 'off'}",
        f"Active planning tasks: {len(tasks)}",
        f"Pending meeting actions: {len(pending_actions)}",
        f"Open meeting todos: {len(open_meeting_todos())}",
        f"Pending calendar slots: {len(pending_slots)}",
        f"Today's brief: {'yes' if latest_brief else 'no'}",
    ]
    if latest_brief:
        lines.append(f"Brief file: {latest_brief[0]}")
    lines.extend(["", "Напиши /help, чтобы увидеть команды."])
    return "\n".join(lines)


def has_question_mark(text: str) -> bool:
    return "?" in text or "？" in text


def is_planning_message(text: str) -> bool:
    first_line = (text.strip().splitlines() or [""])[0].strip().lower()
    return (
        first_line.startswith("мои дела")
        or first_line.startswith("план")
        or first_line.startswith("/checkin")
        or first_line.startswith("/plan")
    )


def is_plan_command(text: str) -> bool:
    return text.strip().lower() in {"/plan", "/checkin"}


def plan_command_payload(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    for command in ("/plan", "/checkin"):
        if lowered.startswith(command + " "):
            return stripped[len(command):].strip()
        if lowered.startswith(command + "\n"):
            return stripped[len(command):].strip()
    return ""


def extract_task_items(text: str) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered in {"мои дела", "план", "/checkin", "/plan"}:
            continue
        line = re.sub(r"^\s*[-•]\s+", "", line)
        line = re.sub(r"^\s*\d+[\.)]\s+", "", line)
        importance = "low"
        if line.startswith("**"):
            importance = "very_important"
            line = line[2:].strip()
        elif line.startswith("*"):
            importance = "important"
            line = line[1:].strip()
        line = re.sub(r"^\s*[-–—]\s+", "", line)
        line = line.strip()
        if line:
            tasks.append({"task": line, "importance": importance})
    return tasks


def extract_task_lines(text: str) -> list[str]:
    return [item["task"] for item in extract_task_items(text)]


def task_key(task: str) -> str:
    lowered = task.lower().replace("ё", "е")
    lowered = re.sub(r"[()]", "", lowered)
    lowered = re.sub(r"[^a-zа-я0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def proposed_estimate(task: str) -> str:
    lowered = task.lower()
    if "счета" in lowered:
        return "30-45 min"
    if "тг" in lowered or "дейли фокус" in lowered:
        return "60-90 min"
    if "mvp" in lowered or "icom" in lowered:
        return "3-5 h"
    if "агентств" in lowered:
        return "60-90 min"
    if "лекци" in lowered:
        return "2-3 h"
    if "зал" in lowered:
        return "3 x 120 min"
    if "medical agent" in lowered or "выписки" in lowered:
        return "2-3 h"
    return "60-90 min"


def proposed_deadline(task: str) -> str:
    lowered = task.lower()
    days = 2
    if "счета" in lowered:
        days = 1
    elif "тг" in lowered or "дейли фокус" in lowered:
        days = 1
    elif "mvp" in lowered or "icom" in lowered:
        days = 2
    elif "агентств" in lowered:
        days = 2
    elif "лекци" in lowered:
        days = 3
    elif "зал" in lowered:
        days = 7
    elif "medical agent" in lowered or "выписки" in lowered:
        days = 3
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def estimate_minutes(estimate: str) -> int:
    lowered = estimate.lower().strip()
    if "3 x 120" in lowered:
        return 360
    if "3 x 60" in lowered:
        return 180
    if "30-45" in lowered:
        return 45
    if "60-90" in lowered:
        return 90
    if "2-3 h" in lowered:
        return 180
    if "3-5 h" in lowered:
        return 300
    match = re.search(r"(\d+)\s*h", lowered)
    if match:
        return int(match.group(1)) * 60
    match = re.search(r"(\d+)\s*min", lowered)
    if match:
        return int(match.group(1))
    return 90


def iso_minute(value: dt.datetime) -> str:
    return value.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def append_planning_inbox(text: str) -> tuple[Path, int, int]:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    inbox_path = PLANNING_INBOX_DIR / f"{{planning}} inbox – {today}.md"
    task_items = extract_task_items(text)
    PLANNING_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if not inbox_path.exists():
        inbox_path.write_text(
            f"# Planning Inbox — {today}\n\n"
            "Manual task notes for Daily Focus.\n\n"
            "## Inbox\n\n",
            encoding="utf-8",
        )
    existing = inbox_path.read_text(encoding="utf-8")
    existing_keys = {task_key(task.get("task", "")) for task in parse_normalized_tasks(existing)}
    new_tasks = [item for item in task_items if task_key(item["task"]) not in existing_keys]

    with inbox_path.open("a", encoding="utf-8") as f:
        f.write(f"\n## Telegram check-in — {now}\n\n")
        f.write("Raw message:\n\n")
        f.write("```text\n")
        f.write(text.strip() + "\n")
        f.write("```\n\n")
        f.write("Normalized tasks:\n\n")
        if new_tasks:
            for item in new_tasks:
                task = item["task"]
                f.write(f"- task: {task}\n")
                f.write("  type: manual\n")
                f.write("  source: manual\n")
                f.write(f"  importance: {item['importance']}\n")
                f.write("  deadline: unknown\n")
                f.write("  estimate: unknown\n")
                f.write(f"  proposed_deadline: {proposed_deadline(task)}\n")
                f.write(f"  proposed_estimate: {proposed_estimate(task)}\n")
                f.write("  proposal_source: telegram_codex_bot heuristic\n")
                f.write("  dependencies: unknown\n")
                f.write("  status: candidate\n\n")
        elif task_items:
            f.write("All parsed tasks were already present in this inbox file.\n\n")
        else:
            f.write("- task: needs clarification\n")
            f.write("  type: manual\n")
            f.write("  source: manual\n")
            f.write("  importance: low\n")
            f.write("  deadline: unknown\n")
            f.write("  estimate: unknown\n")
            f.write(f"  proposed_deadline: {dt.date.today().isoformat()}\n")
            f.write("  proposed_estimate: unknown\n")
            f.write("  proposal_source: telegram_codex_bot heuristic\n")
            f.write("  dependencies: unknown\n")
            f.write("  status: needs clarification\n\n")
    return inbox_path, len(task_items), len(new_tasks)


def append_confirmed_meeting_actions(candidates: list[dict[str, str]]) -> tuple[Path, int]:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    inbox_path = PLANNING_INBOX_DIR / f"{{planning}} inbox – {today}.md"
    PLANNING_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    if not inbox_path.exists():
        inbox_path.write_text(
            f"# Planning Inbox — {today}\n\n"
            "Manual task notes for Daily Focus.\n\n"
            "## Inbox\n\n",
            encoding="utf-8",
        )

    existing = inbox_path.read_text(encoding="utf-8")
    existing_keys = {task_key(task.get("task", "")) for task in parse_normalized_tasks(existing)}
    new_candidates = [candidate for candidate in candidates if task_key(candidate.get("task", "")) not in existing_keys]

    with inbox_path.open("a", encoding="utf-8") as f:
        f.write(f"\n## Meeting action confirmations — {now}\n\n")
        if not new_candidates:
            f.write("Selected meeting action candidates were already present in this inbox file.\n\n")
        for candidate in new_candidates:
            task = candidate.get("task", "").strip()
            f.write(f"- task: {task}\n")
            f.write("  type: meeting-follow-up\n")
            f.write("  source: meeting\n")
            f.write("  importance: low\n")
            f.write("  deadline: unknown\n")
            f.write("  estimate: unknown\n")
            f.write(f"  proposed_deadline: {proposed_deadline(task)}\n")
            f.write(f"  proposed_estimate: {proposed_estimate(task)}\n")
            f.write("  proposal_source: telegram_codex_bot heuristic\n")
            f.write("  dependencies: unknown\n")
            f.write("  status: candidate\n")
            f.write(f"  source_meeting: {candidate.get('source meeting', 'unknown')}\n")
            f.write(f"  source_path: {candidate.get('source path', 'unknown')}\n")
            f.write(f"  evidence: {candidate.get('evidence', 'unknown')}\n")
            f.write(f"  meeting_candidate_id: {candidate.get('id', 'unknown')}\n\n")
    return inbox_path, len(new_candidates)


def latest_files(directory: Path, limit: int = 3) -> list[Path]:
    if not directory.exists():
        return []
    files = [path for path in directory.iterdir() if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def status_rank(status: str) -> int:
    normalized = status.lower().strip()
    if "blocked" in normalized:
        return 5
    if "needs clarification" in normalized or "needs review" in normalized:
        return 4
    if "carry-over" in normalized:
        return 3
    if "ready" in normalized:
        return 2
    if "candidate" in normalized:
        return 1
    return 0


def merge_task(existing: dict[str, str], incoming: dict[str, str]) -> None:
    for key, value in incoming.items():
        if not value:
            continue
        if key == "status":
            if status_rank(value) > status_rank(existing.get("status", "")):
                existing[key] = value
            continue
        if key == "importance":
            ranks = {"low": 0, "important": 1, "very_important": 2}
            if ranks.get(value, 0) > ranks.get(existing.get(key, "low"), 0):
                existing[key] = value
            continue
        if existing.get(key) in {None, "", "unknown"} and value != "unknown":
            existing[key] = value


def current_planning_tasks(include_completed: bool = False) -> list[dict[str, str]]:
    inbox_files = latest_files(PLANNING_INBOX_DIR, limit=3)
    memory_files = [
        PLANNING_MEMORY_DIR / "{planning} current plan.md",
        PLANNING_MEMORY_DIR / "{planning} carry-over log.md",
    ]
    tasks: list[dict[str, str]] = []
    by_key: dict[str, dict[str, str]] = {}
    completed = completed_task_keys_effective() if not include_completed else set()
    for path in inbox_files + [p for p in memory_files if p.exists()]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for task in parse_normalized_tasks(text):
            name = task.get("task", "").strip()
            key = task_key(name)
            if not name or name.lower() == "needs clarification":
                continue
            if key in completed:
                continue
            if key in by_key:
                merge_task(by_key[key], task)
                continue
            if not task.get("proposed_deadline"):
                task["proposed_deadline"] = proposed_deadline(name)
            if not task.get("proposed_estimate"):
                task["proposed_estimate"] = proposed_estimate(name)
            if not task.get("importance"):
                task["importance"] = "low"
            tasks.append(task)
            by_key[key] = task
    return tasks


def today_strategy_review_path() -> Path:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    return STRATEGIC_REVIEW_DIR / f"{{strategy}} planning review – {today}.md"


def latest_strategy_review_path() -> Path | None:
    files = latest_files(STRATEGIC_REVIEW_DIR, limit=1)
    return files[0] if files else None


def latest_planning_source_mtime() -> float:
    candidates = latest_files(PLANNING_INBOX_DIR, limit=3) + [
        path for path in [
            PLANNING_MEMORY_DIR / "{planning} current plan.md",
            PLANNING_MEMORY_DIR / "{planning} carry-over log.md",
        ]
        if path.exists()
    ]
    return max((path.stat().st_mtime for path in candidates), default=0)


def strategy_review_is_fresh(path: Path | None = None) -> bool:
    review_path = path or today_strategy_review_path()
    if not review_path.exists() or review_path.stat().st_mtime < latest_planning_source_mtime():
        return False
    try:
        text = review_path.read_text(encoding="utf-8")
    except Exception:
        return False
    return "## Telegram summary" in text and "## Task priority annotations" in text


def task_payload_for_strategy(tasks: list[dict[str, str]]) -> str:
    payload = []
    for task in tasks:
        payload.append({
            "task": task.get("task", ""),
            "type": task.get("type", "manual"),
            "source": task.get("source", "manual"),
            "importance": task.get("importance", "low"),
            "deadline": task.get("deadline", "unknown"),
            "estimate": task.get("estimate", "unknown"),
            "proposed_deadline": task.get("proposed_deadline", ""),
            "proposed_estimate": task.get("proposed_estimate", ""),
            "status": task.get("status", "candidate"),
        })
    return json.dumps(payload, ensure_ascii=False, indent=2)


def run_strategy_review() -> tuple[bool, str, Path | None]:
    tasks = current_planning_tasks()
    if not tasks:
        return False, "Нет активных задач для Strategic Board review.", None
    review_path = today_strategy_review_path()
    STRATEGIC_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    prompt = f"""Use the Strategic Board skill from:
{STRATEGIC_BOARD_SKILL}

Task: analyze Anton's current planning list before calendar scheduling.

Create or update this exact file:
{review_path}

Use Anton's Second Brain context when useful, especially recent context, meetings, tasks, and Daily Focus planning memory. Keep it concise and source-backed.

Important:
- This is not a calendar layout yet.
- Do not decide for Anton what is "the only priority"; give strategic priority annotations for scheduling.
- Separate facts, interpretation, and recommendation.
- If a line is not a real task, mark it with strategic_priority: 5 and calendar_timing: skip.
- Use calendar_timing: weekend_fallback only for Health, Education, research, or learning tasks. For work/project/admin/outreach/infrastructure tasks use early, afternoon, evening, or skip.
- Do not modify Planning Inbox or Planning Memory.

Current planning tasks JSON:
{task_payload_for_strategy(tasks)}

The file must include this human-readable section first:

## Telegram summary

Write this section in Russian for Anton, not for agents.
Tone and shape should feel like a clear Strategic Board review, similar to a concise board-room analysis:
- 1-2 lines: the real decision frame.
- "Что поставить выше" with 2-4 bullets and why.
- "Что ограничить / отложить" with 2-4 bullets.
- "Почему" with source-backed reasoning in normal language.
- "CEO call" with what Anton needs to decide.
- End with the saved file path.

Do not include technical fields, YAML-like annotations, source trail, diffs, or patch output in this section.

The file must also include this exact parseable section for the calendar scheduler:

## Task priority annotations

- task: [exact task name from JSON]
  strategic_priority: [1-5, where 1 = most strategically important this week, 5 = do not schedule / low signal]
  calendar_timing: [early / afternoon / evening / weekend_fallback / skip]
  rationale: [one short sentence]
  source: [manual / meeting / context / inference]

Also include:
## Strategic summary
## Scheduling implications
## Source trail

After saving the file, reply ONLY with the contents of `## Telegram summary`.
Do not output Task priority annotations, Source trail, file diffs, or technical patch text.
"""
    code, output = run_codex(prompt)
    if code != 0:
        return False, output[-3000:], None
    if not review_path.exists():
        return False, "Strategic Board завершился, но review file не найден.", None
    return True, strategy_review_telegram_summary(review_path) or output[-3000:], review_path


def markdown_section(text: str, heading: str) -> str:
    pattern = rf"^{re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.M | re.S)
    return match.group(1).strip() if match else ""


def strategy_review_telegram_summary(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    summary = markdown_section(text, "## Telegram summary")
    if summary:
        return summary

    strategic = markdown_section(text, "## Strategic summary")
    implications = markdown_section(text, "## Scheduling implications")
    parts = ["Strategic Board review готов."]
    if strategic:
        parts.extend(["", strategic])
    if implications:
        parts.extend(["", "Scheduling implications:", implications])
    parts.extend(["", f"Файл: {path}"])
    return "\n".join(parts).strip()


def strategy_review_is_running() -> bool:
    if not STRATEGY_REVIEW_LOCK_FILE.exists():
        return False
    age = time.time() - STRATEGY_REVIEW_LOCK_FILE.stat().st_mtime
    if age > 1800:
        STRATEGY_REVIEW_LOCK_FILE.unlink(missing_ok=True)
        return False
    return True


def start_strategy_review_background(token: str, chat_id: str, reason: str) -> bool:
    if strategy_review_is_running():
        return False
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_REVIEW_LOCK_FILE.write_text(f"{dt.datetime.now().isoformat(timespec='seconds')} {reason}\n", encoding="utf-8")
    os.chmod(STRATEGY_REVIEW_LOCK_FILE, 0o600)

    def worker() -> None:
        try:
            log_route("strategic_board", codex=True, detail=f"background {reason}")
            ok, summary, path = run_strategy_review()
            if ok:
                send_message(
                    token,
                    chat_id,
                    (summary or f"Strategic Board review готов: {path}") + "\n\nТеперь можно вызвать /calendar.",
                )
            else:
                send_message(token, chat_id, f"Strategic Board review не завершился:\n{summary}")
        except Exception as exc:
            log(f"strategy review background error: {exc}")
            send_message(token, chat_id, f"Strategic Board review упал с ошибкой: {exc}")
        finally:
            STRATEGY_REVIEW_LOCK_FILE.unlink(missing_ok=True)

    threading.Thread(target=worker, name="strategy-review", daemon=True).start()
    return True


def parse_strategy_review(text: str) -> dict[str, dict[str, str]]:
    annotations: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- task:"):
            if current and current.get("task"):
                annotations[task_key(current["task"])] = current
            current = {"task": stripped.split(":", 1)[1].strip()}
            continue
        if current and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            if key in {"strategic_priority", "calendar_timing", "rationale", "source"}:
                current[key] = value.strip()
    if current and current.get("task"):
        annotations[task_key(current["task"])] = current
    return annotations


def strategic_annotations() -> dict[str, dict[str, str]]:
    path = latest_strategy_review_path()
    if not path or not strategy_review_is_fresh(path):
        return {}
    try:
        return parse_strategy_review(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"strategy review parse failed: {exc}")
        return {}


def strategic_annotation_for_task(task: dict[str, str]) -> dict[str, str]:
    return strategic_annotations().get(task_key(task.get("task", "")), {})


def strategic_priority_bias(task: dict[str, str]) -> int:
    annotation = strategic_annotation_for_task(task)
    try:
        priority = int(annotation.get("strategic_priority", "3"))
    except ValueError:
        priority = 3
    return {
        1: -30,
        2: -15,
        3: 0,
        4: 15,
        5: 40,
    }.get(priority, 0)


def strategic_calendar_timing(task: dict[str, str]) -> str:
    return strategic_annotation_for_task(task).get("calendar_timing", "")


def candidate_files(limit: int = 7) -> list[Path]:
    return latest_files(MEETING_ACTION_CANDIDATES_DIR, limit=limit)


def parse_meeting_action_candidates(text: str, path: Path) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- id:"):
            if current:
                candidates.append(current)
            current = {
                "id": stripped.split(":", 1)[1].strip(),
                "file": str(path),
            }
        elif current and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = value.strip()
    if current:
        candidates.append(current)
    return candidates


def pending_meeting_action_candidates() -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for path in candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for candidate in parse_meeting_action_candidates(text, path):
            if candidate.get("status", "") != "pending_confirmation":
                continue
            key = task_key(candidate.get("task", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            pending.append(candidate)
    return pending


def candidate_by_id(ids: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    normalized_ids = [item.upper() for item in ids]
    found: dict[str, dict[str, str]] = {}
    for path in candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for candidate in parse_meeting_action_candidates(text, path):
            candidate_id = candidate.get("id", "").upper()
            if candidate_id in normalized_ids and candidate_id not in found:
                found[candidate_id] = candidate
    missing = [item for item in normalized_ids if item not in found]
    return [found[item] for item in normalized_ids if item in found], missing


def replace_block_field(block: list[str], field: str, value: str) -> list[str]:
    replaced = False
    result: list[str] = []
    for line in block:
        if line.strip().startswith(f"{field}:"):
            indent = line[:len(line) - len(line.lstrip())]
            result.append(f"{indent}{field}: {value}")
            replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"  {field}: {value}")
    return result


def update_candidates_field(candidates: list[dict[str, str]], field: str, value: str, files: list[Path] | None = None) -> int:
    targets_by_file: dict[str, set[str]] = {}
    for candidate in candidates:
        file_path = candidate.get("file", "")
        candidate_id = candidate.get("id", "").upper()
        if file_path and candidate_id:
            targets_by_file.setdefault(file_path, set()).add(candidate_id)

    changed = 0
    for path in (files if files is not None else candidate_files()):
        target_ids = targets_by_file.get(str(path), set())
        if not target_ids:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        output: list[str] = []
        index = 0
        file_changed = False
        while index < len(lines):
            line = lines[index]
            if line.strip().startswith("- id:"):
                block = [line]
                candidate_id = line.split(":", 1)[1].strip().upper()
                index += 1
                while index < len(lines) and not lines[index].strip().startswith("- id:"):
                    block.append(lines[index])
                    index += 1
                if candidate_id in target_ids:
                    block = replace_block_field(block, field, value)
                    changed += 1
                    file_changed = True
                output.extend(block)
                continue
            output.append(line)
            index += 1
        if file_changed:
            path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return changed


def update_candidates_status(candidates: list[dict[str, str]], new_status: str, files: list[Path] | None = None) -> int:
    return update_candidates_field(candidates, "status", new_status, files=files)


def clear_pending_meeting_actions() -> int:
    pending = pending_meeting_action_candidates()
    return update_candidates_status(pending, "cleared_by_anton")


def build_actions_message() -> str:
    pending = pending_meeting_action_candidates()
    if not pending:
        return "Actions: pending-задач после встреч нет."
    lines = [
        f"Actions — {len(pending)} на проверку",
        "В работу: /actions_confirm M1 M3",
        "Не брать: /actions_reject M2",
        "",
    ]
    for candidate in pending[:8]:
        candidate_id = candidate.get("id", "?")
        task = candidate.get("task", "unknown").strip()
        if len(task) > 105:
            task = task[:102].rstrip() + "..."
        source = candidate.get("source meeting", "unknown").strip()
        source = re.sub(r"\s+", " ", source)
        source = re.sub(r"\s+—\s+\d{4}-\d{2}-\d{2}$", "", source)
        if len(source) > 54:
            source = source[:51].rstrip() + "..."
        confidence = candidate.get("confidence", "unknown")
        reason = candidate.get("reason", "unknown").replace("_", " ")
        lines.append(f"{candidate_id}. {task}")
        lines.append(f"   {confidence}; {reason}; {source}")
    if len(pending) > 8:
        lines.append("")
        lines.append(f"Ещё {len(pending) - 8}: покажу после обработки первых.")
        lines.append("")
    lines.extend([
        "",
        "В работу: /actions_confirm M1 M3",
        "Не брать: /actions_reject M2",
        "Очистить всё: /actions_clear",
    ])
    return "\n".join(lines)


def todo_files(limit: int | None = None) -> list[Path]:
    if not MEETING_TODO_DIR.exists():
        return []
    files = [p for p in MEETING_TODO_DIR.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files if limit is None else files[:limit]


def todo_owner(item: dict[str, str]) -> str:
    """me/other/unknown; блоки без owner: классифицируем по who на лету."""
    owner = item.get("owner", "").strip()
    if owner in {"me", "other", "unknown"}:
        return owner
    return classify_owner(item.get("who", ""))


def open_meeting_todos() -> list[dict[str, str]]:
    todos: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for path in todo_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for item in parse_meeting_action_candidates(text, path):
            if item.get("status", "") != "open":
                continue
            if todo_owner(item) != "me":
                continue
            key = task_key(item.get("task", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            todos.append(item)
    return todos


def todo_by_id(ids: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    normalized_ids = [item.upper() for item in ids]
    found: dict[str, dict[str, str]] = {}
    for path in todo_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for item in parse_meeting_action_candidates(text, path):
            item_id = item.get("id", "").upper()
            if item_id in normalized_ids and item_id not in found:
                found[item_id] = item
    missing = [item for item in normalized_ids if item not in found]
    return [found[item] for item in normalized_ids if item in found], missing


def add_manual_todos(texts: list[str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Ручное добавление задач в meeting todo (owner: me).
    Возвращает (added, duplicates); дубли — только против открытых задач."""
    open_by_key: dict[str, dict[str, str]] = {}
    max_id = 0
    for path in todo_files():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for item in parse_meeting_action_candidates(text, path):
            m = re.match(r"^T(\d+)$", item.get("id", "").strip(), flags=re.IGNORECASE)
            if m:
                max_id = max(max_id, int(m.group(1)))
            key = task_key(item.get("task", ""))
            if key and item.get("status", "") == "open" and key not in open_by_key:
                open_by_key[key] = item
    added: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    blocks: list[str] = []
    now_stamp = time.strftime("%Y-%m-%d %H:%M")
    for raw in texts:
        task = re.sub(r"^[-*•]\s*", "", raw.strip())
        key = task_key(task)
        if not key:
            continue
        if key in open_by_key:
            duplicates.append(open_by_key[key])
            continue
        max_id += 1
        todo_id = f"T{max_id}"
        item = {"id": todo_id, "task": task}
        open_by_key[key] = item
        added.append(item)
        blocks.append(
            f"- id: {todo_id}\n"
            f"  task: {task}\n"
            f"  who: Антон\n"
            f"  owner: me\n"
            f"  deadline: unknown\n"
            f"  source_meeting: Добавлено вручную\n"
            f"  source_path: telegram\n"
            f"  status: open\n"
            f"  created: {now_stamp}\n"
        )
    if blocks:
        today = dt.date.today().isoformat()
        todo_path = MEETING_TODO_DIR / f"{{todo}} meeting tasks – {today}.md"
        MEETING_TODO_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not todo_path.exists()
        with todo_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# Meeting TODO — {today}\n\n")
            f.write("\n".join(blocks) + "\n")
    return added, duplicates


def build_todo_message() -> str:
    todos = open_meeting_todos()
    if not todos:
        return "Meeting TODO: открытых задач из встреч нет."
    by_meeting: dict[str, list[dict[str, str]]] = {}
    for todo in todos:
        source = todo.get("source_meeting", "unknown").strip()
        by_meeting.setdefault(source, []).append(todo)
    lines = [f"Meeting TODO — открыто: {len(todos)}", ""]
    for source, items in by_meeting.items():
        display = re.sub(r"\s+", " ", source)
        if len(display) > 60:
            display = display[:57].rstrip() + "..."
        lines.append(f"{display}:")
        for todo in items:
            task = todo.get("task", "unknown").strip()
            if len(task) > 90:
                task = task[:87].rstrip() + "..."
            entry = f"{todo.get('id', '?')}. {task}"
            who = todo.get("who", "").strip()
            if who and who != "unknown":
                entry += f" — {who}"
            deadline = todo.get("deadline", "").strip()
            if deadline and deadline != "unknown":
                entry += f" (до {deadline})"
            lines.append(entry)
        lines.append("")
    first_id = todos[0].get("id", "T1")
    lines.append(f"Кнопка ✔ закрывает задачу. Текстом: /todo_done {first_id} · убрать: /todo_drop {first_id}")
    return "\n".join(lines)


def build_todo_keyboard(todos: list[dict[str, str]]) -> dict | None:
    buttons = [
        {"text": f"✔ {todo.get('id', '?')}", "callback_data": f"todo_done:{todo.get('id', '?')}"}
        for todo in todos[:30]
        if todo.get("id")
    ]
    if not buttons:
        return None
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    return {"inline_keyboard": rows}


def read_meeting_notes_state() -> list[dict[str, str]]:
    """Sent-встречи Meeting Notes Assistant, новые первыми: key, title, date."""
    try:
        data = json.loads(MEETING_NOTES_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    meetings = []
    for key, entry in (data.get("meetings") or {}).items():
        if entry.get("status") != "sent":
            continue
        meetings.append({
            "key": key,
            "title": entry.get("title", "unknown"),
            "date": entry.get("date", ""),
            "has_detail": bool((entry.get("summary") or {}).get("detailed_lines")),
        })
    meetings.sort(key=lambda item: item["date"], reverse=True)
    return meetings


def format_meeting_date(date: str) -> str:
    try:
        return dt.datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        return date


def build_meetings_message() -> str:
    meetings = read_meeting_notes_state()[:7]
    if not meetings:
        return "Встреч в Meeting Notes пока нет."
    lines = ["Последние встречи:"]
    for index, meeting in enumerate(meetings, start=1):
        title = meeting["title"]
        if len(title) > 60:
            title = title[:57].rstrip() + "..."
        lines.append(f"{index}. {title} — {format_meeting_date(meeting['date'])}")
    lines.extend(["", "Детали: /meeting 1 (или просто /meeting — последняя)"])
    return "\n".join(lines)


def meeting_detail_text(index: int) -> str:
    meetings = read_meeting_notes_state()
    if not meetings:
        return "Встреч в Meeting Notes пока нет."
    if index < 1 or index > len(meetings):
        return f"Есть встречи 1–{min(len(meetings), 7)}, см. /meetings"
    meeting = meetings[index - 1]
    command = [sys.executable or "/usr/bin/python3", str(MEETING_NOTES_SCRIPT), "--detail", meeting["key"]]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "Детальное саммари не успело сгенерироваться (timeout). Попробуй ещё раз."
    output = (proc.stdout or "").strip()
    if proc.returncode != 0 or not output:
        log(f"meeting detail failed rc={proc.returncode}: {(proc.stderr or output)[:200]}")
        return "Не получилось собрать детали встречи. Лог: ~/Library/Logs/meeting-notes.log"
    return output


def read_calendar_events(days: int = CALENDAR_PLANNING_DAYS, start_date: dt.date | None = None) -> list[dict[str, str]]:
    if GOOGLE_CALENDAR_TOKEN_FILE.exists():
        return read_google_calendar_events(days=days, start_date=start_date)

    start = dt.datetime.combine(start_date or dt.date.today(), dt.time.min)
    end = start + dt.timedelta(days=days)
    script = """
on makeDate(y, m, d, h, min)
  set resultDate to current date
  set year of resultDate to y
  set month of resultDate to m
  set day of resultDate to d
  set hours of resultDate to h
  set minutes of resultDate to min
  set seconds of resultDate to 0
  return resultDate
end makeDate

on isoDate(d)
  set y to year of d as integer
  set mo to month of d as integer
  set da to day of d as integer
  set h to hours of d as integer
  set mi to minutes of d as integer
  set moText to text -2 thru -1 of ("0" & mo)
  set daText to text -2 thru -1 of ("0" & da)
  set hText to text -2 thru -1 of ("0" & h)
  set miText to text -2 thru -1 of ("0" & mi)
  return (y as text) & "-" & moText & "-" & daText & "T" & hText & ":" & miText
end isoDate

on sanitize(t)
  set AppleScript's text item delimiters to linefeed
  set parts to text items of t
  set AppleScript's text item delimiters to " "
  set t to parts as text
  set AppleScript's text item delimiters to "||"
  set parts to text items of t
  set AppleScript's text item delimiters to "|"
  return parts as text
end sanitize

on run argv
  set calendarName to item 1 of argv
  set startWindow to makeDate((item 2 of argv) as integer, (item 3 of argv) as integer, (item 4 of argv) as integer, (item 5 of argv) as integer, (item 6 of argv) as integer)
  set endWindow to makeDate((item 7 of argv) as integer, (item 8 of argv) as integer, (item 9 of argv) as integer, (item 10 of argv) as integer, (item 11 of argv) as integer)
  set rows to {}
  tell application "Calendar"
    tell calendar calendarName
      set matchingEvents to every event whose start date is less than endWindow and end date is greater than startWindow
      repeat with calendarEvent in matchingEvents
        set eventSummary to sanitize(summary of calendarEvent)
        set eventUid to uid of calendarEvent
        set startIso to isoDate(start date of calendarEvent)
        set endIso to isoDate(end date of calendarEvent)
        set end of rows to calendarName & "||" & eventUid & "||" & eventSummary & "||" & startIso & "||" & endIso
      end repeat
    end tell
  end tell
  set AppleScript's text item delimiters to linefeed
  return rows as text
end run
"""
    events: list[dict[str, str]] = []
    for calendar_name in BUSY_CALENDAR_NAMES:
        try:
            completed = subprocess.run(
                [
                    "osascript", "-e", script,
                    calendar_name,
                    str(start.year), str(start.month), str(start.day), str(start.hour), str(start.minute),
                    str(end.year), str(end.month), str(end.day), str(end.hour), str(end.minute),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=CALENDAR_READ_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise RuntimeError(f"Calendar read failed for `{calendar_name}`: {exc}") from exc
        for line in completed.stdout.splitlines():
            parts = line.split("||")
            if len(parts) != 5:
                continue
            calendar, uid, summary, start_iso, end_iso = parts
            events.append({"calendar": calendar, "uid": uid, "summary": summary, "start": start_iso, "end": end_iso})
    return events


def event_intervals(events: list[dict[str, str]], calendar_name: str | None = None) -> list[tuple[dt.datetime, dt.datetime, dict[str, str]]]:
    intervals = []
    for event in events:
        if calendar_name and event.get("calendar") != calendar_name:
            continue
        try:
            start = dt.datetime.fromisoformat(event["start"])
            end = dt.datetime.fromisoformat(event["end"])
        except Exception:
            continue
        intervals.append((start, end, event))
    return intervals


def overlaps(start: dt.datetime, end: dt.datetime, busy: list[tuple[dt.datetime, dt.datetime, dict[str, str]]], buffer_minutes: int = 0) -> bool:
    buffer = dt.timedelta(minutes=buffer_minutes)
    return any(start < busy_end + buffer and end > busy_start - buffer for busy_start, busy_end, _ in busy)


def current_workweek_end() -> dt.date:
    today = dt.date.today()
    return today + dt.timedelta(days=4 - today.weekday())


def current_weekend_end() -> dt.date:
    today = dt.date.today()
    return today + dt.timedelta(days=6 - today.weekday())


def calendar_schedule_start_day() -> dt.date | None:
    now = dt.datetime.now()
    today = now.date()
    friday = current_workweek_end()
    if today.weekday() > 4:
        return None
    start_day = today if now.time() < dt.time(hour=22) else today + dt.timedelta(days=1)
    while start_day.weekday() > 4 and start_day <= friday:
        start_day += dt.timedelta(days=1)
    if start_day > friday:
        return None
    return start_day


def calendar_schedule_days(start_day: dt.date) -> int:
    return max(0, (current_workweek_end() - start_day).days + 1)


def calendar_schedule_days_through_weekend(start_day: dt.date) -> int:
    return max(0, (current_weekend_end() - start_day).days + 1)


def calendar_read_days_current_week() -> int:
    today = dt.date.today()
    if today.weekday() > 6:
        return 1
    return max(1, (current_weekend_end() - today).days + 1)


def ceil_datetime(value: dt.datetime, step_minutes: int = 15) -> dt.datetime:
    step_seconds = step_minutes * 60
    timestamp = int(value.timestamp())
    rounded = ((timestamp + step_seconds - 1) // step_seconds) * step_seconds
    return dt.datetime.fromtimestamp(rounded)


def has_focus_duplicate(task_name: str, day: dt.date, busy: list[tuple[dt.datetime, dt.datetime, dict[str, str]]]) -> bool:
    expected = "Focus: " + task_name
    for start, _, event in busy:
        if start.date() == day and event.get("calendar") in {DEFAULT_CALENDAR_NAME, GOOGLE_WRITE_CALENDAR_ID} and event.get("summary") == expected:
            return True
    return False


def ordered_offsets(preferred_offsets: list[int], days: int) -> list[int]:
    seen: set[int] = set()
    offsets: list[int] = []
    for offset in preferred_offsets + list(range(days)):
        if offset < 0 or offset >= days or offset in seen:
            continue
        seen.add(offset)
        offsets.append(offset)
    return offsets


def spaced_offsets(count: int, days: int, min_gap_days: int = 1) -> list[int]:
    if count <= 1:
        return [0]
    if min_gap_days > 1:
        return [min(index * min_gap_days, days - 1) for index in range(count)]
    return [round(index * (days - 1) / (count - 1)) for index in range(count)]


def find_free_slot(
    duration_minutes: int,
    busy: list[tuple[dt.datetime, dt.datetime, dict[str, str]]],
    start_day: dt.date,
    days: int,
    window_start: dt.time,
    window_end: dt.time,
    preferred_offsets: list[int] | None = None,
    forbidden_dates: set[dt.date] | None = None,
    buffer_minutes: int = FOCUS_EVENT_BUFFER_MINUTES,
    allow_weekend: bool = False,
    allowed_weekdays: set[int] | None = None,
) -> tuple[dt.datetime, dt.datetime] | None:
    duration = dt.timedelta(minutes=duration_minutes)
    step = dt.timedelta(minutes=15)
    for offset in ordered_offsets(preferred_offsets or [], days):
        day = start_day + dt.timedelta(days=offset)
        if allowed_weekdays is not None and day.weekday() not in allowed_weekdays:
            continue
        if not allow_weekend and day.weekday() > 4:
            continue
        if day > (current_weekend_end() if allow_weekend else current_workweek_end()):
            continue
        if forbidden_dates and day in forbidden_dates:
            continue
        cursor = dt.datetime.combine(day, window_start)
        if day == dt.date.today():
            earliest_today = ceil_datetime(dt.datetime.now() + dt.timedelta(minutes=buffer_minutes), step_minutes=15)
            if earliest_today > cursor:
                cursor = earliest_today
        latest_end = dt.datetime.combine(day, window_end)
        while cursor + duration <= latest_end:
            end = cursor + duration
            if not overlaps(cursor, end, busy, buffer_minutes=buffer_minutes):
                return cursor, end
            cursor += step
    return None


def parse_normalized_tasks(text: str) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("- task:"):
            if current:
                tasks.append(current)
            current = {"task": stripped.split(":", 1)[1].strip()}
        elif current and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            if key in {"type", "source", "importance", "deadline", "estimate", "proposed_deadline", "proposed_estimate", "proposal_source", "dependencies", "status", "source_meeting", "source_path", "evidence", "meeting_candidate_id"}:
                current[key] = value.strip()
    if current:
        tasks.append(current)
    return tasks


def task_block(task: dict[str, str]) -> str:
    task_type = task.get("type", "").lower()
    text = task.get("task", "").lower()
    if task_type in {"health"} or any(word in text for word in ["зал", "спорт"]):
        return "Health"
    if task_type in {"education"} or any(word in text for word in ["обуч", "ai mindset", "курс", "лекци", "лекцию"]):
        if "агентств" not in text and "написать" not in text:
            return "Education"
    if task_type in {"outreach"} or any(word in text for word in ["агентств", "outreach", "клиент", "партнер"]):
        return "Outreach"
    if task_type in {"infrastructure"} or any(word in text for word in ["тг", "telegram", "дейли фокус", "agent", "бот", "репозитор", "скил"]):
        return "Infrastructure"
    if task_type in {"admin"} or any(word in text for word in ["счета", "медицин", "выписки", "документ"]):
        return "Admin"
    return "Deep Work"


def waiting_label(task: dict[str, str]) -> str:
    text = task.get("task", "")
    lowered = text.lower()
    if "icom" in lowered:
        return "Icom / проект"
    if "агентств" in lowered or "лекци" in lowered:
        return "агентства / лекция"
    if "медицин" in lowered or "счета" in lowered or "выписки" in lowered:
        return "медицина / документы"
    if "тг" in lowered or "дейли фокус" in lowered or "agent" in lowered:
        return "инфраструктура агентов"
    if "зал" in lowered:
        return "личный план / здоровье"
    return "без внешнего адресата"


def block_label(block: str) -> str:
    labels = {
        "Admin": "Admin",
        "Deep Work": "Deep Work",
        "Outreach": "Outreach",
        "Health": "Health",
        "Education": "Education",
        "Infrastructure": "Infrastructure",
    }
    return labels.get(block, block)


def activity_color_id(task: dict[str, str]) -> str:
    return ACTIVITY_COLOR_IDS.get(task_block(task), DEFAULT_FOCUS_EVENT_COLOR_ID)


def task_calendar_priority(task: dict[str, str]) -> int:
    name = task.get("task", "")
    lowered = name.lower()
    block = task_block(task)
    importance_bias = strategic_priority_bias(task) + {
        "very_important": -20,
        "important": -10,
        "low": 0,
    }.get(task.get("importance", "low"), 0)
    if any(marker in lowered for marker in ["mvp", "icom", "клиент", "дедлайн"]):
        return importance_bias + 0
    if block == "Admin" and any(marker in lowered for marker in ["счета", "медицин", "документ"]):
        return importance_bias + 1
    if block == "Outreach":
        return importance_bias + 2
    if block == "Infrastructure" and any(marker in lowered for marker in ["тг", "telegram", "дейли фокус", "бот"]):
        return importance_bias + 3
    if block == "Health":
        return importance_bias + 4
    if block == "Infrastructure":
        return importance_bias + 5
    if block == "Education":
        return importance_bias + 6
    return importance_bias + (3 if block == "Deep Work" else 5)


def task_calendar_windows(task: dict[str, str]) -> list[tuple[dt.time, dt.time]]:
    lowered = task.get("task", "").lower()
    block = task_block(task)
    timing = strategic_calendar_timing(task)
    if timing == "early":
        return [(dt.time(hour=12), dt.time(hour=14)), (dt.time(hour=20), dt.time(hour=22)), (dt.time(hour=18), dt.time(hour=20))]
    if timing == "evening":
        return [(dt.time(hour=18), dt.time(hour=20)), (dt.time(hour=20), dt.time(hour=22)), (dt.time(hour=14), dt.time(hour=18))]
    if timing == "afternoon":
        return [(dt.time(hour=14), dt.time(hour=18)), (dt.time(hour=18), dt.time(hour=20)), (dt.time(hour=20), dt.time(hour=22))]
    if task.get("importance") in {"important", "very_important"}:
        return [(dt.time(hour=12), dt.time(hour=14)), (dt.time(hour=20), dt.time(hour=22)), (dt.time(hour=18), dt.time(hour=20))]
    if block == "Health":
        return [(dt.time(hour=14), dt.time(hour=18))]
    if block == "Education" or any(marker in lowered for marker in ["репозитор", "скил", "пересмотреть", "обуч"]):
        return [(dt.time(hour=18), dt.time(hour=20)), (dt.time(hour=14), dt.time(hour=18)), (dt.time(hour=20), dt.time(hour=22)), (dt.time(hour=12), dt.time(hour=14))]
    if task_calendar_priority(task) <= 2:
        return [(dt.time(hour=12), dt.time(hour=14)), (dt.time(hour=20), dt.time(hour=22)), (dt.time(hour=18), dt.time(hour=20))]
    return [(dt.time(hour=14), dt.time(hour=18)), (dt.time(hour=18), dt.time(hour=20)), (dt.time(hour=20), dt.time(hour=22)), (dt.time(hour=12), dt.time(hour=14))]


def focus_task_name(summary: str) -> str:
    prefix = "Focus: "
    return summary[len(prefix):].strip() if summary.startswith(prefix) else ""


def existing_focus_task_keys(events: list[dict[str, str]]) -> set[str]:
    keys: set[str] = set()
    for event in events:
        name = focus_task_name(event.get("summary", ""))
        if name:
            keys.add(task_key(name))
    return keys


def calendar_completion_window() -> tuple[dt.date, int]:
    start = dt.date.today() - dt.timedelta(days=14)
    end = dt.date.today() + dt.timedelta(days=30)
    return start, max(1, (end - start).days)


def completed_task_keys_from_calendar(events: list[dict[str, str]] | None = None, mode: str = "any_past") -> set[str]:
    if events is None:
        start, days = calendar_completion_window()
        try:
            events = read_calendar_events(days=days, start_date=start)
        except Exception as exc:
            log(f"calendar completion read failed: {exc}")
            return set()
    now = dt.datetime.now()
    past_keys: set[str] = set()
    future_keys: set[str] = set()
    for event in events:
        name = focus_task_name(event.get("summary", ""))
        if not name:
            continue
        try:
            end = dt.datetime.fromisoformat(event["end"])
        except Exception:
            continue
        key = task_key(name)
        if end <= now:
            past_keys.add(key)
        else:
            future_keys.add(key)
    if mode == "all_slots_past":
        return past_keys - future_keys
    return past_keys


def completed_task_keys_effective() -> set[str]:
    return completed_task_keys_from_calendar(mode="all_slots_past") | completed_task_keys()


def pending_calendar_task_keys() -> set[str]:
    keys: set[str] = set()
    for slot in read_calendar_pending():
        name = slot.get("task", "")
        if name:
            keys.add(task_key(name))
    return keys


def task_deadline_date(task: dict[str, str]) -> dt.date:
    raw = task.get("deadline", "")
    if not raw or raw == "unknown":
        raw = task.get("proposed_deadline", "")
    try:
        return dt.date.fromisoformat(raw[:10])
    except Exception:
        return dt.date.today() + dt.timedelta(days=7)


def task_estimate_label(task: dict[str, str]) -> str:
    name = task.get("task", "")
    estimate = task.get("estimate", "")
    proposed = False
    if not estimate or estimate == "unknown":
        estimate = task.get("proposed_estimate") or proposed_estimate(name)
        proposed = True
    return f"{estimate}{' proposed' if proposed else ''}"


def next_action(task: dict[str, str]) -> str:
    name = task.get("task", "")
    lowered = name.lower()
    if "счета" in lowered:
        return "собрать счета/документы и начать подачу."
    if "тг" in lowered or "дейли фокус" in lowered:
        return "проверить /plan, /focus и /calendar в Telegram."
    if "mvp" in lowered or "icom" in lowered:
        return "выбрать ближайший недоделанный сценарий и закрыть его."
    if "агентств" in lowered:
        return "собрать список 5-7 агентств и написать первый outreach."
    if "лекци" in lowered and "партнер" in lowered:
        return "собрать структуру лекции и 3-5 примеров."
    if "зал" in lowered:
        return "выбрать ближайший дневной слот."
    if "medical agent" in lowered or "выписки" in lowered:
        return "создать место для агента и загрузить первые выписки."
    if "репозитор" in lowered or "скил" in lowered:
        return "собрать ссылки и коротко отметить, что полезно."
    return "сделать первый конкретный шаг и зафиксировать результат."


def append_focus_group(lines: list[str], title: str, tasks: list[dict[str, str]]) -> None:
    lines.extend(["", f"{title}:"])
    if not tasks:
        lines.append("- нет задач")
        return
    for task in tasks:
        name = task.get("task", "")
        lines.append(f"[{task_estimate_label(task)}] {name}")
        lines.append(f"Next action: {next_action(task)}")


def focus_questions(tasks: list[dict[str, str]]) -> list[str]:
    questions: list[str] = []

    for task in tasks:
        dependencies = task.get("dependencies", "unknown")
        status = task.get("status", "")
        if status == "blocked" or dependencies not in {"", "unknown"}:
            questions.append(f"Что блокирует «{task.get('task', '')}»: {dependencies}?")
            break

    for task in tasks:
        name = task.get("task", "")
        lowered = name.lower()
        if any(marker in lowered for marker in ["mvp", "лекци", "medical agent", "интеграцию"]):
            questions.append(f"Какой done criteria у «{name}»?")
            break

    for task in tasks:
        name = task.get("task", "")
        lowered = name.lower()
        if any(marker in lowered for marker in ["агентств", "клиент", "партнер"]):
            questions.append(f"Кому именно адресована задача «{name}»?")
            break

    if len(questions) < 3:
        missing_deadline = [task for task in tasks if task.get("deadline", "unknown") == "unknown"]
        if missing_deadline:
            questions.append("Есть ли жёсткий deadline у чего-то из этого списка, кроме proposed сроков?")

    deduped: list[str] = []
    for question in questions:
        if question and question not in deduped:
            deduped.append(question)
    return deduped[:3]


def build_fast_focus() -> str:
    today_date = dt.date.today()
    tomorrow = today_date + dt.timedelta(days=1)
    today = dt.datetime.now().strftime("%Y-%m-%d")
    tasks = current_planning_tasks()

    tasks = sorted(tasks, key=lambda task: (task_deadline_date(task), task_block(task), task.get("task", "")))

    for task in tasks:
        name = task.get("task", "")
        if not task.get("proposed_deadline"):
            task["proposed_deadline"] = proposed_deadline(name)
        if not task.get("proposed_estimate"):
            task["proposed_estimate"] = proposed_estimate(name)

    lines = [f"Focus — {today}", ""]
    if not tasks:
        return "\n".join(lines + ["В Planning Inbox / Memory нет активных задач."])

    track_counts: dict[str, int] = {}
    for task in tasks:
        label = block_label(task_block(task))
        track_counts[label] = track_counts.get(label, 0) + 1
    tracks = ", ".join(f"{name} {count}" for name, count in sorted(track_counts.items(), key=lambda item: item[1], reverse=True)[:3])
    proposed_count = sum(1 for task in tasks if task.get("deadline", "unknown") == "unknown" or task.get("estimate", "unknown") == "unknown")
    blockers_count = sum(1 for task in tasks if task.get("status") in {"blocked", "needs clarification"} or task.get("dependencies", "unknown") not in {"", "unknown"})

    lines.extend([
        "Картина:",
        f"{len(tasks)} задач. Треки: {tracks}.",
        f"Неопределённость: {proposed_count} задач с proposed сроком/оценкой; блокеров явно: {blockers_count}.",
    ])

    today_tasks = [task for task in tasks if task_deadline_date(task) <= today_date]
    tomorrow_tasks = [task for task in tasks if task_deadline_date(task) == tomorrow]
    later_tasks = [task for task in tasks if task_deadline_date(task) > tomorrow]

    append_focus_group(lines, "Сегодня", today_tasks)
    append_focus_group(lines, "Завтра", tomorrow_tasks)
    append_focus_group(lines, "Позже / на неделе", later_tasks)

    questions = focus_questions(tasks)
    lines.extend(["", "Нужно уточнить:"])
    if questions:
        for index, question in enumerate(questions, start=1):
            lines.append(f"{index}. {question}")
    else:
        lines.append("1. Нет явных блокеров.")
    return "\n".join(lines)


def build_calendar_slots(tasks: list[dict[str, str]], calendar_events: list[dict[str, str]]) -> list[dict[str, str]]:
    tasks = [
        task for task in tasks
        if strategic_calendar_timing(task) != "skip"
    ]
    tasks = sorted(tasks, key=lambda task: (task_calendar_priority(task), task.get("proposed_deadline", "9999-12-31"), task_block(task), task.get("task", "")))
    slots: list[dict[str, str]] = []
    busy = event_intervals(calendar_events)
    start_day = calendar_schedule_start_day()
    if start_day is None:
        return []
    planning_days = calendar_schedule_days(start_day)
    weekend_planning_days = calendar_schedule_days_through_weekend(start_day)
    scheduled_dates_by_task: dict[str, set[dt.date]] = {}
    weekend_health_sessions = 0

    for task_index, task in enumerate(tasks):
        name = task.get("task", "")
        estimate = task.get("proposed_estimate") or proposed_estimate(name)
        total = estimate_minutes(estimate)
        block = task_block(task)
        chunks: list[int] = [120, 120, 120] if block == "Health" and ("3 x" in estimate.lower() or "зал" in name.lower()) else []
        if not chunks:
            while total > 0:
                chunk = min(total, 120)
                chunks.append(chunk)
                total -= chunk

        if block == "Health":
            preferred_offsets = spaced_offsets(len(chunks), planning_days, min_gap_days=2)
        else:
            preferred_offsets = [0 for _ in chunks]

        task_dates = scheduled_dates_by_task.setdefault(task_key(name), set())
        duplicate_dates = {
            start_day + dt.timedelta(days=offset)
            for offset in range(weekend_planning_days)
            if has_focus_duplicate(name, start_day + dt.timedelta(days=offset), busy)
        }

        for chunk_index, chunk in enumerate(chunks):
            if len(slots) >= 20:
                break
            forbidden_dates = duplicate_dates if block != "Health" else set(task_dates) | duplicate_dates
            if block == "Health":
                forbidden_dates |= {
                    date + dt.timedelta(days=delta)
                    for date in task_dates
                    for delta in (-1, 0, 1)
                }
            found = None
            for window_start, window_end in task_calendar_windows(task):
                found = find_free_slot(
                    chunk,
                    busy,
                    start_day,
                    planning_days,
                    window_start,
                    window_end,
                    preferred_offsets=[preferred_offsets[chunk_index]],
                    forbidden_dates=forbidden_dates,
                )
                if found:
                    break
            if not found and block == "Health":
                for window_start, window_end in task_calendar_windows(task):
                    found = find_free_slot(
                        chunk,
                        busy,
                        start_day,
                        planning_days,
                        window_start,
                        window_end,
                        preferred_offsets=[preferred_offsets[chunk_index]],
                        forbidden_dates=set(task_dates) | duplicate_dates,
                    )
                    if found:
                        break
            if not found and block == "Health" and weekend_health_sessions < 1:
                for window_start, window_end in task_calendar_windows(task):
                    found = find_free_slot(
                        chunk,
                        busy,
                        start_day,
                        weekend_planning_days,
                        window_start,
                        window_end,
                        preferred_offsets=[5 - start_day.weekday(), 6 - start_day.weekday()],
                        forbidden_dates=set(task_dates) | duplicate_dates,
                        allow_weekend=True,
                        allowed_weekdays={5, 6},
                    )
                    if found:
                        weekend_health_sessions += 1
                        break
            if not found and block == "Education":
                for window_start, window_end in task_calendar_windows(task):
                    found = find_free_slot(
                        chunk,
                        busy,
                        start_day,
                        weekend_planning_days,
                        window_start,
                        window_end,
                        preferred_offsets=[5 - start_day.weekday(), 6 - start_day.weekday()],
                        forbidden_dates=set(task_dates) | duplicate_dates,
                        allow_weekend=True,
                        allowed_weekdays={5, 6},
                    )
                    if found:
                        break
            if not found:
                break
            start, end = found
            slot = {"task": name, "start": iso_minute(start), "end": iso_minute(end), "calendar": GOOGLE_WRITE_CALENDAR_ID, "color_id": activity_color_id(task)}
            slots.append(slot)
            busy.append((start, end, {"calendar": GOOGLE_WRITE_CALENDAR_ID, "summary": "Focus: " + name, "uid": "pending"}))
            task_dates.add(start.date())
    return slots


def format_calendar_plan_message(slots: list[dict[str, str]], intro: str) -> str:
    start_day = calendar_schedule_start_day()
    planning_days = calendar_schedule_days(start_day) if start_day else 0
    lines = [
        intro,
        f"План компактно собран в текущую рабочую неделю: {planning_days} рабочих дней, до 22:00, с буфером {FOCUS_EVENT_BUFFER_MINUTES} минут.",
        "Ритм: важное 12-14 и 20-22; зал 14-18; неважное в промежутки.",
        "Выходные используются только как fallback для спорта/education, если не хватило места в будни.",
        "Пока ничего не создал. Для создания всех слотов: /calendar_confirm",
        "",
    ]
    for index, slot in enumerate(slots, start=1):
        start = dt.datetime.fromisoformat(slot["start"]).strftime("%d.%m %H:%M")
        end = dt.datetime.fromisoformat(slot["end"]).strftime("%H:%M")
        lines.append(f"{index}. {start}-{end} — {slot['task']}")
    return "\n".join(lines)


def build_calendar_plan() -> tuple[str, list[dict[str, str]]]:
    tasks = current_planning_tasks()
    if not tasks:
        return "Нет активных задач для календаря.", []

    try:
        calendar_events = read_calendar_events(days=calendar_read_days_current_week())
    except Exception as exc:
        log(f"calendar read failed: {exc}")
        clear_calendar_pending()
        return "Не смог прочитать календарь, поэтому слоты не предлагаю.", []

    slots = build_calendar_slots(tasks, calendar_events)
    write_calendar_pending(slots)
    return format_calendar_plan_message(slots, f"Предлагаю слоты в календарь `{GOOGLE_WRITE_CALENDAR_ID}`."), slots


def build_calendar_new_plan() -> tuple[str, list[dict[str, str]]]:
    tasks = current_planning_tasks()
    if not tasks:
        return "Нет активных задач для календаря.", []

    try:
        calendar_events = read_calendar_events(days=calendar_read_days_current_week())
    except Exception as exc:
        log(f"calendar new read failed: {exc}")
        clear_calendar_pending()
        return "Не смог прочитать календарь, поэтому слоты не предлагаю.", []

    scheduled_keys = existing_focus_task_keys(calendar_events)
    pending_keys = pending_calendar_task_keys()
    new_tasks = [
        task for task in tasks
        if task_key(task.get("task", "")) not in scheduled_keys | pending_keys
    ]
    if not new_tasks:
        clear_calendar_pending()
        return "Новых задач для календаря нет: все активные задачи уже стоят в календаре или pending.", []

    slots = build_calendar_slots(new_tasks, calendar_events)
    write_calendar_pending(slots)
    return format_calendar_plan_message(slots, f"Новые задачи для календаря `{GOOGLE_WRITE_CALENDAR_ID}`:"), slots


def build_calendar_status() -> str:
    tasks = current_planning_tasks()
    try:
        calendar_events = read_calendar_events(days=calendar_read_days_current_week())
    except Exception as exc:
        log(f"calendar status read failed: {exc}")
        return "Не смог прочитать календарь, поэтому status не показываю."

    scheduled_events = [
        event for event in calendar_events
        if focus_task_name(event.get("summary", ""))
    ]
    scheduled_keys = existing_focus_task_keys(calendar_events)
    pending_slots = read_calendar_pending()
    pending_keys = pending_calendar_task_keys()
    unscheduled = [
        task for task in tasks
        if task_key(task.get("task", "")) not in scheduled_keys | pending_keys
    ]

    lines = [
        "Calendar Status",
        f"Активных задач: {len(tasks)}",
        f"Уже в календаре: {len(scheduled_keys)}",
        f"Pending после /calendar или /calendar_new: {len(pending_slots)}",
        f"Ещё не поставлены: {len(unscheduled)}",
        "",
        "Уже стоит:",
    ]
    if scheduled_events:
        for event in sorted(scheduled_events, key=lambda item: item.get("start", ""))[:8]:
            start = dt.datetime.fromisoformat(event["start"]).strftime("%d.%m %H:%M")
            end = dt.datetime.fromisoformat(event["end"]).strftime("%H:%M")
            lines.append(f"- {start}-{end} — {focus_task_name(event.get('summary', ''))}")
    else:
        lines.append("- нет Focus-событий в окне")

    lines.append("")
    lines.append("Pending:")
    if pending_slots:
        for slot in pending_slots[:8]:
            start = dt.datetime.fromisoformat(slot["start"]).strftime("%d.%m %H:%M")
            end = dt.datetime.fromisoformat(slot["end"]).strftime("%H:%M")
            lines.append(f"- {start}-{end} — {slot.get('task', '')}")
    else:
        lines.append("- нет pending slots")

    lines.append("")
    lines.append("Не поставлено:")
    if unscheduled:
        for task in unscheduled[:10]:
            lines.append(f"- {task.get('task', '')}")
    else:
        lines.append("- нет")
    return "\n".join(lines)


def build_calendar_review() -> str:
    tasks = current_planning_tasks()
    try:
        completion_start, completion_days = calendar_completion_window()
        calendar_events = read_calendar_events(days=completion_days, start_date=completion_start)
    except Exception as exc:
        log(f"calendar review read failed: {exc}")
        return "Не смог прочитать календарь, поэтому review не показываю."

    now = dt.datetime.now()
    focus_events = [
        event for event in calendar_events
        if focus_task_name(event.get("summary", ""))
    ]
    focus_events = sorted(focus_events, key=lambda event: event.get("start", ""))
    calendar_completed_keys = completed_task_keys_from_calendar(focus_events, mode="any_past")
    fully_completed_keys = completed_task_keys_from_calendar(focus_events, mode="all_slots_past")
    legacy_completed_keys = completed_task_keys()
    completed_keys = fully_completed_keys | legacy_completed_keys
    completed_names: dict[str, tuple[str, str]] = {}
    in_progress_names: dict[str, str] = {}
    executed_blocks = []
    future_completed_events = []
    remaining_today = []
    future_keys: set[str] = set()
    future_names: dict[str, str] = {}
    for event in focus_events:
        task_name = focus_task_name(event.get("summary", ""))
        try:
            start = dt.datetime.fromisoformat(event["start"])
            end = dt.datetime.fromisoformat(event["end"])
        except Exception:
            continue
        key = task_key(task_name)
        if end <= now:
            executed_blocks.append((end, task_name))
            if key in fully_completed_keys:
                completed_names[key] = (task_name, end.strftime("%d.%m %H:%M"))
        else:
            future_keys.add(key)
            future_names[key] = task_name
        if key in completed_keys and start > now:
            future_completed_events.append(event)
        if start.date() == now.date() and end > now and key not in completed_keys:
            remaining_today.append(event)
    for key in calendar_completed_keys & future_keys:
        in_progress_names[key] = future_names.get(key, "")

    scheduled_keys = existing_focus_task_keys(calendar_events)
    unscheduled = [
        task for task in tasks
        if task_key(task.get("task", "")) not in scheduled_keys | pending_calendar_task_keys()
    ]

    lines = [
        f"Calendar Review — {now.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Выполнено блоков по календарю: {len(executed_blocks)}",
        f"Полностью закрыто задач: {len(fully_completed_keys)}",
        f"Задач в процессе: {len(in_progress_names)}",
        f"Legacy /done overrides: {len(legacy_completed_keys)}",
        f"Будущих слотов под полностью закрытые/override задачи: {len(future_completed_events)}",
        f"Осталось сегодня в календаре: {len(remaining_today)}",
        f"Активных задач без слота/pending: {len(unscheduled)}",
    ]

    lines.extend(["", "Выполненные блоки:"])
    if executed_blocks:
        for end, name in sorted(executed_blocks)[-8:]:
            lines.append(f"- {end.strftime('%d.%m %H:%M')} — {name}")
    else:
        lines.append("- пока нет прошлых Focus-слотов")

    lines.extend(["", "Задачи полностью закрыты:"])
    if completed_names:
        for _, (name, stamp) in sorted(completed_names.items(), key=lambda item: item[1][1])[-8:]:
            lines.append(f"- {stamp} — {name}")
    else:
        lines.append("- нет")

    lines.extend(["", "В процессе:"])
    if in_progress_names:
        for name in sorted(value for value in in_progress_names.values() if value):
            lines.append(f"- {name}")
    else:
        lines.append("- нет")

    lines.extend(["", "Освобождаются будущие слоты:"])
    if future_completed_events:
        for event in future_completed_events[:8]:
            start = dt.datetime.fromisoformat(event["start"]).strftime("%d.%m %H:%M")
            end = dt.datetime.fromisoformat(event["end"]).strftime("%H:%M")
            lines.append(f"- {start}-{end} — {focus_task_name(event.get('summary', ''))}")
    else:
        lines.append("- нет")

    lines.extend(["", "Осталось сегодня:"])
    if remaining_today:
        for event in remaining_today[:8]:
            start = dt.datetime.fromisoformat(event["start"]).strftime("%H:%M")
            end = dt.datetime.fromisoformat(event["end"]).strftime("%H:%M")
            lines.append(f"- {start}-{end} — {focus_task_name(event.get('summary', ''))}")
    else:
        lines.append("- нет Focus-событий на остаток дня")

    lines.extend(["", "Что можно сделать дальше:"])
    if future_completed_events:
        lines.append("- /calendar_clear_done — убрать будущие слоты по полностью закрытым/override задачам")
        lines.append("- /calendar_new — предложить новые слоты в освободившиеся окна")
    elif unscheduled:
        lines.append("- /calendar_new — предложить слоты для задач без календаря")
    else:
        lines.append("- новых действий не требуется")
    return "\n".join(lines)


def slot_is_available(slot: dict[str, str], events: list[dict[str, str]]) -> tuple[bool, str]:
    try:
        start = dt.datetime.fromisoformat(slot["start"])
        end = dt.datetime.fromisoformat(slot["end"])
    except Exception:
        return False, "bad time"
    busy = event_intervals(events)
    if overlaps(start, end, busy, buffer_minutes=FOCUS_EVENT_BUFFER_MINUTES):
        return False, f"slot busy or less than {FOCUS_EVENT_BUFFER_MINUTES}m buffer"
    target_busy = event_intervals(events, calendar_name=slot.get("calendar", DEFAULT_CALENDAR_NAME))
    if has_focus_duplicate(slot.get("task", ""), start.date(), target_busy):
        return False, "duplicate Focus event same day"
    return True, "ok"


def create_calendar_event(slot: dict[str, str]) -> str:
    start = dt.datetime.fromisoformat(slot["start"])
    end = dt.datetime.fromisoformat(slot["end"])
    calendar_id = slot.get("calendar", GOOGLE_WRITE_CALENDAR_ID)
    if GOOGLE_CALENDAR_TOKEN_FILE.exists() and calendar_id == GOOGLE_WRITE_CALENDAR_ID:
        event = google_calendar_service(interactive=False).events().insert(
            calendarId=calendar_id,
            body={
                "summary": "Focus: " + slot["task"],
                "description": "Created by Telegram Codex bot /calendar",
                "start": {"dateTime": start.astimezone().isoformat(timespec="seconds"), "timeZone": "Europe/Moscow"},
                "end": {"dateTime": end.astimezone().isoformat(timespec="seconds"), "timeZone": "Europe/Moscow"},
                "transparency": "opaque",
                "colorId": slot.get("color_id", DEFAULT_FOCUS_EVENT_COLOR_ID),
            },
        ).execute()
        return event.get("id", "")

    script = """
on makeDate(y, m, d, h, min)
  set resultDate to current date
  set year of resultDate to y
  set month of resultDate to m
  set day of resultDate to d
  set hours of resultDate to h
  set minutes of resultDate to min
  set seconds of resultDate to 0
  return resultDate
end makeDate

on run argv
  set calendarName to item 1 of argv
  set eventTitle to item 2 of argv
  set startDate to makeDate((item 3 of argv) as integer, (item 4 of argv) as integer, (item 5 of argv) as integer, (item 6 of argv) as integer, (item 7 of argv) as integer)
  set endDate to makeDate((item 8 of argv) as integer, (item 9 of argv) as integer, (item 10 of argv) as integer, (item 11 of argv) as integer, (item 12 of argv) as integer)
  tell application "Calendar"
    tell calendar calendarName
      set newEvent to make new event at end of events with properties {summary:eventTitle, start date:startDate, end date:endDate, description:"Created by Telegram Codex bot /calendar"}
      return uid of newEvent
    end tell
  end tell
end run
"""
    completed = subprocess.run([
        "osascript", "-e", script,
        slot.get("calendar", DEFAULT_CALENDAR_NAME),
        "Focus: " + slot["task"],
        str(start.year), str(start.month), str(start.day), str(start.hour), str(start.minute),
        str(end.year), str(end.month), str(end.day), str(end.hour), str(end.minute),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    return completed.stdout.strip()


def answer_with_codex(token: str, chat_id: str, question: str, route_detail: str = "") -> None:
    log_route("ask_codex", codex=True, detail=route_detail)
    send_typing(token, chat_id)
    try:
        code, output = run_codex(question)
    except subprocess.TimeoutExpired:
        send_message(token, chat_id, "Codex не успел ответить за 15 минут. Лог: /Users/anton/Library/Logs/telegram-codex-bot.log")
        log("codex timeout")
        return
    except Exception as exc:
        send_message(token, chat_id, f"Ошибка запуска Codex: {exc}")
        log(f"codex error: {exc}")
        return

    if code != 0:
        send_message(token, chat_id, f"Codex завершился с ошибкой {code}.\n\n{output[-3000:]}")
    else:
        send_message(token, chat_id, output[-12000:])


def todo_only_command_key(text: str) -> str:
    command = text.strip().split(maxsplit=1)[0].lower()
    return command


def is_todo_only_disabled_command(text: str) -> bool:
    command = todo_only_command_key(text)
    if command in TODO_ONLY_DISABLED_COMMANDS:
        return True
    return command.startswith("/actions_") or command.startswith("/calendar_")


def save_to_planning_backlog(token: str, chat_id: str, text: str, detail: str) -> None:
    try:
        inbox_path, parsed_count, new_count = append_planning_inbox(text)
        log_route("inbox", codex=False, detail=f"{detail} parsed={parsed_count}; new={new_count}")
        send_message(
            token,
            chat_id,
            f"Записал в backlog / Planning Inbox.\nРаспознано: {parsed_count}. Новых: {new_count}.\nCodex не запускал.\n{inbox_path}",
        )
    except Exception as exc:
        log(f"planning inbox error: {exc}")
        send_message(token, chat_id, f"Не смог сохранить Planning Inbox: {exc}")


def handle_text(token: str, chat_id: str, text: str) -> None:
    normalized = text.strip()
    if normalized in {"/start", "/help", "help", "/menu"}:
        log_route("help", codex=False)
        send_message(token, chat_id, build_help_message())
        return
    if normalized in {"/status", "status"}:
        log_route("status", codex=False)
        send_message(token, chat_id, build_bot_status())
        return
    if normalized in {"/ping", "ping"}:
        log_route("ping", codex=False)
        send_message(token, chat_id, "pong")
        return
    if normalized in {"/todo", "todo"}:
        log_route("todo", codex=False)
        send_message(token, chat_id, build_todo_message(), reply_markup=build_todo_keyboard(open_meeting_todos()))
        return
    if normalized in {"/meetings", "meetings"}:
        log_route("meetings", codex=False)
        send_message(token, chat_id, build_meetings_message())
        return
    if normalized == "/meeting" or normalized.startswith("/meeting "):
        arg = normalized[len("/meeting"):].strip()
        index = int(arg) if arg.isdigit() else 1
        log_route("meeting_detail", codex=False, detail=str(index))
        send_typing(token, chat_id)
        send_message(token, chat_id, meeting_detail_text(index))
        return
    if normalized.startswith("/todo_add"):
        payload = normalized[len("/todo_add"):].strip()
        texts = [line for line in payload.splitlines() if line.strip()]
        if not texts:
            send_message(token, chat_id, "Укажи задачу: /todo_add текст задачи (несколько строк — несколько задач)")
            return
        added, duplicates = add_manual_todos(texts)
        log_route("todo_add", codex=False, detail=",".join(i["id"] for i in added) or "none")
        parts = []
        if added:
            parts.append("Добавил в /todo:\n" + "\n".join(f"• {i['id']} — {i['task']}" for i in added))
        if duplicates:
            parts.append("Уже в списке: " + ", ".join(f"{i.get('id', '?')}" for i in duplicates))
        reply = "\n\n".join(parts) if parts else "Ничего не добавил (пустые строки)."
        keyboard = None
        if added:
            buttons = [{"text": f"✔ {i['id']}", "callback_data": f"mnote_done:{i['id']}"} for i in added[:30]]
            keyboard = {"inline_keyboard": [buttons[i:i + 3] for i in range(0, len(buttons), 3)]}
        send_message(token, chat_id, reply, reply_markup=keyboard)
        return
    if normalized.startswith("/todo_claim"):
        ids = re.findall(r"\bT\d+\b", normalized, flags=re.IGNORECASE)
        if not ids:
            send_message(token, chat_id, "Укажи id задач, например: /todo_claim T16")
            return
        found, missing = todo_by_id(ids)
        to_claim = [item for item in found if todo_owner(item) != "me"]
        already = [item for item in found if todo_owner(item) == "me"]
        changed = update_candidates_field(to_claim, "owner", "me", files=todo_files()) if to_claim else 0
        log_route("todo_claim", codex=False, detail=",".join(ids))
        parts = []
        if changed:
            parts.append(f"Взял себе: {', '.join(item.get('id', '?') for item in to_claim)}")
        if already:
            parts.append(f"Уже твои: {', '.join(item.get('id', '?') for item in already)}")
        if missing:
            parts.append(f"Не нашёл: {', '.join(missing)}")
        send_message(token, chat_id, ". ".join(parts) if parts else "Ничего не изменил.")
        return
    if normalized.startswith("/todo_done") or normalized.startswith("/todo_drop"):
        new_status = "done" if normalized.startswith("/todo_done") else "dropped"
        ids = re.findall(r"\bT\d+\b", normalized, flags=re.IGNORECASE)
        if not ids:
            command = "/todo_done" if new_status == "done" else "/todo_drop"
            send_message(token, chat_id, f"Укажи id задач, например: {command} T1 T2")
            return
        found, missing = todo_by_id(ids)
        changed = update_candidates_status(found, new_status, files=todo_files()) if found else 0
        log_route(f"todo_{new_status}", codex=False, detail=",".join(ids))
        parts = []
        if changed:
            verb = "Закрыл" if new_status == "done" else "Убрал"
            parts.append(f"{verb}: {', '.join(item.get('id', '?') for item in found)}")
        if missing:
            parts.append(f"Не нашёл: {', '.join(missing)}")
        send_message(token, chat_id, ". ".join(parts) if parts else "Ничего не изменил.")
        return
    if is_ignored_message(normalized):
        log_route("ignored", codex=False, detail=normalized)
        if normalized in {"спасибо", "спс"}:
            send_message(token, chat_id, "ок")
        return
    if is_plan_command(normalized):
        log_route("inbox", codex=False, detail="plan pending")
        set_plan_pending(chat_id)
        send_message(token, chat_id, "Ок, пришли список дел следующим сообщением. Я сохраню его в backlog / Planning Inbox.")
        return
    payload = plan_command_payload(normalized)
    if payload:
        save_to_planning_backlog(token, chat_id, payload, "plan payload")
        return
    if is_plan_pending(chat_id):
        save_to_planning_backlog(token, chat_id, normalized, "pending")
        clear_plan_pending()
        return
    if TODO_ONLY_MODE and is_todo_only_disabled_command(normalized):
        log_route("todo_only_disabled", codex=False, detail=todo_only_command_key(normalized))
        send_message(token, chat_id, "Этот режим сейчас отключён. Telegram работает как to-do inbox: пришли задачу обычным текстом или через /plan.")
        return
    if TODO_ONLY_MODE and normalized.startswith("/"):
        log_route("todo_only_unknown_command", codex=False, detail=todo_only_command_key(normalized))
        send_message(token, chat_id, "Команда не активна в to-do режиме. Пришли задачу обычным текстом, я сохраню её в backlog.")
        return
    if has_question_mark(normalized) and not normalized.lower().startswith("/ask"):
        if AUTO_CODEX_FOR_QUESTIONS and not TODO_ONLY_MODE:
            answer_with_codex(token, chat_id, normalized, route_detail="question_mark")
        elif is_planning_message(normalized):
            save_to_planning_backlog(token, chat_id, normalized, "question planning message")
        else:
            log_route("question_no_auto_codex", codex=False)
            send_message(
                token,
                chat_id,
                "Вопрос увидел, Codex не запускал. Если хочешь ответ по vault, напиши: /ask твой вопрос. Если это задача, пришли через /plan или обычным списком.",
            )
        return
    if is_planning_message(normalized):
        save_to_planning_backlog(token, chat_id, normalized, "planning message")
        return
    if normalized == "/focus":
        log_route("focus_fast", codex=False)
        send_message(token, chat_id, build_fast_focus())
        return
    if normalized == "/actions":
        log_route("actions_review", codex=False)
        send_message(token, chat_id, build_actions_message())
        return
    if normalized.startswith("/actions_confirm"):
        log_route("actions_confirm", codex=False)
        ids = [item.upper() for item in normalized.split()[1:]]
        if not ids:
            send_message(token, chat_id, "Укажи candidates: /actions_confirm M1 M3")
            return
        candidates, missing = candidate_by_id(ids)
        candidates = [candidate for candidate in candidates if candidate.get("status") == "pending_confirmation"]
        if not candidates:
            suffix = f" Не нашёл: {', '.join(missing)}." if missing else ""
            send_message(token, chat_id, "Нет pending candidates для подтверждения." + suffix)
            return
        try:
            inbox_path, added = append_confirmed_meeting_actions(candidates)
            changed = update_candidates_status(candidates, "confirmed_by_anton")
            message = f"Подтверждено meeting actions: {changed}. В Planning Inbox добавлено новых задач: {added}.\n{inbox_path}"
            if missing:
                message += "\nНе нашёл: " + ", ".join(missing)
            send_message(token, chat_id, message)
        except Exception as exc:
            log(f"actions confirm error: {exc}")
            send_message(token, chat_id, f"Не смог подтвердить actions: {exc}")
        return
    if normalized.startswith("/actions_reject"):
        log_route("actions_reject", codex=False)
        ids = [item.upper() for item in normalized.split()[1:]]
        if not ids:
            send_message(token, chat_id, "Укажи candidates: /actions_reject M2")
            return
        candidates, missing = candidate_by_id(ids)
        candidates = [candidate for candidate in candidates if candidate.get("status") == "pending_confirmation"]
        changed = update_candidates_status(candidates, "rejected_by_anton")
        suffix = f" Не нашёл: {', '.join(missing)}." if missing else ""
        send_message(token, chat_id, f"Отклонено meeting action candidates: {changed}.{suffix}")
        return
    if normalized == "/actions_clear":
        log_route("actions_clear", codex=False)
        changed = clear_pending_meeting_actions()
        send_message(token, chat_id, f"Pending meeting action candidates очищены: {changed}.")
        return
    if normalized == "/prioritize":
        log_route("strategic_board", codex=True, detail="manual requested")
        if start_strategy_review_background(token, chat_id, "manual"):
            send_message(token, chat_id, "Запустил Strategic Board review в фоне. Когда будет готово, пришлю summary. После этого можно вызвать /calendar.")
        else:
            send_message(token, chat_id, "Strategic Board review уже идёт. Дождись summary, потом вызови /calendar.")
        return
    if normalized == "/calendar":
        log_route("calendar_preview", codex=not strategy_review_is_fresh())
        try:
            if not strategy_review_is_fresh():
                if start_strategy_review_background(token, chat_id, "calendar"):
                    send_message(token, chat_id, "Сначала запустил Strategic Board review в фоне. Calendar preview соберу после review: повтори /calendar, когда придёт summary.")
                else:
                    send_message(token, chat_id, "Strategic Board review уже идёт. Calendar preview пока не собираю; дождись summary и повтори /calendar.")
                return
            message, slots = build_calendar_plan()
            if slots:
                send_message(token, chat_id, message)
            else:
                send_message(token, chat_id, message)
        except Exception as exc:
            log(f"calendar preview error: {exc}")
            send_message(token, chat_id, "Не смог прочитать календарь, поэтому слоты не предлагаю.")
        return
    if normalized == "/calendar_new":
        log_route("calendar_new", codex=not strategy_review_is_fresh())
        try:
            if not strategy_review_is_fresh():
                if start_strategy_review_background(token, chat_id, "calendar_new"):
                    send_message(token, chat_id, "Сначала запустил Strategic Board review для новых задач в фоне. Повтори /calendar_new, когда придёт summary.")
                else:
                    send_message(token, chat_id, "Strategic Board review уже идёт. Дождись summary и повтори /calendar_new.")
                return
            message, slots = build_calendar_new_plan()
            if slots:
                send_message(token, chat_id, message)
            else:
                send_message(token, chat_id, message)
        except Exception as exc:
            log(f"calendar new preview error: {exc}")
            send_message(token, chat_id, "Не смог прочитать календарь, поэтому слоты не предлагаю.")
        return
    if normalized == "/calendar_status":
        log_route("calendar_status", codex=False)
        send_message(token, chat_id, build_calendar_status())
        return
    if normalized == "/calendar_review":
        log_route("calendar_review", codex=False)
        send_message(token, chat_id, build_calendar_review())
        return
    if normalized.startswith("/done"):
        log_route("done", codex=False)
        payload = normalized[5:].strip()
        ok, message = mark_task_done(payload)
        send_message(token, chat_id, message + ("\nОсновная логика выполнения теперь берётся из прошлых Focus-слотов календаря; /done оставлен как ручной override." if ok else ""))
        return
    if normalized == "/calendar_clear_done":
        log_route("calendar_clear_done", codex=False)
        try:
            completion_start, completion_days = calendar_completion_window()
            calendar_events = read_calendar_events(days=completion_days, start_date=completion_start)
            deleted = delete_future_completed_focus_events(calendar_events)
            send_message(token, chat_id, f"Удалил будущих Focus-слотов по задачам, у которых уже был слот в прошлом: {deleted}. Теперь можно вызвать /calendar_new.")
        except Exception as exc:
            log(f"calendar clear done error: {exc}")
            send_message(token, chat_id, f"Не смог очистить выполненные слоты: {exc}")
        return
    if normalized == "/calendar_confirm":
        log_route("calendar_confirm", codex=False)
        slots = read_calendar_pending()
        if not slots:
            send_message(token, chat_id, "Нет pending calendar plan. Сначала напиши /calendar.")
            return
        selected_indices = list(range(len(slots)))
        created = 0
        skipped: list[str] = []
        try:
            calendar_events = read_calendar_events(days=calendar_read_days_current_week())
        except Exception as exc:
            log(f"calendar reread before confirm failed: {exc}")
            send_message(token, chat_id, "Не смог прочитать календарь перед созданием, поэтому слоты не создаю.")
            return
        try:
            for index in selected_indices:
                slot = slots[index]
                available, reason = slot_is_available(slot, calendar_events)
                if not available:
                    skipped.append(f"{index + 1}: {reason}")
                    continue
                event_id = create_calendar_event(slot)
                append_calendar_ledger({
                    "event_id": event_id,
                    "calendar": slot.get("calendar", DEFAULT_CALENDAR_NAME),
                    "task": slot.get("task", ""),
                    "start": slot.get("start", ""),
                    "end": slot.get("end", ""),
                    "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                })
                calendar_events.append({
                    "calendar": slot.get("calendar", DEFAULT_CALENDAR_NAME),
                    "uid": event_id,
                    "summary": "Focus: " + slot.get("task", ""),
                    "start": slot.get("start", ""),
                    "end": slot.get("end", ""),
                })
                created += 1
            if len(selected_indices) == len(slots):
                clear_calendar_pending()
            message = f"Создал {created} слотов в календаре `{GOOGLE_WRITE_CALENDAR_ID}`."
            if skipped:
                message += "\nПропустил:\n" + "\n".join(f"- {item}" for item in skipped)
            send_message(token, chat_id, message)
        except Exception as exc:
            log(f"calendar confirm error after {created}: {exc}")
            send_message(token, chat_id, f"Создал {created} слотов, потом ошибка: {exc}")
        return
    if normalized in {"/calendar_clear", "/clear_calendar", "/calendar clear", "clear calendar"}:
        log_route("calendar_clear", codex=False)
        try:
            clear_calendar_pending()
            deleted = delete_recent_bot_calendar_events(days=7)
            send_message(token, chat_id, f"Calendar очищен: pending plan удалён, событий бота, созданных за последние 7 дней, удалено: {deleted}.")
        except Exception as exc:
            log(f"calendar clear error: {exc}")
            send_message(token, chat_id, f"Не смог очистить calendar events: {exc}")
        return
    if normalized in {"/daily", "/daily_focus", "/daily_planning", "/360brief", "/fullfocus"}:
        cached = today_daily_briefs()
        if cached:
            log_route("daily_cached", codex=False, detail=str(cached[0]))
            send_message(token, chat_id, cached_daily_summary(cached[0])[-12000:])
            return
        log_route("daily_cached", codex=False, detail="no cache")
        send_message(token, chat_id, "Daily Planning Brief за сегодня ещё не найден. Напиши /daily_rerun для нового полного прогона.")
        return
    if normalized == "/daily_rerun":
        log_route("daily_rerun", codex=True)
        send_message(token, chat_id, "Запускаю 360 Brief: полный Daily Planning Brief с meeting memory, transcripts/project sources и critic.")
        code, output = run_daily_focus()
        readable_output = extract_codex_answer(output)
        if code == 0:
            send_message(token, chat_id, readable_output[-12000:] or "360 Brief готов, но readable output пустой. Проверь /Users/anton/Library/Logs/daily-focus.last-message.md")
        else:
            send_message(token, chat_id, f"360 Brief завершился с ошибкой {code}.\n\n{output[-2500:]}")
        return
    if normalized == "/ask":
        log_route("ask_codex", codex=False, detail="empty")
        send_message(token, chat_id, "Напиши вопрос так: /ask твой вопрос")
        return
    if normalized.lower().startswith("/ask ") or normalized.lower().startswith("/ask\n"):
        question = ask_payload(normalized)
        if not question:
            log_route("ask_codex", codex=False, detail="empty")
            send_message(token, chat_id, "Напиши вопрос так: /ask твой вопрос")
            return
        answer_with_codex(token, chat_id, question, route_detail="ask_command")
        return

    save_to_planning_backlog(token, chat_id, normalized, "ordinary")
    return


def handle_callback(token: str, allowed_chat_id: str, callback: dict) -> None:
    callback_id = str(callback.get("id", ""))
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if chat_id != str(allowed_chat_id):
        answer_callback_query(token, callback_id)
        log(f"ignored callback from chat_id={chat_id}")
        return
    data = str(callback.get("data", ""))
    match = re.match(r"^(todo_done|mnote_done|todo_claim):(T\d+)$", data)
    if not match:
        answer_callback_query(token, callback_id)
        return
    origin, todo_id = match.group(1), match.group(2)

    if origin == "todo_claim":
        # ➕ из per-meeting сообщения: взять unknown-задачу себе (owner → me)
        found, _missing = todo_by_id([todo_id])
        if not found:
            answer_callback_query(token, callback_id, f"{todo_id} не найден")
            return
        if todo_owner(found[0]) == "me":
            answer_callback_query(token, callback_id, f"{todo_id} уже твоя")
        else:
            update_candidates_field(found, "owner", "me", files=todo_files())
            answer_callback_query(token, callback_id, f"➕ {todo_id} добавил в /todo")
        log_route("todo_claim", codex=False, detail=todo_id)
        message_id = message.get("message_id")
        if message_id is None:
            return
        # кнопку ➕ заменяем на ✔, чтобы задачу можно было закрыть из этого же сообщения
        rows = ((message.get("reply_markup") or {}).get("inline_keyboard")) or []
        new_rows = [
            [
                {"text": f"✔ {todo_id}", "callback_data": f"mnote_done:{todo_id}"}
                if b.get("callback_data") == data else b
                for b in row
            ]
            for row in rows
        ]
        try:
            telegram_request(token, "editMessageReplyMarkup", data={
                "chat_id": chat_id,
                "message_id": str(message_id),
                "reply_markup": json.dumps({"inline_keyboard": new_rows}, ensure_ascii=False),
            }, timeout=15)
        except Exception as exc:
            log(f"editMessageReplyMarkup warning: {exc}")
        return

    found, _missing = todo_by_id([todo_id])
    if found and found[0].get("status") != "open":
        answer_callback_query(token, callback_id, f"{todo_id} уже закрыт")
        changed = 0
    else:
        changed = update_candidates_status(found, "done", files=todo_files()) if found else 0
        if changed:
            answer_callback_query(token, callback_id, f"✔ {todo_id} закрыт")
        else:
            answer_callback_query(token, callback_id, f"{todo_id} не найден")
    log_route("todo_button", codex=False, detail=f"{origin}:{todo_id}:changed={changed}")

    message_id = message.get("message_id")
    if message_id is None:
        return
    if origin == "todo_done":
        # /todo-сообщение: перерисовать список и клавиатуру
        todos = open_meeting_todos()
        try:
            edit_message_text(
                token, chat_id, str(message_id), build_todo_message(),
                reply_markup=build_todo_keyboard(todos) or {"inline_keyboard": []},
            )
        except Exception as exc:
            log(f"editMessageText warning: {exc}")
    else:
        # per-meeting сообщение: текст не трогаем, убираем нажатую кнопку
        rows = ((message.get("reply_markup") or {}).get("inline_keyboard")) or []
        new_rows = [[b for b in row if b.get("callback_data") != data] for row in rows]
        new_rows = [row for row in new_rows if row]
        try:
            telegram_request(token, "editMessageReplyMarkup", data={
                "chat_id": chat_id,
                "message_id": str(message_id),
                "reply_markup": json.dumps({"inline_keyboard": new_rows}, ensure_ascii=False),
            }, timeout=15)
        except Exception as exc:
            log(f"editMessageReplyMarkup warning: {exc}")


def run_loop(token: str, allowed_chat_id: str, once: bool = False) -> None:
    offset = read_offset()
    log(f"bot loop started; offset={offset}; allowed_chat_id={allowed_chat_id}")
    while True:
        try:
            updates = get_updates(token, offset=offset, timeout=50)
        except TelegramPollTimeout:
            log("getUpdates idle timeout; no updates")
            if once:
                return
            continue
        except Exception as exc:
            log(f"getUpdates error: {exc}")
            time.sleep(10)
            continue

        for update in updates:
            update_id = int(update["update_id"])
            offset = update_id + 1
            write_offset(offset)

            callback = update.get("callback_query")
            if callback:
                try:
                    handle_callback(token, allowed_chat_id, callback)
                except Exception as exc:
                    log(f"callback error: {exc}")
                continue

            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            if chat_id != str(allowed_chat_id):
                log(f"ignored message from chat_id={chat_id}")
                continue
            text = message.get("text")
            if not text:
                send_message(token, chat_id, "Пока умею отвечать только на текстовые сообщения.")
                continue
            log(f"processing update_id={update_id}")
            handle_text(token, chat_id, text)

        if once:
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram -> Daily Focus local polling bot")
    parser.add_argument("--init-offset", action="store_true", help="Mark current Telegram updates as seen")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--send-test", action="store_true", help="Send test message and exit")
    parser.add_argument("--calendar-auth", action="store_true", help="Authorize local Google Calendar API access")
    parser.add_argument("--calendar-check", action="store_true", help="Check local Google Calendar API access")
    args = parser.parse_args()

    if args.calendar_auth:
        print(calendar_auth())
        return 0
    if args.calendar_check:
        events = read_calendar_events(days=calendar_read_days_current_week())
        print(f"calendar events/busy windows: {len(events)}")
        for event in events[:10]:
            print(f"{event.get('calendar')} {event.get('start')} {event.get('end')} {event.get('summary')}")
        return 0

    env = load_env_file(ENV_FILE)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")

    if args.init_offset:
        init_offset(token)
        return 0
    if args.send_test:
        send_message(token, chat_id, "Telegram -> Codex bot настроен. Напиши /ping для проверки.")
        return 0
    run_loop(token, chat_id, once=args.once)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"fatal: {exc}")
        print(f"fatal: {exc}", file=sys.stderr)
        raise
