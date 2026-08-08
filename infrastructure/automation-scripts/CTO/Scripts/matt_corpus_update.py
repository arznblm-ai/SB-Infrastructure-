#!/usr/bin/env python3
"""
matt_corpus_update.py — еженедельное автообновление корпуса личного CTO Мэта.

Раз в неделю (вс 20:00 MSK, systemd-таймер на VPS) смотрит два источника:

  1. YouTube-канал @mattpocockuk — RSS, только НОВЫЕ long-form видео (≥180 с;
     шортсы и стримы пропускаются). Субтитры тянутся тем же кодом, что и ручной
     ингест (`system/skills/youtube-transcribe/scripts/fetch_youtube_subs.py`),
     заметка ложится в `transcripts/external resources/Matt Pocock/`.
  2. GitHub `mattpocock/skills` — коммиты с последнего виденного sha + новые
     секции CHANGELOG.md (он объясняет «почему»).

Если появилось новое — ОДИН вызов `claude -p --model sonnet` дистиллирует это в
стиле ядра доктрины и результат добавляется датированной секцией в дельта-файл
`infrastructure/CTO/Matt Pocock/{self} {research} Мэт Покок дельта доктрины – 2026-08-08.md`.
Ядро доктрины скрипт НЕ трогает: консолидация дельты в ядро — ручная команда
Антона «/matt консолидируй дельту» (решение грилинг-сессии 2026-08-08).

Отчёт — одним сообщением в главный чат Гермеса (без топика). Нет новостей за
неделю → сообщение не шлётся вообще.

Контракт надёжности: best-effort. Сбой любого источника → строка в лог и (если
есть что слать) строка в отчёт; наружу исключения не отдаются, ретраев нет,
выход всегда 0.

Кроссплатформенно (мак + VPS): корень vault — env `SECOND_BRAIN_VAULT`, иначе
/root/second-brain, иначе — расположение самого скрипта.

Заметка про имена файлов: заметки видео пишет канонический билдер Link Inbox
(тот же, что писал остальные 41 заметку папки), поэтому имя — `{self} {transcript}
<название> – YYYY-MM-DD.md` и frontmatter `type: external-resource`. Плановый
вариант `{lab} {transcript} Matt Pocock …` намеренно не используется: он бы
разошёлся с существующим корпусом и индексом external resources.

Запуск:
    python3 matt_corpus_update.py              # боевой прогон
    python3 matt_corpus_update.py --dry-run    # всё собрать и напечатать, ничего не менять
    python3 matt_corpus_update.py --bootstrap  # засеять state текущим состоянием, ничего не слать

Лог:   ~/Library/Logs/matt-corpus-update.log (мак) / ~/.local/share/... (VPS)
State: ~/.config/second-brain/matt-corpus-state.json
Доставка: ~/.config/second-brain/matt-corpus-delivery.env (пишет ранбук deploy/)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple, Optional

# ── Пути ──────────────────────────────────────────────────────────────────


def _vault_root() -> Path:
    """Корень vault: env → зеркало на VPS → расположение скрипта (мак)."""
    override = os.environ.get("SECOND_BRAIN_VAULT")
    if override:
        return Path(override).expanduser()
    vps = Path("/root/second-brain")
    if vps.is_dir():
        return vps
    # <vault>/infrastructure/CTO/Scripts/matt_corpus_update.py → parents[3]
    return Path(__file__).resolve().parents[3]


def _log_path() -> Path:
    """~/Library/Logs на маке, ~/.local/share на Linux (VPS)."""
    mac_logs = Path.home() / "Library" / "Logs"
    if mac_logs.is_dir():
        return mac_logs / "matt-corpus-update.log"
    linux_logs = Path.home() / ".local" / "share"
    try:
        linux_logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path("/tmp/matt-corpus-update.log")
    return linux_logs / "matt-corpus-update.log"


VAULT = _vault_root()
LOG_FILE = _log_path()

STATE_DIR = Path.home() / ".config" / "second-brain"
STATE_FILE = STATE_DIR / "matt-corpus-state.json"

# Маркер контракта доставки — его грепает ранбук deploy/apply_matt_corpus_vps.sh.
# Не удалять и не переименовывать без правки ранбука.
DELIVERY_CONTRACT = "matt-corpus-delivery-v1"
DELIVERY_ENV_FILE = STATE_DIR / "matt-corpus-delivery.env"

NOTES_DIR = VAULT / "transcripts" / "external resources" / "Matt Pocock"
DELTA_FILE = (
    VAULT
    / "infrastructure"
    / "CTO"
    / "Matt Pocock"
    / "{self} {research} Мэт Покок дельта доктрины – 2026-08-08.md"
)
YT_SUBS_SCRIPTS = VAULT / "system" / "skills" / "youtube-transcribe" / "scripts"
LINK_INBOX_SCRIPTS = VAULT / "infrastructure" / "Link Inbox" / "Scripts"

# ── Источники ─────────────────────────────────────────────────────────────

# Канал @mattpocockuk. ID снят 2026-08-08 через yt-dlp с видео gaDdrDdczO4
# (`channel_id`); RSS работает только по channel_id, handle он не принимает.
CHANNEL_ID = "UCswG6FSbgZjbWtdf_hMLaow"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"

GITHUB_REPO = "mattpocock/skills"
GITHUB_BRANCH = "main"
GITHUB_COMMITS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits"
CHANGELOG_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{{ref}}/CHANGELOG.md"

USER_AGENT = "matt-corpus-update/1.0 (+second-brain vault automation)"

# ── Пороги ────────────────────────────────────────────────────────────────

MIN_DURATION_SEC = 180          # long-form: короче — шортс, в корпус не идёт

# Наборы player_client для yt-dlp, по порядку. Первый — как у ручного ингеста;
# остальные — фолбэк: 08.08.2026 web-клиенты возвращали пустой список
# automatic_captions, а ios/android/tv отдавали субтитры нормально.
CAPTION_CLIENTS = (["web_safari", "mweb"], ["ios"], ["android"], ["tv"])
# Заведомо длинное видео канала — на нём ранбук проверяет, отдаёт ли YouTube
# субтитры с этой машины (VPS сидит на датацентровом IP, его могут резать).
SMOKE_VIDEO_ID = "gaDdrDdczO4"

MAX_VIDEOS_PER_RUN = 6          # чтобы не долбить YouTube после долгого простоя
MAX_VIDEO_FAILURES = 3          # после N неудач видео помечается виденным
TRANSCRIPT_CHARS_FOR_LLM = 15_000
GITHUB_FIRST_RUN_DAYS = 14      # окно, если last_commit_date ещё нет
DELTA_LINE_LIMIT = 200          # превышение = сигнал «пора консолидировать»
SEEN_IDS_KEEP = 120

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local" / "bin" / "claude"))
CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT = 600

TG_DEFAULT_CHAT_ID = "324186708"   # главный чат Гермеса, БЕЗ топика
TG_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

TG_SPLIT_MARKER = "---TG---"


# ── Лог ───────────────────────────────────────────────────────────────────


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass  # лог не должен ронять прогон


# ── State ─────────────────────────────────────────────────────────────────


def load_state() -> dict:
    default = {
        "last_video_ids": [],
        "last_commit_sha": "",
        "last_commit_date": "",
        "video_failures": {},
        "last_run": "",
        "last_report_at": "",
    }
    if not STATE_FILE.exists():
        return default
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"state: {STATE_FILE} не читается ({type(exc).__name__}: {exc}) → начинаю с пустого")
        return default
    if not isinstance(data, dict):
        return default
    for key, value in default.items():
        data.setdefault(key, value)
    return data


def save_state(state: dict) -> None:
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["last_video_ids"] = state["last_video_ids"][-SEEN_IDS_KEEP:]
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        log(f"state: не сохранён ({type(exc).__name__}: {exc})")


# ── HTTP ──────────────────────────────────────────────────────────────────


def http_get(url: str, *, timeout: int = 30, accept: Optional[str] = None) -> str:
    """GET через curl, а не urllib.

    Системный python3 на маке ходит без CA-бандла (CERTIFICATE_VERIFY_FAILED на
    youtube.com и api.github.com), curl же работает на обеих машинах и уже нужен
    для Telegram. Один транспорт — одна точка отказа.
    """
    command = [
        "curl", "-sSL", "--max-time", str(timeout),
        "-w", "\n%{http_code}",
        "-H", f"User-Agent: {USER_AGENT}",
    ]
    if accept:
        command.extend(["-H", f"Accept: {accept}"])
    command.append(url)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"curl timeout {timeout + 15} с: {url}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    body, _, status = proc.stdout.rpartition("\n")
    if not status.startswith("2"):
        # Тело ответа важнее кода: GitHub кладёт в него «API rate limit exceeded».
        raise RuntimeError(f"HTTP {status.strip() or '?'}: {body.strip()[:200]}")
    return body


# ── YouTube ───────────────────────────────────────────────────────────────

ATOM = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"


class FeedEntry(NamedTuple):
    video_id: str
    title: str
    published: str  # YYYY-MM-DD

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def fetch_feed() -> list[FeedEntry]:
    """Свежие ролики канала из RSS (обычно 15 штук, новые сверху)."""
    raw = http_get(RSS_URL)
    root = ET.fromstring(raw)
    entries: list[FeedEntry] = []
    for node in root.findall(f"{ATOM}entry"):
        video_id = (node.findtext(f"{YT_NS}videoId") or "").strip()
        if not video_id:
            continue
        title = " ".join((node.findtext(f"{ATOM}title") or "").split())
        published = (node.findtext(f"{ATOM}published") or "")[:10]
        entries.append(FeedEntry(video_id, title or video_id, published))
    return entries


def load_subs_module():
    """Модуль ручного ингеста субтитров — переиспользуем его целиком.

    На VPS корень vault другой, поэтому подменяем его константу пути к билдеру
    заметок: без этого load_note_builder() ищет Link Inbox по мак-пути.
    """
    if str(YT_SUBS_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(YT_SUBS_SCRIPTS))
    import fetch_youtube_subs as fys  # noqa: WPS433

    fys.LINK_INBOX_SCRIPTS = LINK_INBOX_SCRIPTS
    return fys


def subs_options(fys, clients: list[str]) -> dict:
    opts = dict(fys.BASE_YDL_OPTS)
    opts["sleep_interval_requests"] = 1
    opts["extractor_args"] = {"youtube": {"player_client": list(clients)}}
    return opts


def transcript_of(fys, info: dict, opts: dict) -> tuple[str, str]:
    """(lang, transcript) из авто-субтитров. Одна повторная попытка на 429."""
    namespace = SimpleNamespace(
        langs=["en-orig", "en"], paragraph_gap=1.5, max_paragraph_chars=600
    )
    attempt = 0
    while True:
        try:
            lang, url = fys.pick_caption_track(info, namespace.langs)
            vtt = fys.download_vtt(url, opts)
            cues = fys.dedupe_cues(fys.parse_vtt_cues(vtt))
            if not cues:
                raise RuntimeError("caption track parsed to zero lines")
            paragraphs = fys.build_paragraphs(
                cues, namespace.paragraph_gap, namespace.max_paragraph_chars
            )
            return lang, fys.render_transcript(paragraphs)
        except Exception as exc:  # noqa: BLE001 — yt-dlp кидает что угодно
            message = f"{type(exc).__name__}: {exc}"
            if attempt or not fys.is_rate_limited(message):
                raise RuntimeError(message) from exc
            attempt += 1
            log(f"youtube: rate limit на {info.get('id')} — пауза 60 с и одна повторная попытка")
            time.sleep(60)


class Probe(NamedTuple):
    info: Optional[dict]
    lang: Optional[str]
    transcript: Optional[str]
    error: str


def probe_video(fys, video_id: str) -> Probe:
    """Метаданные + субтитры, перебирая player_client, пока трек не найдётся.

    Первым идёт набор ручного ингеста (web_safari/mweb), дальше — мобильные и
    tv-клиенты: 08.08.2026 YouTube отдавал `automatic_captions` только им, а
    web-клиентам возвращал пустой список. Шортсы и стримы отсекаются по первому
    же успешному extract_info, до перебора остальных клиентов.
    """
    first_info: Optional[dict] = None
    problems: list[str] = []

    for clients in CAPTION_CLIENTS:
        label = "/".join(clients)
        opts = subs_options(fys, clients)
        try:
            info = fys.fetch_info(video_id, opts)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

        if first_info is None:
            first_info = info
            duration = int(info.get("duration") or 0)
            if info.get("was_live") or info.get("is_live") or duration < MIN_DURATION_SEC:
                return Probe(info, None, None, "")  # шортс/стрим — субтитры не нужны

        try:
            lang, transcript = transcript_of(fys, info, opts)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: {exc}")
            continue
        return Probe(info, lang, transcript, "")

    return Probe(first_info, None, None, "; ".join(problems)[:300])


class VideoResult(NamedTuple):
    video_id: str
    title: str
    published: str
    url: str
    duration: int
    transcript: str
    note_path: Optional[Path]


def collect_videos(state: dict, args: argparse.Namespace) -> tuple[list[VideoResult], list[str], list[str]]:
    """Новые long-form видео с транскриптами.

    Возвращает (обработанные, строки-заметки для отчёта/лога, ошибки).
    Побочно двигает state: пропущенные (шортсы/стримы) и обработанные видео
    помечаются виденными, упавшие — нет (до MAX_VIDEO_FAILURES попыток).
    """
    notes: list[str] = []
    errors: list[str] = []

    try:
        feed = fetch_feed()
    except Exception as exc:
        errors.append(f"YouTube RSS недоступен ({type(exc).__name__}: {exc})")
        log(f"youtube: RSS упал: {type(exc).__name__}: {exc}")
        return [], notes, errors

    seen = set(state["last_video_ids"])
    fresh = [entry for entry in feed if entry.video_id not in seen]
    notes.append(f"в RSS {len(feed)} роликов, новых с прошлого прогона {len(fresh)}")
    if not fresh:
        return [], notes, errors

    if len(fresh) > args.max_videos:
        notes.append(
            f"беру {args.max_videos} из {len(fresh)} новых — остальные подхватит следующий прогон"
        )
        fresh = fresh[: args.max_videos]

    try:
        fys = load_subs_module()
    except Exception as exc:
        errors.append(f"не загрузился модуль субтитров ({type(exc).__name__}: {exc})")
        log(f"youtube: import fetch_youtube_subs упал: {type(exc).__name__}: {exc}")
        return [], notes, errors

    build_note = None
    results: list[VideoResult] = []

    for index, entry in enumerate(fresh, start=1):
        probe = probe_video(fys, entry.video_id)
        info = probe.info
        if info is None:
            errors.append(f"{entry.title[:60]}: метаданные не получены ({probe.error[:120]})")
            _register_failure(state, entry.video_id, probe.error)
            continue

        duration = int(info.get("duration") or 0)
        title = fys.clean_title(info.get("title") or entry.title)

        if info.get("was_live") or info.get("is_live"):
            notes.append(f"пропуск (стрим): {title}")
            _mark_seen(state, entry.video_id)
            continue
        if duration < MIN_DURATION_SEC:
            notes.append(f"пропуск (шортс {duration} с): {title}")
            _mark_seen(state, entry.video_id)
            continue

        lang, transcript = probe.lang, probe.transcript
        if not transcript:
            errors.append(f"{title[:60]}: субтитры не получены ({probe.error[:120]})")
            _register_failure(state, entry.video_id, probe.error)
            continue

        note_path: Optional[Path] = None
        if not args.dry_run:
            try:
                if build_note is None:
                    build_note = fys.load_note_builder()
                note_path = fys.write_note(build_note, NOTES_DIR, info, lang, transcript)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                errors.append(f"{title[:60]}: заметка не записана ({reason[:120]})")
                log(f"youtube: write_note упал на {entry.video_id}: {reason}")

        results.append(
            VideoResult(
                video_id=entry.video_id,
                title=title,
                published=fys.normalize_date(info.get("upload_date")) or entry.published,
                url=info.get("webpage_url") or entry.url,
                duration=duration,
                transcript=transcript,
                note_path=note_path,
            )
        )
        _mark_seen(state, entry.video_id)
        state["video_failures"].pop(entry.video_id, None)
        notes.append(
            f"long-form {duration // 60} мин, субтитры {lang}, {len(transcript)} симв: {title}"
        )
        if index < len(fresh):
            time.sleep(5)

    return results, notes, errors


def _mark_seen(state: dict, video_id: str) -> None:
    if video_id not in state["last_video_ids"]:
        state["last_video_ids"].append(video_id)


def _register_failure(state: dict, video_id: str, reason: str) -> None:
    """Не помечаем виденным — попробуем на следующей неделе. Но не вечно."""
    count = int(state["video_failures"].get(video_id, 0)) + 1
    state["video_failures"][video_id] = count
    log(f"youtube: {video_id} неудача {count}/{MAX_VIDEO_FAILURES}: {reason[:200]}")
    if count >= MAX_VIDEO_FAILURES:
        _mark_seen(state, video_id)
        log(f"youtube: {video_id} сдаюсь после {count} попыток, помечаю виденным")


# ── GitHub ────────────────────────────────────────────────────────────────


class GithubResult(NamedTuple):
    commits: list[dict]          # [{sha, date, message}]
    head_sha: str
    head_date: str
    changelog_sections: list[str]


def fetch_commits(state: dict) -> tuple[list[dict], str, str]:
    since = state.get("last_commit_date") or (
        datetime.now(timezone.utc) - timedelta(days=GITHUB_FIRST_RUN_DAYS)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    url = f"{GITHUB_COMMITS_URL}?sha={GITHUB_BRANCH}&per_page=100&since={since}"
    payload = json.loads(http_get(url, accept="application/vnd.github+json"))
    if not isinstance(payload, list):
        raise RuntimeError(f"неожиданный ответ GitHub API: {str(payload)[:200]}")

    if len(payload) >= 100:
        # Страница GitHub кончилась — за окном может быть ещё история. При
        # недельном ритме это не наступает; наступило — значит, был долгий простой.
        log("github: страница из 100 коммитов заполнена, часть истории могла не влезть")

    known = state.get("last_commit_sha") or ""
    commits: list[dict] = []
    for item in payload:  # newest first
        sha = item.get("sha") or ""
        if known and sha == known:
            break
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        commits.append(
            {
                "sha": sha,
                "date": author.get("date") or "",
                "message": " ".join((commit.get("message") or "").splitlines()[:1]).strip(),
            }
        )
    head_sha = (payload[0].get("sha") or "") if payload else known
    head_date = ""
    if payload:
        head_date = ((payload[0].get("commit") or {}).get("author") or {}).get("date") or ""
    return commits, head_sha, head_date


def split_changelog(text: str) -> list[tuple[str, str]]:
    """CHANGELOG → [(заголовок версии, тело секции)] сверху вниз."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading:
                sections.append((heading, "\n".join(buffer).strip()))
            heading = line[3:].strip()
            buffer = []
        elif heading:
            buffer.append(line)
    if heading:
        sections.append((heading, "\n".join(buffer).strip()))
    return sections


def fetch_changelog_delta(state: dict) -> list[str]:
    """Новые версии CHANGELOG: секции, которых не было на прошлом виденном sha."""
    current = split_changelog(http_get(CHANGELOG_RAW.format(ref=GITHUB_BRANCH)))
    if not current:
        return []
    old_headings: set[str] = set()
    previous_sha = state.get("last_commit_sha") or ""
    if previous_sha:
        try:
            old = split_changelog(http_get(CHANGELOG_RAW.format(ref=previous_sha)))
            old_headings = {heading for heading, _ in old}
        except Exception as exc:
            log(f"github: старый CHANGELOG ({previous_sha[:8]}) не получен: {type(exc).__name__}: {exc}")
    if not old_headings:
        # Сравнивать не с чем — берём только верхнюю (самую свежую) секцию.
        heading, body = current[0]
        return [f"## {heading}\n\n{body}".strip()]
    return [
        f"## {heading}\n\n{body}".strip()
        for heading, body in current
        if heading not in old_headings
    ]


def collect_github(state: dict) -> tuple[Optional[GithubResult], list[str], list[str]]:
    notes: list[str] = []
    errors: list[str] = []
    try:
        commits, head_sha, head_date = fetch_commits(state)
    except Exception as exc:
        errors.append(f"GitHub API недоступен ({type(exc).__name__}: {exc})")
        log(f"github: коммиты не получены: {type(exc).__name__}: {exc}")
        return None, notes, errors

    sections: list[str] = []
    if commits:
        try:
            sections = fetch_changelog_delta(state)
        except Exception as exc:
            errors.append(f"CHANGELOG не получен ({type(exc).__name__}: {exc})")
            log(f"github: CHANGELOG не получен: {type(exc).__name__}: {exc}")

    notes.append(
        f"коммитов с прошлого прогона {len(commits)}, новых секций CHANGELOG {len(sections)}"
    )
    return GithubResult(commits, head_sha, head_date, sections), notes, errors


MERGE_RE = re.compile(r"^Merge (pull request|branch|remote)", re.I)
NOISE_RE = re.compile(r"^(chore: version skills|Version Packages)$", re.I)


def meaningful_commits(commits: list[dict]) -> list[str]:
    """Сообщения без merge-шума — то, что реально описывает изменение."""
    out: list[str] = []
    for commit in commits:
        message = commit["message"]
        if not message or MERGE_RE.match(message) or NOISE_RE.match(message):
            continue
        if message not in out:
            out.append(message)
    return out


# ── Дистилляция ───────────────────────────────────────────────────────────


PROMPT_HEADER = """Ты дистиллируешь свежие материалы Мэта Покока (Matt Pocock) в дельту его доктрины
AI-инжиниринга. Дельта лежит рядом с ядром корпуса и читается агентом-CTO при старте сессии.

Стиль ядра, которому нужно следовать:
- по-русски, сжато, без воды и без пересказа «о чём видео»;
- единица разбора — ТЕЗИС + МЕХАНИЗМ: что он утверждает и почему это работает
  (без механизма тезис превращается в карго-культ, против которого он сам выступает);
- 1–2 короткие verbatim-цитаты на весь ответ, каждая с пометкой [авто-субтитры] и названием
  видео; цитата иллюстрирует механизм, а не заменяет его;
- если новое противоречит прежней его позиции — назвать это разворотом прямо;
- количественные заявления помечать как его личную оценку, а не измерение;
- никаких выдумок: чего нет в материалах ниже — того не пишем.

Формат ответа (без преамбулы, без «Вот дистилляция»):
1) Сначала тело секции дельты в markdown: подзаголовки `### ` по темам, под ними тезисы.
2) Затем строка-разделитель ровно `{marker}`.
3) После неё — 3–5 буллетов (`- `) для короткого отчёта в мессенджер: самая суть, одна строка на буллет.
"""


def build_prompt(videos: list[VideoResult], github: Optional[GithubResult]) -> str:
    parts = [PROMPT_HEADER.format(marker=TG_SPLIT_MARKER)]

    if videos:
        parts.append("# Новые видео канала (авто-субтитры)")
        for video in videos:
            transcript = video.transcript
            if len(transcript) > TRANSCRIPT_CHARS_FOR_LLM:
                transcript = transcript[:TRANSCRIPT_CHARS_FOR_LLM] + "\n\n[... транскрипт обрезан ...]"
            parts.append(
                f"## {video.title}\n"
                f"- дата: {video.published}\n"
                f"- ссылка: {video.url}\n"
                f"- длительность: {video.duration // 60} мин\n\n"
                f"### Транскрипт\n{transcript}"
            )

    if github and (github.commits or github.changelog_sections):
        parts.append("# Репозиторий mattpocock/skills")
        messages = meaningful_commits(github.commits)
        if messages:
            listed = "\n".join(f"- {message}" for message in messages[:40])
            parts.append(f"## Коммиты с прошлого прогона ({len(github.commits)} всего)\n{listed}")
        if github.changelog_sections:
            parts.append("## Новые секции CHANGELOG.md\n\n" + "\n\n".join(github.changelog_sections))

    return "\n\n".join(parts)


def run_claude(prompt: str) -> Optional[str]:
    if not Path(CLAUDE_BIN).exists():
        log(f"claude: бинарь не найден: {CLAUDE_BIN}")
        return None
    command = [
        CLAUDE_BIN, "-p",
        "--model", CLAUDE_MODEL,
        "--output-format", "json",
        "--max-turns", "1",
        "--no-session-persistence",
        "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "Read", "Glob", "Grep", "Task",
    ]
    try:
        proc = subprocess.run(
            command, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        log(f"claude: timeout {CLAUDE_TIMEOUT} с")
        return None
    except Exception as exc:
        log(f"claude: запуск не удался: {type(exc).__name__}: {exc}")
        return None
    if proc.returncode != 0:
        log(f"claude: exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        text = proc.stdout.strip()
        return text or None
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            log(f"claude: is_error=true: {str(envelope.get('result'))[:200]}")
            return None
        result = envelope.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
    log("claude: в ответе нет текста")
    return None


def split_distillation(answer: str) -> tuple[str, list[str]]:
    """Тело секции дельты + буллеты для отчёта."""
    if TG_SPLIT_MARKER in answer:
        body, _, tail = answer.partition(TG_SPLIT_MARKER)
        bullets = [
            line.strip().lstrip("-•* ").strip()
            for line in tail.splitlines()
            if line.strip().startswith(("-", "•", "*"))
        ]
        return body.strip(), [bullet for bullet in bullets if bullet][:5]
    bullets = [
        line.strip().lstrip("-•* ").strip()
        for line in answer.splitlines()
        if line.strip().startswith(("-", "•", "*"))
    ]
    return answer.strip(), [bullet for bullet in bullets if bullet][:5]


# ── Дельта-файл ───────────────────────────────────────────────────────────


DELTA_HEADER = """---
tags: type/research, project/self, topic/agent-architecture, status/active
date: 2026-08-08
status: active
agent: matt
---

# Мэт Покок — дельта доктрины

Тезис: это накопитель того, что появилось у Мэта Покока ПОСЛЕ сборки ядра
(06.08.2026). Ядро (`{self} {research} Мэт Покок доктрина AI-инжиниринга – 2026-08-06.md`)
автоматически не переписывается — новое падает сюда датированными секциями.

**Как пользоваться:** Мэт читает этот файл при старте сессии сразу после ядра.
При конфликте свежая секция дельты старше ядра по дате — значит, она и права;
разворот называть вслух, а не сглаживать.

**Кто пишет:** `infrastructure/CTO/Scripts/matt_corpus_update.py` (systemd-таймер
на VPS, вс 20:00 MSK). Источники — long-form видео канала @mattpocockuk и
репозиторий `mattpocock/skills` (коммиты + CHANGELOG).

**Лимит:** ~200 строк. Превышение — сигнал в отчёт: пора консолидировать дельту
в ядро руками, командой «/matt консолидируй дельту». Вплавленные секции после
консолидации уходят из этого файла.

**Цитаты из видео — по авто-субтитрам**, как и в ядре: смысл сверен, точная
формулировка — только по видео.

---
"""


def append_delta(body: str, videos: list[VideoResult], github: Optional[GithubResult]) -> int:
    """Дописывает датированную секцию. Возвращает итоговое число строк файла."""
    today = datetime.now().strftime("%Y-%m-%d")
    counts = []
    if videos:
        counts.append(f"{len(videos)} видео")
    if github and github.commits:
        counts.append(f"{len(github.commits)} коммитов skills")
    heading = f"## {today} — " + (", ".join(counts) if counts else "обновление")

    sources: list[str] = []
    for video in videos:
        sources.append(f"[{video.title}]({video.url}) ({video.published})")
    if github and github.commits:
        span = github.commits[-1]["sha"][:7] + ".." + github.commits[0]["sha"][:7]
        sources.append(f"`mattpocock/skills` {span}")
    footer = "_Источники: " + " · ".join(sources) + "._" if sources else ""

    DELTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DELTA_FILE.exists():
        DELTA_FILE.write_text(DELTA_HEADER, encoding="utf-8")
        log(f"дельта: создан файл {DELTA_FILE.name}")

    chunk = f"\n{heading}\n\n{body.strip()}\n"
    if footer:
        chunk += f"\n{footer}\n"
    with DELTA_FILE.open("a", encoding="utf-8") as handle:
        handle.write(chunk)
    return len(DELTA_FILE.read_text(encoding="utf-8").splitlines())


# ── Telegram ──────────────────────────────────────────────────────────────


class Channel(NamedTuple):
    token: str
    chat_id: str


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = shlex.split(value.strip())[0] if value.strip() else ""
    return env


def read_token_file(path: Path) -> str:
    """Токен из отдельного файла: строка KEY=значение или голая строка."""
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
        if TG_TOKEN_RE.match(value):
            return value
        fallback = fallback or value
    return fallback


def load_channel() -> Optional[Channel]:
    """Канал отчёта из matt-corpus-delivery.env; None = слать некуда."""
    if not DELIVERY_ENV_FILE.exists():
        log(f"delivery: нет конфига {DELIVERY_ENV_FILE} ({DELIVERY_CONTRACT}) → отчёт только в лог")
        return None
    try:
        env = load_env_file(DELIVERY_ENV_FILE)
    except Exception as exc:
        log(f"delivery: {DELIVERY_ENV_FILE.name} не разобран ({type(exc).__name__}: {exc})")
        return None
    token = env.get("MATT_CORPUS_TG_TOKEN", "").strip()
    token_file = env.get("MATT_CORPUS_TG_TOKEN_FILE", "").strip()
    if not token and token_file:
        token = read_token_file(Path(token_file).expanduser())
    chat_id = env.get("MATT_CORPUS_CHAT_ID", "").strip() or TG_DEFAULT_CHAT_ID
    if not token:
        log(f"delivery: в {DELIVERY_ENV_FILE.name} нет MATT_CORPUS_TG_TOKEN|_FILE → отчёт только в лог")
        return None
    log(f"delivery: отчёт уходит в chat {chat_id} без топика ({DELIVERY_CONTRACT})")
    return Channel(token=token, chat_id=chat_id)


def send_message(channel: Channel, text: str) -> bool:
    """Главный чат Гермеса, БЕЗ message_thread_id (решение 2026-08-08)."""
    remaining = text.strip() or "(пустой отчёт)"
    while remaining:
        chunk, remaining = remaining[:3500], remaining[3500:]
        command = [
            "curl", "-fsS", "--max-time", "30",
            "--data-urlencode", f"chat_id={channel.chat_id}",
            "--data-urlencode", f"text={chunk}",
            "--data-urlencode", "disable_web_page_preview=true",
            f"https://api.telegram.org/bot{channel.token}/sendMessage",
        ]
        try:
            subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=60)
        except Exception as exc:
            detail = getattr(exc, "output", "") or str(exc)
            log(f"telegram: отправка не удалась: {str(detail)[:300]}")
            return False
    return True


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def build_report(
    videos: list[VideoResult],
    github: Optional[GithubResult],
    bullets: list[str],
    errors: list[str],
    delta_lines: Optional[int],
    distilled: bool,
) -> str:
    commit_count = len(github.commits) if github else 0
    lines = [
        f"📚 Мэт за неделю: {len(videos)} видео, "
        f"{commit_count} {_plural(commit_count, 'коммит', 'коммита', 'коммитов')} skills"
    ]

    if videos:
        lines.append("")
        for video in videos:
            lines.append(f"• {video.title}")
            lines.append(f"  {video.url}")

    if github and commit_count:
        lines.append("")
        messages = meaningful_commits(github.commits)
        versions = [
            section.splitlines()[0].replace("## ", "").strip()
            for section in github.changelog_sections
            if section.strip()
        ]
        head = "Коммиты skills"
        if versions:
            head += f" (CHANGELOG: {', '.join(versions[:4])})"
        lines.append(head + ":")
        for message in messages[:5]:
            lines.append(f"• {message[:120]}")
        if len(messages) > 5:
            lines.append(f"• …и ещё {len(messages) - 5}")

    lines.append("")
    if distilled and bullets:
        lines.append("Дельта дополнена:")
        lines.extend(f"• {bullet[:200]}" for bullet in bullets)
    elif distilled:
        lines.append("Дельта дополнена (буллеты не разобрались — смотри файл).")
    else:
        lines.append("⚠️ Дистилляция не удалась — дельта не дополнена, материалы в vault.")

    if delta_lines and delta_lines > DELTA_LINE_LIMIT:
        lines.append("")
        lines.append(
            f"⚠️ Дельта разрослась: {delta_lines} строк (лимит {DELTA_LINE_LIMIT}). "
            f"Пора консолидировать: «/matt консолидируй дельту»."
        )

    if errors:
        lines.append("")
        lines.append("Сбои источников:")
        lines.extend(f"• {error[:200]}" for error in errors)

    return "\n".join(lines)


# ── Прогон ────────────────────────────────────────────────────────────────


def seed_state(state: dict) -> None:
    """Первый прогон: запомнить текущее состояние, ничего не обрабатывая."""
    try:
        for entry in fetch_feed():
            _mark_seen(state, entry.video_id)
    except Exception as exc:
        log(f"bootstrap: RSS не прочитан ({type(exc).__name__}: {exc})")
    try:
        payload = json.loads(
            http_get(
                f"{GITHUB_COMMITS_URL}?sha={GITHUB_BRANCH}&per_page=1",
                accept="application/vnd.github+json",
            )
        )
        if isinstance(payload, list) and payload:
            state["last_commit_sha"] = payload[0].get("sha") or ""
            state["last_commit_date"] = (
                ((payload[0].get("commit") or {}).get("author") or {}).get("date") or ""
            )
    except Exception as exc:
        log(f"bootstrap: GitHub не прочитан ({type(exc).__name__}: {exc})")
    log(
        f"bootstrap: засеяно {len(state['last_video_ids'])} видео, "
        f"sha {state['last_commit_sha'][:8] or '—'} — обработки и отчёта не будет"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Еженедельное обновление корпуса Мэта.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Всё собрать и напечатать; не писать файлы, не звать claude, не слать TG.")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Засеять state текущим состоянием источников и выйти.")
    parser.add_argument("--max-videos", type=int, default=MAX_VIDEOS_PER_RUN,
                        help=f"Максимум видео за прогон (по умолчанию {MAX_VIDEOS_PER_RUN}).")
    parser.add_argument("--state-file", help="Переопределить путь к state (для тестов).")
    parser.add_argument("--smoke", action="store_true",
                        help="Проверить, отдаёт ли YouTube субтитры с этой машины (для ранбука). "
                             "Exit 0 — отдаёт, 3 — нет.")
    return parser.parse_args()


def smoke_youtube() -> int:
    """Одно известное long-form видео: получаются ли метаданные и субтитры."""
    try:
        fys = load_subs_module()
    except Exception as exc:
        log(f"smoke: модуль субтитров не грузится ({type(exc).__name__}: {exc}) — нет yt-dlp?")
        return 3
    probe = probe_video(fys, SMOKE_VIDEO_ID)
    if probe.info is None:
        log(f"smoke: метаданные {SMOKE_VIDEO_ID} не получены: {probe.error}")
        return 3
    if not probe.transcript:
        log(f"smoke: субтитры {SMOKE_VIDEO_ID} НЕ получены: {probe.error}")
        return 3
    log(
        f"smoke: ок — {fys.clean_title(probe.info.get('title'))}, "
        f"{int(probe.info.get('duration') or 0)} с, субтитры {probe.lang}, "
        f"{len(probe.transcript)} символов"
    )
    return 0


def main() -> int:
    global STATE_FILE

    args = parse_args()
    if args.state_file:
        STATE_FILE = Path(args.state_file).expanduser()

    if args.smoke:
        log(f"=== смоук YouTube, vault={VAULT} ===")
        return smoke_youtube()

    mode = "dry-run" if args.dry_run else ("bootstrap" if args.bootstrap else "боевой")
    log(f"=== старт ({mode}), vault={VAULT} ===")

    state = load_state()
    virgin = not state["last_video_ids"] and not state["last_commit_sha"]

    if args.bootstrap or (virgin and not args.dry_run):
        if virgin and not args.bootstrap:
            log("state пуст — первый прогон работает как bootstrap (иначе засыплет дельту архивом)")
        seed_state(state)
        save_state(state)
        return 0

    if virgin and args.dry_run:
        log("state пуст: в dry-run считаю новыми все ролики из RSS-окна")

    videos, video_notes, errors = collect_videos(state, args)
    for note in video_notes:
        log(f"youtube: {note}")

    github, github_notes, github_errors = collect_github(state)
    for note in github_notes:
        log(f"github: {note}")
    errors.extend(github_errors)

    has_news = bool(videos) or bool(github and github.commits)
    if not has_news:
        log("нового нет — отчёт не шлём")
        if not args.dry_run:
            save_state(state)
        log("=== конец ===")
        return 0

    prompt = build_prompt(videos, github)
    log(f"дистилляция: промпт {len(prompt)} символов, видео {len(videos)}, "
        f"коммитов {len(github.commits) if github else 0}")

    if args.dry_run:
        _print_dry_run(videos, github, prompt, errors)
        log("=== конец (dry-run: файлы не тронуты, claude не вызван, TG не отправлен) ===")
        return 0

    answer = run_claude(prompt)
    body, bullets = split_distillation(answer) if answer else ("", [])
    delta_lines: Optional[int] = None
    if body:
        try:
            delta_lines = append_delta(body, videos, github)
            log(f"дельта: секция добавлена, файл {delta_lines} строк")
        except Exception as exc:
            errors.append(f"дельта не дописана ({type(exc).__name__}: {exc})")
            log(f"дельта: запись упала: {type(exc).__name__}: {exc}")
            body = ""
    else:
        errors.append("claude не вернул дистилляцию — дельта не дополнена")

    # Sha двигаем только если дистилляция реально легла в дельту: иначе эти
    # коммиты просто исчезли бы из корпуса. Не легла — на следующей неделе
    # они приедут снова (видео к тому моменту уже лежат заметками в vault).
    if github and github.head_sha and body:
        state["last_commit_sha"] = github.head_sha
        state["last_commit_date"] = github.head_date or state.get("last_commit_date", "")

    report = build_report(videos, github, bullets, errors, delta_lines, distilled=bool(body))
    channel = load_channel()
    if channel and send_message(channel, report):
        state["last_report_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log("отчёт отправлен в Telegram")
    else:
        log("отчёт НЕ отправлен, ниже его текст:\n" + report)

    save_state(state)
    log("=== конец ===")
    return 0


def _print_dry_run(
    videos: list[VideoResult],
    github: Optional[GithubResult],
    prompt: str,
    errors: list[str],
) -> None:
    print("\n────────── ПЛАН (dry-run) ──────────")
    print(f"Long-form видео к обработке: {len(videos)}")
    for video in videos:
        target = "(заметка была бы создана в transcripts/external resources/Matt Pocock/)"
        print(f"  • {video.title}")
        print(f"    {video.url} · {video.published} · {video.duration // 60} мин · "
              f"транскрипт {len(video.transcript)} симв")
        print(f"    {target}")

    if github:
        print(f"\nКоммиты skills с прошлого прогона: {len(github.commits)}")
        for commit in github.commits[:20]:
            print(f"  • {commit['sha'][:8]} {commit['date'][:10]} {commit['message'][:90]}")
        if len(github.commits) > 20:
            print(f"  • …и ещё {len(github.commits) - 20}")
        print(f"Новых секций CHANGELOG: {len(github.changelog_sections)}")
        for section in github.changelog_sections:
            print(f"  • {section.splitlines()[0]}")

    print(f"\nВ claude ушёл бы промпт на {len(prompt)} символов "
          f"(модель {CLAUDE_MODEL}, один вызов, инструменты запрещены).")
    print(f"В дельту {DELTA_FILE.name} ушла бы датированная секция "
          f"{datetime.now().strftime('%Y-%m-%d')} с разбором этих материалов.")
    if DELTA_FILE.exists():
        current = len(DELTA_FILE.read_text(encoding="utf-8").splitlines())
        print(f"Сейчас в дельте {current} строк (лимит {DELTA_LINE_LIMIT}).")
    else:
        print(f"Дельта-файла ещё нет — он был бы создан с шапкой-контрактом.")

    sample = build_report(
        videos, github, ["<буллеты от claude>"], errors, None, distilled=True
    )
    print("\n────────── ОТЧЁТ, который ушёл бы в TG ──────────")
    print(sample)
    print("──────────────────────────────────────────────────\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # best-effort: наружу не падаем
        log(f"НЕОЖИДАННАЯ ОШИБКА: {type(exc).__name__}: {exc}")
        raise SystemExit(0)
