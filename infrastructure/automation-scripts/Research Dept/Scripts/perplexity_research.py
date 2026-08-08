#!/usr/bin/env python3
"""CLI-обёртка над Perplexity Sonar API.

Только stdlib. Ключ берётся из env PERPLEXITY_API_KEY либо из
~/.config/second-brain/perplexity.env

Примеры:
    ./perplexity_research.py "рынок вертикальных сериалов в РФ 2026"
    ./perplexity_research.py "цены на AI-видео" --model sonar-pro --recency month
    ./perplexity_research.py "..." --model sonar-deep-research --json
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

API_URL = "https://api.perplexity.ai/chat/completions"
ENV_FILE = Path.home() / ".config" / "second-brain" / "perplexity.env"
KEY_NAME = "PERPLEXITY_API_KEY"

DEFAULT_SYSTEM = (
    "Ты research-ассистент. Отвечай фактами с опорой на источники. "
    "Не выдумывай цифры. Если данных нет — так и скажи."
)

MODELS = [
    "sonar",
    "sonar-pro",
    "sonar-reasoning",
    "sonar-reasoning-pro",
    "sonar-deep-research",
]

DEFAULT_TIMEOUT = 120
DEEP_RESEARCH_TIMEOUT = 900


def _parse_env_file(path: Path) -> str:
    """Достаёт PERPLEXITY_API_KEY из простого .env-файла."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() != KEY_NAME:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value.strip()
    return ""


def get_api_key() -> str:
    key = (os.environ.get(KEY_NAME) or "").strip()
    if key:
        return key

    key = _parse_env_file(ENV_FILE)
    if key:
        return key

    print(
        "Ошибка: не найден ключ Perplexity API.\n"
        "\n"
        f"1. Получи ключ здесь: https://www.perplexity.ai/settings/api\n"
        f"2. Положи его в файл: {ENV_FILE}\n"
        f"   в формате одной строкой:  {KEY_NAME}=pplx-...\n"
        "\n"
        f"Либо экспортируй переменную окружения: export {KEY_NAME}=pplx-...",
        file=sys.stderr,
    )
    sys.exit(2)


def build_payload(args: argparse.Namespace) -> dict:
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": args.system},
            {"role": "user", "content": args.query},
        ],
    }
    if args.max_tokens is not None:
        payload["max_tokens"] = args.max_tokens
    if args.recency:
        payload["search_recency_filter"] = args.recency
    return payload


def call_api(payload: dict, api_key: str, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = "(тело ответа прочитать не удалось)"
        print(
            f"Ошибка HTTP {exc.code} {exc.reason} от Perplexity API:\n"
            f"{err_body[:2000]}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(
            "Ошибка сети при обращении к Perplexity API: "
            f"{exc.reason}\n"
            "Проверь интернет-соединение и доступность api.perplexity.ai.",
            file=sys.stderr,
        )
        sys.exit(1)
    except TimeoutError:
        print(
            f"Таймаут запроса ({timeout} c). Для тяжёлых моделей "
            "увеличь --timeout.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        print(
            "Не удалось разобрать ответ API как JSON:\n" + body[:2000],
            file=sys.stderr,
        )
        sys.exit(1)


def extract_content(resp: dict) -> str:
    choices = resp.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def print_sources(resp: dict) -> None:
    print("## Источники")

    search_results = resp.get("search_results")
    if isinstance(search_results, list) and search_results:
        for i, item in enumerate(search_results, 1):
            if not isinstance(item, dict):
                print(f"{i}. {item}")
                continue
            title = item.get("title") or "(без названия)"
            url = item.get("url") or ""
            date = item.get("date")
            line = f"{i}. {title} — {url}"
            if date:
                line += f" ({date})"
            print(line)
        return

    citations = resp.get("citations")
    if isinstance(citations, list) and citations:
        for i, url in enumerate(citations, 1):
            print(f"{i}. {url}")
        return

    print("(источников не вернулось)")


def print_json(resp: dict, model: str) -> None:
    out = {"model": resp.get("model") or model, "content": extract_content(resp)}
    if resp.get("search_results"):
        out["search_results"] = resp["search_results"]
    else:
        out["citations"] = resp.get("citations") or []
    out["usage"] = resp.get("usage") or {}
    print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="perplexity_research.py",
        description="CLI-обёртка над Perplexity Sonar API (поиск с источниками).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ключ: env PERPLEXITY_API_KEY либо "
            "~/.config/second-brain/perplexity.env"
        ),
    )
    parser.add_argument("query", help="Поисковый запрос / вопрос")
    parser.add_argument(
        "--model",
        choices=MODELS,
        default="sonar",
        help="Модель Perplexity (default: sonar)",
    )
    parser.add_argument(
        "--system",
        default=DEFAULT_SYSTEM,
        help="System prompt (по умолчанию — research-ассистент)",
    )
    parser.add_argument(
        "--recency",
        choices=["day", "week", "month", "year"],
        default=None,
        help="Фильтр свежести источников (search_recency_filter)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Лимит токенов ответа",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Сырой JSON-вывод вместо текста",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            f"Таймаут запроса в секундах (default: {DEFAULT_TIMEOUT}; "
            f"для sonar-deep-research автоматически {DEEP_RESEARCH_TIMEOUT})"
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.model == "sonar-deep-research" and args.timeout == DEFAULT_TIMEOUT:
        args.timeout = DEEP_RESEARCH_TIMEOUT

    api_key = get_api_key()
    resp = call_api(build_payload(args), api_key, args.timeout)

    if args.as_json:
        print_json(resp, args.model)
    else:
        content = extract_content(resp)
        print(content if content else "(пустой ответ модели)")
        print()
        print_sources(resp)

    return 0


if __name__ == "__main__":
    sys.exit(main())
