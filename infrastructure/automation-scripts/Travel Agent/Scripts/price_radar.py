#!/usr/bin/env python3
"""Радар цен на авиабилеты (Марко): Aviasales Data API → снапшот + md-радар + Google Sheet.

Источник — Travelpayouts (Aviasales) Data API, эндпоинт
`https://api.travelpayouts.com/aviasales/v3/prices_for_dates`: кэш самых дешёвых
билетов, которые пользователи Aviasales находили за последние 48 часов.
Это НЕ живой поиск: цены индикативные, для мониторинга динамики, а не для покупки.

Что делает прогон:
  1. читает маршруты из `config/routes.json`;
  2. по каждому маршруту тянет топ дешёвых вариантов (sorting=price);
  3. пишет сырой снапшот дня в `data/aviasales/YYYY-MM-DD.json` (конверт);
  4. считает минимум и Δ к прошлому минимуму (state в ~/.config/second-brain/);
  5. пишет md-радар дня в `outputs/`;
  6. если настроен веб-хук — дозаписывает строки на вкладку «Радар цен».

Ключи (env-переменная приоритетнее файла ~/.config/second-brain/travel.env):
  TRAVELPAYOUTS_TOKEN     — токен Travelpayouts (личный кабинет → API)
  TRAVEL_WEBHOOK_URL      — URL веб-приложения Apps Script
  TRAVEL_WEBHOOK_SECRET   — общий секрет с веб-хуком

Запуск:
    ./price_radar.py                 # штатный прогон (systemd-таймер)
    ./price_radar.py --once          # прогон + компактная таблица в stdout
    ./price_radar.py --dry-run       # без сети и без записи: что было бы сделано
    ./price_radar.py --json          # результат прогона сырым JSON в stdout
    ./price_radar.py --route mow-bkk-oct2026 -v

Коды возврата: 0 — ок; 1 — все маршруты упали; 2 — нет токена (кроме --dry-run).
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

try:
    from zoneinfo import ZoneInfo

    MSK = ZoneInfo("Europe/Moscow")
except Exception:  # noqa: BLE001 — нет tzdata: Москва круглый год UTC+3
    MSK = timezone(timedelta(hours=3), "MSK")

# --------------------------------------------------------------------------
# Пути
# --------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent
ROUTES_FILE = PROJECT_DIR / "config" / "routes.json"
SNAPSHOT_DIR = PROJECT_DIR / "data" / "aviasales"
OUTPUTS_DIR = PROJECT_DIR / "outputs"

CONFIG_DIR = Path.home() / ".config" / "second-brain"
TRAVEL_ENV = CONFIG_DIR / "travel.env"
STATE_PATH = CONFIG_DIR / "travel-radar-state.json"

# EN DASH перед датой — требование конвенции имён vault
RADAR_NAME = "{{self}} {{research}} радар цен – {date}.md"

# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

API_HOST = "https://api.travelpayouts.com"
PRICES_FOR_DATES_URL = f"{API_HOST}/aviasales/v3/prices_for_dates"
BOOKING_HOST = "https://www.aviasales.ru"

API_TIMEOUT = 45
FETCH_LIMIT = 30          # сколько вариантов просим у API
TOP_N = 10                # сколько показываем в md
ALT_FOR_SHEET = 3         # сколько альтернатив кроме минимума уходит в таблицу
HISTORY_LEN = 60          # длина истории минимумов в state

WEBHOOK_TIMEOUT = 30
SHEET_NAME = "Радар цен"
SHEET_HEADER = [
    "Дата проверки",
    "Маршрут",
    "Вылет",
    "Обратно",
    "Цена ₽",
    "Авиакомпания",
    "Пересадок",
    "Ссылка",
    "Δ к прошлому мин.",
    "Комментарий",
]


# --------------------------------------------------------------------------
# Секреты
# --------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict:
    """Простой .env: игнорирует # и пустые строки, срезает export и кавычки."""
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


def get_secret(name: str, env_file: Path = TRAVEL_ENV) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    return parse_env_file(env_file).get(name, "").strip()


# --------------------------------------------------------------------------
# Конфиг маршрутов
# --------------------------------------------------------------------------


def load_routes(route_filter: str = "") -> tuple:
    """(currency, routes). Валидирует обязательные поля, кидает RuntimeError."""
    try:
        raw = ROUTES_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"не читается {ROUTES_FILE}: {exc}") from exc
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{ROUTES_FILE} не JSON: {exc}") from exc

    currency = (config.get("currency") or "rub").lower()
    routes = config.get("routes") or []
    if not isinstance(routes, list):
        raise RuntimeError("routes в конфиге не список")

    clean = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            raise RuntimeError(f"routes[{index}] не объект")
        for field in ("id", "origin", "destination"):
            if not route.get(field):
                raise RuntimeError(f"routes[{index}]: нет обязательного поля «{field}»")
        if not (route.get("depart_date") or route.get("depart_month")):
            raise RuntimeError(
                f"routes[{index}] ({route['id']}): нужен depart_date или depart_month"
            )
        clean.append(route)

    if route_filter:
        clean = [r for r in clean if r["id"] == route_filter]
        if not clean:
            raise RuntimeError(f"маршрут «{route_filter}» не найден в {ROUTES_FILE}")
    return currency, clean


def route_params(route: dict, currency: str) -> dict:
    """Маршрут из конфига → query-параметры v3/prices_for_dates (без токена)."""
    one_way = bool(route.get("one_way"))
    params = {
        "origin": route["origin"],
        "destination": route["destination"],
        "departure_at": route.get("depart_date") or route.get("depart_month"),
        "currency": currency,
        "sorting": "price",
        "unique": "false",
        "limit": str(FETCH_LIMIT),
        "page": "1",
        "one_way": "true" if one_way else "false",
    }
    if not one_way:
        return_at = route.get("return_date") or route.get("return_month")
        if return_at:
            params["return_at"] = return_at
    if route.get("direct_only"):
        params["direct"] = "true"
    return params


# --------------------------------------------------------------------------
# Запрос к API
# --------------------------------------------------------------------------


def fetch_route(route: dict, currency: str, token: str) -> dict:
    """Один запрос. Возвращает распарсенный ответ; при ошибке — RuntimeError.

    Токен передаётся заголовком X-Access-Token, а не query-параметром, чтобы
    не светить его в URL (логи, journald, история прокси).
    """
    url = f"{PRICES_FOR_DATES_URL}?{urllib.parse.urlencode(route_params(route, currency))}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "X-Access-Token": token,
            "Accept": "application/json",
            "User-Agent": "second-brain-travel-radar/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            err_body = "(тело ответа прочитать не удалось)"
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"сеть: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"таймаут {API_TIMEOUT} c") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ответ не JSON: {body[:200]}") from exc

    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"API вернул success=false: {payload.get('error')}")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError("в ответе нет списка data (изменился контракт API?)")
    return payload


# --------------------------------------------------------------------------
# Нормализация вариантов
# --------------------------------------------------------------------------


def _as_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _iso_short(value: str) -> str:
    """'2026-10-08T22:35:00+03:00' → '2026-10-08 22:35'; мусор возвращаем как есть."""
    text = (value or "").strip()
    if not text:
        return ""
    date_part = text[:10]
    time_part = text[11:16] if len(text) >= 16 and text[10:11] in ("T", " ") else ""
    if len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-":
        return f"{date_part} {time_part}".strip()
    return text


def _fmt_duration(minutes) -> str:
    value = _as_int(minutes)
    if value is None or value <= 0:
        return ""
    return f"{value // 60} ч {value % 60:02d} м"


def _fmt_price(value) -> str:
    price = _as_int(value)
    if price is None:
        return ""
    return f"{price:,}".replace(",", " ")


def _fmt_delta(delta, prev_min) -> str:
    """'−3 400 ₽ / −7.2%' или 'первое измерение'."""
    if prev_min is None or delta is None:
        return "первое измерение"
    if delta == 0:
        return "без изменений"
    sign = "+" if delta > 0 else "−"
    pct = (delta / prev_min * 100) if prev_min else 0
    return f"{sign}{_fmt_price(abs(delta))} ₽ / {sign}{abs(pct):.1f}%"


def normalize_offer(raw: dict) -> dict:
    """Элемент data → плоский dict радара. Терпим к отсутствию полей."""
    if not isinstance(raw, dict):
        return {}
    price = _as_int(raw.get("price") or raw.get("value"))
    if price is None:
        return {}

    link = (raw.get("link") or "").strip()
    if link and not link.startswith("http"):
        link = f"{BOOKING_HOST}/{link.lstrip('/')}"

    duration = raw.get("duration")
    if duration is None:
        to_min = _as_int(raw.get("duration_to")) or 0
        back_min = _as_int(raw.get("duration_back")) or 0
        duration = (to_min + back_min) or None

    transfers = _as_int(raw.get("transfers"))
    return_transfers = _as_int(raw.get("return_transfers"))
    if transfers is None:
        transfers = _as_int(raw.get("number_of_changes"))
    if return_transfers is not None and transfers is not None:
        transfers_text = f"{transfers} / {return_transfers}"
    elif transfers is not None:
        transfers_text = str(transfers)
    else:
        transfers_text = ""

    return {
        "price": price,
        "price_text": _fmt_price(price),
        "departure_at": _iso_short(raw.get("departure_at") or raw.get("depart_date")),
        "return_at": _iso_short(raw.get("return_at") or raw.get("return_date")),
        "airline": (raw.get("airline") or "").strip(),
        "flight_number": str(raw.get("flight_number") or "").strip(),
        "transfers": transfers_text,
        "duration_min": _as_int(duration),
        "duration_text": _fmt_duration(duration),
        "link": link,
    }


def top_offers(payload: dict, limit: int = TOP_N) -> list:
    offers = [normalize_offer(item) for item in payload.get("data") or []]
    offers = [offer for offer in offers if offer]
    offers.sort(key=lambda item: item["price"])
    return offers[:limit]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def load_state() -> dict:
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(
            f"Warning: state {STATE_PATH} повреждён — начинаю с пустого.",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def update_state(state: dict, route_id: str, min_price: int, today: str) -> None:
    entry = state.get(route_id)
    if not isinstance(entry, dict):
        entry = {}
    history = entry.get("history")
    if not isinstance(history, list):
        history = []
    history = [h for h in history if isinstance(h, dict) and h.get("date") != today]
    history.append({"date": today, "min_price": min_price})
    state[route_id] = {
        "min_price": min_price,
        "date": today,
        "history": history[-HISTORY_LEN:],
    }


# --------------------------------------------------------------------------
# Снапшот
# --------------------------------------------------------------------------


def write_snapshot(results: list, currency: str, today: str, now_iso: str) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    envelope = {
        "source": "aviasales_data_api",
        "date": today,
        "fetched_at": now_iso,
        "meta": {
            "currency": currency,
            "routes": [item["route"]["id"] for item in results],
            "endpoint": PRICES_FOR_DATES_URL,
            "errors": sum(1 for item in results if item.get("error")),
        },
        "data": {
            item["route"]["id"]: (
                {"error": item["error"]} if item.get("error") else item["payload"]
            )
            for item in results
        },
    }
    path = SNAPSHOT_DIR / f"{today}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------
# md-радар
# --------------------------------------------------------------------------


def radar_path(today: str) -> Path:
    return OUTPUTS_DIR / RADAR_NAME.format(date=today)


def route_title(route: dict) -> str:
    window = route.get("depart_date") or route.get("depart_month") or "?"
    back = route.get("return_date") or route.get("return_month")
    if route.get("one_way") or not back:
        window_text = f"{window}, в одну сторону"
    else:
        window_text = f"{window} → {back}"
    return f"{route['origin']} → {route['destination']} ({window_text})"


def write_radar(results: list, currency: str, today: str, now_msk: str) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ok_routes = [item for item in results if not item.get("error")]
    failed = [item for item in results if item.get("error")]

    lines = [
        "---",
        "tags: [type/research, project/self, topic/travel, status/active]",
        f"date: {today}",
        "status: active",
        "agent: marco",
        "---",
        "",
        f"# Радар цен – {today}",
        "",
        f"Прогон {now_msk} (Europe/Moscow). Источник: Aviasales Data API "
        f"(`v3/prices_for_dates`, кэш находок пользователей за последние 48 ч), "
        f"валюта `{currency}`. Цены индикативные, за одного пассажира — "
        f"не финальная стоимость бронирования.",
        "",
        f"Маршрутов: {len(results)} (ок: {len(ok_routes)}, ошибок: {len(failed)}).",
        "",
    ]

    for item in results:
        route = item["route"]
        lines.append(f"## {route_title(route)}")
        lines.append("")
        if route.get("note"):
            lines.append(f"_{route['note']}_")
            lines.append("")

        if item.get("error"):
            lines.append(f"**error:** {item['error']}")
            lines.append("")
            continue

        offers = item["offers"]
        if not offers:
            lines.append("_Вариантов не найдено (пустой ответ API)._")
            lines.append("")
            continue

        lines.append(
            "| Вылет | Обратно | Цена ₽ | Авиакомпания | Пересадок | В пути | Ссылка |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for offer in offers:
            link = f"[открыть]({offer['link']})" if offer["link"] else "—"
            airline = offer["airline"] or "—"
            if offer["flight_number"]:
                airline = f"{airline} {offer['flight_number']}"
            lines.append(
                f"| {offer['departure_at'] or '—'} | {offer['return_at'] or '—'} | "
                f"{offer['price_text']} | {airline} | {offer['transfers'] or '—'} | "
                f"{offer['duration_text'] or '—'} | {link} |"
            )
        lines.append("")
        lines.append(
            f"Минимум: {_fmt_price(item['min_price'])} ₽ "
            f"(Δ к прошлому: {item['delta_text']})"
        )
        lines.append("")

    lines.append("## Verification")
    lines.append("")
    if failed and not ok_routes:
        status = "not verified: все маршруты упали"
    elif failed:
        status = f"not verified частично: упало маршрутов {len(failed)} из {len(results)}"
    else:
        status = "ok"
    lines.append(f"- status: {status}")
    lines.append(
        "- проверено: ответ API распарсен, минимум посчитан по полю `price`, "
        "Δ считается к прошлому минимуму из `~/.config/second-brain/travel-radar-state.json`."
    )
    lines.append(
        "- gap: цена — за одного пассажира; поле `adults` из `config/routes.json` "
        "в запрос не уходит (эндпоинт его не принимает) и используется только "
        "как контекст — итог на двоих считать вручную."
    )
    lines.append(
        "- gap: `v3/prices_for_dates` отдаёт кэш находок за 48 ч, а не живой поиск — "
        "по ссылке цена может отличаться."
    )
    if failed:
        for item in failed:
            lines.append(f"- gap: маршрут `{item['route']['id']}` — {item['error']}")

    path = radar_path(today)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Веб-хук Apps Script
# --------------------------------------------------------------------------


def get_webhook_config():
    url = get_secret("TRAVEL_WEBHOOK_URL")
    secret = get_secret("TRAVEL_WEBHOOK_SECRET")
    if url and secret:
        return url, secret
    print(
        "Warning: веб-хук не настроен, пишу только md-радар "
        f"(нужны TRAVEL_WEBHOOK_URL и TRAVEL_WEBHOOK_SECRET в {TRAVEL_ENV}).",
        file=sys.stderr,
    )
    return None, None


def sheet_rows(results: list, today: str) -> list:
    """Строка минимума + до ALT_FOR_SHEET альтернатив на маршрут."""
    rows = []
    for item in results:
        if item.get("error") or not item.get("offers"):
            continue
        route = item["route"]
        label = f"{route['origin']}–{route['destination']} ({route['id']})"
        for index, offer in enumerate(item["offers"][: 1 + ALT_FOR_SHEET]):
            is_min = index == 0
            comment = "минимум" if is_min else "альтернатива"
            if is_min and route.get("note"):
                comment = f"минимум | {route['note']}"
            rows.append(
                [
                    today,
                    label,
                    offer["departure_at"],
                    offer["return_at"],
                    offer["price"],
                    (f"{offer['airline']} {offer['flight_number']}").strip(),
                    offer["transfers"],
                    offer["link"],
                    item["delta_text"] if is_min else "",
                    comment,
                ]
            )
    return rows


def post_to_sheet(rows: list) -> bool:
    """POST строк в веб-хук. Ошибки не валят прогон, только warning."""
    if not rows:
        return False
    url, secret = get_webhook_config()
    if not url:
        return False

    payload = {"secret": secret, "sheet": SHEET_NAME, "rows": rows}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
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
            f"Warning: веб-хук не подтвердил запись в «{SHEET_NAME}»: {body[:300]}",
            file=sys.stderr,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — таблица не должна валить прогон
        print(
            f"Warning: не записалось в «{SHEET_NAME}» через веб-хук: {exc}",
            file=sys.stderr,
        )
        return False


# --------------------------------------------------------------------------
# Вывод в stdout
# --------------------------------------------------------------------------


def print_table(results: list) -> None:
    for item in results:
        route = item["route"]
        print(f"\n{route_title(route)}  [{route['id']}]")
        if item.get("error"):
            print(f"  error: {item['error']}")
            continue
        if not item["offers"]:
            print("  вариантов не найдено")
            continue
        print(
            f"  {'вылет':<17}{'обратно':<17}{'цена':>10}  "
            f"{'ак':<6}{'перес.':<8}{'в пути'}"
        )
        for offer in item["offers"]:
            print(
                f"  {offer['departure_at'] or '—':<17}{offer['return_at'] or '—':<17}"
                f"{offer['price_text']:>10}  {offer['airline'] or '—':<6}"
                f"{offer['transfers'] or '—':<8}{offer['duration_text'] or '—'}"
            )
        print(
            f"  минимум: {_fmt_price(item['min_price'])} ₽ "
            f"(Δ к прошлому: {item['delta_text']})"
        )


def result_json(results: list, currency: str, today: str, now_iso: str) -> dict:
    return {
        "date": today,
        "fetched_at": now_iso,
        "currency": currency,
        "routes": [
            {
                "id": item["route"]["id"],
                "origin": item["route"]["origin"],
                "destination": item["route"]["destination"],
                "error": item.get("error"),
                "min_price": item.get("min_price"),
                "prev_min_price": item.get("prev_min"),
                "delta": item.get("delta"),
                "delta_text": item.get("delta_text"),
                "offers": item.get("offers", []),
            }
            for item in results
        ],
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run_dry(currency: str, routes: list, today: str, as_json: bool) -> int:
    token = get_secret("TRAVELPAYOUTS_TOKEN")
    webhook_url = get_secret("TRAVEL_WEBHOOK_URL")
    webhook_secret = get_secret("TRAVEL_WEBHOOK_SECRET")

    plan = {
        "dry_run": True,
        "date": today,
        "currency": currency,
        "token_present": bool(token),
        "webhook_configured": bool(webhook_url and webhook_secret),
        "would_write": {
            "snapshot": str(SNAPSHOT_DIR / f"{today}.json"),
            "radar": str(radar_path(today)),
            "state": str(STATE_PATH),
            "sheet": SHEET_NAME,
        },
        "requests": [
            {
                "route": route["id"],
                "url": f"{PRICES_FOR_DATES_URL}?"
                + urllib.parse.urlencode(route_params(route, currency)),
            }
            for route in routes
        ],
    }

    if not token:
        print(
            "Warning: нет TRAVELPAYOUTS_TOKEN в "
            f"{TRAVEL_ENV} — боевой прогон сейчас упал бы (exit 2).",
            file=sys.stderr,
        )
    if not (webhook_url and webhook_secret):
        print(
            "Warning: веб-хук не настроен, боевой прогон писал бы только md-радар.",
            file=sys.stderr,
        )

    if as_json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    print(f"dry-run {today}: сети нет, файлы не пишутся. Маршрутов: {len(routes)}.")
    print(f"  токен: {'есть' if token else 'НЕТ'} | "
          f"веб-хук: {'настроен' if webhook_url and webhook_secret else 'не настроен'}")
    for request in plan["requests"]:
        print(f"  GET  [{request['route']}] {request['url']}")
    print("  записал бы:")
    for label, target in plan["would_write"].items():
        print(f"    {label}: {target}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="price_radar.py",
        description="Радар цен на авиабилеты (Aviasales Data API) для агента Марко.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Один прогон + компактная таблица в stdout (для сессии Марко)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Не ходить в сеть и ничего не писать; показать, что было бы сделано",
    )
    parser.add_argument("--json", action="store_true", help="Сырой JSON результата в stdout")
    parser.add_argument("--route", default="", help="Прогнать только один маршрут по id")
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный лог в stderr")
    args = parser.parse_args(argv)

    now = datetime.now(MSK)
    today = now.strftime("%Y-%m-%d")
    now_iso = now.isoformat(timespec="seconds")
    now_msk = now.strftime("%Y-%m-%d %H:%M")

    try:
        currency, routes = load_routes(args.route)
    except RuntimeError as exc:
        print(f"Ошибка конфига: {exc}", file=sys.stderr)
        return 2
    if not routes:
        print(f"В {ROUTES_FILE} нет маршрутов — нечего проверять.", file=sys.stderr)
        return 0

    if args.dry_run:
        return run_dry(currency, routes, today, args.json)

    token = get_secret("TRAVELPAYOUTS_TOKEN")
    if not token:
        print(
            f"not verified: нет TRAVELPAYOUTS_TOKEN в {TRAVEL_ENV}\n"
            "Положи туда строку TRAVELPAYOUTS_TOKEN=... (личный кабинет "
            "travelpayouts.com → раздел API) или экспортируй env-переменную.",
            file=sys.stderr,
        )
        return 2

    state = load_state()
    results = []

    for route in routes:
        entry = {"route": route}
        try:
            payload = fetch_route(route, currency, token)
        except RuntimeError as exc:
            # Один маршрут не валит прогон — помечаем и идём дальше.
            print(f"Warning: маршрут «{route['id']}» упал — {exc}", file=sys.stderr)
            entry["error"] = str(exc)
            results.append(entry)
            continue

        offers = top_offers(payload)
        entry["payload"] = payload
        entry["offers"] = offers

        prev_entry = state.get(route["id"]) if isinstance(state.get(route["id"]), dict) else {}
        prev_min = _as_int(prev_entry.get("min_price"))
        if offers:
            min_price = offers[0]["price"]
            delta = (min_price - prev_min) if prev_min is not None else None
            entry.update(
                {
                    "min_price": min_price,
                    "prev_min": prev_min,
                    "delta": delta,
                    "delta_text": _fmt_delta(delta, prev_min),
                }
            )
            update_state(state, route["id"], min_price, today)
        else:
            entry.update(
                {
                    "min_price": None,
                    "prev_min": prev_min,
                    "delta": None,
                    "delta_text": "нет данных",
                }
            )
        results.append(entry)
        if args.verbose:
            print(
                f"[{route['id']}] вариантов: {len(payload.get('data') or [])}, "
                f"в отбор: {len(offers)}, минимум: {entry.get('min_price')}",
                file=sys.stderr,
            )

    failed = [item for item in results if item.get("error")]
    all_failed = len(failed) == len(results)

    snapshot = write_snapshot(results, currency, today, now_iso)
    radar = write_radar(results, currency, today, now_msk)
    if not all_failed:
        save_state(state)

    rows = sheet_rows(results, today)
    sheet_written = len(rows) if rows and post_to_sheet(rows) else 0

    if args.json:
        print(
            json.dumps(
                result_json(results, currency, today, now_iso), ensure_ascii=False, indent=2
            )
        )
    elif args.once:
        print_table(results)

    if not args.json:
        print(
            f"\nМаршрутов: {len(results)} | ошибок: {len(failed)} | "
            f"строк в Sheet: {sheet_written}"
        )
        print(f"Снапшот: {snapshot}")
        print(f"Радар:   {radar}")

    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
