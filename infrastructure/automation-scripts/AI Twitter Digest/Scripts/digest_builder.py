#!/usr/bin/env python3
"""
digest_builder.py — сборка русскоязычного тематического дайджеста из твитов.

Вход: JSON от fetch_tweets.py (--in file или stdin).
Выход: готовый текст для Telegram (--out file или stdout).

Ровно ОДИН вызов `claude -p` на выпуск (модель по умолчанию claude-sonnet-5).
Формат вывода — plain text без parse_mode: Telegram не парсит сущности,
значит ни один спецсимвол из твита не сломает отправку.

Пустой вход → короткое «тишина в ленте», exit 0, LLM не вызывается.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LOG_FILE = Path.home() / "Library" / "Logs" / "ai-twitter-digest.log"
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/anton/.local/bin/claude")
MODEL = os.environ.get("AI_DIGEST_MODEL", "claude-sonnet-5")

MAX_TWEETS_IN_PROMPT = 300
MAX_TWEET_CHARS = 800
CLAUDE_TIMEOUT_SECONDS = 300
MORNING_BEFORE_HOUR = 15


def _per_account_cap() -> int:
    """Сколько твитов максимум берём от одного автора (env AI_DIGEST_PER_ACCOUNT_CAP)."""
    raw = os.environ.get("AI_DIGEST_PER_ACCOUNT_CAP", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 4
    return value if value > 0 else 4


PROMPT_TEMPLATE = """Ты — редактор ежедневного дайджеста про AI для одного читателя (предприниматель, делает AI-продукты и продакшн-студию). Ниже — свежие твиты из его подборки AI-аккаунтов. Сделай из них редакторский дайджест на русском языке.

Главное правило: дайджест — это срез по всей индустрии, а не хроника одной компании и не лента одного автора. Даже если в материале много твитов от одного аккаунта или про один запуск, выпуск должен показывать разные части индустрии.

ПЕРВАЯ СЕКЦИЯ ВЫПУСКА — «📌 Новость дня»:
- Это ОДНО главное событие батча — то, которое обсуждают НЕСКОЛЬКО разных авторов.
- Формат секции: суть события по-русски (2-3 предложения) → 2-3 дословные цитаты В ОРИГИНАЛЕ от РАЗНЫХ авторов, у каждой цитаты свой (@handle) и своя ссылка на отдельной строке → последняя строка «Почему важно: …».
- Жёсткое правило против выдумывания: если в батче НЕТ события, которое минимум два автора обсуждают независимо друг от друга, секцию «Новость дня» полностью пропусти и начинай выпуск сразу с тематических секций. Один громкий твит одного автора — это НЕ новость дня. Не склеивай разные события в одно ради этой секции и не выдавай за общий сюжет то, что просто относится к одной теме.
- Твиты, использованные в «Новости дня», больше нигде не повторяются: ни в тематических секциях, ни в «⚡ Коротко».

ЧТО ЧИТАТЕЛЮ ИНТЕРЕСНО — высокий приоритет, в порядке убывания:
1. Релизы моделей и инструментов и, главное, что они позволяют делать нового.
2. ИИ в создании контента — это индустрия читателя, поэтому приоритет наравне с релизами: генерация видео, изображений, аудио и музыки (Veo, Sora, Runway, Kling, Midjourney, ElevenLabs и подобные), инструменты монтажа и продакшна, воркфлоу создания контента, UGC-пайплайны, кейсы применения ИИ в рекламе, кино и соцсетях.
3. Прогнозы и большие тезисы о том, куда идёт ИИ.
4. Практические кейсы применения: кто что построил, что получилось, что сломалось.
5. Важные технические объяснения простым языком.
6. Значимые бенчмарки и замеры.

ЧТО ЧИТАТЕЛЮ НЕИНТЕРЕСНО — низкий приоритет:
- биржевые и фондовые драмы, котировки, IPO-сплетни;
- слухи и расследования о репутации персон, кто кому что сказал;
- разборки инвесторов и фондов, светская хроника индустрии, кто кого подколол.
Такой материал в основные пункты не выносится, даже если у него много лайков. Максимум — одна строка в секции «Коротко», а лучше просто пропусти.

ОБЪЁМ — не пережимай выпуск:
- Если материала достаточно, делай 10-18 основных пунктов, а не 5-10. Короткий выпуск оправдан только тогда, когда в батче реально мало содержательного.
- Содержательный твит не выбрасывается молча: он либо становится пунктом, либо уходит строкой в «⚡ Коротко».
- Правила баланса ниже (≤2 пункта на автора, склейка серии анонсов) — про доминирование одного автора, а не про общий объём выпуска. Общий объём они не ограничивают.

ФОРМАТ ОСНОВНОГО ПУНКТА — строго в этом порядке:
1) Суть по-русски одной фразой: что произошло.
2) Прямая цитата из твита В ОРИГИНАЛЕ (как правило по-английски), в кавычках «…» — 1-3 ключевых предложения. Если твит длинный, бери только самый содержательный фрагмент, а не весь текст.
3) Если нужно — одна строка русского контекста «почему это важно». Если и так понятно, не пиши.
4) Автор в скобках — (@handle).
5) Ссылки на твиты.
Порядок не переставляй: между строкой автора и ссылками не должно быть ничего — «почему важно» идёт до автора, а ссылки всегда замыкают пункт.

Про цитату отдельно: цитату НЕ переводить и НЕ перефразировать — это дословный фрагмент исходного текста, слово в слово. Если твит написан не по-английски, цитируй на языке оригинала. Ничего не дописывай внутрь кавычек. Пункт без цитаты — брак; если цитировать нечего (в твите одна ссылка или пара слов), такой материал вообще не тянет на основной пункт — отправь его в «Коротко».

Секция «⚡ Коротко» — последняя в выпуске. В неё одной строкой (суть по-русски + ссылка на следующей строке) идёт всё заметное, что не стало основным пунктом: мелкие новости, второстепенные анонсы, низкоприоритетные темы из списка выше. Цитаты здесь не нужны.

Остальные правила:
- Только русский язык (кроме цитат — они в оригинале). Никакого вступления вроде «вот дайджест» и никакого заключения.
- Сгруппируй по темам (модели и релизы, создание контента, инструменты, практика, исследования, прогнозы и мнения — бери только те темы, которые реально есть в материале). Тему помечай строкой с эмодзи и названием, например «🚀 Релизы».
- Секция «🎬 Создание контента» обязательна всегда, когда в батче есть хоть какой-то материал по теме: генерация видео, изображений, аудио и музыки, инструменты монтажа и продакшна, воркфлоу и UGC-пайплайны, применение ИИ в рекламе, кино и соцсетях. Не растворяй такие твиты в «Релизах» или «Практике» — собирай их в эту секцию. Если материала по теме в батче нет, секция просто пропускается, как и любая другая: выдумывать или притягивать за уши нерелевантное нельзя.
- Максимум 2 основных пункта на одного автора. Если от автора осталось больше — выбери самое значимое, остальное отправь в «Коротко».
- Серию анонсов одной компании сжимай в ОДИН пункт с несколькими ссылками, а не в несколько соседних пунктов.
- Если несколько авторов пишут об одном событии — это один пункт, в нём перечисли всех авторов и все ссылки.
- Обязательно включай материал из разных «ролей» ленты, если он есть в батче: лаборатории и их анонсы, независимые исследователи и практики, скептики и критика, аналитика рынка. Такие пункты бери, даже если по лайкам и ретвитам они слабее громких анонсов.
- Объём твитов автора в батче не равен важности: много постов подряд от одного аккаунта — это его привычка публиковать, а не вес события.
- Каждая ссылка — на отдельной строке, целиком, сама по себе. Ничего не приписывай к URL вплотную: ни скобок, ни знаков препинания, ни второй ссылки. Две ссылки — две строки.
- Строго по материалу: ничего не выдумывай, не додумывай цифры, названия моделей и компаний. Если в твите только анонс без деталей — так и пиши.
- Пропускай шум: приветствия, мемы без содержания, чистый самопиар, треды-опросы, анонсы стримов.
- Формат — обычный текст для Telegram. НЕ используй markdown-разметку (*, _, `, []()), только текст, эмодзи и голые URL.

{truncation_note}Материал ({tweet_count} твитов за период):

{body}"""


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] digest: {message}\n")
    print(f"digest: {message}", file=sys.stderr, flush=True)


# ── Подготовка материала ─────────────────────────────────────────────────

def engagement(tweet: dict) -> int:
    return int(tweet.get("like_count", 0) or 0) + 2 * int(tweet.get("retweet_count", 0) or 0)


def cap_per_account(tweets: list[dict], cap: int | None = None) -> tuple[list[dict], dict[str, int]]:
    """Не больше `cap` твитов от одного автора — иначе плодовитый аккаунт съедает выпуск.

    Отбор внутри автора — по реакциям (like + 2×retweet).
    Возвращает (оставшиеся твиты, {handle: сколько выброшено}).
    """
    if cap is None:
        cap = _per_account_cap()
    by_handle: dict[str, list[dict]] = {}
    for tweet in tweets:
        by_handle.setdefault(tweet.get("handle", "?"), []).append(tweet)
    kept: list[dict] = []
    trimmed: dict[str, int] = {}
    for handle, items in by_handle.items():
        if len(items) <= cap:
            kept.extend(items)
            continue
        items.sort(key=engagement, reverse=True)
        kept.extend(items[:cap])
        trimmed[handle] = len(items) - cap
    kept.sort(key=lambda t: t.get("date", ""), reverse=True)
    return kept, trimmed


def cap_tweets(tweets: list[dict], cap: int = MAX_TWEETS_IN_PROMPT) -> tuple[list[dict], bool]:
    """Если твитов больше лимита — оставляем самые заметные, по кругу между аккаунтами."""
    if len(tweets) <= cap:
        return tweets, False
    by_handle: dict[str, list[dict]] = {}
    for tweet in tweets:
        by_handle.setdefault(tweet.get("handle", "?"), []).append(tweet)
    for items in by_handle.values():
        items.sort(key=engagement, reverse=True)
    kept: list[dict] = []
    round_index = 0
    while len(kept) < cap:
        added = False
        for items in by_handle.values():
            if round_index < len(items):
                kept.append(items[round_index])
                added = True
                if len(kept) >= cap:
                    break
        if not added:
            break
        round_index += 1
    kept.sort(key=lambda t: t.get("date", ""), reverse=True)
    return kept, True


def format_tweet(index: int, tweet: dict) -> str:
    date = str(tweet.get("date", ""))[:16].replace("T", " ")
    stats = f"❤{tweet.get('like_count', 0)} 🔁{tweet.get('retweet_count', 0)}"
    head = f"[{index}] @{tweet.get('handle', '?')} ({tweet.get('name', '')}) · {date} · {stats}"
    lines = [head, str(tweet.get("url", "")), str(tweet.get("text", ""))[:MAX_TWEET_CHARS]]
    if tweet.get("is_quote") and tweet.get("quoted_text"):
        lines.append(f"— цитирует: {str(tweet['quoted_text'])[:MAX_TWEET_CHARS]}")
    return "\n".join(part for part in lines if part)


def build_prompt(
    tweets: list[dict],
    truncated: bool,
    total: int,
    trimmed: dict[str, int] | None = None,
) -> str:
    body = "\n\n".join(format_tweet(i + 1, t) for i, t in enumerate(tweets))
    notes: list[str] = []
    if trimmed:
        shown = ", ".join(
            f"@{handle} (−{count})"
            for handle, count in sorted(trimmed.items(), key=lambda kv: kv[1], reverse=True)
        )
        notes.append(
            "Внимание: ленты этих авторов уже подрезаны — оставлено не больше "
            f"{_per_account_cap()} твитов на автора, остальное выброшено: {shown}. "
            "Значит объём в материале не отражает реальный объём их постинга и тем более "
            "не отражает важность: не делай из них главных героев выпуска."
        )
    if truncated:
        notes.append(
            f"Внимание: исходно твитов было {total}, в материал попали только "
            f"{len(tweets)} самых заметных по реакциям — это выборка, а не полная лента."
        )
    note = ("\n\n".join(notes) + "\n\n") if notes else ""
    return PROMPT_TEMPLATE.format(truncation_note=note, tweet_count=len(tweets), body=body)


def prepare_tweets(tweets: list[dict]) -> tuple[list[dict], bool, dict[str, int]]:
    """Общий детерминированный отбор: сначала лимит на автора, потом общий лимит."""
    balanced, trimmed = cap_per_account(tweets)
    capped, truncated = cap_tweets(balanced)
    return capped, truncated, trimmed


# ── LLM ──────────────────────────────────────────────────────────────────

def run_claude(prompt: str) -> str | None:
    cmd = [
        CLAUDE_BIN, "-p",
        "--model", MODEL,
        "--max-turns", "1",
        "--no-session-persistence",
        "--disallowedTools", "Bash", "Edit", "Write", "WebFetch", "WebSearch", "Read", "Glob", "Grep",
    ]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        log(f"claude не найден: {CLAUDE_BIN}")
        return None
    except subprocess.TimeoutExpired:
        log(f"claude: таймаут {CLAUDE_TIMEOUT_SECONDS}s")
        return None
    if proc.returncode != 0:
        log(f"claude: exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
        return None
    text = (proc.stdout or "").strip()
    if len(text) < 40:
        log(f"claude: подозрительно короткий ответ ({len(text)} симв.)")
        return None
    return text


def fallback_body(tweets: list[dict], limit: int = 12) -> str:
    """Детерминированная выжимка, если LLM недоступен — выпуск всё равно уходит."""
    top = sorted(tweets, key=engagement, reverse=True)[:limit]
    lines = ["(без LLM-разбора — топ твитов по реакциям)", ""]
    for tweet in top:
        text = " ".join(str(tweet.get("text", "")).split())[:220]
        lines.append(f"• {text} (@{tweet.get('handle', '?')})")
        lines.append(str(tweet.get("url", "")))
        lines.append("")
    return "\n".join(lines).strip()


# ── Сборка выпуска ───────────────────────────────────────────────────────

def header(now: dt.datetime) -> str:
    part = "утро" if now.hour < MORNING_BEFORE_HOUR else "вечер"
    return f"🤖 AI Twitter — {part} {now.strftime('%Y-%m-%d')}"


def errors_footer(errors: list[dict]) -> str:
    handles = [f"@{e.get('handle', '?')}" for e in errors if isinstance(e, dict)]
    if not handles:
        return ""
    shown = ", ".join(handles[:10])
    if len(handles) > 10:
        shown += f" и ещё {len(handles) - 10}"
    return f"\n\n⚠️ не удалось прочитать: {shown}"


def build_digest(payload: dict, now: dt.datetime) -> str:
    tweets = [t for t in (payload.get("tweets") or []) if isinstance(t, dict)]
    errors = [e for e in (payload.get("errors") or []) if isinstance(e, dict)]
    head = header(now)
    if not tweets:
        return f"{head}\n\nТишина в ленте: новых постов за период нет." + errors_footer(errors)
    capped, truncated, trimmed = prepare_tweets(tweets)
    body = run_claude(build_prompt(capped, truncated, len(tweets), trimmed))
    if body is None:
        body = fallback_body(capped)
    stats = f"\n\nИсточник: {len(tweets)} твитов, {payload.get('accounts_ok', '?')} аккаунтов."
    return f"{head}\n\n{body}{stats}{errors_footer(errors)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сборка AI Twitter дайджеста из JSON fetch_tweets.py")
    parser.add_argument("--in", dest="input", help="JSON от fetch_tweets.py (по умолчанию stdin)")
    parser.add_argument("--out", help="куда писать текст дайджеста (по умолчанию stdout)")
    parser.add_argument("--no-llm", action="store_true", help="без вызова claude (детерминированная выжимка)")
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="напечатать промпт для LLM и выйти (отладка отбора, без вызова claude)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        log(f"вход не JSON: {exc}")
        return 1
    if not isinstance(payload, dict):
        log("вход не похож на выход fetch_tweets.py")
        return 1

    now = dt.datetime.now()
    if args.print_prompt:
        tweets = [t for t in (payload.get("tweets") or []) if isinstance(t, dict)]
        if not tweets:
            log("нет твитов — промпт не строится")
            return 0
        capped, truncated, trimmed = prepare_tweets(tweets)
        print(build_prompt(capped, truncated, len(tweets), trimmed))
        return 0

    if args.no_llm:
        tweets = [t for t in (payload.get("tweets") or []) if isinstance(t, dict)]
        errors = [e for e in (payload.get("errors") or []) if isinstance(e, dict)]
        if tweets:
            capped, _, _ = prepare_tweets(tweets)
            text = f"{header(now)}\n\n{fallback_body(capped)}{errors_footer(errors)}"
        else:
            text = f"{header(now)}\n\nТишина в ленте: новых постов за период нет.{errors_footer(errors)}"
    else:
        text = build_digest(payload, now)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        log(f"дайджест собран: {len(text)} симв. → {out_path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
