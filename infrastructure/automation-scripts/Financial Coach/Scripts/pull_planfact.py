#!/usr/bin/env python3
"""Забор бизнес-финансов из ПланФакта -> data/planfact/YYYY-MM-DD.json.

Тянет ПОЛНЫЙ срез за прогон: все операции с начала истории аккаунта
(пагинация paging.limit/paging.offset) + все счета с остатками.

Инкрементальности в v1 нет намеренно: объём небольшой (≈1,4 тыс. операций,
единицы мегабайт, единицы секунд), а полный срез каждый день даёт корректные
задним числом правки в ПланФакте и снимает целый класс багов рассинхрона.
Поэтому state.json этот скрипт не пишет и не читает — в отличие от ZenMoney.

Особенности API (проверено живыми запросами 2026-08-15):
    база     https://api.planfact.io/api/v1     (дока: https://apidoc.planfact.io/)
    аутентификация — заголовок X-ApiKey
    ответ    {"data": {"items": [...], "total": N}, "isSuccess": true, ...}
    поле data.total на /operations возвращается нулём — на него НЕ полагаемся,
    признак конца пагинации — короткая (меньше лимита) страница.
    ВАЖНО: GET /accounts отдаёт remainder=0 по всем счетам (поле не заполняется).
    Настоящие остатки живут в POST /dashboards/accountbalance — его и берём как
    источник истины по деньгам на счетах; /accounts остаётся справочником счетов.

Сеть вызывается ТОЛЬКО с флагом --live. По умолчанию dry-run.

Примеры:
    python3 pull_planfact.py            # dry-run, ничего не трогает
    python3 pull_planfact.py --live     # полный срез + запись снапшота
"""

from __future__ import annotations

import argparse

import common
from common import EXIT_ERROR, EXIT_OK, EXIT_SKIPPED

SOURCE = "planfact"

API_BASE = "https://api.planfact.io/api/v1"

ENDPOINTS = {
    "accounts": "/accounts",          # справочник счетов (remainder тут всегда 0)
    "operations": "/operations",      # операции за всю историю
}

#: остатки на счетах — только POST-эндпоинт показателей
BALANCES_PATH = "/dashboards/accountbalance"

#: размер страницы пагинации
PAGE_LIMIT = 500

#: предохранитель от бесконечного цикла, если API вдруг перестанет укорачивать страницы
MAX_PAGES = 400

log = common.get_logger("planfact")


def unwrap(response: object, endpoint: str) -> dict:
    """Разворачивает конверт ПланФакта и проверяет isSuccess."""
    if not isinstance(response, dict):
        raise RuntimeError(f"{endpoint}: неожиданный ответ {type(response).__name__}")
    if response.get("isSuccess") is False:
        raise RuntimeError(
            f"{endpoint}: API вернул ошибку "
            f"{response.get('errorCode')} {response.get('errorMessage')}"
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{endpoint}: в ответе нет объекта data")
    return data


def fetch_all(endpoint_key: str, api_key: str, timeout: int = 120) -> list[dict]:
    """Живой GET со сквозной пагинацией: собирает items со всех страниц."""
    url = API_BASE + ENDPOINTS[endpoint_key]
    headers = {"X-ApiKey": api_key}
    items: list[dict] = []
    offset = 0

    for page in range(MAX_PAGES):
        params = {"paging.limit": PAGE_LIMIT, "paging.offset": offset}
        raw = common.http_get_json(url, params=params, headers=headers, timeout=timeout)
        chunk = unwrap(raw, endpoint_key).get("items") or []
        items.extend(chunk)
        log.debug("%s: страница %s, offset=%s, получено %s", endpoint_key, page, offset, len(chunk))
        if len(chunk) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    else:
        raise RuntimeError(f"{endpoint_key}: превышен лимит {MAX_PAGES} страниц — похоже на зацикливание")

    log.info("%s: получено %s записей", endpoint_key, len(items))
    return items


def fetch_balances(api_key: str, date: str, timeout: int = 120) -> dict:
    """Остатки на счетах на дату снапшота.

    Тело запроса обязано содержать currentDate/periodStartDate/periodEndDate.
    Остатки в items[].total считаются на currentDate, период влияет только на
    графики-детализацию, поэтому берём короткое окно (30 дней) — снапшот легче.
    """
    current = f"{date}T00:00:00"
    start = f"{common.shift_days(date, -30)}T00:00:00"
    body = {"currentDate": current, "periodStartDate": start, "periodEndDate": current}
    raw = common.http_post_json(
        API_BASE + BALANCES_PATH,
        body,
        headers={"X-ApiKey": api_key},
        timeout=timeout,
    )
    data = unwrap(raw, "balances")
    log.info("balances: итого %s (счетов в разрезе: %s)",
             data.get("total"), len(data.get("items") or []))
    return data


def summarize(operations: list[dict], accounts: list[dict], balances: dict) -> dict:
    """Счётчики для meta снапшота (без сумм — агрегаты считает слой модели)."""
    dates = sorted(o.get("operationDate") for o in operations if o.get("operationDate"))
    types: dict[str, int] = {}
    for op in operations:
        key = str(op.get("operationType"))
        types[key] = types.get(key, 0) + 1
    return {
        "operations_count": len(operations),
        "operations_by_type": types,
        "operation_date_min": dates[0] if dates else None,
        "operation_date_max": dates[-1] if dates else None,
        "accounts_count": len(accounts),
        "accounts_active": sum(1 for a in accounts if a.get("active")),
        "balances_total_user_currency": balances.get("total"),
        "balances_items": len(balances.get("items") or []),
        "api_base": API_BASE,
        "incremental": False,  # каждый прогон — полный срез, state не ведётся
    }


def collect(api_key: str, date: str) -> dict:
    """Полный срез: все операции + справочник счетов + остатки на дату."""
    return {
        "operations": fetch_all("operations", api_key),
        "accounts": fetch_all("accounts", api_key),
        "balances": fetch_balances(api_key, date),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Синк ПланФакт -> data/planfact/YYYY-MM-DD.json")
    common.add_common_args(parser)
    args = parser.parse_args(argv)
    common.apply_common_args(args, log)

    env = common.load_env()
    api_key = common.get_secret("PLANFACT_API_KEY", env, required=False)
    if not api_key:
        log.warning("PLANFACT_API_KEY не найден — шаг пропущен")
        return EXIT_SKIPPED

    log.info("Ключ: %s · эндпоинты: %s", common.mask(api_key), list(ENDPOINTS))

    if not args.live:
        log.info("DRY-RUN: запросы не отправлены (%s, страница %s)", API_BASE, PAGE_LIMIT)
        log.info("DRY-RUN: записал бы %s", common.snapshot_path(SOURCE, args.date, args.data_dir))
        return EXIT_OK

    try:
        payload = collect(api_key, common.today_str(args.date))
    except Exception as exc:
        log.error("Запрос к ПланФакту не удался: %s", exc)
        return EXIT_ERROR

    meta = summarize(payload["operations"], payload["accounts"], payload["balances"])
    log.info("Итог: %s операций (%s…%s), %s счетов, остаток %s",
             meta["operations_count"], meta["operation_date_min"],
             meta["operation_date_max"], meta["accounts_count"],
             meta["balances_total_user_currency"])
    common.write_snapshot(SOURCE, payload, date=args.date, data_dir=args.data_dir, meta=meta)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
