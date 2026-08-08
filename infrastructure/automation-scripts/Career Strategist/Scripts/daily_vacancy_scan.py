#!/usr/bin/env python3
"""Ежедневный сканер вакансий: hh.ru (RU-корпорации) + Perplexity (worldwide).

Два источника:
  * RU — hh.ru HTML-поиск (API отдаёт 403, HTML работает), жёсткий фильтр
    по бренд-планке (BRAND_WHITELIST);
  * Worldwide — Perplexity Sonar API (международные стартапы + AI-native роли).

Дедуп по ledger, дозапись в радар-файл vault и в Google-таблицу
«Anton job research» (вкладки «Радар RU» и «Радар Worldwide»).

Запись в таблицу идёт через веб-хук Apps Script (OAuth-клиент org_internal
недоступен, поэтому googleapiclient не используется).

Ключи:
  PERPLEXITY_API_KEY      — env или ~/.config/second-brain/perplexity.env
  VACANCY_WEBHOOK_URL     — env или ~/.config/second-brain/vacancy-sheet.env
  VACANCY_WEBHOOK_SECRET  — env или ~/.config/second-brain/vacancy-sheet.env

Запуск:
    ./daily_vacancy_scan.py
    ./daily_vacancy_scan.py --dry-run   # без записи ledger и без записи в Sheet
"""

import argparse
import html
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# --------------------------------------------------------------------------
# Пути и константы
# --------------------------------------------------------------------------

API_URL = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar-pro"
RECENCY = "week"
TIMEOUT = 180

CONFIG_DIR = Path.home() / ".config" / "second-brain"
PERPLEXITY_ENV = CONFIG_DIR / "perplexity.env"
LEDGER_PATH = CONFIG_DIR / "vacancy-scan-seen.json"
WEBHOOK_ENV = CONFIG_DIR / "vacancy-sheet.env"

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
OUTPUTS_DIR = VAULT / "infrastructure" / "Career Strategist" / "outputs"
RADAR_FILE = OUTPUTS_DIR / "{self} {research} радар вакансий – 2026-08-01.md"

# Google Sheet «Anton job research» — запись через веб-хук Apps Script
WEBHOOK_TIMEOUT = 30
SHEET_RU = "Радар RU"
SHEET_WORLDWIDE = "Радар Worldwide"
# Порядок колонок строки (совпадает с шапкой, которую ставит веб-хук)
SHEET_HEADER = [
    "Дата находки",
    "Компания",
    "Роль",
    "Ссылка",
    "Зарплата/дата публикации",
    "Комментарий",
    "Статус ссылки",
]

# --------------------------------------------------------------------------
# hh.ru
# --------------------------------------------------------------------------

HH_SEARCH_URL = "https://hh.ru/search/vacancy"
HH_VACANCY_URL = "https://hh.ru/vacancy/{vacancy_id}"
HH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HH_HEADERS = {"User-Agent": HH_UA, "Accept-Language": "ru-RU,ru;q=0.9"}
HH_TIMEOUT = 30
HH_STATE_RE = re.compile(
    r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', re.S
)

# area=1 — Москва. Последний запрос — общий, по удалёнке (без привязки к городу).
HH_QUERIES = [
    {"label": "руководитель контент-студии", "text": "руководитель контент-студии", "area": "1"},
    {"label": "head of content", "text": "head of content", "area": "1"},
    {"label": "креативный продюсер", "text": "креативный продюсер", "area": "1"},
    {"label": "руководитель спецпроектов", "text": "руководитель спецпроектов", "area": "1"},
    {"label": "head of social media", "text": "head of social media", "area": "1"},
    {"label": "директор по контенту", "text": "директор по контенту", "area": "1"},
    {"label": "руководитель контента (remote)", "text": "руководитель контента", "schedule": "remote"},
]

# Бренд-планка Антона: только узнаваемые без гугла компании.
# Список пополняемый — сравнение по вхождению подстроки, case-insensitive.
BRAND_WHITELIST = [
    "Ozon", "Озон",
    "Яндекс", "Yandex",
    "Т-Банк", "Тинькофф",
    "Альфа",
    "Сбер",
    "МТС", "МТС Медиа",
    "Билайн", "Вымпелком",
    "Авито", "Avito",
    "VK", "ВКонтакте",
    "Wildberries",
    "X5",
    "Магнит",
    "Лемана",
    "12 STOREEZ",
    "Lamoda",
    "Кинопоиск",
    "Okko",
    "Иви", "ivi",
    "Мегафон",
    "Ростелеком",
    "Купер",
    "Самокат",
    "Золотое Яблоко",
    "Aviasales",
    "hh.ru", "HeadHunter",
    "Skyeng",
    "Литрес",
    "Букмейт",
]
_BRAND_WHITELIST_LOWER = [b.lower() for b in BRAND_WHITELIST]
# Короткие маркеры (VK, X5, МТС, ivi/Иви) как чистая подстрока дают ложные
# срабатывания внутри чужих слов («дивизион» содержит «иви»), поэтому для них
# требуем, чтобы соседние символы не были буквами.
_SHORT_BRAND_MAX = 3
_BRAND_MATCHERS = [
    re.compile(r"(?<![^\W\d_])" + re.escape(brand) + r"(?![^\W\d_])")
    if len(brand) <= _SHORT_BRAND_MAX
    else None
    for brand in _BRAND_WHITELIST_LOWER
]

CURRENCY_SIGNS = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYR": "Br"}

# --------------------------------------------------------------------------
# Perplexity (только worldwide)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Ты — ассистент по поиску вакансий. Возвращай ТОЛЬКО реальные опубликованные "
    "вакансии с прямой ссылкой (career-страница компании, LinkedIn, getmatch "
    "и т.п.) и датой публикации, свежие (до 2 недель). Формат каждой: "
    '"Компания | Роль | ссылка | дата | одна строка сути". '
    'Если реальных вакансий не нашлось — напиши "ничего не найдено". '
    "Не выдумывай вакансии и ссылки."
)

QUERIES = [
    (
        "B. Международные стартапы",
        "Find open roles for a Russian-speaking creative producer / head of content / "
        "content lead at international startups (AdTech, MarTech, fintech, gamedev), "
        "remote or with relocation, posted within the last week.",
    ),
    (
        "C. AI-native роли",
        "Найди вакансии AI creative director / AI content lead / head of AI content / "
        "руководитель генеративного продакшена в международных стартапах "
        "(AdTech, MarTech, fintech, gamedev). Локация: remote или релокация.",
    ),
]

VACANCY_LINE_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)+/\S+")
LEADING_JUNK_RE = re.compile(r"^\s*(?:[-*+•—–]|\d+[.)])\s*")

# Perplexity клеит к ссылке маркеры цитат: ...-5367920[8] или ...[8][12]
CITATION_TAIL_RE = re.compile(r"(?:\[\d+\])+$")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
TRAILING_PUNCT = ".,;:)»\"'"

# Проверка ссылок новых вакансий
LINK_CHECK_TIMEOUT = 10
LINK_CHECK_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
LINK_OK_MARK = "✓"
LINK_BAD_MARK = "⚠️ ссылка не открылась"


def normalize_url(url: str) -> str:
    """Чистит ссылку из ответа Perplexity.

    Срезает маркеры цитат ([8], [8][12]) и висячую пунктуацию с хвоста,
    добавляет https:// если схемы нет.
    """
    url = (url or "").strip()
    if not url:
        return url

    # маркеры и пунктуация могут чередоваться: "...[1][2]," → чистим до стабильности
    while True:
        before = url
        url = url.rstrip(TRAILING_PUNCT)
        url = CITATION_TAIL_RE.sub("", url)
        if url == before:
            break

    if url and not SCHEME_RE.match(url):
        url = "https://" + url
    return url


def check_url_alive(url: str) -> bool:
    """HEAD-запрос (fallback GET с Range) — жива ли ссылка. Ошибки не пробрасывает."""
    if not url:
        return False

    def _request(method: str, extra_headers=None):
        headers = {"User-Agent": LINK_CHECK_UA}
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, method=method, headers=headers)
        with urllib.request.urlopen(
            req, timeout=LINK_CHECK_TIMEOUT, context=_SSL_CTX
        ) as resp:
            return getattr(resp, "status", None) or resp.getcode()

    try:
        try:
            status = _request("HEAD")
        except urllib.error.HTTPError as exc:
            if exc.code in (405, 501):
                status = _request("GET", {"Range": "bytes=0-0"})
            else:
                status = exc.code
        return bool(status) and 200 <= status < 400
    except Exception as exc:  # noqa: BLE001 — проверка не должна валить прогон
        print(f"Warning: проверка ссылки {url} не удалась: {exc}", file=sys.stderr)
        return False


# --------------------------------------------------------------------------
# Конфиг
# --------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict:
    """Читает простой .env: игнорирует # и пустые строки, срезает export и кавычки."""
    result = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return result

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[name] = value.strip()
    return result


def get_secret(name: str, env_file: Path) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    return parse_env_file(env_file).get(name, "").strip()


def get_api_key() -> str:
    key = get_secret("PERPLEXITY_API_KEY", PERPLEXITY_ENV)
    if key:
        return key
    print(
        "Ошибка: не найден ключ Perplexity API.\n"
        f"Положи его в {PERPLEXITY_ENV} строкой PERPLEXITY_API_KEY=pplx-...\n"
        "или экспортируй env PERPLEXITY_API_KEY.",
        file=sys.stderr,
    )
    sys.exit(2)


# --------------------------------------------------------------------------
# hh.ru: загрузка и парсинг
# --------------------------------------------------------------------------


def brand_matches(company: str) -> bool:
    """Проходит ли компания бренд-планку (вхождение подстроки, case-insensitive)."""
    name = (company or "").lower()
    if not name:
        return False
    for brand, matcher in zip(_BRAND_WHITELIST_LOWER, _BRAND_MATCHERS):
        if matcher is not None:
            if matcher.search(name):
                return True
        elif brand in name:
            return True
    return False


def _format_compensation(comp) -> str:
    if not isinstance(comp, dict):
        return ""
    low = comp.get("from")
    high = comp.get("to")
    if low is None and high is None:
        return ""
    sign = CURRENCY_SIGNS.get(comp.get("currencyCode") or "", comp.get("currencyCode") or "")

    def fmt(value):
        try:
            return f"{int(value):,}".replace(",", " ")
        except (TypeError, ValueError):
            return str(value)

    if low is not None and high is not None:
        body = f"{fmt(low)}–{fmt(high)}"
    elif low is not None:
        body = f"от {fmt(low)}"
    else:
        body = f"до {fmt(high)}"
    return f"{body} {sign}".strip()


def _format_publication(raw) -> str:
    """publicationTime приходит как {'$': ISO-строка, '@timestamp': int}."""
    value = ""
    if isinstance(raw, dict):
        value = raw.get("$") or ""
    elif isinstance(raw, str):
        value = raw
    if not value:
        return ""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else str(value)


def fetch_hh_vacancies(query: dict) -> list:
    """Один запрос к hh.ru HTML-поиску → список сырых вакансий (до 50)."""
    params = {"text": query["text"]}
    if query.get("area"):
        params["area"] = query["area"]
    if query.get("schedule"):
        params["schedule"] = query["schedule"]
    url = f"{HH_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url, headers=HH_HEADERS)
    with urllib.request.urlopen(req, timeout=HH_TIMEOUT, context=_SSL_CTX) as resp:
        page = resp.read().decode("utf-8", errors="replace")

    match = HH_STATE_RE.search(page)
    if not match:
        raise RuntimeError("не найден блок HH-Lux-InitialState (изменилась вёрстка?)")
    data = json.loads(html.unescape(match.group(1)))
    result = (data.get("vacancySearchResult") or {}).get("vacancies") or []
    if not isinstance(result, list):
        raise RuntimeError("vacancySearchResult.vacancies не список")
    return result


def parse_hh_vacancy(raw: dict, label: str) -> dict:
    """Сырая вакансия hh → нормализованный dict радара."""
    company = ((raw.get("company") or {}).get("name") or "").strip()
    role = (raw.get("name") or "").strip()
    vacancy_id = raw.get("vacancyId")
    url = HH_VACANCY_URL.format(vacancy_id=vacancy_id) if vacancy_id else ""
    area = ((raw.get("area") or {}).get("name") or "").strip()
    salary = _format_compensation(raw.get("compensation"))
    published = _format_publication(raw.get("publicationTime"))

    extra_bits = [bit for bit in (salary, published) if bit]
    extra = " | ".join(extra_bits)
    comment_bits = [bit for bit in (area, f"запрос: {label}") if bit]
    comment = " | ".join(comment_bits)

    line_bits = [company, role, url] + extra_bits + [area]
    line = " | ".join(bit for bit in line_bits if bit)

    return {
        "source": "hh",
        "company": company,
        "role": role,
        "url": url,
        "extra": extra,
        "comment": comment,
        "line": line,
    }


def collect_hh(verbose: bool = True):
    """Все hh-запросы → (вакансии прошедшие фильтр, отфильтровано, успешных запросов)."""
    collected = []
    filtered_out = 0
    queries_done = 0
    seen_urls = set()

    for index, query in enumerate(HH_QUERIES):
        try:
            raw_items = fetch_hh_vacancies(query)
        except Exception as exc:  # noqa: BLE001 — один запрос не валит прогон
            print(
                f"Warning: hh-запрос «{query['label']}» упал — {exc}", file=sys.stderr
            )
            continue

        queries_done += 1
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = parse_hh_vacancy(raw, query["label"])
            if not item["company"] or not item["role"] or not item["url"]:
                continue
            if not brand_matches(item["company"]):
                filtered_out += 1
                continue
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            collected.append(item)

        if verbose:
            print(
                f"hh «{query['label']}»: получено {len(raw_items)}, "
                f"в отборе {len(collected)}",
                file=sys.stderr,
            )
        if index < len(HH_QUERIES) - 1:
            time.sleep(random.uniform(1.0, 2.0))

    return collected, filtered_out, queries_done


# --------------------------------------------------------------------------
# Perplexity
# --------------------------------------------------------------------------


def ask_perplexity(query: str, api_key: str) -> str:
    """Один запрос. Возвращает текст ответа; при ошибке кидает RuntimeError."""
    payload = {
        "model": MODEL,
        "search_recency_filter": RECENCY,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
    }
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
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = "(тело ответа прочитать не удалось)"
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"сеть: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"таймаут {TIMEOUT} c") from exc

    try:
        resp_json = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ответ не JSON: {body[:300]}") from exc

    choices = resp_json.get("choices") or []
    if not choices:
        raise RuntimeError("пустой список choices в ответе API")
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()


# --------------------------------------------------------------------------
# Парсинг вакансий Perplexity
# --------------------------------------------------------------------------


def _clean_field(text: str) -> str:
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"^\[|\]$", "", text.strip())
    return text.strip(" \t*_-—–:")


def parse_vacancies(answer: str):
    """Достаёт строки-вакансии: есть разделитель | и http-ссылка.

    Возвращает список dict: {line, company, role, url, comment, extra, source}.
    """
    found = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "|" not in line:
            continue
        url_match = VACANCY_LINE_RE.search(line)
        if not url_match:
            continue

        raw_url = url_match.group(0)
        url = normalize_url(raw_url)
        # нормализованная ссылка должна попасть в саму строку ДО дедупа,
        # ledger, радар-файла и Sheet
        if url != raw_url:
            line = line.replace(raw_url, url, 1)

        line = LEADING_JUNK_RE.sub("", line, count=1).strip()
        # markdown-таблица: срезаем краевые пайпы и строки-разделители
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        line = line.strip()
        if not line or set(line.replace("|", "").strip()) <= {"-", ":", " "}:
            continue

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            continue

        company = _clean_field(parts[0])
        role = _clean_field(parts[1])
        if not company or not role:
            continue
        # заголовок markdown-таблицы
        if company.lower() in ("компания", "company") and role.lower() in (
            "роль",
            "role",
            "должность",
            "position",
        ):
            continue

        rest = [
            _clean_field(part)
            for part in parts[2:]
            if url not in part and raw_url not in part
        ]
        comment = " | ".join(bit for bit in rest if bit)

        display = line.replace("**", "").replace("`", "").strip()
        found.append(
            {
                "source": "perplexity",
                "line": display,
                "company": company,
                "role": role,
                "url": url,
                "extra": "",
                "comment": comment,
            }
        )
    return found


def dedup_key(vacancy: dict) -> str:
    def norm(value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"\s+", " ", value)
        return value

    return f"{norm(vacancy['company'])}::{norm(vacancy['role'])}"


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


def load_ledger() -> dict:
    try:
        raw = LEDGER_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(
            f"Warning: ledger {LEDGER_PATH} повреждён — начинаю с пустого.",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(LEDGER_PATH)


# --------------------------------------------------------------------------
# Вывод в vault
# --------------------------------------------------------------------------


RADAR_HEADER = """# {self} {research} радар вакансий

Автоматический ежедневный скан вакансий: hh.ru (RU-корпорации по бренд-планке)
и Perplexity Sonar API (worldwide).
Скрипт: `infrastructure/Career Strategist/scripts/daily_vacancy_scan.py`
LaunchAgent: `com.user.vacancy-scan` (ежедневно 10:00).
Результаты также дозаписываются в Google-таблицу «Anton job research».

"""


def append_radar_section(new_ru, new_ww, seen_items, unparsed, filtered_out) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    if not RADAR_FILE.exists():
        RADAR_FILE.write_text(RADAR_HEADER, encoding="utf-8")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    chunk = [
        f"\n## Прогон {stamp}\n",
        f"Новые: {len(new_ru) + len(new_ww)} "
        f"(RU: {len(new_ru)}, worldwide: {len(new_ww)}); "
        f"отфильтровано по бренду: {filtered_out}\n",
    ]

    chunk.append("### RU (hh.ru)\n")
    if new_ru:
        for item in new_ru:
            chunk.append(f"- {item['line']}")
        chunk.append("")
    else:
        chunk.append("_Новых нет._\n")

    chunk.append("### Worldwide (Perplexity)\n")
    if new_ww:
        for item in new_ww:
            chunk.append(f"- {item['line']}")
        chunk.append("")
    else:
        chunk.append("_Новых нет._\n")

    if unparsed:
        chunk.append("### Не распарсилось\n")
        for label, snippet in unparsed:
            chunk.append(f"**{label}** (не распарсилось, проверить формат):\n")
            chunk.append(f"> {snippet}\n")

    if seen_items:
        chunk.append("<details>")
        chunk.append(f"<summary>Уже виденные: {len(seen_items)}</summary>")
        chunk.append("")
        for item in seen_items:
            chunk.append(f"- {item['line']}")
        chunk.append("")
        chunk.append("</details>")
        chunk.append("")

    with RADAR_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(chunk) + "\n")


# --------------------------------------------------------------------------
# Google Sheets через веб-хук Apps Script
# --------------------------------------------------------------------------

_webhook_warned = False


def get_webhook_config():
    """(url, secret) веб-хука или (None, None) с одноразовым warning."""
    global _webhook_warned
    url = get_secret("VACANCY_WEBHOOK_URL", WEBHOOK_ENV)
    secret = get_secret("VACANCY_WEBHOOK_SECRET", WEBHOOK_ENV)
    if url and secret:
        return url, secret
    if not _webhook_warned:
        _webhook_warned = True
        print(
            "Warning: веб-хук не настроен, пишу только радар-файл "
            f"(нужны VACANCY_WEBHOOK_URL и VACANCY_WEBHOOK_SECRET в {WEBHOOK_ENV}).",
            file=sys.stderr,
        )
    return None, None


def post_to_sheet(sheet_name: str, rows) -> bool:
    """POST строк в веб-хук Apps Script. Ошибки не валят прогон, только warning.

    Apps Script отвечает 302-редиректом на script.googleusercontent.com; urllib
    следует за ним, перепосылая запрос как GET — для Apps Script это штатно,
    тело ответа доезжает.
    """
    if not rows:
        return False

    url, secret = get_webhook_config()
    if not url:
        return False

    payload = {"secret": secret, "sheet": sheet_name, "rows": rows}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(
            req, timeout=WEBHOOK_TIMEOUT, context=_SSL_CTX
        ) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        result = json.loads(body)
        if isinstance(result, dict) and result.get("ok") is True:
            return True
        print(
            f"Warning: веб-хук не подтвердил запись в «{sheet_name}»: {body[:300]}",
            file=sys.stderr,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — таблица не должна валить прогон
        print(
            f"Warning: не записалось в «{sheet_name}» через веб-хук: {exc}",
            file=sys.stderr,
        )
        return False


def sheet_rows(items, today: str):
    return [
        [
            today,
            item.get("company", ""),
            item.get("role", ""),
            item.get("url", ""),
            item.get("extra", ""),
            item.get("comment", ""),
            item.get("link_status", ""),
        ]
        for item in items
    ]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="daily_vacancy_scan.py",
        description="Ежедневный сканер вакансий: hh.ru (RU) + Perplexity (worldwide).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Сделать запросы и записать радар-файл, но не трогать ledger и Google Sheet",
    )
    args = parser.parse_args(argv)

    ledger = load_ledger()
    today = datetime.now().strftime("%Y-%m-%d")

    # --- RU: hh.ru ---
    hh_items, filtered_out, hh_done = collect_hh()

    # --- Worldwide: Perplexity ---
    api_key = get_api_key()
    pplx_done = 0
    pplx_items = []
    unparsed = []

    for label, query in QUERIES:
        try:
            answer = ask_perplexity(query, api_key)
        except RuntimeError as exc:
            print(f"Warning: запрос «{label}» упал — {exc}", file=sys.stderr)
            continue

        pplx_done += 1
        parsed = parse_vacancies(answer)
        if parsed:
            pplx_items.extend(parsed)
            continue

        lowered = answer.lower()
        if answer and "ничего не найдено" not in lowered:
            unparsed.append((label, answer[:500]))

    # дедуп внутри прогона + по ledger
    new_ru, new_ww, seen_items = [], [], []
    batch_keys = set()

    for vacancy in hh_items + pplx_items:
        key = dedup_key(vacancy)
        if key in batch_keys:
            continue
        batch_keys.add(key)
        if key in ledger:
            seen_items.append(vacancy)
        elif vacancy["source"] == "hh":
            new_ru.append(vacancy)
        else:
            new_ww.append(vacancy)

    # Проверяем ссылки только новых worldwide-вакансий — чтобы не долбить сеть
    # на каждом прогоне. Ссылки hh канонические (собраны из vacancyId), их не
    # проверяем: колонка «Статус ссылки» для них остаётся пустой.
    # Мёртвую ссылку не выбрасываем: сайты нередко блокируют ботов.
    dead_links = 0
    for vacancy in new_ww:
        if check_url_alive(vacancy["url"]):
            vacancy["link_status"] = LINK_OK_MARK
        else:
            dead_links += 1
            vacancy["link_status"] = LINK_BAD_MARK
        vacancy["line"] = f"{vacancy['line']} {vacancy['link_status']}"

    append_radar_section(new_ru, new_ww, seen_items, unparsed, filtered_out)

    sheet_written = 0
    if not args.dry_run:
        for vacancy in new_ru + new_ww:
            ledger[dedup_key(vacancy)] = {
                "first_seen": today,
                "url": vacancy["url"],
                "source": vacancy["source"],
            }
        save_ledger(ledger)

        if new_ru and post_to_sheet(SHEET_RU, sheet_rows(new_ru, today)):
            sheet_written += len(new_ru)
        if new_ww and post_to_sheet(SHEET_WORLDWIDE, sheet_rows(new_ww, today)):
            sheet_written += len(new_ww)

    print(
        f"hh-запросов: {hh_done}/{len(HH_QUERIES)} | "
        f"Perplexity-запросов: {pplx_done}/{len(QUERIES)} | "
        f"новых RU: {len(new_ru)} | новых worldwide: {len(new_ww)} | "
        f"уже виденных: {len(seen_items)} | "
        f"отфильтровано по бренду: {filtered_out} | "
        f"всего в ledger: {len(ledger)}"
        + (f" | ссылок не открылось: {dead_links}" if dead_links else "")
        + (" | dry-run" if args.dry_run else "")
        + (f" | в Sheet записано: {sheet_written}" if sheet_written else "")
    )
    if unparsed:
        print(f"Не распарсилось ответов: {len(unparsed)} (см. радар-файл)")
    print(f"Файл: {RADAR_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
