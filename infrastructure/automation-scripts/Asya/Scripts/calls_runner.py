#!/usr/bin/env python3
"""Ася: разбор каждого звонка в базы проектов (ADR-033).

Один тик таймера (asya-calls.timer, каждые 10 мин, oneshot):
  1. очередь вопросов о треке (`memory/calls-queue.json` в vault): ответ Антона
     (поле `track`, его пишет Ася в диалоге) -> перезапуск разбора с принудительным
     треком; нет ответа 24 ч -> «мимо», строка в следующий отчёт;
  2. кандидаты: `transcripts/{meeting} *.md` за 7 дней (или `--since`), mtime
     старше 20 мин, нет в state.json. Пустой/короткий транскрипт -> pending_retry
     до 24 ч, потом «нет транскрипта» в отчёт;
  3. `claude -p --model sonnet` с заданием `calls_prompt.md` + пути файлов.
     Модель сама пишет саммари в meetings/ и правки баз (Edit/Write разрешены
     флагами CLI только туда), ставит задачи в Todoist (add_task/set_track) и
     печатает в конце строгий JSON;
  4. код (не модель) шлёт `report_text` Антону токеном Аси, пишет строку в общий
     журнал Аси, `cost_usd` -> log.jsonl, state.json <- done.

Принцип как у Аси: детерминированное — код, суждение — модель. Сети у модели нет.

Запуск: python3 calls_runner.py [--dry-run] [--since YYYY-MM-DD] [--max N]
"""

import argparse
import fcntl
import glob
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asya_bot  # noqa: E402  (права, split_message, разбор CLI, атомарная запись)

logger = logging.getLogger("asya.calls")

MSK = ZoneInfo("Europe/Moscow")

# ---------------------------------------------------------------------------
# Константы и пути (vault-relative)
# ---------------------------------------------------------------------------
TRACKS = ("luch", "zekiro", "producer", "none")
TRACK_RU = {"luch": "Луч", "zekiro": "Зекиро", "producer": "фриланс", "none": "мимо"}
TRACK_ALIASES = {
    "luch": "luch", "луч": "luch", "луч ии": "luch",
    "zekiro": "zekiro", "зекиро": "zekiro",
    "producer": "producer", "фриланс": "producer", "портал": "producer", "portal": "producer",
    "none": "none", "мимо": "none", "личное": "none", "пропуск": "none",
}
CONFIDENCE_MIN = 0.7

TRANSCRIPTS_REL = "transcripts"
TRANSCRIPT_GLOB = "{meeting} *.md"
MEETINGS_REL = "meetings"
TRACK_MAP_REL = "context/{self} {plan} карта треков – 2026-08-19.md"
SUMMARIZER_REL = "system/skills/transcript-summarizer/SKILL.md"
KB_REL = {
    "luch": "tasks/Луч ИИ/{startup} {research} база знаний Луч ИИ – 2026-10-21.md",
    "zekiro": "tasks/Зекиро/{startup} {research} база знаний Зекиро – 2026-10-24.md",
    "producer": "tasks/Фриланс/{startup} {research} база знаний фриланс – 2026-10-24.md",
}
# Правила прав без пробелов (CLI режет списки по пробелам): глобы на файлы баз.
KB_WRITE_GLOBS_REL = (
    "tasks/Луч*/*база*знаний*",
    "tasks/Зекиро/*база*знаний*",
    "tasks/Фриланс/*база*знаний*",
)
QUEUE_REL = "infrastructure/Asya/memory/calls-queue.json"
JOURNAL_REL = "infrastructure/Asya/memory/{self} {research} общая память Аси с Антоном – 2026-10-20.md"

TODOIST_TOOLS = ("todoist_add_task", "todoist_set_track")
AVAILABLE_TOOLS = asya_bot.AVAILABLE_TOOLS  # Read,Glob,Grep,Edit,Write — без Bash/Web

EMPTY_MARKER = "Транскрипт не получен"
MIN_SPEAKER_LINES = 30
SETTLE_MINUTES = 20
WINDOW_DAYS = 7
RETRY_HOURS = 24
QUESTION_HOURS = 24
MAX_ATTEMPTS = 3  # первый прогон + retry ×2
BACKLOG_STANDALONE_HOURS = 6
CLAUDE_TIMEOUT = 1200

SPEAKER_RE = re.compile(r"^\*\*[^*\n]+\|\s*\d{1,2}:\d{2}(?::\d{2})?\*\*\s*$")
NAME_DATE_RE = re.compile(r"–\s*(\d{4}-\d{2}-\d{2})\.md$")
FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

# Статусы state.json
ST_DONE = "done"
ST_RETRY = "pending_retry"  # транскрипта ещё нет
ST_ERROR = "error_retry"  # сбой claude, повтор следующим тиком
ST_TRACK = "pending_track"  # ждём ответа Антона о треке
ST_FAILED = "failed"
ST_NO_TRANSCRIPT = "no_transcript"
FINAL = (ST_DONE, ST_FAILED, ST_NO_TRANSCRIPT)


def _env_int(name, default):
    try:
        value = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass
class Config:
    vault: str = field(default_factory=lambda: os.environ.get("ASYA_VAULT_DIR", "/root/second-brain"))
    home: str = field(default_factory=lambda: os.environ.get("ASYA_CALLS_HOME", "/var/lib/asya-calls"))
    home_dir: str = "/root"  # секреты сервера (.config, .claude, .hermes, .ssh)
    model: str = field(default_factory=lambda: os.environ.get("ASYA_CALLS_MODEL", "sonnet"))
    claude_bin: str = field(default_factory=lambda: os.environ.get("ASYA_CLAUDE_BIN", "/root/.local/bin/claude"))
    max_per_tick: int = field(default_factory=lambda: _env_int("ASYA_CALLS_MAX", 1))
    mcp_config: str = field(default_factory=lambda: os.environ.get("ASYA_CALLS_MCP_CONFIG", ""))
    prompt_file: str = field(default_factory=lambda: os.environ.get("ASYA_CALLS_PROMPT", ""))
    queue_file: str = field(default_factory=lambda: os.environ.get("ASYA_CALLS_QUEUE", ""))
    chatlog_dir: str = field(default_factory=lambda: os.environ.get("ASYA_CHATLOG_DIR", "/var/lib/asya/chatlog"))
    chat_id: str = field(
        default_factory=lambda: (os.environ.get("ASYA_ANTON_CHAT_ID") or os.environ.get("USER_ANTON_ID") or "").strip()
    )

    @property
    def workspace(self):
        return os.path.join(self.home, "workspace")

    @property
    def state_file(self):
        return os.path.join(self.home, "state.json")

    @property
    def log_file(self):
        return os.path.join(self.home, "log.jsonl")

    @property
    def run_log(self):
        return os.path.join(self.home, "runner.log")

    def queue_path(self):
        return self.queue_file or os.path.join(self.vault, QUEUE_REL)

    def journal_path(self):
        return os.path.join(self.vault, JOURNAL_REL)

    def mcp_path(self):
        return self.mcp_config or os.path.join(self.workspace, "mcp.json")

    def prompt_path(self):
        if self.prompt_file:
            return self.prompt_file
        for cand in (os.path.join(HERE, "calls_prompt.md"), os.path.join(HERE, "..", "config", "calls_prompt.md")):
            if os.path.exists(cand):
                return cand
        return os.path.join(HERE, "calls_prompt.md")


def now_utc():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Права: один источник истины для CLI и calls-settings.json
# ---------------------------------------------------------------------------
def write_rules_paths(vault):
    """Абсолютные шаблоны, куда модели разрешены Edit/Write."""
    if " " in vault or "," in vault:
        raise ValueError("путь vault с пробелом/запятой ломает правила CLI: %r" % vault)
    base = asya_bot._abs_rule(vault)
    return ["%s/%s/**" % (base, MEETINGS_REL)] + ["%s/%s" % (base, g) for g in KB_WRITE_GLOBS_REL]


def allowed_tools(vault):
    """Edit/Write — только meetings/ (включая index.md) и три файла баз; Todoist — 2 тулзы.

    Голых Read/Glob/Grep нет (иначе чтение всего диска, см. ARCHITECTURE.md §3).
    """
    rules = []
    for path in write_rules_paths(vault):
        rules += ["Edit(%s)" % path, "Write(%s)" % path]
    return rules + ["mcp__todoist__" + t for t in TODOIST_TOOLS]


def deny_rules(vault, home_dir="/root"):
    """Как у Аси (приватные зоны, секреты, Bash/Web) + календарь целиком."""
    return asya_bot.deny_rules(vault, home_dir) + ["mcp__gcal"]


def settings_payload(vault="/root/second-brain", home_dir="/root"):
    return {"permissions": {"allow": allowed_tools(vault), "deny": deny_rules(vault, home_dir)}}


def build_claude_cmd(prompt, cfg):
    return [
        cfg.claude_bin,
        "-p",
        prompt,
        "--model",
        cfg.model,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "project",
        "--no-session-persistence",
        "--tools",
        AVAILABLE_TOOLS,
        "--allowedTools",
        ",".join(allowed_tools(cfg.vault)),
        "--disallowedTools",
        ",".join(deny_rules(cfg.vault, cfg.home_dir)),
        "--mcp-config",
        cfg.mcp_path(),
        "--strict-mcp-config",
        "--add-dir",
        cfg.vault,
    ]


# ---------------------------------------------------------------------------
# Транскрипты
# ---------------------------------------------------------------------------
def frontmatter(text):
    m = FM_RE.match(text or "")
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def transcript_date(path, text=None):
    m = NAME_DATE_RE.search(os.path.basename(path))
    raw = m.group(1) if m else frontmatter(text or "").get("date")
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.fromtimestamp(os.path.getmtime(path), tz=MSK).date()


def transcript_title(text, fallback=""):
    for line in (text or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def speaker_lines(text):
    """Реплики Krisp `**Имя | 00:33**`; формат без меток — непустые строки тела."""
    body = text or ""
    if "## Транскрипт" in body:
        body = body.split("## Транскрипт", 1)[1]
    count = sum(1 for ln in body.splitlines() if SPEAKER_RE.match(ln.strip()))
    if count:
        return count
    return sum(1 for ln in body.splitlines() if ln.strip() and not ln.startswith("#"))


def transcript_ready(text):
    """(готов?, причина). Пустой Krisp или <30 реплик — ждём."""
    if EMPTY_MARKER in (text or ""):
        return False, "empty"
    n = speaker_lines(text)
    if n < MIN_SPEAKER_LINES:
        return False, "short:%d" % n
    return True, "ok:%d" % n


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def scan_candidates(cfg, state, now, since=None):
    """Файлы на разбор (старые первыми): окно дат, mtime ≥20 мин, не финальные в state."""
    start = since or (now.astimezone(MSK).date() - timedelta(days=WINDOW_DAYS))
    calls = state.get("calls", {})
    done_ids = {v.get("krisp_call_id") for v in calls.values() if v.get("status") in FINAL and v.get("krisp_call_id")}
    out = []
    prefix = TRANSCRIPT_GLOB.split("*", 1)[0]  # "{meeting} " — фигурные скобки glob не любит
    for path in glob.glob(os.path.join(glob.escape(os.path.join(cfg.vault, TRANSCRIPTS_REL)), "*.md")):
        name = os.path.basename(path)
        if not name.startswith(prefix):
            continue
        entry = calls.get(name) or {}
        if entry.get("status") in FINAL or entry.get("status") == ST_TRACK:
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now.timestamp() - mtime < SETTLE_MINUTES * 60:
            continue
        text = read_text(path)
        day = transcript_date(path, text)
        if day < start:
            continue
        call_id = frontmatter(text).get("krisp_call_id") or None
        if call_id and call_id in done_ids:
            continue  # тот же звонок уже разобран под другим именем файла
        out.append({"name": name, "path": path, "date": day, "krisp_call_id": call_id, "text": text})
    out.sort(key=lambda c: (c["date"], c["name"]))
    return out


# ---------------------------------------------------------------------------
# Разбор ответа модели
# ---------------------------------------------------------------------------
def normalize_track(value):
    key = " ".join(str(value or "").strip().lower().split())
    return TRACK_ALIASES.get(key)


def extract_result_json(text):
    """Последний JSON-объект с ключом track в выводе модели (с ```json или без)."""
    if not text:
        return None
    decoder = json.JSONDecoder()
    idx = text.rfind("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text[idx:])
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "track" in obj:
            return obj
        idx = text.rfind("{", 0, idx)
    return None


def validate_result(obj):
    """Нормализованный результат или None, если схема не соблюдена."""
    if not isinstance(obj, dict):
        return None
    track = normalize_track(obj.get("track"))
    try:
        confidence = float(obj.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    report = str(obj.get("report_text") or "").strip()
    return {
        "track": track,
        "confidence": max(0.0, min(1.0, confidence)),
        "secondary_tracks": [t for t in (normalize_track(x) for x in obj.get("secondary_tracks") or []) if t],
        "summary_path": str(obj.get("summary_path") or "").strip(),
        "kb_updates": obj.get("kb_updates") if isinstance(obj.get("kb_updates"), list) else [],
        "tasks": obj.get("tasks") if isinstance(obj.get("tasks"), list) else [],
        "report_text": report,
    }


def claude_cost(stdout):
    try:
        return json.loads(stdout).get("total_cost_usd")
    except (ValueError, AttributeError, TypeError):
        return None


def run_claude(prompt, cfg):
    """(текст, error_kind, cost_usd). error_kind: None | quota | timeout | error."""
    cmd = build_claude_cmd(prompt, cfg)
    env = os.environ.copy()
    env.setdefault("HOME", cfg.home_dir)
    os.makedirs(cfg.workspace, exist_ok=True)
    try:
        proc = subprocess.run(
            cmd, cwd=cfg.workspace, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "", "timeout", None
    except OSError as exc:
        logger.warning("claude не запустился: %s", exc)
        return "", "error", None
    text, kind = asya_bot.parse_claude_output(proc.returncode, proc.stdout, proc.stderr)
    if kind:
        logger.warning("claude %s rc=%s tail=%s", kind, proc.returncode, (proc.stderr or proc.stdout)[-400:])
    return text, kind, claude_cost(proc.stdout)


# ---------------------------------------------------------------------------
# Промпт
# ---------------------------------------------------------------------------
def build_prompt(cfg, cand, forced_track=None, existing_summary=None, now=None):
    try:
        with open(cfg.prompt_path(), "r", encoding="utf-8") as f:
            instructions = f.read().strip()
    except OSError:
        raise RuntimeError("нет файла задания %s" % cfg.prompt_path())
    v = lambda rel: os.path.join(cfg.vault, rel)  # noqa: E731
    local = (now or now_utc()).astimezone(MSK)
    lines = [
        instructions,
        "",
        "## Этот звонок",
        "",
        "- Сейчас: %s MSK." % local.strftime("%Y-%m-%d %H:%M"),
        "- Транскрипт: %s" % cand["path"],
        "- Дата звонка: %s" % cand["date"].isoformat(),
        "- krisp_call_id: %s" % (cand.get("krisp_call_id") or "—"),
        "- Карта треков: %s" % v(TRACK_MAP_REL),
        "- Формат саммари: %s" % v(SUMMARIZER_REL),
        "- Папка саммари: %s/" % v(MEETINGS_REL),
        "- Индекс встреч: %s" % v(os.path.join(MEETINGS_REL, "index.md")),
        "- База Луч (luch): %s" % v(KB_REL["luch"]),
        "- База Зекиро (zekiro): %s" % v(KB_REL["zekiro"]),
        "- База фриланс (producer): %s" % v(KB_REL["producer"]),
    ]
    if forced_track:
        lines.append(
            "- ТРЕК ЗАДАН АНТОНОМ: %s (%s). Не определяй заново, confidence = 1.0."
            % (forced_track, TRACK_RU.get(forced_track, forced_track))
        )
        if existing_summary:
            lines.append(
                "- Саммари уже создано прошлым разбором с track: none: %s — обнови его (Edit: track и «Связи»), второе не создавай."
                % existing_summary
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# State, очередь, журнал, лог
# ---------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def load_state(cfg):
    state = load_json(cfg.state_file, {})
    state.setdefault("calls", {})
    state.setdefault("backlog", [])
    return state


def load_queue(cfg):
    queue = load_json(cfg.queue_path(), {})
    if not isinstance(queue.get("pending"), list):
        queue["pending"] = []
    return queue


def merge_queue(ours, disk):
    """Наша очередь + ответы, которые Ася записала на диск за время тика."""
    on_disk = {q.get("transcript"): q for q in disk.get("pending", []) if isinstance(q, dict)}
    merged = dict(ours)
    items = []
    for item in ours.get("pending", []):
        theirs = on_disk.pop(item.get("transcript"), None)
        if theirs and item.get("status") in (None, "pending") and not item.get("track") and theirs.get("track"):
            item = dict(item, track=theirs["track"])
        items.append(item)
    items += [q for q in on_disk.values()]  # чужие записи не теряем
    merged["pending"] = items
    return merged


def append_log(cfg, record):
    os.makedirs(os.path.dirname(cfg.log_file), exist_ok=True)
    with open(cfg.log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def journal_insert(text, line):
    """Вставить строку сразу под `## Журнал` (над старыми `- ` строками)."""
    lines = text.splitlines()
    try:
        head = next(i for i, ln in enumerate(lines) if ln.strip() == "## Журнал")
    except StopIteration:
        return text.rstrip("\n") + "\n\n## Журнал\n\n" + line + "\n"
    pos = None
    for i in range(head + 1, len(lines)):
        if lines[i].startswith("## "):
            pos = i
            while pos > head + 1 and not lines[pos - 1].strip():
                pos -= 1
            break
        if lines[i].startswith("- "):
            pos = i
            break
    if pos is None:
        pos = len(lines)
        while pos > head + 1 and not lines[pos - 1].strip():
            pos -= 1
    lines.insert(pos, line)
    return "\n".join(lines) + "\n"


def append_journal(cfg, what, now):
    path = cfg.journal_path()
    if not os.path.exists(path):
        logger.warning("нет общего журнала %s — строку не пишу", path)
        return False
    stamp = now.astimezone(MSK).strftime("%Y-%m-%d %H:%M")
    line = "- %s · tg · %s" % (stamp, " ".join(str(what).split()))
    tmp = path + ".tmp-calls"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(journal_insert(read_text(path), line))
    os.replace(tmp, path)
    return True


def chatlog_note(cfg, text, now):
    """Вопрос/отчёт — в чатлог дня Аси, чтобы диалоговая Ася видела, о чём спросили."""
    try:
        line = asya_bot.format_chatlog_line("Ася [разбор звонков]", text, dt=now)
        asya_bot.append_chatlog([line], dt=now, chatlog_dir=cfg.chatlog_dir)
    except OSError as exc:
        logger.warning("чатлог Аси не записан: %s", exc)


# ---------------------------------------------------------------------------
# Telegram (токен из env, не печатается)
# ---------------------------------------------------------------------------
def send_telegram(cfg, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not cfg.chat_id:
        logger.warning("нет TELEGRAM_BOT_TOKEN или chat id — отчёт не отправлен")
        return False
    import httpx  # лениво: тестам и dry-run не нужен

    ok = True
    for chunk in asya_bot.split_message(text):
        try:
            resp = httpx.post(
                "https://api.telegram.org/bot%s/sendMessage" % token,
                json={"chat_id": cfg.chat_id, "text": chunk, "disable_web_page_preview": True},
                timeout=30,
            )
            ok = ok and resp.status_code == 200
            if resp.status_code != 200:
                logger.warning("Telegram ответил %s", resp.status_code)
        except Exception as exc:  # noqa: BLE001 - в тексте исключения может быть URL с токеном
            logger.warning("Telegram: сбой отправки (%s)", type(exc).__name__)
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Тик
# ---------------------------------------------------------------------------
QUESTION_TAIL = "К какому проекту звонок? Ответь мне одним словом: Луч / Зекиро / фриланс / мимо."


def _short(name):
    return re.sub(r"\.md$", "", name)


class Tick:
    """Один прогон. claude_fn(prompt, cfg) -> (text, kind, cost); send_fn(cfg, text) -> bool."""

    def __init__(self, cfg, now=None, dry_run=False, since=None, max_calls=None, claude_fn=None, send_fn=None):
        self.cfg = cfg
        self.now = now or now_utc()
        self.dry = dry_run
        self.since = since
        self.max = max_calls if max_calls is not None else cfg.max_per_tick
        self.claude_fn = claude_fn or run_claude
        self.send_fn = send_fn or send_telegram
        self.state = load_state(cfg)
        self.queue = load_queue(cfg)
        self.queue_dirty = False
        self.used = 0
        self.actions = []  # для dry-run и отчёта о тике

    # --- вывод ---------------------------------------------------------------
    def plan(self, msg):
        self.actions.append(msg)
        if self.dry:
            print(msg)

    def entry(self, name):
        return self.state["calls"].setdefault(name, {})

    def backlog(self, line):
        self.state["backlog"].append({"at": _iso(self.now), "text": line})

    def take_backlog(self):
        lines = [b["text"] for b in self.state["backlog"]]
        self.state["backlog"] = []
        return lines

    def send(self, text, with_backlog=True):
        extra = self.take_backlog() if with_backlog else []
        if extra:
            text = text.rstrip() + "\n\nТакже:\n" + "\n".join("- " + x for x in extra)
        ok = self.send_fn(self.cfg, text)
        chatlog_note(self.cfg, text, self.now)
        return ok

    # --- шаги ---------------------------------------------------------------
    def run(self):
        self.process_queue()
        self.process_candidates()
        self.flush_old_backlog()
        if not self.dry:
            asya_bot.write_json_atomic(self.cfg.state_file, self.state)
            if self.queue_dirty:
                # Ася могла вписать ответ Антона, пока шёл тик: перечитать и не затереть
                merged = merge_queue(self.queue, load_queue(self.cfg))
                asya_bot.write_json_atomic(self.cfg.queue_path(), merged)
        return self.actions

    def process_queue(self):
        for item in self.queue["pending"]:
            if item.get("status") not in (None, "pending"):
                continue
            name = item.get("transcript") or item.get("id")
            track = normalize_track(item.get("track")) if item.get("track") else None
            asked = _parse_iso(item.get("asked_at")) or self.now
            if track == "none":
                self.plan("очередь: %s -> мимо (ответ Антона)" % name)
                if not self.dry:
                    item.update(status="resolved", resolved_at=_iso(self.now), track="none")
                    self.queue_dirty = True
                    self.entry(name).update(status=ST_DONE, track="none", updated_at=_iso(self.now))
                    append_journal(self.cfg, "📞 звонок «%s»: мимо (ответ Антона)" % _short(name), self.now)
            elif track:
                if self.used >= self.max:
                    self.plan("очередь: %s -> %s, перезапуск в следующем тике (лимит)" % (name, track))
                    continue
                path = os.path.join(self.cfg.vault, TRANSCRIPTS_REL, name)
                text = read_text(path)
                if not text:
                    self.plan("очередь: %s — транскрипт пропал, закрываю" % name)
                    if not self.dry:
                        item.update(status="resolved", resolved_at=_iso(self.now))
                        self.queue_dirty = True
                        self.entry(name).update(status=ST_FAILED, updated_at=_iso(self.now))
                    continue
                cand = {
                    "name": name, "path": path, "date": transcript_date(path, text),
                    "krisp_call_id": frontmatter(text).get("krisp_call_id"), "text": text,
                }
                self.plan("очередь: %s -> перезапуск разбора с треком %s" % (name, track))
                if not self.dry:
                    resolved = self.analyze(cand, forced_track=track, existing_summary=item.get("summary_path"))
                    if resolved:
                        item.update(status="resolved", resolved_at=_iso(self.now))
                        self.queue_dirty = True
            elif self.now - asked >= timedelta(hours=QUESTION_HOURS):
                self.plan("очередь: %s — нет ответа 24 ч -> мимо" % name)
                if not self.dry:
                    item.update(status="expired", resolved_at=_iso(self.now), track="none")
                    self.queue_dirty = True
                    self.entry(name).update(status=ST_DONE, track="none", updated_at=_iso(self.now))
                    self.backlog("«%s»: ответа о проекте не было 24 ч, считаю мимо" % _short(name))

    def process_candidates(self):
        queued = {q.get("transcript") for q in self.queue["pending"] if q.get("status") in (None, "pending")}
        for cand in scan_candidates(self.cfg, self.state, self.now, self.since):
            name = cand["name"]
            if name in queued:
                continue  # ждёт ответа о треке — разбирается только из очереди
            ready, why = transcript_ready(cand["text"])
            entry = self.state["calls"].get(name) or {}
            if not ready:
                first = _parse_iso(entry.get("first_seen")) or self.now
                if self.now - first >= timedelta(hours=RETRY_HOURS):
                    self.plan("%s: нет транскрипта 24 ч (%s) -> в отчёт" % (name, why))
                    if not self.dry:
                        self.entry(name).update(status=ST_NO_TRANSCRIPT, updated_at=_iso(self.now))
                        self.backlog("«%s»: не разобран, нет транскрипта" % _short(name))
                else:
                    self.plan("%s: транскрипт не готов (%s), жду" % (name, why))
                    if not self.dry:
                        e = self.entry(name)
                        e.setdefault("first_seen", _iso(self.now))
                        e.update(status=ST_RETRY, krisp_call_id=cand["krisp_call_id"], updated_at=_iso(self.now))
                continue
            if self.used >= self.max:
                self.plan("%s: в очереди на разбор (лимит %d за тик)" % (name, self.max))
                continue
            self.plan("%s: разбор (%s, %s)" % (name, cand["date"].isoformat(), why))
            if not self.dry:
                self.analyze(cand)
            else:
                self.used += 1

    def analyze(self, cand, forced_track=None, existing_summary=None):
        """Один вызов модели. True — звонок закрыт (done/вопрос задан/failed)."""
        self.used += 1
        name = cand["name"]
        entry = self.entry(name)
        entry.setdefault("first_seen", _iso(self.now))
        entry["krisp_call_id"] = cand.get("krisp_call_id")
        attempt = int(entry.get("attempts") or 0) + 1
        entry["attempts"] = attempt
        started = time.monotonic()
        prompt = build_prompt(self.cfg, cand, forced_track, existing_summary, self.now)
        text, kind, cost = self.claude_fn(prompt, self.cfg)
        result = None
        if not kind:
            result = validate_result(extract_result_json(text))
            if not result or not result["report_text"] or (forced_track is None and result["track"] is None):
                kind, result = "bad_json", None
        log = {
            "ts": _iso(self.now), "transcript": name, "attempt": attempt, "model": self.cfg.model,
            "forced_track": forced_track, "error": kind, "cost_usd": cost,
            "duration_s": round(time.monotonic() - started, 1),
        }
        if kind:
            append_log(self.cfg, log)
            if attempt >= MAX_ATTEMPTS:
                entry.update(status=ST_FAILED, error=kind, updated_at=_iso(self.now))
                self.send("Не смогла разобрать звонок «%s» (%s, попыток %d). Посмотри руками." % (_short(name), kind, attempt))
                return True
            entry.update(status=ST_ERROR, error=kind, updated_at=_iso(self.now))
            return False

        track = forced_track or result["track"]
        log.update(track=track, confidence=result["confidence"], summary_path=result["summary_path"],
                   kb_updates=len(result["kb_updates"]), tasks=len(result["tasks"]))
        append_log(self.cfg, log)
        entry.pop("error", None)

        if forced_track is None and (result["confidence"] < CONFIDENCE_MIN or track is None):
            entry.update(status=ST_TRACK, track=None, summary_path=result["summary_path"], attempts=0,
                         updated_at=_iso(self.now))
            self.queue["pending"] = [q for q in self.queue["pending"] if q.get("transcript") != name]
            self.queue["pending"].append({
                "id": name, "transcript": name, "title": transcript_title(cand["text"], _short(name)),
                "date": cand["date"].isoformat(), "summary_path": result["summary_path"],
                "guess": result["track"], "confidence": result["confidence"],
                "asked_at": _iso(self.now), "status": "pending", "track": None,
            })
            self.queue_dirty = True
            self.send(result["report_text"].rstrip() + "\n\n" + QUESTION_TAIL, with_backlog=False)
            append_journal(self.cfg, "📞 звонок «%s»: не уверена в проекте, спросила Антона" % _short(name), self.now)
            return True

        entry.update(status=ST_DONE, track=track, summary_path=result["summary_path"], updated_at=_iso(self.now))
        self.send(result["report_text"])
        summary = os.path.basename(result["summary_path"]).replace(".md", "") if result["summary_path"] else ""
        what = "📞 разбор звонка «%s»: %s" % (_short(name), TRACK_RU.get(track, track))
        if summary:
            what += ", саммари [[%s]]" % summary
        if result["kb_updates"]:
            what += ", в базу %d" % len(result["kb_updates"])
        if result["tasks"]:
            what += ", задач %d" % len(result["tasks"])
        append_journal(self.cfg, what, self.now)
        return True

    def flush_old_backlog(self):
        """Строки «мимо»/«нет транскрипта» ждут отчёта, но не дольше 6 ч."""
        items = self.state["backlog"]
        if not items:
            return
        oldest = min((_parse_iso(b.get("at")) or self.now) for b in items)
        if self.now - oldest < timedelta(hours=BACKLOG_STANDALONE_HOURS):
            return
        self.plan("отдельное сообщение: %d строк без отчёта" % len(items))
        if not self.dry:
            lines = self.take_backlog()
            self.send("Разбор звонков:\n" + "\n".join("- " + x for x in lines), with_backlog=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def setup_logging(cfg):  # pragma: no cover - побочный эффект
    os.makedirs(cfg.home, exist_ok=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # в URL лежит токен
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(cfg.run_log), logging.StreamHandler(sys.stdout)],
    )


def main(argv=None):  # pragma: no cover - рантайм
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="только показать кандидатов и план, без claude/записей/отправок")
    ap.add_argument("--since", help="бэкфилл: брать транскрипты с даты YYYY-MM-DD (вместо 7 дней)")
    ap.add_argument("--max", type=int, help="сколько звонков разбирать за тик (дефолт ASYA_CALLS_MAX или 1)")
    args = ap.parse_args(argv)
    cfg = Config()
    since_raw = args.since or os.environ.get("ASYA_CALLS_SINCE", "").strip()  # бэкфилл и через drop-in юнита
    since = date.fromisoformat(since_raw) if since_raw else None
    if args.dry_run:
        logging.basicConfig(level=logging.WARNING)
        print("dry-run: vault=%s, модель=%s, лимит за тик=%s" % (cfg.vault, cfg.model, args.max or cfg.max_per_tick))
        actions = Tick(cfg, dry_run=True, since=since, max_calls=args.max).run()
        if not actions:
            print("кандидатов нет")
        return 0
    setup_logging(cfg)
    os.makedirs(cfg.home, exist_ok=True)
    with open(os.path.join(cfg.home, ".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.info("другой тик ещё идёт — выхожу")
            return 0
        actions = Tick(cfg, since=since, max_calls=args.max).run()
    logger.info("тик: %d действий", len(actions))
    for a in actions:
        logger.info("  %s", a)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
