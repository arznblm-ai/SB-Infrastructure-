#!/usr/bin/env python3
"""
fetch_tweets.py — сбор свежих твитов по списку AI-аккаунтов через twscrape.

Читает список аккаунтов из config/accounts.json, ходит в X через twscrape
(аккаунты X живут в ~/.config/ai-twitter-digest/accounts.db — их добавляет
Антон вручную, скрипт креды НЕ трогает), отдаёт JSON со свежими твитами.

Окно свежести: по state-файлу (last_seen tweet id на аккаунт), fallback —
последние N часов (default 12).

State коммитится ОТДЕЛЬНЫМ вызовом (--commit-state), уже после того как
дайджест реально ушёл в Telegram — чтобы упавшая отправка не съедала твиты.

Накопительный режим (--spool): свежие твиты дописываются в общий спул
(spool.py), и сразу после успешной записи коммитится last_seen_id — выпуск
собирается отдельной редкой digest-фазой из накопленного, не каждый fetch.

Использование:
    fetch_tweets.py --out /tmp/tweets.json
    fetch_tweets.py --spool                             # fetch-фаза: копим в спул
    fetch_tweets.py --account karpathy --limit 5        # smoke-тест
    fetch_tweets.py --commit-state /tmp/tweets.json
    fetch_tweets.py --alert-state get|set|clear
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import spool  # noqa: E402 — соседний модуль, путь добавлен выше

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_DIR / "config" / "accounts.json"
RUNTIME_DIR = spool.runtime_dir()      # env AI_DIGEST_RUNTIME_DIR → ~/.config/ai-twitter-digest
DB_FILE = RUNTIME_DIR / "accounts.db"
STATE_FILE = RUNTIME_DIR / "state.json"
LOG_FILE = Path.home() / "Library" / "Logs" / "ai-twitter-digest.log"

DEFAULT_WINDOW_HOURS = 12
DEFAULT_LIMIT = 40          # сколько твитов тянуть на аккаунт за проход
DEFAULT_DELAY = 2.0         # пауза между аккаунтами, сек (бережём аккаунт X)
MAX_TEXT_CHARS = 2000

EXIT_OK = 0
EXIT_FATAL = 1              # нет конфига / нет БД / нет twscrape
EXIT_ALL_FAILED = 3         # ни один аккаунт не прочитан


# ── Логирование ──────────────────────────────────────────────────────────

def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] fetch: {message}\n")
    print(f"fetch: {message}", file=sys.stderr, flush=True)


# ── State ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("accounts", {})
                data.setdefault("alert_sent", False)
                return data
        except Exception as exc:
            log(f"state повреждён, начинаю с чистого: {exc}")
    return {"version": 1, "accounts": {}, "alert_sent": False, "last_run": ""}


def save_state(state: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUNTIME_DIR, 0o700)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


# ── Конфиг аккаунтов ─────────────────────────────────────────────────────

def load_accounts(config_path: Path) -> list[dict]:
    if not config_path.exists():
        raise RuntimeError(f"нет конфига аккаунтов: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    raw = data.get("accounts") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise RuntimeError(f"{config_path}: ожидался список accounts")
    accounts = []
    for item in raw:
        if isinstance(item, str):
            item = {"handle": item}
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle", "")).strip().lstrip("@")
        if not handle:
            continue
        accounts.append({
            "handle": handle,
            "name": str(item.get("name", "")).strip() or handle,
            "category": str(item.get("category", "")).strip(),
        })
    return accounts


# ── Разбор твита (twscrape-версии отличаются полями — везде getattr) ──────

def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def is_plain_retweet(tweet: Any) -> bool:
    """Ретвит без комментария: есть retweetedTweet и нет собственного текста-цитаты."""
    return _attr(tweet, "retweetedTweet") is not None


def is_foreign_reply(tweet: Any, handle: str) -> bool:
    """Ответ другому пользователю. Собственные треды (self-reply) оставляем."""
    reply_user = _attr(tweet, "inReplyToUser")
    if reply_user is None:
        # часть версий отдаёт только id — тогда считаем ответом чужому,
        # если есть inReplyToTweetId и он не наш (проверить нельзя → оставляем)
        return False
    username = str(_attr(reply_user, "username", "screen_name", default="")).lstrip("@")
    return bool(username) and username.lower() != handle.lower()


def tweet_to_dict(tweet: Any, account: dict) -> dict:
    handle = account["handle"]
    tweet_id = str(_attr(tweet, "id_str", "id", default=""))
    text = str(_attr(tweet, "rawContent", "content", default="") or "")[:MAX_TEXT_CHARS]
    quoted = _attr(tweet, "quotedTweet")
    quoted_text = ""
    if quoted is not None:
        quoted_user = _attr(quoted, "user")
        quoted_handle = str(_attr(quoted_user, "username", "screen_name", default="") or "")
        quoted_body = str(_attr(quoted, "rawContent", "content", default="") or "")[:MAX_TEXT_CHARS]
        quoted_text = f"@{quoted_handle}: {quoted_body}" if quoted_handle else quoted_body
    date = _attr(tweet, "date")
    date_iso = date.isoformat() if isinstance(date, dt.datetime) else str(date or "")
    return {
        "handle": handle,
        "name": account.get("name") or handle,
        "category": account.get("category", ""),
        "tweet_id": tweet_id,
        "url": str(_attr(tweet, "url", default="") or f"https://x.com/{handle}/status/{tweet_id}"),
        "date": date_iso,
        "text": text,
        "is_quote": quoted is not None,
        "quoted_text": quoted_text,
        "like_count": int(_attr(tweet, "likeCount", "like_count", default=0) or 0),
        "retweet_count": int(_attr(tweet, "retweetCount", "retweet_count", default=0) or 0),
    }


# ── Сбор ─────────────────────────────────────────────────────────────────

async def fetch_account(
    api: Any,
    account: dict,
    since_id: Optional[int],
    cutoff: dt.datetime,
    limit: int,
) -> tuple[list[dict], Optional[int]]:
    """Возвращает (свежие твиты, максимальный увиденный id) для одного аккаунта."""
    handle = account["handle"]
    user = await api.user_by_login(handle)
    if user is None:
        raise RuntimeError("аккаунт не найден или недоступен")
    uid = getattr(user, "id", None)
    if uid is None:
        raise RuntimeError("twscrape не отдал user id")
    if not account.get("name") or account["name"] == handle:
        account["name"] = str(getattr(user, "displayname", "") or handle)

    fresh: list[dict] = []
    max_seen: Optional[int] = None
    async for tweet in api.user_tweets(uid, limit=limit):
        try:
            numeric_id = int(_attr(tweet, "id", "id_str", default=0) or 0)
        except (TypeError, ValueError):
            numeric_id = 0
        if numeric_id and (max_seen is None or numeric_id > max_seen):
            max_seen = numeric_id

        if since_id is not None and numeric_id and numeric_id <= since_id:
            continue
        if since_id is None:
            date = _attr(tweet, "date")
            if isinstance(date, dt.datetime):
                if date.tzinfo is None:
                    date = date.replace(tzinfo=dt.timezone.utc)
                if date < cutoff:
                    continue
        if is_plain_retweet(tweet):
            continue
        if is_foreign_reply(tweet, handle):
            continue
        fresh.append(tweet_to_dict(tweet, account))
    return fresh, max_seen


async def run_fetch(args: argparse.Namespace) -> tuple[dict, int]:
    try:
        from twscrape import API  # noqa: WPS433 — опциональная внешняя зависимость
    except ImportError:
        log("twscrape не установлен. Запусти Scripts/setup.sh")
        return {}, EXIT_FATAL

    if not DB_FILE.exists():
        log(f"нет базы аккаунтов X: {DB_FILE}. Сначала twscrape add_accounts / login_accounts")
        return {}, EXIT_FATAL

    try:
        accounts = load_accounts(Path(args.config))
    except Exception as exc:
        log(f"конфиг аккаунтов: {exc}")
        return {}, EXIT_FATAL

    if args.account:
        wanted = args.account.strip().lstrip("@").lower()
        accounts = [a for a in accounts if a["handle"].lower() == wanted] or [
            {"handle": wanted, "name": wanted, "category": "smoke-test"}
        ]

    if not accounts:
        log(f"список аккаунтов пуст: {args.config}. Заполни config/accounts.json")
        return {}, EXIT_FATAL

    state = load_state()
    per_account_state: dict = state.get("accounts", {})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.window_hours)

    api = API(str(DB_FILE))
    tweets: list[dict] = []
    errors: list[dict] = []
    proposal: dict[str, str] = {}
    ok_count = 0

    for index, account in enumerate(accounts):
        handle = account["handle"]
        since_raw = (per_account_state.get(handle) or {}).get("last_seen_id")
        if args.ignore_state or args.account:
            since_raw = None
        try:
            since_id = int(since_raw) if since_raw else None
        except (TypeError, ValueError):
            since_id = None
        try:
            fresh, max_seen = await fetch_account(api, account, since_id, cutoff, args.limit)
        except Exception as exc:
            errors.append({"handle": handle, "error": f"{type(exc).__name__}: {exc}"})
            log(f"@{handle}: {type(exc).__name__}: {exc}")
        else:
            ok_count += 1
            tweets.extend(fresh)
            if max_seen:
                proposal[handle] = str(max_seen)
            log(f"@{handle}: {len(fresh)} свежих")
        if index < len(accounts) - 1 and args.delay > 0:
            await asyncio.sleep(args.delay)

    tweets.sort(key=lambda t: t.get("date", ""), reverse=True)
    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "window_hours": args.window_hours,
        "accounts_total": len(accounts),
        "accounts_ok": ok_count,
        "tweets": tweets,
        "errors": errors,
        "state_proposal": proposal,
    }
    if ok_count == 0:
        log("ни один аккаунт не прочитан")
        return payload, EXIT_ALL_FAILED
    return payload, EXIT_OK


# ── Служебные режимы ─────────────────────────────────────────────────────

def commit_state(payload_path: Path) -> int:
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"commit-state: не читается {payload_path}: {exc}")
        return 1
    return commit_state_payload(payload)


def commit_state_payload(payload: dict) -> int:
    proposal = (payload or {}).get("state_proposal") or {}
    if not isinstance(proposal, dict):
        log("commit-state: некорректный state_proposal")
        return 1
    state = load_state()
    accounts = state.setdefault("accounts", {})
    for handle, last_id in proposal.items():
        entry = accounts.setdefault(handle, {})
        entry["last_seen_id"] = str(last_id)
        entry["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    log(f"commit-state: обновлено аккаунтов {len(proposal)}")
    return 0


def alert_state(mode: str) -> int:
    state = load_state()
    if mode == "get":
        print("1" if state.get("alert_sent") else "0")
        return 0
    state["alert_sent"] = (mode == "set")
    save_state(state)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сбор свежих твитов AI-аккаунтов через twscrape")
    parser.add_argument("--out", help="куда писать JSON (по умолчанию stdout)")
    parser.add_argument("--spool", action="store_true",
                        help="fetch-фаза: дописать свежие твиты в спул и сразу закоммитить state")
    parser.add_argument("--config", default=str(CONFIG_FILE), help="путь к accounts.json")
    parser.add_argument("--account", help="smoke-тест: только этот аккаунт (state игнорируется)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="сколько твитов тянуть на аккаунт")
    parser.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS,
                        help="fallback-окно свежести, если по аккаунту нет state")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="пауза между аккаунтами, сек")
    parser.add_argument("--ignore-state", action="store_true", help="игнорировать last_seen и брать всё окно")
    parser.add_argument("--commit-state", metavar="JSON",
                        help="служебный режим: записать state_proposal из готового JSON в state.json")
    parser.add_argument("--alert-state", choices=["get", "set", "clear"],
                        help="служебный режим: флаг одноразового ❌-алерта в state.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.commit_state:
        return commit_state(Path(args.commit_state))
    if args.alert_state:
        return alert_state(args.alert_state)

    payload, code = asyncio.run(run_fetch(args))
    if not payload:
        return code

    if args.spool:
        # Ошибки чтения копятся за всё окно: футер выпуска строится по объединению,
        # а не по последнему прогону.
        try:
            spool.merge_errors(payload.get("errors"), payload.get("generated_at", ""))
        except Exception as exc:  # noqa: BLE001 — meta не имеет права ронять fetch
            log(f"spool_meta не обновлён ({type(exc).__name__}: {exc})")
        if code != EXIT_OK:
            return code
        written = spool.append(payload.get("tweets") or [])
        log(f"в спул дописано {written} твитов")
        # last_seen_id двигаем ТОЛЬКО после успешной записи в спул: упавший
        # append оставит твиты в окне и следующий fetch заберёт их снова.
        commit_state_payload(payload)
        return code

    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        log(f"записал {len(payload['tweets'])} твитов в {out_path}")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
