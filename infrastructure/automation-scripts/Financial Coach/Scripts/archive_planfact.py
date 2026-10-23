#!/usr/bin/env python3
"""Полная архивная выгрузка ПланФакта -> data/planfact_archive/YYYY-MM-DD/.

Зачем: подписка ПланФакта заканчивается (банк-импорт встал 02.10.2026), API на
чтение пока отвечает. Скрипт снимает ВСЁ, что отдаёт API на чтение, чтобы
данные остались у Антона навсегда.

Только чтение. Вызываются GET-эндпоинты и read-only POST-отчёты
(dashboards, reports, */list, */calculated) — ни одного запроса, меняющего
данные. Список эндпоинтов составлен по https://apidoc.planfact.io/ (2026-10-22).

Что пишется в папку прогона:
    <name>.json            — сырой ответ каждого эндпоинта (списки — со всех страниц)
    details/<name>.json    — карточки по id (сделки, счета, бюджеты, origin операций…)
    reports/<name>.json    — отчёты/дашборды за всю историю (помесячно)
    attachments/           — скачанные вложения (если API отдал ссылки)
    operations.csv         — операции по частям, id расшифрованы в названия
    accounts_balances.csv  — остатки на сегодня + на конец каждого месяца
    manifest.json          — эндпоинт, записей, HTTP-статус, время, ошибка

CSV: UTF-8 с BOM, разделитель «;» — открывается в Excel/Numbers.

Ошибка или пустой ответ эндпоинта фиксируется в манифесте, выгрузка идёт дальше.

Примеры:
    python3 archive_planfact.py              # dry-run: печатает план, сеть не трогает
    python3 archive_planfact.py --live       # полная выгрузка
    python3 archive_planfact.py --live --no-origin   # без 1,4 тыс. запросов origin
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import common
from common import EXIT_ERROR, EXIT_OK

import pull_planfact as pf

API_ROOT = "https://api.planfact.io"
V1 = "/api/v1"
V2 = "/api/v2"

ARCHIVE_DIR = common.DATA_DIR / "planfact_archive"
HISTORY_START = date(2023, 1, 1)
PAGE_LIMIT = 500           # часть эндпоинтов молча режет фрейм до 100 — учтено в paged_get
MAX_PAGES = 2000
WORKERS = 1                # лимит API: 20 запросов в минуту на ключ
MIN_INTERVAL = 3.2         # сек между запросами (≈18/мин, с запасом)
QUOTA_RESERVE = 60         # месячная квота 2500 запросов: столько оставляем нетронутыми

log = common.get_logger("planfact_archive")

# --- справочники и списки (GET, с пагинацией) --------------------------------
PAGED_GET = {
    "accounts": "/accounts",
    "accounts_groups": "/accounts/groups",
    "operations": "/operations",
    "operationcategories": "/operationcategories",
    "projects": "/projects",
    "projects_groups": "/projects/groups",
    "contragents": "/contragents",
    "contragents_summary": "/contragents/summary",
    "contragents_groups": "/contragents/groups",
    "companies": "/companies",
    "deals": "/deals",
    "deals_selling": "/deals/selling",
    "deals_purchase": "/deals/purchase",
    "budgets": "/budgets",
    "businesses_actionlog": "/businesses",
    "entity_statuses": "/entity-statuses",
    "importlogs": "/importlogs",
    "invoice_documents": "/invoice-documents",
    "invoice_templates": "/invoice-templates",
    "products": "/products",
    "products_groups": "/products/groups",
}

# --- GET без пагинации ----------------------------------------------------------
PLAIN_GET = {
    "currencies": ("/currencies", {}),
    "operationcategories_group_list": ("/operationcategories/group-list", {}),
    "operations_dates_summary": ("/operations/dates-summary", {}),
    "operations_summary": ("/operations/summary", {}),
    "operations_undistributed_count": ("/operations/get-undistributed-operation-count", {}),
    "deals_summary_selling": ("/deals/summary/selling", {}),
    "deals_summary_purchase": ("/deals/summary/purchase", {}),
    "invoice_documents_summary": ("/invoice-documents/summary", {}),
    "products_count": ("/products/count", {}),
    "products_groups_count": ("/products/groups/count", {}),
    "products_units": ("/products/productUnits", {}),
    "quick_filters_directories": ("/quick-filters/directories", {}),
    "bizinfos_accountshistory": ("/bizinfos/accountshistory", {}),
    "contragents_calculations": ("/contragents/calculations", {}),
    "projects_calc_cashflow": ("/projects/calculations", {"calculationMethod": "Cashflow"}),
    "projects_calc_by_operation_date": ("/projects/calculations",
                                        {"calculationMethod": "IncomeExpensesByOperationDate"}),
    "projects_calc_by_calculation_date": ("/projects/calculations",
                                          {"calculationMethod": "IncomeExpensesByCalculationDate"}),
}

# --- read-only POST-списки (тело = фильтр) -------------------------------------
POST_LISTS = {
    "contragents_list": ("/contragents/list", {}),
    "contragents_calculated_cashflow": ("/contragents/calculated", {"calculationMethod": "Cashflow"}),
    "contragents_calculated_calculation": ("/contragents/calculated", {"calculationMethod": "Calculation"}),
    "contragents_groups_calculated": ("/contragents/groups/calculated", {"calculationMethod": "Cashflow"}),
    "deals_list": ("/deals/list", {}),
    "deals_calculated": ("/deals/calculated", {}),
    "deals_selling_list": ("/deals/selling/list", {}),
    "deals_purchase_list": ("/deals/purchase/list", {}),
}


# ============================================================================
# HTTP
# ============================================================================

class Api:
    def __init__(self, api_key: str, timeout: int = 120):
        import requests  # noqa: PLC0415 — на маке urllib не проходит TLS, см. common
        self.requests = requests
        self.headers = {"X-ApiKey": api_key, "Accept": "application/json"}
        self.timeout = timeout
        self.quota_remaining: int | None = None
        self.quota_used: int | None = None
        self.requests_made = 0
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def quota_ok(self, need: int = 1) -> bool:
        return self.quota_remaining is None or self.quota_remaining - need >= QUOTA_RESERVE

    def call(self, method: str, path: str, params: dict | None = None,
             body: dict | None = None, raw: bool = False) -> tuple[int, Any, str | None]:
        """-> (http_status, payload, error). Никогда не бросает."""
        url = path if path.startswith("http") else API_ROOT + path
        last_err = None
        is_api = url.startswith(API_ROOT)
        for attempt in range(6):
            if is_api:
                if not self.quota_ok():
                    return 0, None, f"пропущено: месячная квота почти исчерпана (осталось {self.quota_remaining})"
                self._throttle()
                self.requests_made += 1
            try:
                if method == "GET":
                    r = self.requests.get(url, params=params, headers=self.headers, timeout=self.timeout)
                else:
                    r = self.requests.post(url, json=body or {}, params=params,
                                           headers={**self.headers, "Content-Type": "application/json"},
                                           timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
                time.sleep(5 * (attempt + 1))
                continue
            if is_api and r.headers.get("X-Quota-Remaining"):
                try:
                    self.quota_remaining = int(r.headers["X-Quota-Remaining"])
                    self.quota_used = int(r.headers.get("X-Quota-Used") or 0)
                except ValueError:
                    pass
            if r.status_code == 429:
                last_err = "HTTP 429 (лимит запросов в минуту)"
                log.info("429 — жду 61 с")
                time.sleep(61)
                continue
            if r.status_code in (502, 503, 504):
                last_err = f"HTTP {r.status_code}"
                time.sleep(10 * (attempt + 1))
                continue
            if raw:
                return r.status_code, r, None if r.ok else f"HTTP {r.status_code}"
            try:
                payload = r.json()
            except ValueError:
                payload = r.text[:2000]
            if not r.ok:
                msg = payload.get("errorMessage") if isinstance(payload, dict) else str(payload)[:300]
                return r.status_code, payload, f"HTTP {r.status_code}: {msg}"
            if isinstance(payload, dict) and payload.get("isSuccess") is False:
                return r.status_code, payload, f"isSuccess=false: {payload.get('errorCode')} {payload.get('errorMessage')}"
            return r.status_code, payload, None
        return 0, None, last_err


def data_of(payload: Any) -> Any:
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def count_of(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return len(data["items"])
        return 1 if data else 0
    return 0 if data in (None, "") else 1


# ============================================================================
# Архиватор
# ============================================================================

class Archiver:
    """Каждый шаг пишет свой файл. Повторный прогон в ту же папку (--resume по
    умолчанию) не перезапрашивает то, что уже выгружено без ошибок, — экономит
    месячную квоту API (2500 запросов)."""

    def __init__(self, api: Api, out: Path, resume: bool = True):
        self.api = api
        self.out = out
        self.resume = resume
        self.manifest: list[dict] = []

    # --- запись ---------------------------------------------------------------
    def save(self, rel: str, payload: dict) -> Path:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def cached(self, rel: str) -> dict | None:
        if not self.resume:
            return None
        path = self.out / rel
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def record(self, name: str, method: str, endpoint: str, status: int | str, records: int,
               started: float, error: str | None = None, file: str | None = None, **extra) -> None:
        row = {
            "name": name, "method": method, "endpoint": endpoint, "http_status": status,
            "records": records, "seconds": round(time.time() - started, 2),
            "fetched_at": common.now_iso(), "file": file, "error": error,
            "empty": records == 0 and not error,
        }
        row.update(extra)
        self.manifest.append(row)
        flag = "ERR " + error if error else ("пусто" if records == 0 else "")
        log.info("%-44s %4s %6s зап. %s%s", name, status, records,
                 "(из кэша) " if extra.get("from_cache") else "", flag)

    # --- типовые выгрузки -----------------------------------------------------
    def paged_get(self, name: str, path: str, params: dict | None = None) -> list[dict]:
        t0 = time.time()
        file = f"{name}.json"
        c = self.cached(file)
        if c and not c.get("error"):
            self.record(name, "GET", V1 + path, c.get("http_status", 200), c["count"], t0,
                        file=file, api_total=c.get("api_total"), from_cache=True)
            return c["items"]
        items: list[dict] = []
        offset, status, err, total, seen_first = 0, None, None, None, set()
        for _ in range(MAX_PAGES):
            q = dict(params or {})
            q.update({"paging.limit": PAGE_LIMIT, "paging.offset": offset})
            status, payload, err = self.api.call("GET", V1 + path, params=q)
            if err:
                break
            d = data_of(payload)
            chunk = (d.get("items") if isinstance(d, dict) else d) or []
            if isinstance(d, dict) and d.get("total"):
                total = d.get("total")
            if not chunk:
                break
            key = hashlib.sha1(json.dumps(chunk, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if key in seen_first:  # эндпоинт игнорирует пагинацию
                break
            seen_first.add(key)
            items.extend(chunk)
            offset += len(chunk)
            # короткая страница = конец; ровно 100 — подозрение на молчаливый кап, листаем дальше
            if len(chunk) < PAGE_LIMIT and len(chunk) != 100:
                break
            if total and offset >= total:
                break
        if err and items:
            err = f"НЕПОЛНАЯ выгрузка ({len(items)} записей): {err}"
        self.save(file, {"endpoint": V1 + path, "method": "GET", "params": params or {},
                         "fetched_at": common.now_iso(), "http_status": status, "count": len(items),
                         "api_total": total, "error": err, "items": items})
        self.record(name, "GET", V1 + path, status, len(items), t0, error=err, file=file,
                    api_total=total)
        return items

    def single(self, name: str, method: str, path: str, params: dict | None = None,
               body: dict | None = None, folder: str = "") -> Any:
        t0 = time.time()
        file = f"{folder}{name}.json"
        c = self.cached(file)
        if c and not c.get("error"):
            d = data_of(c.get("response"))
            self.record(name, method, path, c.get("http_status", 200), count_of(d), t0,
                        file=file, from_cache=True)
            return d
        status, payload, err = self.api.call(method, path, params=params, body=body)
        d = data_of(payload) if not err else None
        self.save(file, {"endpoint": path, "method": method, "params": params, "body": body,
                         "fetched_at": common.now_iso(), "http_status": status, "error": err,
                         "response": payload})
        self.record(name, method, path, status, count_of(d), t0, error=err, file=file)
        return d

    def per_id(self, name: str, path_tpl: str, ids: list, params: dict | None = None) -> dict:
        """GET по каждому id -> один файл details/<name>.json {id: data}.
        При повторном прогоне дозапрашивает только недостающие id."""
        t0 = time.time()
        file = f"details/{name}.json"
        c = self.cached(file) or {}
        results: dict[str, Any] = dict(c.get("items") or {})
        errors: dict[str, str] = {}
        todo = [i for i in ids if str(i) not in results]
        cached_n = len(ids) - len(todo)
        for n, i in enumerate(todo, 1):
            status, payload, err = self.api.call("GET", V1 + path_tpl.format(id=i), params=params)
            if err:
                errors[str(i)] = err
                if err.startswith("пропущено"):
                    for rest in todo[n:]:
                        errors[str(rest)] = err
                    break
            else:
                results[str(i)] = data_of(payload)
            if n % 50 == 0:
                log.info("  %s: %s/%s (квота осталась %s)", name, n, len(todo), self.api.quota_remaining)
                self.save(file, {"endpoint": V1 + path_tpl, "items": results, "errors": errors})
        self.save(file, {"endpoint": V1 + path_tpl, "method": "GET", "requested": len(ids),
                         "fetched_at": common.now_iso(), "errors": errors, "items": results})
        err_txt = None
        if errors:
            err_txt = f"{len(errors)}/{len(ids)} id не выгружено (напр. {next(iter(errors.values()))})"
        self.record(name, "GET", V1 + path_tpl, 200 if results else "-", len(results), t0,
                    error=err_txt, file=file, requested=len(ids), failed=len(errors),
                    from_cache_ids=cached_n)
        return results

    def periodic(self, name: str, path: str, make_body, start: date, end: date) -> None:
        """Отчёт за всю историю: одним запросом за весь период (разбивка по месяцам
        внутри отчёта через standardPeriod=Month); не прошёл — по годам."""
        t0 = time.time()
        file = f"reports/{name}.json"
        c = self.cached(file)
        if c and not c.get("error") and not c.get("errors"):
            self.record(name, "POST", path, 200, c.get("count", 1), t0, file=file,
                        mode=c.get("mode"), from_cache=True)
            return
        status, payload, err = self.api.call("POST", path, body=make_body(start, end))
        if not err:
            n = count_of(data_of(payload))
            self.save(file, {"endpoint": path, "method": "POST", "body": make_body(start, end),
                             "mode": "full_period", "fetched_at": common.now_iso(), "count": n,
                             "error": None, "response": payload})
            self.record(name, "POST", path, status, n, t0, file=file, mode="full_period")
            return
        if (err.startswith("пропущено") or status in (401, 403) or "Forbidden" in err
                or "тариф" in err or "только за весь период" in err):
            self.save(file, {"endpoint": path, "method": "POST", "body": make_body(start, end),
                             "fetched_at": common.now_iso(), "error": err, "response": payload})
            self.record(name, "POST", path, status, 0, t0, error=err, file=file)
            return
        log.info("%s: весь период не прошёл (%s) — по годам", name, err)
        chunks, errors = {}, {}
        for y in range(start.year, end.year + 1):
            ys, ye = max(start, date(y, 1, 1)), min(end, date(y, 12, 31))
            status, payload, e2 = self.api.call("POST", path, body=make_body(ys, ye))
            if e2:
                errors[str(y)] = e2
            else:
                chunks[str(y)] = payload
        err_txt = None
        if errors:
            err_txt = (f"весь период: {err}; по годам {len(errors)} из {len(errors) + len(chunks)} "
                       f"с ошибкой (напр. {next(iter(errors.values()))})")
        self.save(file, {"endpoint": path, "method": "POST", "mode": "yearly",
                         "body_example": make_body(start, end), "fetched_at": common.now_iso(),
                         "count": len(chunks), "full_period_error": err, "error": err_txt if not chunks else None,
                         "errors": errors, "years": chunks})
        self.record(name, "POST", path, status, len(chunks), t0, error=err_txt, file=file,
                    mode="yearly")


# ============================================================================
# Помощники
# ============================================================================

def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    out, cur = [], start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((cur, min(nxt - timedelta(days=1), end)))
        cur = nxt
    return out


def dt(d: date) -> str:
    return f"{d.isoformat()}T00:00:00"


def ids_of(items: list[dict], key: str) -> list:
    return [i[key] for i in items if isinstance(i, dict) and i.get(key) is not None]


def title(obj: Any, key: str = "title") -> str:
    return (obj or {}).get(key) or "" if isinstance(obj, dict) else ""


def safe_name(s: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", s, flags=re.UNICODE).strip("_")[:120] or "file"


# ============================================================================
# CSV
# ============================================================================

def write_operations_csv(path: Path, ops: list[dict], dicts: dict[str, dict]) -> int:
    accounts, cats, projects, agents = (dicts["accounts"], dicts["cats"],
                                        dicts["projects"], dicts["agents"])
    cols = ["operation_id", "part_id", "дата", "период_начисления", "тип", "сумма_части",
            "сумма_операции", "валюта", "сумма_в_валюте_учёта", "счёт", "юрлицо", "статья",
            "проект", "контрагент", "сделка_продажи", "сделка_закупки", "подтверждена",
            "комментарий", "номер_документа", "есть_вложения", "создана", "изменена"]
    n = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols)
        for o in ops:
            acc = o.get("account") or {}
            acc_title = title(acc) or accounts.get(acc.get("accountId"), "")
            cur = (o.get("accountCurrency") or {}).get("currencyCode") or acc.get("currencyCode") or ""
            parts = o.get("operationParts") or [{}]
            for p in parts:
                cat = p.get("operationCategory") or o.get("operationCategory") or {}
                proj = p.get("project") or {}
                ag = p.get("contrAgent") or o.get("contrAgent") or {}
                w.writerow([
                    o.get("operationId"), p.get("operationPartId"),
                    (o.get("operationDate") or "")[:10], (p.get("calculationDate") or "")[:10],
                    o.get("operationType"), p.get("value"), o.get("value"), cur,
                    p.get("valueInUserCurrency", o.get("valueInUserCurrency")),
                    acc_title, title(o.get("accountCompany")),
                    title(cat) or cats.get(cat.get("operationCategoryId"), ""),
                    title(proj) or projects.get(proj.get("projectId"), ""),
                    title(ag) or agents.get(ag.get("contrAgentId"), ""),
                    title(p.get("sellingDeal")), title(p.get("purchaseDeal")),
                    "да" if o.get("isCommitted") else "нет",
                    (o.get("comment") or "").replace("\r", " ").replace("\n", " "),
                    o.get("documentNumber") or "", "да" if o.get("hasAnyAttachments") else "",
                    (o.get("createDate") or "")[:19], (o.get("modifyDate") or "")[:19],
                ])
                n += 1
    return n


def write_balances_csv(path: Path, snapshots: dict[str, dict], accounts: dict) -> int:
    n = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["на_дату", "account_id", "счёт", "валюта", "остаток", "остаток_в_валюте_учёта"])
        for on_date in sorted(snapshots):
            d = snapshots[on_date] or {}
            for it in d.get("items") or []:
                acc = it.get("account") or {}
                aid = acc.get("accountId") or it.get("accountId")
                w.writerow([on_date, aid, title(acc) or accounts.get(aid, ""),
                            acc.get("currencyCode") or it.get("currencyCode") or "",
                            it.get("total", it.get("value")),
                            it.get("totalInUserCurrency", it.get("valueInUserCurrency", ""))])
                n += 1
            w.writerow([on_date, "", "ИТОГО", "", d.get("total"), ""])
    return n


# ============================================================================
# Прогон
# ============================================================================

def run(api: Api, out: Path, today: date, with_origin: bool, resume: bool = True) -> Archiver:
    a = Archiver(api, out, resume=resume)
    months = month_ranges(HISTORY_START, today)
    s, e = HISTORY_START, today

    # 1. справочники и списки
    got: dict[str, list] = {}
    for name, path in PAGED_GET.items():
        got[name] = a.paged_get(name, path)
    for name, (path, params) in PLAIN_GET.items():
        a.single(name, "GET", V1 + path, params=params)
    a.single("currencies_history", "GET", V1 + "/currencies/history",
             params={"filter.startDate": s.isoformat(), "filter.endDate": e.isoformat()})
    a.single("quick_filters", "GET", V1 + "/quick-filters", params={"filter.sectionId": "operations"})
    for name, (path, body) in POST_LISTS.items():
        a.single(name, "POST", V1 + path, body=body)

    ops = got["operations"]
    accounts = got["accounts"]
    deals = got["deals"] + got["deals_selling"] + got["deals_purchase"]
    deal_ids = sorted({d.get("dealId") for d in deals if d.get("dealId")})
    invoice_ids = ids_of(got["invoice_documents"], "invoiceDocumentId")
    budget_ids = ids_of(got["budgets"], "budgetId")
    op_ids = ids_of(ops, "operationId")

    # 2. остатки: сегодня + конец каждого месяца (pull_planfact.fetch_balances)
    t0 = time.time()
    c = a.cached("accounts_balances.json") or {}
    snaps, berr = dict(c.get("by_date") or {}), {}
    points = [me for _, me in months[:-1]] + [today]
    for d in points:
        if d.isoformat() in snaps:
            continue
        if not api.quota_ok():
            berr[d.isoformat()] = "пропущено: квота"
            continue
        api._throttle()
        api.requests_made += 1
        try:
            snaps[d.isoformat()] = pf.fetch_balances(api.headers["X-ApiKey"], d.isoformat())
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"[:300]
            if "429" in msg:  # лимит в минуту — подождать и повторить один раз
                time.sleep(61)
                try:
                    snaps[d.isoformat()] = pf.fetch_balances(api.headers["X-ApiKey"], d.isoformat())
                    continue
                except Exception as exc2:  # noqa: BLE001
                    msg = f"{type(exc2).__name__}: {exc2}"[:300]
            berr[d.isoformat()] = msg
    a.save("accounts_balances.json", {"endpoint": V1 + pf.BALANCES_PATH, "method": "POST",
                                      "fetched_at": common.now_iso(), "errors": berr, "by_date": snaps})
    a.record("accounts_balances", "POST", V1 + pf.BALANCES_PATH, 200 if snaps else "-", len(snaps), t0,
             error=(f"{len(berr)} дат не выгружено: {next(iter(berr.values()))}" if berr else None),
             file="accounts_balances.json", dates_requested=len(points))

    # 3. отчёты за всю историю (одним запросом с помесячной разбивкой)
    base = lambda ms, me: {"periodStartDate": dt(ms), "periodEndDate": dt(me), "standardPeriod": "Month"}  # noqa: E731
    for gen in ("OperationCategory", "Projects", "Deals"):
        a.periodic(f"report_dds_{gen}", V2 + "/reports/dds", lambda ms, me, g=gen: {
            **base(ms, me), "reportGenMethod": g, "isPeriodDetail": True, "withOther": True,
            "includePlanValues": True}, s, e)
        a.periodic(f"report_opu_{gen}", V2 + "/reports/opu", lambda ms, me, g=gen: {
            **base(ms, me), "reportGenMethod": g, "isPeriodDetail": True, "withOther": True,
            "includePlanValues": True, "isCalculation": True}, s, e)
    a.periodic("balance_table", V1 + "/accounting/balance/table", base, s, e)
    for method in ("General", "Operating", "Investment", "Financial", "Moves"):
        a.periodic(f"dash_cashflow_{method}", V2 + "/dashboardpro/cashflow",
                   lambda ms, me, m=method: {**base(ms, me), "method": m}, s, e)
    a.periodic("dash_paymentstructure_cash_flow", V2 + "/dashboardpro/paymentstructure-cash-flow",
               lambda ms, me: {**base(ms, me), "method": "General"}, s, e)
    for calc in (True, False):
        sfx = "calc" if calc else "cash"
        for nm in ("businessprofit", "profitofprojects", "profitabledeals", "profitablecontractors",
                   "rentabilityofbalance", "workingcapital", "dividends"):
            a.periodic(f"dash_{nm}_{sfx}", V2 + f"/dashboardpro/{nm}",
                       lambda ms, me, c=calc: {**base(ms, me), "isCalculation": c}, s, e)
        a.periodic(f"dash_paymentstructure_income_expenses_{sfx}",
                   V2 + "/dashboardpro/paymentstructure-income-expenses",
                   lambda ms, me, c=calc: {**base(ms, me), "isCalculation": c}, s, e)
    a.periodic("dash_liquidityratios", V2 + "/dashboardpro/liquidityratios", base, s, e)
    a.periodic("dash_accountbalance_v2", V2 + "/dashboardpro/accountbalance",
               lambda ms, me: {**base(ms, me), "currentDate": dt(me)}, s, e)
    for bid in budget_ids:
        a.single(f"budget_{bid}_bdr", "POST", V2 + "/dashboardpro/bdr",
                 body={"budgetId": bid, "standardPeriod": "Month", "isCalculation": True}, folder="reports/")
        a.single(f"budget_{bid}_bdds", "POST", V2 + "/dashboardpro/bdds",
                 body={"budgetId": bid, "standardPeriod": "Month", "method": "General"}, folder="reports/")
    for key in ("accounts", "operationcategories", "projects", "contragents"):
        a.single(f"bizinfos_incomeoutcome_by_{key}", "GET", V1 + f"/bizinfos/incomeoutcomehistoryby{key}",
                 params={"filter.periodStartDate": s.isoformat(), "filter.periodEndDate": e.isoformat()},
                 folder="reports/")

    # 4. карточки по id (мелкие)
    company_ids = ids_of(got["companies"], "companyId")
    if company_ids:
        a.per_id("companies_files", "/companies/{id}/files", company_ids)
        a.per_id("companies_currencies", "/companies/{id}/currencies", company_ids)
    proj_ids = ids_of(got["projects"], "projectId")
    if proj_ids:
        a.per_id("projects_summary", "/projects/{id}/summary", proj_ids)
    if deal_ids:
        a.per_id("deals_detail", "/deals/{id}", deal_ids)
        a.per_id("deals_products", "/deals/{id}/deal-products", deal_ids)
    if invoice_ids:
        a.per_id("invoice_documents_detail", "/invoice-documents/{id}", invoice_ids)
        a.per_id("invoice_documents_files", "/invoice-documents/{id}/files", invoice_ids)
    if budget_ids:
        a.per_id("budgets_detail", "/budgets/{id}", budget_ids)
        a.per_id("budgets_settings", "/budgets/settings/{id}", budget_ids)
    agent_ids = ids_of(got["contragents"], "contrAgentId")
    if agent_ids:
        a.per_id("contragents_additional", "/contragents/{id}/additional", agent_ids)

    # 5. история изменений операций — пачками по 50
    entity_history(a, op_ids)

    # 6. вложения
    download_attachments(a, ops, invoice_ids, deal_ids)

    # 7. CSV
    dicts = {
        "accounts": {x.get("accountId"): x.get("title") for x in accounts},
        "cats": {x.get("operationCategoryId"): x.get("title") for x in got["operationcategories"]},
        "projects": {x.get("projectId"): x.get("title") for x in got["projects"]},
        "agents": {x.get("contrAgentId"): x.get("title") for x in got["contragents"]},
    }
    t0 = time.time()
    n = write_operations_csv(out / "operations.csv", ops, dicts)
    a.record("operations.csv", "-", "derived", "-", n, t0, file="operations.csv",
             operations=len(ops))
    t0 = time.time()
    n = write_balances_csv(out / "accounts_balances.csv", snaps, dicts["accounts"])
    a.record("accounts_balances.csv", "-", "derived", "-", n, t0, file="accounts_balances.csv")

    # 8. исходные банковские данные операций — последним: ~1,4 тыс. запросов, под квоту
    if with_origin and op_ids:
        a.per_id("operations_origin", "/operations/{id}/origin", op_ids)
    return a


def entity_history(a: Archiver, op_ids: list) -> None:
    t0 = time.time()
    rel = "details/operations_entity_history.json"
    c = a.cached(rel) or {}
    done = set(c.get("done_batches") or [])
    hist = list(c.get("items") or [])
    herr = []
    for k in range(0, len(op_ids), 50):
        if k in done:
            continue
        batch = op_ids[k:k + 50]
        st, payload, err = a.api.call("GET", V1 + "/entity-history/list",
                                      params={"filter.entityIds": batch, "filter.entityType": "Operation"})
        if err:
            herr.append(err)
            if err.startswith("пропущено") or "Forbidden" in err:
                break
            continue
        d = data_of(payload)
        hist.extend((d.get("items") if isinstance(d, dict) else d) or [])
        done.add(k)
    a.save(rel, {"endpoint": V1 + "/entity-history/list", "fetched_at": common.now_iso(),
                 "done_batches": sorted(done), "errors": herr[:20], "items": hist})
    total_batches = (len(op_ids) + 49) // 50
    a.record("operations_entity_history", "GET", V1 + "/entity-history/list",
             200 if hist else "-", len(hist), t0,
             error=(f"{total_batches - len(done)}/{total_batches} пачек не выгружено: {herr[0]}" if herr else None),
             file=rel)


def download_attachments(a: Archiver, ops: list[dict], invoice_ids: list, deal_ids: list) -> None:
    t0 = time.time()
    att_dir = a.out / "attachments"
    found: list[dict] = []
    # ссылки, встроенные в операции
    for o in ops:
        for doc in o.get("attachedDocuments") or []:
            found.append({"source": "operation.attachedDocuments", "entityId": o.get("operationId"),
                          "doc": doc})
    # вложения сущностей
    targets = [("Operation", o.get("operationId")) for o in ops
               if o.get("hasAnyAttachments") and not o.get("attachedDocuments")]
    targets += [("InvoiceDocument", i) for i in invoice_ids] + [("Deal", d) for d in deal_ids]
    for etype, eid in targets:
        st, payload, err = a.api.call("GET", V1 + "/entity-attachments",
                                      params={"entityId": eid, "entityType": etype})
        if err:
            found.append({"source": "entity-attachments", "entityType": etype, "entityId": eid, "error": err})
            continue
        d = data_of(payload)
        for doc in (d.get("items") if isinstance(d, dict) else d) or []:
            found.append({"source": "entity-attachments", "entityType": etype, "entityId": eid, "doc": doc})

    results = []
    for f in found:
        doc = f.get("doc")
        if not isinstance(doc, dict):
            results.append({**f, "downloaded": False})
            continue
        doc_id = (doc.get("attachedDocumentId") or doc.get("entityAttachmentId")
                  or doc.get("documentId") or doc.get("id"))
        url = doc.get("url") or doc.get("link") or doc.get("downloadUrl")
        err = None
        if not url and doc_id:
            st, payload, err = a.api.call("GET", V1 + f"/attached-documents/{doc_id}")
            d = data_of(payload) if not err else None
            url = d if isinstance(d, str) else (d or {}).get("url") if isinstance(d, dict) else None
        saved = None
        if url and isinstance(url, str):
            try:
                st, resp, err = a.api.call("GET", url, raw=True)
                if not err:
                    fname = safe_name(f"{f.get('entityId')}_{doc_id}_{doc.get('fileName') or doc.get('title') or doc.get('name') or 'file'}")
                    att_dir.mkdir(parents=True, exist_ok=True)
                    (att_dir / fname).write_bytes(resp.content)
                    saved = f"attachments/{fname}"
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
        elif not err:
            err = "нет ссылки/идентификатора для скачивания"
        results.append({**f, "file": saved, "downloaded": bool(saved), "error": err})
    a.save("attachments_index.json", {"fetched_at": common.now_iso(), "items": results})
    ok = sum(1 for r in results if r.get("downloaded"))
    a.record("attachments", "GET", V1 + "/entity-attachments + /attached-documents/{id}",
             "-", ok, t0, error=(f"{len(results) - ok} не скачано" if len(results) - ok else None),
             file="attachments_index.json", found=len(results), checked_entities=len(targets))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="выполнить выгрузку (без флага — dry-run)")
    parser.add_argument("--no-origin", action="store_true", help="не тянуть /operations/{id}/origin")
    parser.add_argument("--date", help="дата папки прогона, по умолчанию сегодня")
    parser.add_argument("--fresh", action="store_true",
                        help="перезапросить всё, не используя уже выгруженные файлы папки")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.date) if args.date else date.today()
    out = ARCHIVE_DIR / today.isoformat()
    if not args.live:
        print(f"DRY-RUN. Папка: {out}")
        print(f"Списки GET: {len(PAGED_GET)}, GET: {len(PLAIN_GET) + 2}, POST-списки: {len(POST_LISTS)}")
        print(f"Отчёты: с {HISTORY_START} по {today}, {len(month_ranges(HISTORY_START, today))} мес.")
        return EXIT_OK

    api_key = common.get_secret("PLANFACT_API_KEY", None, required=True)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        api = Api(api_key)
        a = run(api, out, today, with_origin=not args.no_origin, resume=not args.fresh)
    except Exception as exc:  # noqa: BLE001
        log.error("прогон упал: %s", exc)
        return EXIT_ERROR
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    common.write_json(out / "manifest.json", {
        "source": "planfact", "api_root": API_ROOT, "doc": "https://apidoc.planfact.io/",
        "run_date": today.isoformat(), "finished_at": common.now_iso(),
        "seconds": round(time.time() - started, 1), "history_start": HISTORY_START.isoformat(),
        "size_bytes": size, "requests_this_run": api.requests_made,
        "api_quota": {"limit_per_month": 2500, "used": api.quota_used,
                      "remaining": api.quota_remaining, "resets": "1-е число месяца"},
        "endpoints": a.manifest,
        "errors": [m["name"] for m in a.manifest if m.get("error")],
        "empty": [m["name"] for m in a.manifest if m.get("empty")],
    })
    log.info("готово: %s, %.1f МБ, %s эндпоинтов, ошибок %s", out, size / 1e6, len(a.manifest),
             sum(1 for m in a.manifest if m.get("error")))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
