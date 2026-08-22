#!/usr/bin/env python3
"""
spool.py — накопительный спул твитов между редкими выпусками дайджеста.

Модель работы: fetch-фаза ходит в X часто и дописывает свежие твиты в
append-only JSONL (`~/.config/ai-twitter-digest/spool.jsonl`), digest-фаза
запускается раз в 2-3 суток, забирает накопленное одним батчем, строит и
шлёт один жёстко отобранный выпуск и только после успешной отправки чистит
спул.

Строка спула = один твит в формате `fetch_tweets.tweet_to_dict`.
Битые/недописанные строки при чтении пропускаются с предупреждением в лог —
оборванный fetch не должен ронять выпуск.

Рядом живёт `spool_meta.json` — накопленные за окно ошибки чтения аккаунтов
(футер «⚠️ не удалось прочитать: …» строится за ВСЁ окно, а не за последний
fetch) и таймстамп самого раннего fetch окна. Чистится вместе со спулом.

Runtime-каталог (вне vault): env AI_DIGEST_RUNTIME_DIR → ~/.config/ai-twitter-digest

CLI (служебные режимы для run_digest.sh и отладки):
    spool.py --stats                 # сколько твитов, за какой возраст, ошибки окна
    spool.py --gate                  # гейт каденции: rc=0 «шлём», rc=10 «ещё рано»
    spool.py --materialize OUT.json  # спул → JSON в формате fetch_tweets.py
    spool.py --clear                 # очистить спул и meta
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "ai-twitter-digest.log"

RUNTIME_ENV = "AI_DIGEST_RUNTIME_DIR"
DEFAULT_RUNTIME_DIR = Path.home() / ".config" / "ai-twitter-digest"
SPOOL_NAME = "spool.jsonl"
META_NAME = "spool_meta.json"
STATE_NAME = "state.json"

# Гейт каденции (детерминированный, до любого LLM)
MIN_AGE_DAYS = 2.0          # моложе — точно рано
FORCE_AGE_DAYS = 3.0        # старше — шлём в любом случае
DEFAULT_MIN_SPOOL = 40      # в «серой зоне» 2-3 суток нужен хотя бы такой объём

GATE_GO = 0
GATE_SKIP = 10


def log(message: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] spool: {message}\n")
    except OSError:
        pass
    print(f"spool: {message}", file=sys.stderr, flush=True)


# ── Пути ─────────────────────────────────────────────────────────────────

def runtime_dir() -> Path:
    raw = os.environ.get(RUNTIME_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_RUNTIME_DIR


def spool_path() -> Path:
    return runtime_dir() / SPOOL_NAME


def meta_path() -> Path:
    return runtime_dir() / META_NAME


def state_path() -> Path:
    return runtime_dir() / STATE_NAME


def _ensure_runtime_dir() -> Path:
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def _min_spool() -> int:
    raw = os.environ.get("AI_DIGEST_MIN_SPOOL", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MIN_SPOOL
    return value if value > 0 else DEFAULT_MIN_SPOOL


# ── Даты ─────────────────────────────────────────────────────────────────

def parse_dt(raw: str) -> dt.datetime | None:
    """ISO-строка твита → aware datetime (UTC, если таймзоны нет)."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value


# ── Спул ─────────────────────────────────────────────────────────────────

def append(tweets: list[dict]) -> int:
    """Дописать твиты в спул (append + flush + fsync). Возвращает сколько записано."""
    items = [t for t in (tweets or []) if isinstance(t, dict)]
    if not items:
        return 0
    _ensure_runtime_dir()
    path = spool_path()
    with path.open("a", encoding="utf-8") as f:
        for tweet in items:
            f.write(json.dumps(tweet, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return len(items)


def read() -> list[dict]:
    """Прочитать спул: дедуп по tweet_id, битые строки пропускаются с warn."""
    path = spool_path()
    if not path.exists():
        return []
    tweets: list[dict] = []
    seen: set[str] = set()
    broken = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                broken += 1
                continue
            if not isinstance(item, dict):
                broken += 1
                continue
            tweet_id = str(item.get("tweet_id", "") or "")
            key = tweet_id or f"{item.get('handle', '?')}|{item.get('url', '')}"
            if key in seen:
                continue
            seen.add(key)
            tweets.append(item)
    if broken:
        log(f"пропущено битых строк спула: {broken}")
    tweets.sort(key=lambda t: str(t.get("date", "")), reverse=True)
    return tweets


def clear() -> None:
    """Очистить спул и meta — только после успешно отправленного выпуска."""
    for path in (spool_path(), meta_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log(f"не удалось очистить {path.name}: {exc}")
    log("спул очищен")


# ── Meta: ошибки чтения аккаунтов за всё окно ────────────────────────────

def load_meta() -> dict:
    path = meta_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("failed_handles", [])
                data.setdefault("started_at", "")
                return data
        except Exception as exc:  # noqa: BLE001 — meta не имеет права ронять прогон
            log(f"spool_meta повреждён, начинаю с чистого: {exc}")
    return {"version": 1, "failed_handles": [], "started_at": ""}


def save_meta(meta: dict) -> None:
    _ensure_runtime_dir()
    path = meta_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def merge_errors(errors: list[dict] | None, generated_at: str = "") -> dict:
    """Объединить errors[] очередного fetch с множеством упавших handle за окно."""
    meta = load_meta()
    handles = {str(h) for h in meta.get("failed_handles", []) if str(h)}
    for item in errors or []:
        if isinstance(item, dict):
            handle = str(item.get("handle", "") or "").lstrip("@")
        else:
            handle = str(item or "").lstrip("@")
        if handle:
            handles.add(handle)
    meta["failed_handles"] = sorted(handles)
    if not meta.get("started_at"):
        meta["started_at"] = generated_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    save_meta(meta)
    return meta


def meta_errors() -> list[dict]:
    """Ошибки окна в формате errors[] fetch_tweets.py (для футера выпуска)."""
    meta = load_meta()
    return [{"handle": h, "error": "не прочитан за окно"} for h in meta.get("failed_handles", [])]


# ── Возраст спула и гейт каденции ────────────────────────────────────────

def state_last_run() -> dt.datetime | None:
    """last_run из state.json — fallback, если в спуле нет разбираемых дат."""
    path = state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    raw = str((data or {}).get("last_run", "") or "")
    if not raw:
        return None
    value = parse_dt(raw)
    if value is not None:
        return value
    try:
        naive = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.astimezone()


def oldest_tweet_time(tweets: list[dict]) -> dt.datetime | None:
    stamps = [parse_dt(t.get("date", "")) for t in tweets if isinstance(t, dict)]
    stamps = [s for s in stamps if s is not None]
    return min(stamps) if stamps else None


def spool_age_days(tweets: list[dict] | None = None, now: dt.datetime | None = None) -> float | None:
    """Возраст спула в сутках: now − самый старый твит (fallback — last_run)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    tweets = read() if tweets is None else tweets
    oldest = oldest_tweet_time(tweets) or state_last_run()
    if oldest is None:
        return None
    return (now - oldest).total_seconds() / 86400.0


def gate(now: dt.datetime | None = None) -> tuple[bool, str]:
    """Каденция выпуска. Возвращает (слать ли, человекочитаемая причина)."""
    tweets = read()
    count = len(tweets)
    age = spool_age_days(tweets, now=now)
    minimum = _min_spool()
    if count == 0:
        return False, "спул пуст — слать нечего"
    if age is None:
        return False, f"не удалось определить возраст спула ({count} твитов) — жду следующего прогона"
    age_text = f"возраст {age:.1f} сут., твитов {count}"
    if age < MIN_AGE_DAYS:
        return False, f"рано: {age_text} (порог {MIN_AGE_DAYS:g} сут.)"
    if age >= FORCE_AGE_DAYS:
        return True, f"шлём: {age_text} (≥{FORCE_AGE_DAYS:g} сут.)"
    if count >= minimum:
        return True, f"шлём: {age_text} (≥{minimum} твитов в серой зоне 2-3 сут.)"
    return False, f"ждём: {age_text} — в серой зоне нужно ≥{minimum} твитов"


# ── Материализация батча в формат fetch_tweets.py ────────────────────────

def materialize(tweets: list[dict] | None = None) -> dict:
    """Спул + meta → payload того же формата, что отдаёт fetch_tweets.py."""
    tweets = read() if tweets is None else tweets
    handles = {str(t.get("handle", "")) for t in tweets if isinstance(t, dict)}
    handles.discard("")
    meta = load_meta()
    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_hours": None,
        "spool_started_at": meta.get("started_at", ""),
        "accounts_total": len(handles) + len(meta.get("failed_handles", [])),
        "accounts_ok": len(handles),
        "tweets": tweets,
        "errors": meta_errors(),
        "state_proposal": {},
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Накопительный спул твитов AI Twitter Digest")
    parser.add_argument("--stats", action="store_true", help="объём, возраст и ошибки окна")
    parser.add_argument("--gate", action="store_true",
                        help="гейт каденции: rc=0 «шлём», rc=10 «ещё рано / ждём»")
    parser.add_argument("--materialize", metavar="OUT",
                        help="записать спул как JSON в формате fetch_tweets.py")
    parser.add_argument("--clear", action="store_true", help="очистить спул и meta")
    parser.add_argument("--where", action="store_true", help="показать пути спула и meta")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.where:
        print(spool_path())
        print(meta_path())
        return 0
    if args.stats:
        tweets = read()
        age = spool_age_days(tweets)
        meta = load_meta()
        age_text = f"{age:.1f} сут." if age is not None else "неизвестен"
        print(f"твитов: {len(tweets)}")
        print(f"возраст: {age_text}")
        print(f"окно с: {meta.get('started_at') or '—'}")
        print(f"не прочитано: {', '.join('@' + h for h in meta.get('failed_handles', [])) or '—'}")
        return 0
    if args.gate:
        go, reason = gate()
        log(f"гейт каденции — {reason}")
        return GATE_GO if go else GATE_SKIP
    if args.materialize:
        payload = materialize()
        out_path = Path(args.materialize)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"материализовано {len(payload['tweets'])} твитов в {out_path}")
        return 0
    if args.clear:
        clear()
        return 0
    print("нечего делать: укажи --stats / --gate / --materialize / --clear", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
