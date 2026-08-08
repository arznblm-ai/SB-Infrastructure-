#!/usr/bin/env python3
"""
meeting_notes.py — Meeting Notes Assistant.

Следит за transcripts/: когда Krisp-транскрипт встречи готов, делает короткое
shareable summary (claude haiku), шлёт его в Telegram и складывает таски встречи
в "meeting todo/" (для /todo в боте) и в Todoist.

Кроссплатформенно (мак + VPS): корень vault и пути берутся от расположения
скрипта / $HOME, env `SECOND_BRAIN_VAULT` переопределяет корень.
Мак: LaunchAgent com.user.meeting-notes (WatchPaths на transcripts/ + StartInterval 600).
VPS: systemd meeting-notes.timer (deploy/meeting-notes.{service,timer}).

Лог: ~/Library/Logs/meeting-notes.log (мак) / ~/.local/share/meeting-notes.log (Linux)
State: ~/.config/second-brain/meeting-notes-state.json (ключ = krisp id, exactly-once)
Доставка ноутса: ~/.config/second-brain/meeting-notes-delivery.env (топик Гермеса),
при отсутствии/неполноте конфига — прежний канал daily-focus.env (личный чат).
VPS sync: на маке в конце прогона state и todoist-todo-map best-effort пушатся на
VPS, где их читает TG-бот (см. sync_state_to_vps; на самом VPS выключен).
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
from typing import NamedTuple, Optional

import todoist_client
from task_owner import classify_owner


def _vault_root() -> Path:
    """Корень vault: env-переопределение, иначе — от расположения скрипта.

    Скрипт лежит в <vault>/infrastructure/daily focus/Scripts/, поэтому parents[3]
    даёт корень и на маке, и на VPS (/root/second-brain), где vault зеркалится.
    """
    override = os.environ.get("SECOND_BRAIN_VAULT")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3]


def _log_path() -> Path:
    """~/Library/Logs на маке, ~/.local/share на Linux (VPS)."""
    mac_logs = Path.home() / "Library" / "Logs"
    if mac_logs.is_dir():
        return mac_logs / "meeting-notes.log"
    linux_logs = Path.home() / ".local" / "share"
    try:
        linux_logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path("/tmp/meeting-notes.log")
    return linux_logs / "meeting-notes.log"


VAULT = _vault_root()
TRANSCRIPTS_DIR = VAULT / "transcripts"
DAILY_DIR = VAULT / "infrastructure" / "daily focus"
TODO_DIR = DAILY_DIR / "meeting todo"
STATE_DIR = Path.home() / ".config" / "second-brain"
ENV_FILE = STATE_DIR / "daily-focus.env"
TODOIST_LABEL_MAP_PATH = STATE_DIR / "todoist-label-map.json"
STATE_FILE = STATE_DIR / "meeting-notes-state.json"
LOCK_FILE = STATE_DIR / "meeting-notes.lock"
LOG_FILE = _log_path()

# ── Доставка ноутса встречи ───────────────────────────────────────────────
# Ноутс уходит в топик Гермеса токеном его бота, если есть полный конфиг
# meeting-notes-delivery.env; иначе — прежний канал (daily-focus.env, личный чат).
# Маркер контракта грепает ранбук deploy/apply_meeting_notes_vps.sh — не удалять.
DELIVERY_CONTRACT = "meeting-notes-delivery-v1"
DELIVERY_ENV_FILE = STATE_DIR / "meeting-notes-delivery.env"
TG_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

# ── Синк state на VPS ─────────────────────────────────────────────────────
# Кэш саммари (/meeting, /meetings, /summary) и mapping T<n> → Todoist id пишутся
# здесь, а читаются TG-ботом на VPS — поэтому в конце прогона пушим оба файла.
# На самом VPS синк не нужен: sync_state_to_vps выходит рано (см. running_on_vps).
# Best-effort: любая ошибка → строка в лог, прогон не падает.
VPS_SYNC_HOST = os.environ.get("VPS_SYNC_HOST", "root@163.5.29.10")
VPS_SYNC_KEY = Path(os.environ.get("VPS_SYNC_KEY", str(Path.home() / ".ssh" / "id_ed25519_vps")))
VPS_SYNC_REMOTE_DIR = os.environ.get("VPS_SYNC_REMOTE_DIR", "/root/.config/second-brain/")
VPS_SYNC_REMOTE_VAULT = Path("/root/second-brain")
VPS_SYNC_CONNECT_TIMEOUT = 5
VPS_SYNC_TOTAL_TIMEOUT = 15

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local" / "bin" / "claude"))
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

# ── Классификация задач (2026-08-05) ──────────────────────────────────────
# Области — ровно те, что заведены как area_labels в todoist-ids.json.
# Модель НЕ придумывает новые: только выбор из списка или NO_AREA.
NO_AREA = "без области"
AREA_CHOICES = [
    "AI-видеопродакшн",
    "UGC/SMM контент-завод",
    "Yandex.Scale CGI Promo",
    "Карьера",
    "Кино и экранизации",
    "Консалтинг Creative Industry",
    "Перекрёсток",
    NO_AREA,
]
# Короткие префиксы для title_short (формула «Область · действие — с кем»)
AREA_SHORT = {
    "AI-видеопродакшн": "AI-видео",
    "UGC/SMM контент-завод": "UGC",
    "Yandex.Scale CGI Promo": "Яндекс.Скейл",
    "Карьера": "Карьера",
    "Кино и экранизации": "Кино",
    "Консалтинг Creative Industry": "Хаб",
    "Перекрёсток": "Перекрёсток",
}
TITLE_SHORT_MAX = 70
DUE_MAX_DAYS_AHEAD = 90

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
                    "type": {"type": "string", "enum": ["task", "fact"]},
                    "area": {"type": "string", "enum": AREA_CHOICES},
                    "counterparty": {"type": ["string", "null"]},
                    "title_short": {"type": "string", "maxLength": TITLE_SHORT_MAX},
                    "due_suggested": {"type": ["string", "null"]},
                },
                "required": ["who", "what", "type", "area", "title_short"],
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
- tasks: только actionable поручения — конкретное действие, которое конкретный человек обязался или должен сделать после встречи (глагол действия + исполнитель). who — имя исполнителя из транскрипта. Владелец этих заметок — Антон Розенблюм: если исполнитель — участник, подписанный как Speaker_N, но по контексту транскрипта понятно, кто это (например, к нему обращаются по имени), пиши реальное имя. Если транскрипт без меток спикеров (диктофонная запись) и обещание сформулировано от первого лица («я сделаю», «я напишу»), исполнитель — Антон. "unknown" — только если исполнителя действительно нельзя определить. what — короткая формулировка задачи. deadline — только если срок явно назван (формат как в транскрипте), иначе пустая строка.
- type у каждого элемента tasks: "task" — поручение или обещание (кто-то что-то сделает после встречи); "fact" — констатация, решение, итог, вывод или цель встречи, которые НЕ являются действием. Это факты, а не задачи: «Цель встречи — обсудить X», «Брендирование подтверждено», «YouTube оказался единственной площадкой». Факты и решения относятся в summary_lines/detailed_lines, в tasks их включать не нужно. Если сомневаешься — ставь type: "fact".
- area у каждого элемента tasks: к какой рабочей области относится задача. Выбирай СТРОГО одно значение из списка: {areas}. Новые области придумывать нельзя. Если по содержанию встречи область не определяется однозначно — ставь "{no_area}".
- counterparty у каждого элемента tasks: имя человека из встречи, с кем связана задача (кому отправить, с кем согласовать, от кого ждём). Если такого человека нет — null.
- title_short у каждого элемента tasks: короткая формулировка задачи для трекера, максимум 70 символов, по формуле «Область · действие — с кем». Короткие префиксы областей: {area_prefixes}. Если область "{no_area}" — префикс не ставь, пиши только действие. Примеры: «Хаб · отправить Юрчику резюме», «Карьера · пересобрать профиль LinkedIn с нуля», «UGC · матрица форматов UGC/AIGC (10+) — для Ильдара». Не копируй дословную фразу из транскрипта — переформулируй в осмысленное действие.
- due_suggested у каждого элемента tasks: срок в формате YYYY-MM-DD, и ТОЛЬКО если срок реально прозвучал в разговоре. Относительные формулировки («в понедельник», «завтра», «до конца недели», «через две недели») пересчитывай в абсолютную дату относительно даты встречи, указанной ниже. Если срок не назывался — null. Выдумывать сроки запрещено.
- title: короткое название встречи по её содержанию (2-5 слов).
- detailed_lines: развёрнутое саммари, 10-25 строк (каждая до 200 символов) — тоже безопасное для пересылки коллегам, те же запреты на личную информацию. Покрой: контекст и цель встречи; участники и их роли; ход обсуждения с позициями и аргументами сторон; названные цифры, факты, названия; принятые решения; договорённости; открытые вопросы и на чём остановились. Пиши плотно и конкретно, без воды, каждая строка — законченная мысль.

Название файла встречи: {title}
Дата встречи: {date} — считай эту дату «сегодня» при пересчёте относительных сроков в due_suggested.

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


def send_message(token: str, chat_id: str, text: str, thread_id: Optional[str] = None) -> None:
    remaining = text.strip() or "(empty message)"
    while remaining:
        chunk, remaining = remaining[:3500], remaining[3500:]
        command = [
            "curl", "-fsS", "--max-time", "30",
            "--data-urlencode", f"chat_id={chat_id}",
            "--data-urlencode", f"text={chunk}",
            "--data-urlencode", "disable_web_page_preview=true",
        ]
        if thread_id:
            command.extend(["--data-urlencode", f"message_thread_id={thread_id}"])
        command.append(f"https://api.telegram.org/bot{token}/sendMessage")
        subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)


# ── Каналы доставки ───────────────────────────────────────────────────────

class Channel(NamedTuple):
    """Куда слать: токен бота, чат и (опционально) топик форума."""
    token: str
    chat_id: str
    thread_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.token and self.chat_id)


def send_to(channel: Channel, text: str) -> None:
    send_message(channel.token, channel.chat_id, text, thread_id=channel.thread_id)


def read_token_file(path: Path) -> str:
    """Токен из отдельного файла: строка вида KEY=значение или голая строка."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        log(f"delivery: не читается token file {path}: {type(exc).__name__}: {exc}")
        return ""
    fallback = ""
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value = line.split("=", 1)[1] if "=" in line else line
        value = value.strip().strip('"').strip("'").strip()
        if not value:
            continue
        if TG_TOKEN_RE.match(value):  # похоже на телеграм-токен — берём сразу
            return value
        fallback = fallback or value
    return fallback


def load_delivery_channel() -> Optional[Channel]:
    """Канал доставки ноутса из meeting-notes-delivery.env, либо None.

    None означает «конфига нет или он неполон» — вызывающий код падает на
    прежний канал (daily-focus.env, личный чат) и пишет строку в лог.
    """
    if not DELIVERY_ENV_FILE.exists():
        log(f"delivery: конфига {DELIVERY_ENV_FILE} нет → прежний канал (daily-focus.env)")
        return None
    try:
        env = load_env_file(DELIVERY_ENV_FILE)
    except Exception as exc:
        log(f"delivery: {DELIVERY_ENV_FILE} не разобран ({type(exc).__name__}: {exc}) → прежний канал")
        return None
    token = env.get("MEETING_NOTES_TG_TOKEN", "").strip()
    token_file = env.get("MEETING_NOTES_TG_TOKEN_FILE", "").strip()
    if not token and token_file:
        token = read_token_file(Path(token_file).expanduser())
    chat_id = env.get("MEETING_NOTES_CHAT_ID", "").strip()
    thread_id = env.get("MEETING_NOTES_THREAD_ID", "").strip()
    missing = [
        name for name, value in (
            ("MEETING_NOTES_TG_TOKEN|_FILE", token),
            ("MEETING_NOTES_CHAT_ID", chat_id),
            ("MEETING_NOTES_THREAD_ID", thread_id),
        ) if not value
    ]
    if missing:
        log(f"delivery: в {DELIVERY_ENV_FILE.name} не хватает {', '.join(missing)} → прежний канал")
        return None
    log(f"delivery: ноутсы уходят в chat {chat_id}, топик {thread_id} ({DELIVERY_CONTRACT})")
    return Channel(token=token, chat_id=chat_id, thread_id=thread_id)


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


def clean_area(value: object) -> str:
    """Область только из фиксированного списка; всё прочее → «без области»."""
    text = str(value or "").strip()
    if text in AREA_CHOICES and text != NO_AREA:
        return text
    return NO_AREA


def clean_title_short(value: object, fallback: str) -> str:
    """<=70 символов по границе слова; пустое → исходный текст задачи."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        text = re.sub(r"\s+", " ", str(fallback or "").strip())
    if len(text) <= TITLE_SHORT_MAX:
        return text
    cut = text[:TITLE_SHORT_MAX]
    space = cut.rfind(" ")
    if space >= TITLE_SHORT_MAX // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—-·")


def clean_due(value: object, today: Optional[dt.date] = None) -> Optional[str]:
    """YYYY-MM-DD в окне [сегодня; сегодня+90 дней], иначе None."""
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if not m:
        return None
    try:
        parsed = dt.datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None
    base = today or dt.date.today()
    if parsed < base or parsed > base + dt.timedelta(days=DUE_MAX_DAYS_AHEAD):
        return None
    return parsed.isoformat()


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
    dropped_facts = 0
    for task in tasks[:10]:
        if not isinstance(task, dict):
            continue
        what = str(task.get("what", "")).strip()
        if not what:
            continue
        # classify-then-filter: модель сама помечает констатации как fact,
        # отсутствующий/пустой type = "task" (fallback-движок и старый кэш state)
        if str(task.get("type", "task")).strip().lower() == "fact":
            dropped_facts += 1
            continue
        counterparty = task.get("counterparty")
        counterparty = str(counterparty).strip() if counterparty else ""
        clean_tasks.append({
            "who": str(task.get("who", "")).strip() or "unknown",
            "what": what,
            "deadline": str(task.get("deadline", "")).strip(),
            # haiku слабая — поля модели всегда проходят валидацию в коде
            "area": clean_area(task.get("area")),
            "counterparty": counterparty,
            "title_short": clean_title_short(task.get("title_short"), what),
            "due_suggested": clean_due(task.get("due_suggested")),
        })
    detailed = []
    for line in data.get("detailed_lines") or []:
        text = str(line).strip()
        if not text:
            continue
        if len(text) > 250:
            text = text[:247].rstrip() + "..."
        detailed.append(text)
    if dropped_facts:
        log(f"tasks: отброшено как факты (type=fact): {dropped_facts}")
    return {
        "title": title,
        "summary_lines": clean_lines[:8],
        "tasks": clean_tasks,
        "detailed_lines": detailed[:25],
    }


def summarize_llm(content: str, title: str, date: str) -> Optional[dict]:
    prompt = PROMPT_TEMPLATE.format(
        title=title,
        date=date,
        body=build_llm_body(content),
        areas=" | ".join(AREA_CHOICES),
        no_area=NO_AREA,
        area_prefixes=", ".join(f"{short} (= {full})" for full, short in AREA_SHORT.items()),
    )
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
        # Детерминированный путь: область доопределяется label-map в append_todos,
        # title_short = исходный текст, срок модель не пересчитывает.
        tasks.append({
            "who": who,
            "what": text[:200],
            "deadline": deadline,
            "area": NO_AREA,
            "counterparty": "",
            "title_short": clean_title_short(text[:200], text[:200]),
            "due_suggested": None,
        })
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
    # Todoist: area-label встречи ищем один раз на всю встречу (все задачи — в Inbox,
    # рабочая область помечается label-ом; best-effort, см. todoist_client)
    try:
        todoist_area_label = todoist_client.todoist_label_for_meeting(
            source_meeting, TODOIST_LABEL_MAP_PATH, log_fn=log
        )
    except Exception as exc:
        todoist_area_label = None
        log(f"todoist: area label lookup failed for '{source_meeting}': {exc}")
    # Секция «📥 Новое — без даты»: все задачи встреч приземляются туда (id из конфига)
    intake_section = None
    if not dry_run:
        try:
            intake_section = todoist_client.section_id("intake", log_fn=log)
        except Exception as exc:
            log(f"todoist: intake section lookup failed: {type(exc).__name__}: {exc}")
    todoist_created: dict[str, str] = {}
    for task in tasks:
        key = task_key(task["what"])
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        max_id += 1
        todo_id = f"T{max_id}"
        owner = classify_owner(task["who"])
        items.append({"id": todo_id, "what": task["what"], "who": task["who"], "owner": owner})
        # Область: ручной override Антона (label-map) → поле area модели → без области
        area = todoist_area_label or clean_area(task.get("area"))
        area_label = area if area != NO_AREA else None
        title_short = clean_title_short(task.get("title_short"), task["what"])
        due_suggested = clean_due(task.get("due_suggested"))
        if owner == "other":
            # Чужие задачи в Todoist не переносятся (решение Антона 2026-07-31):
            # они остаются в vault-сторе и в shareable-сообщении встречи.
            (print if dry_run else log)(f"todoist: skip other-owned task {todo_id}")
        elif dry_run:
            print(
                f"todoist: would create task for {todo_id} (owner={owner})\n"
                f"    area:          {area}\n"
                f"    title_short:   {title_short}\n"
                f"    due_suggested: {due_suggested or 'нет'}\n"
                f"    дедуп:         не проверялся (--dry-run в Todoist не ходит)"
            )
        else:
            # Создание задачи не должно ломать запись в vault-стор ни при каком сбое
            task_id = None
            try:
                duplicate_id = todoist_client.find_duplicate(
                    title_short, area_label=area_label, vault_id=None, log_fn=log
                )
            except Exception as exc:
                duplicate_id = None
                log(f"todoist: duplicate lookup failed for {todo_id}: {type(exc).__name__}: {exc}")
            if duplicate_id:
                log(f"todoist: duplicate {duplicate_id} for {todo_id} '{title_short}', задача не создаётся")
                try:
                    todoist_client.append_description_line(
                        duplicate_id,
                        f"Также из встречи: {source_meeting} ({todo_id})",
                        log_fn=log,
                    )
                except Exception as exc:
                    log(f"todoist: description append failed for {duplicate_id}: {type(exc).__name__}: {exc}")
            else:
                try:
                    task_id = todoist_client.create_task(
                        title=title_short,
                        vault_id=todo_id,
                        source_meeting=source_meeting,
                        who=task["who"],
                        created=now_stamp,
                        owner=owner,
                        area_label=area_label,
                        log_fn=log,
                        section_id=intake_section,
                        due_date=due_suggested,
                    )
                except Exception as exc:
                    task_id = None
                    log(f"todoist: create task failed for {todo_id}: {type(exc).__name__}: {exc}")
            if task_id:
                todoist_created[todo_id] = task_id
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
    if todoist_created:
        # Один merge-write на встречу: T<n> → task id (для быстрого закрытия по ✔)
        try:
            mapping = todoist_client.load_todo_task_map(log_fn=log)
            mapping.update(todoist_created)
            todoist_client.save_todo_task_map(mapping, log_fn=log)
        except Exception as exc:
            log(f"todoist: cannot persist todo map: {type(exc).__name__}: {exc}")
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


def running_on_vps() -> bool:
    """Скрипт уже крутится на VPS (vault смонтирован как /root/second-brain)."""
    try:
        return VAULT.resolve() == VPS_SYNC_REMOTE_VAULT
    except Exception:
        return False


def sync_state_to_vps() -> None:
    """Best-effort push кэша встреч и todo-map на VPS (там их читает TG-бот).

    Никогда не бросает исключений: любая проблема (нет ключа, нет сети, VPS лежит)
    пишется одной строкой в лог с префиксом "vps-sync:" и прогон продолжается.
    Отключается через VPS_SYNC_DISABLE=1; на самом VPS выключается автоматически
    (пушить некуда — state уже локальный).
    """
    if os.environ.get("VPS_SYNC_DISABLE") == "1":
        log("vps-sync: skipped (VPS_SYNC_DISABLE=1)")
        return
    if running_on_vps():
        log(f"vps-sync: skipped (уже на VPS, vault={VAULT})")
        return
    try:
        candidates = [STATE_FILE, todoist_client.TODO_TASK_MAP_FILE]
        files = [p for p in candidates if p.exists()]
        if not files:
            log("vps-sync: nothing to push (state files not found)")
            return
        if not VPS_SYNC_KEY.exists():
            log(f"vps-sync: ssh key not found: {VPS_SYNC_KEY}")
            return
        cmd = [
            "scp", "-q",
            "-i", str(VPS_SYNC_KEY),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={VPS_SYNC_CONNECT_TIMEOUT}",
            "-o", "StrictHostKeyChecking=accept-new",
            *[str(p) for p in files],
            f"{VPS_SYNC_HOST}:{VPS_SYNC_REMOTE_DIR}",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=VPS_SYNC_TOTAL_TIMEOUT,
        )
        if proc.returncode != 0:
            detail = " ".join((proc.stderr or proc.stdout or "").split())[:200]
            log(f"vps-sync: scp exit {proc.returncode}: {detail}")
            return
        names = ", ".join(p.name for p in files)
        log(f"vps-sync: pushed {names} → {VPS_SYNC_HOST}:{VPS_SYNC_REMOTE_DIR}")
    except subprocess.TimeoutExpired:
        log(f"vps-sync: timeout after {VPS_SYNC_TOTAL_TIMEOUT}s")
    except Exception as exc:
        log(f"vps-sync: {type(exc).__name__}: {exc}")


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
    note_channel: Channel,
    alert_channel: Channel,
    dry_run: bool,
) -> None:
    """note_channel — shareable-ноутс (топик Гермеса или прежний канал),
    alert_channel — технические алерты (⏳/❌), всегда прежний личный чат."""
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
                        send_to(
                            alert_channel,
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
                        send_to(
                            alert_channel,
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

    if dry_run:
        print("--- message (shareable note) ---")
        print(shareable)
        print("--- detail (/meeting) ---")
        print(format_detail_message(summary, entry["date"]))
        return

    try:
        send_to(note_channel, shareable)
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
        print(f"Не удалось сгенерировать детальное саммари (ошибка LLM). Лог: {LOG_FILE}")
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
            sync_state_to_vps()
            return 0

        # Каналы: ноутс — в топик Гермеса (если есть конфиг), алерты — прежний
        # личный чат. Нет одного из каналов → его роль берёт на себя второй.
        note_channel = alert_channel = Channel("", "")
        if not args.dry_run:
            try:
                env = load_env_file(ENV_FILE)
            except Exception as exc:
                env = {}
                log(f"env: {exc}")
            alert_channel = Channel(
                token=env.get("TELEGRAM_BOT_TOKEN", ""),
                chat_id=env.get("TELEGRAM_CHAT_ID", ""),
            )
            delivery = load_delivery_channel()
            note_channel = delivery or alert_channel
            if not alert_channel.ok:
                if not note_channel.ok:
                    log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing in env file")
                    return 1
                log("env: daily-focus.env неполон — алерты уйдут в канал доставки ноутсов")
                alert_channel = note_channel

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
                    send_to(
                        alert_channel,
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
                process_meeting(
                    entry, path, content, date, note_channel, alert_channel, args.dry_run
                )
            except Exception as exc:
                log(f"error processing {path.name}: {exc}")

        if not args.dry_run:
            save_state(state)
            sync_state_to_vps()
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
