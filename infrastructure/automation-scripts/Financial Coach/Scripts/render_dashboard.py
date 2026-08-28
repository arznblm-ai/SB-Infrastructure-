#!/usr/bin/env python3
"""Детерминированный рендер финансового дашборда: data/model.json -> dashboard/index.html.

Без LLM (cost gate плана): данные + шаблон = HTML. Плюс копия в ~/Desktop/Vibecode OUT/
(постоянное правило Антона).

Публикация (VPS): если задан FINANCE_PUBLISH_DIR (переменная окружения или ключ в
finance.env) — готовый index.html атомарно кладётся в этот каталог (там его отдаёт Caddy,
см. Scripts/deploy/README.md). На маке переменная не задана — шаг просто пропускается.
Нет каталога или нет прав — предупреждение в stderr, но не падение: рендер уже состоялся.

Что откуда:
  * план-слой (приходы, долги, налоги, burn, pipeline) — data/model.json;
  * живые остатки счетов — последний data/planfact/YYYY-MM-DD.json, поле data.balances
    (только рублёвые счета; USD/крипто-счета в ликвидное не входят — Конституция модели, п.5).
Остатки в model.json НЕ хардкодятся: accounts.liquid_total = null, цифра приходит из снапшота.

Плюс сторож задвоения: прогнозные строки модели сверяются с фактическими приходами
из того же снапшота (только сумма и окно дат — названия в плане и в выписке не совпадают).
Совпадение у строки без received -> жёлтый блок вверху + WARNING в stderr; у строки
с received=true -> зелёная пометка «подтверждено фактом». Ничего не удаляется автоматически.

Вёрстка наследует референс dashboard/finance_dashboard_2026_08_14_v9.html (карточки, таблицы,
слайдеры, тумблеры, график, вердикт). Прогноз считает vanilla JS на странице, данные лежат
одним JSON-блоком <script id="model-data"> — вёрстка и цифры разделены.

Коды возврата: 0 — ок, 3 — нет model.json (шаг синка пропускается, не фейл), 1 — ошибка.

По умолчанию dry-run: только валидация, без записи файлов. Запись включает --live.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any

import common
from common import EXIT_ERROR, EXIT_OK, EXIT_SKIPPED

#: контракт model.json — по data/README.md департамента
REQUIRED_KEYS = ("updated_at",)
RECOMMENDED_KEYS = (
    "accounts",           # остатки по счетам (ПланФакт главнее скриншотов)
    "confirmed_income",   # подтверждённые приходы
    "team_debts",         # долги команде (не задваивать с confirmed_income)
    "pipeline",           # переговоры (НЕ смешивать с подтверждённым)
    "burn_monthly",       # расходная база
    "one_off_expenses",   # разовые расходы
    "tax_rules",          # 12% по умолчанию, исключения
    "crypto_reference",   # подушка вне операционки, справочно
)

NBSP = " "

# --- сторож задвоения --------------------------------------------------------
# Мотив: 14.08 пришёл платёж «Аллфуд» 140 300, а прогнозная строка «Алмафуд остаток»
# 140 000 провисела в модели до 27.08 — 12 дней задвоения (~119к чистыми).
# Названия в плане и в выписке не совпадают дословно, поэтому матчинг только
# детерминированный: по СУММЕ (относительный допуск) и по ОКНУ ДАТ. Ничего не
# удаляется автоматически — решение об исключении строки принимает Антон/Том.

#: допуск по сумме: |факт − план| / план
DUPE_TOLERANCE = 0.05
#: окно поиска факта начинается за столько дней до первого числа месяца строки
DUPE_WINDOW_DAYS = 45
#: глубина выборки фактических приходов из снапшота
DUPE_LOOKBACK_DAYS = 90

log = common.get_logger("render")


# --- утилиты форматирования --------------------------------------------------


def fmt_rub(value: float | int | None, suffix: str = f"{NBSP}₽") -> str:
    """1234567.4 -> '1 234 567 ₽' (неразрывные пробелы, без округления вверх)."""
    if value is None:
        return "—"
    sign = "−" if value < 0 else ""
    return sign + f"{abs(round(value)):,}".replace(",", NBSP) + suffix


def esc(value: Any) -> str:
    return html.escape(str(value))


def rich(value: Any) -> str:
    """Поля модели, где допустим простой инлайн-HTML (<b> в подписях тумблеров)."""
    return str(value)


def short_name(project: str, amount: float) -> str:
    """'Френдс (личный гонорар)' + 300000 -> 'Френдс 300к' (для подписей в прогнозе)."""
    base = project.split("(")[0].split("/")[0].strip()
    return f"{base} {round(amount / 1000)}к"


# --- данные ------------------------------------------------------------------


def validate_model(model: object) -> list[str]:
    """Простая проверка схемы. Возвращает список проблем (пустой = ок)."""
    problems: list[str] = []
    if not isinstance(model, dict):
        return [f"model.json должен быть объектом, а не {type(model).__name__}"]
    for key in REQUIRED_KEYS:
        if key not in model:
            problems.append(f"нет обязательного ключа: {key}")
    for key in RECOMMENDED_KEYS:
        if key not in model:
            log.warning("В модели нет рекомендованного ключа: %s", key)
    for item in model.get("confirmed_income", []):
        expected = round(item.get("amount", 0) - item.get("team_debt", 0) - item.get("tax", 0))
        if round(item.get("net", expected)) != expected:
            problems.append(
                f"{item.get('project')}: net {item.get('net')} != выручка − команде − налог ({expected})"
            )
    # долг с paid_by_schedule гасится графиком (one_off_expenses), в team_debt приходов не входит
    team_sum = sum(d.get("amount", 0) for d in model.get("team_debts", []) if not d.get("paid_by_schedule"))
    income_team = sum(i.get("team_debt", 0) for i in model.get("confirmed_income", []))
    if team_sum and income_team and team_sum != income_team:
        problems.append(
            f"долги команде не сходятся: детализация {team_sum} vs в приходах {income_team}"
        )
    return problems


def read_live_balances(data_dir: Path) -> dict[str, Any]:
    """Живые остатки из последнего снапшота ПланФакта (конверт: сырьё в ['data']).

    Ликвидное = только рублёвые счета. Валютные/крипто-счета возвращаются отдельным
    списком — они показываются справочно и в оперативный расчёт не входят.
    """
    result: dict[str, Any] = {
        "snapshot": None, "date": None, "liquid_total": None,
        "rub_accounts": [], "fx_accounts": [], "verified": False,
        "problem": None,
    }
    snap = common.latest_snapshot("planfact", data_dir)
    if snap is None:
        result["problem"] = "снапшота ПланФакта нет"
        return result
    result["snapshot"] = snap.name
    envelope = common.read_json(snap)
    if not isinstance(envelope, dict):
        result["problem"] = f"снапшот нечитаем: {snap.name}"
        return result
    result["date"] = envelope.get("date")
    balances = (envelope.get("data") or {}).get("balances") or {}
    items = balances.get("items") or []
    if not items:
        result["problem"] = f"в снапшоте {snap.name} нет data.balances.items"
        return result

    total = 0.0
    for item in items:
        account = item.get("account") or {}
        title = account.get("title", "").strip() or f"счёт {account.get('accountId')}"
        currency = account.get("currencyCode") or "?"
        row = {
            "title": title,
            "currency": currency,
            "amount": item.get("total") or 0.0,
            "amount_rub": item.get("totalInUserCurrency") or 0.0,
            "active": bool(account.get("active")),
        }
        if currency == "RUB":
            total += row["amount"]
            result["rub_accounts"].append(row)
        else:
            result["fx_accounts"].append(row)
    result["rub_accounts"].sort(key=lambda r: -r["amount"])
    result["fx_accounts"].sort(key=lambda r: -r["amount_rub"])
    result["liquid_total"] = round(total, 2)
    result["verified"] = True
    return result


# --- сторож задвоения: факт из ПланФакта vs прогнозные строки модели ---------


def op_contragent(op: dict) -> str:
    """Контрагент операции: верхний уровень пуст почти всегда — берём из operationParts."""
    top = ((op.get("contrAgent") or {}).get("title") or "").strip()
    if top:
        return top
    names: list[str] = []
    for part in op.get("operationParts") or []:
        title = ((part.get("contrAgent") or {}).get("title") or "").strip()
        if title and title not in names:
            names.append(title)
    return ", ".join(names) or "контрагент не указан"


def read_recent_incomes(
    data_dir: Path, today: str, lookback_days: int = DUPE_LOOKBACK_DAYS
) -> dict[str, Any]:
    """Фактические приходы по РУБЛЁВЫМ счетам за последние N дней.

    Сырьё — тот же снапшот ПланФакта, что и остатки (конверт: ['data']['operations']).
    Валютные/крипто-счета не берём: модель считает рубли (Конституция модели, п.5).
    """
    result: dict[str, Any] = {
        "snapshot": None, "date": None, "since": common.shift_days(today, -lookback_days),
        "ops": [], "problem": None,
    }
    snap = common.latest_snapshot("planfact", data_dir)
    if snap is None:
        result["problem"] = "снапшота ПланФакта нет"
        return result
    result["snapshot"] = snap.name
    envelope = common.read_json(snap)
    if not isinstance(envelope, dict):
        result["problem"] = f"снапшот нечитаем: {snap.name}"
        return result
    result["date"] = envelope.get("date")
    operations = (envelope.get("data") or {}).get("operations")
    if not isinstance(operations, list):
        result["problem"] = f"в снапшоте {snap.name} нет data.operations"
        return result

    since = result["since"]
    for op in operations:
        if op.get("operationType") != "Income":
            continue
        date_str = op.get("operationDate") or ""
        if not date_str or date_str[:10] < since or date_str[:10] > today:
            continue
        account = op.get("account") or {}
        currency = account.get("currencyCode") or (op.get("accountCurrency") or {}).get("currencyCode")
        if currency != "RUB":
            continue
        try:
            date_obj = _date.fromisoformat(date_str[:10])
        except ValueError:
            continue
        result["ops"].append({
            "operation_id": op.get("operationId"),
            "date": date_str[:10],
            "date_obj": date_obj,
            "value": float(op.get("value") or 0.0),
            "contragent": op_contragent(op),
            "comment": (op.get("comment") or "").strip(),
            "account": account.get("title") or "?",
        })
    result["ops"].sort(key=lambda o: o["date"])
    return result


def month_start(month_key: Any) -> _date | None:
    """'2026-09' -> date(2026, 9, 1); всё непонятное -> None."""
    try:
        year, month = str(month_key).split("-")[:2]
        return _date(int(year), int(month), 1)
    except (ValueError, TypeError):
        return None


def model_income_rows(model: dict) -> list[dict[str, Any]]:
    """Строки прогноза, которые сторож проверяет: confirmed_income + pipeline (тумблеры)."""
    rows: list[dict[str, Any]] = []
    for item in model.get("confirmed_income", []):
        rows.append({
            "id": item.get("id"),
            "project": item.get("project", "?"),
            "amount": item.get("amount", 0) or 0,
            "month": item.get("month"),
            "received": bool(item.get("received")),
            "kind": "confirmed",
        })
    for item in model.get("pipeline", []):
        rows.append({
            "id": item.get("id"),
            "project": item.get("project", "?"),
            "amount": item.get("estimate", 0) or 0,
            "month": item.get("month"),
            "received": bool(item.get("received")),
            "kind": "pipeline",
        })
    return rows


def find_income_duplicates(model: dict, incomes: dict[str, Any], today: str) -> dict[str, Any]:
    """Сверяет прогнозные строки с фактическими приходами.

    Матчинг детерминированный, без имён:
      * сумма: |факт − план| / план <= DUPE_TOLERANCE (5%);
      * дата: operationDate в окне [первое число месяца строки − 45 дней; сегодня].
    Строки с received=true проверяются первыми — для них совпадение означает
    «подтверждено фактом» (зелёный статус), а не подозрение на задвоение.
    Один фактический приход закрывает максимум одну строку.
    """
    report: dict[str, Any] = {
        "suspects": [], "confirmed": [], "checked_rows": 0,
        "ops_count": len(incomes.get("ops") or []),
        "problem": incomes.get("problem"),
        "since": incomes.get("since"),
        "snapshot": incomes.get("snapshot"),
    }
    ops = incomes.get("ops") or []
    if not ops:
        return report

    today_obj = _date.fromisoformat(today)
    rows = model_income_rows(model)
    rows.sort(key=lambda r: 0 if r["received"] else 1)  # received — в первую очередь
    used: set[Any] = set()

    for row in rows:
        amount = row["amount"]
        start = month_start(row["month"])
        if amount <= 0 or start is None:
            log.debug("Сторож пропустил строку %s (сумма %s, месяц %s)",
                      row["id"], amount, row["month"])
            continue
        window_start = start - timedelta(days=DUPE_WINDOW_DAYS)
        report["checked_rows"] += 1

        best: tuple[float, dict[str, Any]] | None = None
        for op in ops:
            if op["operation_id"] in used:
                continue
            if op["date_obj"] < window_start or op["date_obj"] > today_obj:
                continue
            delta = abs(op["value"] - amount) / amount
            if delta > DUPE_TOLERANCE:
                continue
            if best is None or delta < best[0]:
                best = (delta, op)
        if best is None:
            continue

        delta, op = best
        used.add(op["operation_id"])
        match = {
            "row": row,
            "op": op,
            "delta": round(op["value"] - amount, 2),
            "delta_pct": round(delta * 100, 2),
            "window": f"{window_start.isoformat()}…{today}",
        }
        if row["received"]:
            report["confirmed"].append(match)
        else:
            report["suspects"].append(match)
    return report


def log_duplicates(report: dict[str, Any]) -> None:
    """Тот же сигнал, что и на дашборде, но в stderr рендера."""
    if report.get("problem"):
        log.warning("Сторож задвоения: not verified — %s", report["problem"])
        return
    log.info("Сторож задвоения: проверено строк %s против %s фактических приходов с %s "
             "(допуск %.0f%%, окно −%s дн от месяца строки)",
             report["checked_rows"], report["ops_count"], report["since"],
             DUPE_TOLERANCE * 100, DUPE_WINDOW_DAYS)
    for m in report["confirmed"]:
        op, row = m["op"], m["row"]
        log.info("ПОДТВЕРЖДЕНО ФАКТОМ: строка «%s» (%s ₽, received=true) = приход %s, %s, %s ₽ "
                 "(расхождение %s ₽ / %s%%)",
                 row["project"], round(row["amount"]), op["date"], op["contragent"],
                 round(op["value"]), m["delta"], m["delta_pct"])
    for m in report["suspects"]:
        op, row = m["op"], m["row"]
        log.warning("ВОЗМОЖНОЕ ЗАДВОЕНИЕ: строка прогноза «%s» (%s ₽) похожа на уже пришедший "
                    "платёж: %s, %s, %s ₽ — проверь задвоение",
                    row["project"], round(row["amount"]), op["date"], op["contragent"],
                    round(op["value"]))
    if not report["suspects"] and not report["confirmed"]:
        log.info("Сторож задвоения: совпадений нет")


def dupe_marks(report: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """id строки модели -> совпадение (для пометок прямо в таблице приходов)."""
    marks: dict[Any, dict[str, Any]] = {}
    for kind in ("confirmed", "suspects"):
        for m in report.get(kind, []):
            rid = m["row"].get("id")
            if rid is not None:
                marks[rid] = {**m, "status": "ok" if kind == "confirmed" else "warn"}
    return marks


def team_detail_map(model: dict) -> dict[str, str]:
    """Проект -> 'креатив 100 + дизайн 150' (в тысячах), для колонки «Команде»."""
    grouped: dict[str, list[str]] = {}
    for debt in model.get("team_debts", []):
        grouped.setdefault(debt.get("project", ""), []).append(
            f"{debt.get('item', '')} {round(debt.get('amount', 0) / 1000)}"
        )
    return {k: " + ".join(v) for k, v in grouped.items()}


def build_page_data(model: dict, balances: dict[str, Any]) -> dict[str, Any]:
    """JSON-блок для JS: всё, от чего считается прогноз (вёрстка отдельно)."""
    start = balances["liquid_total"]
    if start is None:
        start = (model.get("accounts", {}).get("handoff_reference", {}) or {}).get("liquid_total", 0)

    flows: list[dict[str, Any]] = []
    toggles: list[dict[str, Any]] = []
    seen_toggles: set[str] = set()

    def push_toggle(spec: dict[str, Any] | None, kind: str) -> str | None:
        if not spec:
            return None
        tid = spec.get("id")
        if tid and tid not in seen_toggles:
            seen_toggles.add(tid)
            toggles.append({
                "id": tid,
                "label": spec.get("label", tid),
                "default": bool(spec.get("default", True)),
                "off": spec.get("off", "shift"),
                "kind": kind,
            })
        return tid

    for item in model.get("confirmed_income", []):
        tid = push_toggle(item.get("toggle"), "confirmed")
        flows.append({
            "id": item.get("id"),
            "name": short_name(item.get("project", "?"), item.get("amount", 0)),
            "revenue": item.get("amount", 0),
            "tax": item.get("tax", 0),
            "team": item.get("team_debt", 0),
            "month": item.get("month"),
            "alt_month": item.get("alt_month"),
            "toggle": tid,
            "kind": "confirmed",
        })
    for item in model.get("pipeline", []):
        tid = push_toggle(item.get("toggle"), "pipeline")
        flows.append({
            "id": item.get("id"),
            "name": short_name(item.get("project", "?"), item.get("estimate", 0)),
            "revenue": item.get("estimate", 0),
            "tax": item.get("tax", 0),
            "team": item.get("team_cost", 0),
            "month": item.get("month"),
            "alt_month": None,
            "toggle": tid,
            "kind": "pipeline",
        })

    crypto = model.get("crypto_reference", {}) or {}
    return {
        "updated_at": model.get("updated_at"),
        "start_balance": start,
        "start_verified": balances["verified"],
        "salary": model.get("salary_monthly", 400000),
        "opex": model.get("opex", {"default": 50000, "min": 30000, "max": 200000, "step": 10000}),
        "months": model.get("forecast", {}).get("months", []),
        "flows": flows,
        "toggles": toggles,
        "one_offs": model.get("one_off_expenses", []),
        "tax_one_off": model.get("tax_rules", {}).get("one_off", []),
        "crypto_rub": crypto.get("rub_estimate", 0),
        "runway_variants": model.get("runway_variants", []),
    }


# --- вёрстка -----------------------------------------------------------------


CSS = """
  :root {
    --bg: #fafaf7; --card: #ffffff; --card-sec: #f2f0e9;
    --text: #1a1a1a; --text-sec: #6b6b6b; --text-ter: #9a9a9a; --border: #e5e2d9;
    --ok-bg: #e8f3e8; --ok-text: #2e7d32;
    --warn-bg: #fdf3d8; --warn-text: #a86400;
    --danger-bg: #fce4e4; --danger-text: #b52020;
    --info-bg: #e3edf7; --info-text: #2a5a94;
    --radius: 8px;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.5; }
  .wrap { max-width: 1000px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; font-weight: 600; }
  .subtitle { font-size: 12px; color: var(--text-sec); margin-bottom: 20px; }
  .stitle { font-size: 12px; font-weight: 600; color: var(--text-sec); text-transform: uppercase; letter-spacing: .07em; margin: 22px 0 10px; }
  .divider { border: none; border-top: 1px solid var(--border); margin: 24px 0; }
  .g4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
  .g3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; }
  .clabel { font-size: 11px; color: var(--text-sec); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .05em; }
  .cval { font-size: 20px; font-weight: 600; }
  .cval-lg { font-size: 28px; font-weight: 700; }
  .csub { font-size: 11px; color: var(--text-sec); margin-top: 3px; }
  .card-warn { background: var(--warn-bg); border-color: transparent; }
  .card-warn .cval, .card-warn .cval-lg { color: var(--warn-text); }
  .card-danger { background: var(--danger-bg); border-color: transparent; }
  .card-danger .cval, .card-danger .cval-lg { color: var(--danger-text); }
  .card-ok { background: var(--ok-bg); border-color: transparent; }
  .card-ok .cval, .card-ok .cval-lg { color: var(--ok-text); }
  .card-info { background: var(--info-bg); border-color: transparent; }
  .card-info .cval, .card-info .cval-lg { color: var(--info-text); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { font-size: 11px; font-weight: 500; color: var(--text-sec); text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); text-transform: uppercase; letter-spacing: .04em; }
  th.r { text-align: right; }
  td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  td.r { text-align: right; }
  tr.total td { background: var(--card-sec); font-weight: 600; }
  .pill { display: inline-block; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 10px; text-transform: uppercase; letter-spacing: .04em; }
  .p-ok { background: var(--ok-bg); color: var(--ok-text); }
  .p-warn { background: var(--warn-bg); color: var(--warn-text); }
  .p-info { background: var(--info-bg); color: var(--info-text); }
  .p-crit { background: var(--danger-bg); color: var(--danger-text); }
  .srow { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; flex-wrap: wrap; }
  .slabel { font-size: 13px; color: var(--text-sec); min-width: 190px; }
  .sval { font-size: 14px; font-weight: 600; min-width: 110px; text-align: right; }
  input[type="range"] { flex: 1; min-width: 200px; accent-color: var(--info-text); }
  .toggle-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13px; color: var(--text-sec); }
  .toggle-row input { width: 16px; height: 16px; accent-color: var(--info-text); }
  .toggle-pipeline { background: #f7f4ee; padding: 6px 8px; border-radius: 6px; }
  .bal-ok { color: var(--ok-text); font-weight: 600; }
  .bal-warn { color: var(--warn-text); font-weight: 600; }
  .bal-crit { color: var(--danger-text); font-weight: 600; }
  .note { font-size: 11px; color: var(--text-sec); line-height: 1.6; margin-top: 8px; }
  .flag { font-size: 11px; line-height: 1.6; margin-top: 10px; padding: 8px 10px; border-radius: 6px; background: var(--warn-bg); color: var(--warn-text); }
  .dupe { margin: 0 0 18px; padding: 12px 14px; border-radius: var(--radius); background: var(--warn-bg); color: var(--warn-text); font-size: 13px; line-height: 1.6; }
  .dupe .dupe-h { font-weight: 700; margin-bottom: 6px; }
  .dupe ul { margin: 0; padding-left: 18px; }
  .dupe li + li { margin-top: 6px; }
  .dupe .dupe-note { font-size: 11px; opacity: .8; margin-top: 8px; }
  .dupe-ok { background: var(--ok-bg); color: var(--ok-text); }
  .details { font-size: 10px; color: var(--text-ter); margin-top: 3px; }
  .chart { position: relative; height: 210px; padding: 20px 10px 30px; margin-top: 12px; }
  .chart-grid { position: absolute; left: 50px; right: 10px; top: 20px; bottom: 30px; }
  .chart-x { position: absolute; left: 50px; right: 10px; bottom: 5px; display: flex; justify-content: space-between; font-size: 10px; color: var(--text-ter); }
  .chart-y { position: absolute; left: 0; top: 20px; bottom: 30px; width: 44px; display: flex; flex-direction: column; justify-content: space-between; align-items: flex-end; font-size: 10px; color: var(--text-ter); }
  .trip-row td { background: #fdf3d8; }
  .crm td { background: #f7f4ee; }
  footer { margin-top: 28px; padding-top: 12px; border-top: 1px solid var(--border); font-size: 11px; color: var(--text-ter); line-height: 1.7; }
  @media (max-width: 760px) { .g4, .g3 { grid-template-columns: 1fr 1fr; } body { padding: 14px; } }
"""


JS = r"""
const D = JSON.parse(document.getElementById('model-data').textContent);

const fmt  = (v) => (v < 0 ? '−' : '') + Math.abs(Math.round(v)).toLocaleString('ru') + ' ₽';
const fmtK = (v) => (v < 0 ? '−' : '') + Math.round(Math.abs(v) / 1000) + 'к';
const fc   = (v) => v >= 800000 ? 'bal-ok' : v >= 200000 ? 'bal-warn' : 'bal-crit';
const pill = (v) => v >= 800000 ? '<span class="pill p-ok">стабильно</span>'
                  : v >= 200000 ? '<span class="pill p-warn">напряжённо</span>'
                  : v >= 0      ? '<span class="pill p-warn">тонко</span>'
                                : '<span class="pill p-crit">минус</span>';

const el = (id) => document.getElementById(id);
const toggleState = () => {
  const s = {};
  D.toggles.forEach(t => { const n = el('t-' + t.id); s[t.id] = n ? n.checked : t.default; });
  return s;
};

function compute(opex, oneOffs, toggles) {
  const salary = D.salary;
  const byMonth = {};
  D.months.forEach(m => { byMonth[m.key] = { income: 0, out: 0, notes: [] }; });

  D.flows.forEach(f => {
    const spec = D.toggles.find(t => t.id === f.toggle);
    const on = f.toggle ? toggles[f.toggle] : true;
    let month = f.month;
    if (!on) {
      if (!spec || spec.off === 'exclude') return;
      if (!f.alt_month) return;
      month = f.alt_month;
    }
    if (!byMonth[month]) return;
    byMonth[month].income += f.revenue;
    byMonth[month].out += f.tax + f.team;
    byMonth[month].notes.push('+' + f.name);
  });

  const rows = [];
  let bal = D.start_balance;
  const balances = [bal];
  let minBal = bal, minMonth = 'старт';

  D.months.forEach(m => {
    const bucket = byMonth[m.key];
    const factor = m.days_left && m.days_total ? m.days_left / m.days_total : 1;
    const fixed = Math.round((salary + opex) * factor);
    let out = bucket.out + fixed;
    const notes = bucket.notes.slice();
    if (factor < 1) {
      notes.push('−ЗП+опекс (' + m.days_left + ' дн)');
    } else {
      notes.push('−ЗП ' + Math.round(salary / 1000) + 'к', '−опекс ' + Math.round(opex / 1000) + 'к');
    }

    D.one_offs.forEach(o => {
      const value = oneOffs[o.id];
      if (o.month === m.key && value > 0) {
        out += value;
        notes.push('−' + o.label.replace(/\s*\(.*$/, '') + ' ' + Math.round(value / 1000) + 'к');
      }
    });
    D.tax_one_off.forEach(t => {
      if (t.month === m.key && t.amount) {
        out += t.amount;
        notes.push('−налог ' + Math.round(t.amount / 1000) + 'к');
      }
    });

    bal = bal + bucket.income - out;
    balances.push(bal);
    if (bal < minBal) { minBal = bal; minMonth = m.label; }
    rows.push({ label: m.label, income: bucket.income, out: out, bal: bal, notes: notes });
  });

  return { rows, balances, minBal, minMonth, final: bal, monthlyBurn: salary + opex };
}

function drawChart(balances) {
  const maxB = Math.max(...balances, 100000);
  const minB = Math.min(...balances, 0);
  const range = (maxB - minB) || 1;
  el('chart-y').innerHTML = '<span>' + fmtK(maxB) + '</span><span>' + fmtK((maxB + minB) / 2) + '</span><span>' + fmtK(minB) + '</span>';

  const w = 100, h = 100;
  const pts = balances.map((v, i) => [ (i / (balances.length - 1)) * w, h - ((v - minB) / range) * h, v ]);
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0] + ',' + p[1]).join(' ');
  const zeroY = h - ((0 - minB) / range) * h;
  el('chart-grid').innerHTML =
    '<svg viewBox="0 0 100 100" preserveAspectRatio="none" style="width:100%;height:100%;">' +
    '<line x1="0" y1="' + zeroY + '" x2="100" y2="' + zeroY + '" stroke="#c9c5b8" stroke-width="0.3" stroke-dasharray="1.5 1.5"/>' +
    '<path d="' + path + '" fill="none" stroke="#2a5a94" stroke-width="0.8" vector-effect="non-scaling-stroke"/>' +
    pts.map(p => '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="1.6" fill="' +
      (p[2] >= 200000 ? '#2e7d32' : (p[2] >= 0 ? '#a86400' : '#b52020')) + '"/>').join('') +
    '</svg>';
}

function render() {
  const opex = parseInt(el('opex').value, 10);
  el('opex-val').textContent = fmt(opex);

  const oneOffs = {};
  D.one_offs.forEach(o => {
    const node = el('oneoff-' + o.id);
    const value = node ? parseInt(node.value, 10) : o.amount;
    oneOffs[o.id] = value;
    const valNode = el('oneoff-val-' + o.id);
    if (valNode) valNode.textContent = fmt(value);
    const tableNode = el('oneoff-table-' + o.id);
    if (tableNode) tableNode.textContent = fmt(value);
  });

  const toggles = toggleState();
  const r = compute(opex, oneOffs, toggles);

  const tbody = el('tbody');
  tbody.innerHTML = '';
  r.rows.forEach(row => {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td>' + row.label + '</td>' +
      '<td class="r">' + (row.income ? '+' + fmt(row.income) : '—') + '</td>' +
      '<td class="r">−' + fmt(row.out) + '<div class="details">' + row.notes.join(' · ') + '</div></td>' +
      '<td class="r ' + fc(row.bal) + '">' + fmt(row.bal) + '</td>' +
      '<td>' + pill(row.bal) + '</td>';
    tbody.appendChild(tr);
  });

  const setKpi = (id, valId, subId, value, cls, sub) => {
    el(id).className = 'card ' + cls;
    el(valId).textContent = value;
    if (subId && sub !== undefined) el(subId).textContent = sub;
  };
  setKpi('kpi-min', 'kpi-min-val', 'kpi-min-sub', fmt(r.minBal),
    r.minBal >= 300000 ? 'card-ok' : (r.minBal >= 0 ? 'card-warn' : 'card-danger'), r.minMonth);
  setKpi('kpi-dec', 'kpi-dec-val', null, fmt(r.final),
    r.final >= 800000 ? 'card-ok' : (r.final >= 0 ? 'card-warn' : 'card-danger'));
  const growth = r.final - D.start_balance;
  setKpi('kpi-flow', 'kpi-flow-val', 'kpi-flow-sub', (growth >= 0 ? '+' : '') + fmt(growth),
    growth >= 500000 ? 'card-ok' : (growth >= 0 ? 'card-warn' : 'card-danger'),
    'от старта ' + fmt(D.start_balance));

  drawChart(r.balances);

  el('runway-body').innerHTML = D.runway_variants.map(v => {
    const burn = v.salary + opex;
    const base = r.final + (v.with_crypto ? D.crypto_rub : 0);
    const months = burn > 0 ? base / burn : 0;
    return '<tr><td>' + v.label + '</td><td class="r">' + fmt(burn) + '</td><td class="r">' + fmt(base) +
           '</td><td class="r ' + (months >= 5 ? 'bal-ok' : months >= 3 ? 'bal-warn' : 'bal-crit') + '">' +
           months.toFixed(1) + ' мес</td></tr>';
  }).join('');

  let cls, msg;
  const runway = (r.final / r.monthlyBurn).toFixed(1);
  if (r.minBal >= 300000) {
    cls = 'card-ok';
    msg = 'Год закрывается на ' + fmt(r.final) + '. Минимум за период ' + fmt(r.minBal) + ' (' + r.minMonth +
          '). Runway в 2027 без новых продаж: ~' + runway + ' мес.';
  } else if (r.minBal >= 0) {
    cls = 'card-warn';
    msg = 'Год в плюсе (' + fmt(r.final) + '), но минимум ' + fmt(r.minBal) + ' в ' + r.minMonth +
          '. Runway: ~' + runway + ' мес.';
  } else {
    cls = 'card-danger';
    msg = 'Кассовый разрыв в ' + r.minMonth + ' (' + fmt(r.minBal) + '). Год закрывается на ' + fmt(r.final) + '.';
  }
  el('verdict').innerHTML = '<div class="card ' + cls + '"><div class="clabel">Вердикт</div>' +
    '<div style="font-size:14px;margin-top:4px;font-weight:500;">' + msg + '</div></div>';
}

['opex'].concat(D.one_offs.map(o => 'oneoff-' + o.id)).concat(D.toggles.map(t => 't-' + t.id))
  .forEach(id => {
    const node = el(id);
    if (node) node.addEventListener(node.type === 'range' ? 'input' : 'change', render);
  });
render();
"""


def section_assets(model: dict, balances: dict[str, Any], page: dict[str, Any]) -> str:
    crypto = model.get("crypto_reference", {}) or {}
    if balances["verified"]:
        liquid_sub = " + ".join(
            f"{esc(a['title'])} {fmt_rub(a['amount'], '')}" for a in balances["rub_accounts"] if a["amount"]
        ) or "рублёвых остатков нет"
        liquid_sub += f" · снапшот {esc(balances['date'])}"
    else:
        liquid_sub = f"not verified: {esc(balances['problem'])} — цифра из handoff"

    team_total = sum(d.get("amount", 0) for d in model.get("team_debts", []))
    team_sub = " + ".join(
        f"{esc(p)} {round(sum(d['amount'] for d in model['team_debts'] if d['project'] == p) / 1000)}к"
        for p in dict.fromkeys(d["project"] for d in model.get("team_debts", []))
    )
    fx_note = ""
    if balances["fx_accounts"]:
        fx_note = " · в ПланФакте: " + ", ".join(
            f"{esc(a['title'])} {a['amount']:,.0f} {esc(a['currency'])}".replace(",", NBSP)
            for a in balances["fx_accounts"]
        )
    return f"""
<div class="stitle">Активы и обязательства</div>
<div class="g4">
  <div class="card"><div class="clabel">Ликвидные счета</div><div class="cval">{fmt_rub(page['start_balance'])}</div><div class="csub">{liquid_sub}</div></div>
  <div class="card"><div class="clabel">Крипта (несгораемая)</div><div class="cval">~{fmt_rub(crypto.get('usd_estimate', 0), NBSP + '$')}</div><div class="csub">{esc(crypto.get('composition', ''))} · вне модели{fx_note}</div></div>
  <div class="card card-ok"><div class="clabel">Долги бизнеса</div><div class="cval">{fmt_rub(model.get('business_debt', 0))}</div><div class="csub">погашены; личных долгов нет</div></div>
  <div class="card card-warn"><div class="clabel">Долги команде</div><div class="cval">{fmt_rub(team_total)}</div><div class="csub">{team_sub}</div></div>
</div>
"""


def section_team_debts(model: dict) -> str:
    rows = "\n".join(
        f'    <tr><td>{esc(d.get("project"))}</td><td>{esc(d.get("item"))}</td>'
        f'<td class="r">{fmt_rub(d.get("amount"))}</td></tr>'
        for d in model.get("team_debts", [])
    )
    total = sum(d.get("amount", 0) for d in model.get("team_debts", []))
    return f"""
<div class="stitle">Долги команде · детализация</div>
<table>
  <thead><tr><th>Проект</th><th>Статья</th><th class="r">Сумма</th></tr></thead>
  <tbody>
{rows}
    <tr class="total"><td colspan="2">Итого</td><td class="r">{fmt_rub(total)}</td></tr>
  </tbody>
</table>
<div class="note">{esc(model.get('team_debts_note', ''))}</div>
"""


def ddmm(date_str: str) -> str:
    """'2026-08-14' -> '14.08'."""
    parts = str(date_str).split("-")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else str(date_str)


def section_duplicates(report: dict[str, Any]) -> str:
    """Блоки сторожа задвоения: жёлтый (подозрение) и зелёный (подтверждено фактом)."""
    blocks: list[str] = []
    if report.get("suspects"):
        items = "\n".join(
            "    <li>Строка прогноза «{project}» ({amount}) похожа на уже пришедший платёж: "
            "{date}, {contragent}, {value} — проверь задвоение.{comment}</li>".format(
                project=esc(m["row"]["project"]),
                amount=fmt_rub(m["row"]["amount"]),
                date=esc(ddmm(m["op"]["date"]) + "." + m["op"]["date"][:4]),
                contragent=esc(m["op"]["contragent"]),
                value=fmt_rub(m["op"]["value"]),
                comment=f' <span class="dupe-note">({esc(m["op"]["comment"])})</span>'
                        if m["op"]["comment"] else "",
            )
            for m in report["suspects"]
        )
        blocks.append(f"""
<div class="dupe">
  <div class="dupe-h">⚠️ Сторож задвоения: {len(report['suspects'])} совпадени(е/я) по сумме и дате</div>
  <ul>
{items}
  </ul>
  <div class="dupe-note">Матчинг детерминированный: сумма ±{round(DUPE_TOLERANCE * 100)}%, окно
  [месяц строки −{DUPE_WINDOW_DAYS} дн … сегодня], названия не сравниваются. Строку из прогноза
  автоматически никто не убирает — решение за Антоном.</div>
</div>
""")
    if report.get("confirmed"):
        items = "\n".join(
            "    <li>«{project}» ({amount}) — подтверждено фактом {date}: {contragent}, {value}.</li>".format(
                project=esc(m["row"]["project"]),
                amount=fmt_rub(m["row"]["amount"]),
                date=esc(ddmm(m["op"]["date"])),
                contragent=esc(m["op"]["contragent"]),
                value=fmt_rub(m["op"]["value"]),
            )
            for m in report["confirmed"]
        )
        blocks.append(f"""
<div class="dupe dupe-ok">
  <div class="dupe-h">✅ Найдено в выписке ПланФакта (строки с флагом «получен»)</div>
  <ul>
{items}
  </ul>
  <div class="dupe-note">Деньги уже в остатке — строку в прогнозе стоит закрыть, чтобы не задваивать.
  Автоматически не убираем.</div>
</div>
""")
    if report.get("problem"):
        blocks.append(
            f'<div class="flag">Сторож задвоения: not verified — {esc(report["problem"])}.</div>'
        )
    return "".join(blocks)


def section_confirmed(model: dict, marks: dict[Any, dict[str, Any]] | None = None) -> str:
    marks = marks or {}
    details = team_detail_map(model)
    default_pct = model.get("tax_rules", {}).get("default_pct", 12)
    rows = []
    sum_rev = sum_team = sum_tax = sum_net = 0
    for item in model.get("confirmed_income", []):
        rev = item.get("amount", 0)
        team = item.get("team_debt", 0)
        tax = item.get("tax", 0)
        net = rev - team - tax
        sum_rev += rev
        sum_team += team
        sum_tax += tax
        sum_net += net
        if item.get("off_ip"):
            tax_cell = "—<div class=\"details\">мимо ИП</div>"
        elif item.get("tax_pct") not in (default_pct, None):
            tax_cell = f'{fmt_rub(tax, "")}<div class="details">{esc(item["tax_pct"])}%</div>'
        else:
            tax_cell = fmt_rub(tax, "")
        detail = details.get(item.get("project", ""), "")
        team_cell = (f'{fmt_rub(team, "")}<div class="details">{esc(detail)}</div>' if team
                     else '0 ✓<div class="details">оплачено ранее</div>' if not item.get("off_ip") else "—")
        mark = marks.get(item.get("id"))
        if mark and mark["status"] == "ok":
            mark_cell = (f'<div><span class="pill p-ok">подтверждено фактом {esc(ddmm(mark["op"]["date"]))}</span></div>'
                         f'<div class="details">{esc(mark["op"]["contragent"])} · {fmt_rub(mark["op"]["value"])}</div>')
        elif mark:
            mark_cell = (f'<div><span class="pill p-warn">похоже на задвоение</span></div>'
                         f'<div class="details">{esc(ddmm(mark["op"]["date"]))} · {esc(mark["op"]["contragent"])} · {fmt_rub(mark["op"]["value"])}</div>')
        else:
            mark_cell = ""
        rows.append(
            f'    <tr><td>{esc(item.get("project"))}{mark_cell}</td>'
            f'<td class="r">{fmt_rub(rev, "")}</td>'
            f'<td class="r">{team_cell}</td>'
            f'<td class="r">{tax_cell}</td>'
            f'<td class="r bal-ok">{fmt_rub(net, "")}</td>'
            f'<td>{esc(item.get("due"))}</td></tr>'
        )
    flags = "\n".join(
        f'<div class="flag">{esc(n)}</div>' for n in model.get("notes", []) if "522" in n
    )
    return f"""
<div class="stitle">Подтверждённые приходы</div>
<table>
  <thead><tr><th>Проект</th><th class="r">Выручка</th><th class="r">Команде</th><th class="r">Налог</th><th class="r">Чистое</th><th>Срок</th></tr></thead>
  <tbody>
{chr(10).join(rows)}
    <tr class="total"><td>Итого</td><td class="r">{fmt_rub(sum_rev, "")}</td><td class="r">{fmt_rub(sum_team, "")}</td>
      <td class="r">{fmt_rub(sum_tax, "")}</td><td class="r bal-ok">{fmt_rub(sum_net, "")}</td><td></td></tr>
  </tbody>
</table>
{flags}
"""


def section_pipeline(model: dict) -> str:
    rows = "\n".join(
        f'    <tr class="crm"><td><b>{esc(p.get("project"))}</b></td>'
        f'<td class="r">{fmt_rub(p.get("estimate_min"), "")} — {fmt_rub(p.get("estimate_max"), "")}</td>'
        f'<td class="r">{esc(p.get("margin_pct"))}%</td>'
        f'<td class="r">~{fmt_rub(p.get("team_cost"), "")}<div class="details">{esc(p.get("team_cost_note", ""))}</div></td>'
        f'<td>{esc(p.get("due"))}</td>'
        f'<td><span class="pill p-warn">{esc(p.get("status"))}</span></td></tr>'
        for p in model.get("pipeline", [])
    ) or '    <tr><td colspan="6">переговоров нет</td></tr>'
    return f"""
<div class="stitle">CRM · В переговорах</div>
<table>
  <thead><tr><th>Проект</th><th class="r">Смета</th><th class="r">Маржа</th><th class="r">Расход (оценка)</th><th>Срок денег</th><th>Статус</th></tr></thead>
  <tbody>
{rows}
  </tbody>
</table>
<div class="note">{esc(model.get('pipeline_note', ''))} Тумблер ниже показывает сценарий с ним.</div>
"""


def section_one_offs(model: dict) -> str:
    rows = "\n".join(
        f'    <tr class="trip-row"><td><b>{esc(o.get("label"))}</b></td>'
        f'<td class="r" id="oneoff-table-{esc(o.get("id"))}">{fmt_rub(o.get("amount"))}</td>'
        f'<td>{esc(o.get("month"))}</td><td>{esc(o.get("note", ""))}</td></tr>'
        for o in model.get("one_off_expenses", [])
    ) or '    <tr><td colspan="4">разовых трат нет</td></tr>'
    tax_rows = "\n".join(
        f'    <tr><td>{esc(t.get("label"))}</td><td class="r">{fmt_rub(t.get("amount"))}</td>'
        f'<td>{esc(t.get("month"))}</td><td>{esc(t.get("due", ""))}</td></tr>'
        for t in model.get("tax_rules", {}).get("one_off", [])
    )
    return f"""
<div class="stitle">Разовые траты и налоги</div>
<table>
  <thead><tr><th>Статья</th><th class="r">Сумма</th><th>Месяц</th><th>Комментарий</th></tr></thead>
  <tbody>
{rows}
{tax_rows}
  </tbody>
</table>
"""


def section_controls(model: dict, page: dict[str, Any]) -> str:
    opex = page["opex"]
    sliders = [
        f"""<div class="srow">
  <span class="slabel">Операционка (без ЗП)</span>
  <input type="range" min="{opex.get('min', 30000)}" max="{opex.get('max', 200000)}" step="{opex.get('step', 10000)}" value="{opex.get('default', 50000)}" id="opex">
  <span class="sval" id="opex-val">{fmt_rub(opex.get('default', 50000))}</span>
</div>"""
    ]
    for o in model.get("one_off_expenses", []):
        s = o.get("slider", {})
        sliders.append(f"""<div class="srow">
  <span class="slabel">{esc(o.get('label'))} ({esc(o.get('month'))})</span>
  <input type="range" min="{s.get('min', 0)}" max="{s.get('max', 500000)}" step="{s.get('step', 50000)}" value="{o.get('amount', 0)}" id="oneoff-{esc(o.get('id'))}">
  <span class="sval" id="oneoff-val-{esc(o.get('id'))}">{fmt_rub(o.get('amount', 0))}</span>
</div>""")

    toggles = []
    for t in page["toggles"]:
        cls = "toggle-row toggle-pipeline" if t["kind"] == "pipeline" else "toggle-row"
        checked = " checked" if t["default"] else ""
        toggles.append(
            f'<div class="{cls}"><input type="checkbox" id="t-{esc(t["id"])}"{checked}>'
            f'<label for="t-{esc(t["id"])}">{rich(t["label"])}</label></div>'
        )
    salary = fmt_rub(page["salary"])
    return f"""
<div class="stitle">Прогноз до конца года</div>
{chr(10).join(sliders)}
<div class="note" style="margin: 0 0 14px;">ЗП Антона {salary}/мес фиксированно. Налог и выплаты команде удерживаются в момент прихода проекта.</div>
{chr(10).join(toggles)}
"""


def build_html(
    model: dict,
    balances: dict[str, Any],
    page: dict[str, Any],
    sources: dict[str, str],
    dupes: dict[str, Any] | None = None,
) -> str:
    dupes = dupes or {"suspects": [], "confirmed": []}
    data_json = json.dumps(page, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    months_x = "".join(
        f"<span>{esc(m.get('label', '').split()[0][:3])}</span>" for m in page["months"]
    )
    src_line = (
        f"данные: ПланФакт снапшот от {esc(balances.get('date') or sources.get('planfact', '—'))}"
        f" · ZenMoney снапшот от {esc(sources.get('zenmoney_date') or '—')}"
        f" · модель обновлена {esc(model.get('updated_at'))}"
        f" · собрано {esc(common.now_iso())}"
    )
    handoff_ref = (model.get("accounts", {}).get("handoff_reference", {}) or {})
    delta_note = ""
    if balances["verified"] and handoff_ref.get("liquid_total"):
        delta = page["start_balance"] - handoff_ref["liquid_total"]
        if abs(delta) >= 1:
            delta_note = (
                f'<div class="flag">Стартовый остаток — живой из ПланФакта: {fmt_rub(page["start_balance"])} '
                f'против {fmt_rub(handoff_ref["liquid_total"])} в handoff от {esc(handoff_ref.get("date"))} '
                f'(дельта {"+" if delta > 0 else ""}{fmt_rub(delta)}). Прогноз сдвинут на эту дельту — это факт, не подгонка.</div>'
            )
    model_notes = "".join(
        f'<div class="note">{esc(n)}</div>' for n in model.get("notes", []) if "522" not in n
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Финансовая сводка · {esc(model.get('updated_at'))}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<h1>Финансовая сводка</h1>
<div class="subtitle">модель от {esc(model.get('updated_at'))} · остатки из ПланФакта {esc(balances.get('date') or '—')} · единый пул: бизнес + личное</div>
{section_duplicates(dupes)}
<div class="g3" style="margin-bottom:18px;">
  <div class="card" id="kpi-min"><div class="clabel">Минимум за период</div><div class="cval-lg" id="kpi-min-val">—</div><div class="csub" id="kpi-min-sub">—</div></div>
  <div class="card" id="kpi-dec"><div class="clabel">Баланс на 1 января 2027</div><div class="cval-lg" id="kpi-dec-val">—</div><div class="csub">без крипты</div></div>
  <div class="card" id="kpi-flow"><div class="clabel">Чистый прирост</div><div class="cval-lg" id="kpi-flow-val">—</div><div class="csub" id="kpi-flow-sub">—</div></div>
</div>
{delta_note}
{section_assets(model, balances, page)}
{section_team_debts(model)}
<hr class="divider">
{section_confirmed(model, dupe_marks(dupes))}
{section_pipeline(model)}
<hr class="divider">
{section_one_offs(model)}
<hr class="divider">
{section_controls(model, page)}

<div style="overflow-x:auto;margin-top:14px;">
<table>
  <thead><tr><th>Месяц</th><th class="r">Приход</th><th class="r">Выплаты</th><th class="r">Баланс</th><th>Статус</th></tr></thead>
  <tbody id="tbody"></tbody>
</table>
</div>

<div class="chart">
  <div class="chart-y" id="chart-y"></div>
  <div class="chart-grid" id="chart-grid"></div>
  <div class="chart-x"><span>Старт</span>{months_x}</div>
</div>

<div id="verdict" style="margin-top:14px;"></div>

<div class="stitle">Runway на 2027 (без новых продаж)</div>
<table>
  <thead><tr><th>Сценарий</th><th class="r">Burn/мес</th><th class="r">База</th><th class="r">Хватит на</th></tr></thead>
  <tbody id="runway-body"></tbody>
</table>
<div class="note">Крипта в оперативном прогнозе не участвует — только отдельной строкой runway (Конституция модели, п.5).</div>

<div class="stitle">Крипта · справочно</div>
<div class="card">
  <div class="clabel">Несгораемая подушка</div>
  <div class="cval">~{fmt_rub(model.get('crypto_reference', {}).get('usd_estimate', 0), NBSP + '$')} · {fmt_rub(model.get('crypto_reference', {}).get('rub_estimate', 0))}</div>
  <div class="csub">{esc(model.get('crypto_reference', {}).get('composition', ''))} — {esc(model.get('crypto_reference', {}).get('note', ''))}</div>
</div>
{model_notes}

<footer>
  {src_line}<br>
  Источник плана: {esc(model.get('source', ''))}. Рендер детерминированный (Scripts/render_dashboard.py), цифры не генерируются моделью.
</footer>

</div>

<script id="model-data" type="application/json">{data_json}</script>
<script>{JS}</script>
</body>
</html>
"""


def copy_to_vibecode_out(src: Path, dest_dir: Path) -> Path:
    """Копия деливерабла в ~/Desktop/Vibecode OUT/ (постоянное правило Антона)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "finance_dashboard.html"
    shutil.copy2(src, dest)
    log.info("Копия: %s", dest)
    return dest


def resolve_publish_dir(cli_value: str | None) -> Path | None:
    """Куда публиковать index.html: CLI > os.environ > finance.env. Нет значения — None.

    На маке ключ не задан вовсе, поэтому шаг публикации молча пропускается и поведение
    остаётся прежним. На VPS значение приходит из /root/.config/second-brain/finance.env
    (его же читает systemd EnvironmentFile) — обычно /var/www/finance.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    value = os.environ.get("FINANCE_PUBLISH_DIR")
    if not value:
        try:
            value = common.load_env().get("FINANCE_PUBLISH_DIR")
        except OSError as exc:  # нечитаемый env-файл не должен валить рендер
            log.warning("env-файл не прочитан (%s) — публикация пропущена", exc)
            return None
    return Path(value).expanduser() if value else None


def publish_dashboard(src: Path, dest_dir: Path) -> Path:
    """Атомарно кладёт index.html в веб-каталог: temp рядом + os.replace.

    Пишем temp в тот же каталог, иначе os.replace может уехать через границу ФС.
    Readers (Caddy) видят либо старый файл целиком, либо новый — половинчатого нет.
    """
    tmp = dest_dir / f".index.html.{os.getpid()}.tmp"
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest_dir / "index.html")
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    dest = dest_dir / "index.html"
    log.info("Опубликовано: %s", dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Рендер дашборда из data/model.json")
    common.add_common_args(parser)
    parser.add_argument("--model", default=None, metavar="PATH",
                        help=f"путь к model.json (по умолчанию {common.MODEL_PATH})")
    parser.add_argument("--dashboard-dir", default=None, metavar="PATH",
                        help=f"куда писать index.html (по умолчанию {common.DASHBOARD_DIR})")
    parser.add_argument("--copy-dir", default=None, metavar="PATH",
                        help=f"куда класть копию (по умолчанию {common.VIBECODE_OUT})")
    parser.add_argument("--no-copy", action="store_true", help="не копировать наружу")
    parser.add_argument("--publish-dir", default=None, metavar="PATH",
                        help="куда публиковать index.html (по умолчанию FINANCE_PUBLISH_DIR "
                             "из окружения или finance.env; на маке обычно не задан)")
    parser.add_argument("--no-publish", action="store_true",
                        help="не публиковать, даже если FINANCE_PUBLISH_DIR задан")
    args = parser.parse_args(argv)
    common.apply_common_args(args, log)

    data_dir = Path(args.data_dir) if args.data_dir else common.DATA_DIR
    model_path = Path(args.model) if args.model else data_dir / "model.json"
    dashboard_dir = Path(args.dashboard_dir) if args.dashboard_dir else common.DASHBOARD_DIR
    copy_dir = Path(args.copy_dir) if args.copy_dir else common.VIBECODE_OUT
    publish_dir = None if args.no_publish else resolve_publish_dir(args.publish_dir)

    if not model_path.is_file():
        # план-слоя ещё нет — это штатное состояние молодого департамента, не ошибка синка
        log.warning("Модели нет: %s — шаг рендера пропущен (SKIP)", model_path)
        return EXIT_SKIPPED

    model = common.read_json(model_path)
    if model is None:
        log.error("Модель битая (не парсится JSON): %s", model_path)
        return EXIT_ERROR

    problems = validate_model(model)
    if problems:
        for p in problems:
            log.error("Схема model.json: %s", p)
        return EXIT_ERROR
    log.info("Модель валидна: %s (%d ключей)", model_path, len(model))

    sources = {}
    for source in common.SOURCES:
        snap = common.latest_snapshot(source, data_dir)
        sources[source] = snap.name if snap else "нет снапшота"
    zen = common.latest_snapshot("zenmoney", data_dir)
    sources["zenmoney_date"] = zen.stem if zen else ""
    log.info("Свежие снапшоты: %s", {k: v for k, v in sources.items() if k in common.SOURCES})

    balances = read_live_balances(data_dir)
    if balances["verified"]:
        log.info("Живые остатки (рублёвые счета): %s ₽ из %s",
                 balances["liquid_total"], balances["snapshot"])
        for acc in balances["fx_accounts"]:
            log.info("Вне ликвидного (валюта %s): %s = %s", acc["currency"], acc["title"], acc["amount"])
    else:
        log.warning("not verified: %s — стартовый остаток берём из handoff_reference", balances["problem"])

    today = common.today_str(args.date)
    incomes = read_recent_incomes(data_dir, today)
    dupes = find_income_duplicates(model, incomes, today)
    log_duplicates(dupes)

    page = build_page_data(model, balances)
    html_text = build_html(model, balances, page, sources, dupes)

    target = dashboard_dir / "index.html"
    if not args.live:
        log.info("DRY-RUN: записал бы %s (%d символов)%s%s", target, len(html_text),
                 "" if args.no_copy else f" и копию в {copy_dir}",
                 f", публикация в {publish_dir}" if publish_dir else "")
        return EXIT_OK

    dashboard_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(html_text, encoding="utf-8")
    log.info("Дашборд записан: %s (%d символов)", target, len(html_text))

    if not args.no_copy:
        try:
            copy_to_vibecode_out(target, copy_dir)
        except OSError as exc:
            # на VPS каталога Desktop нет — это не повод валить синк
            log.warning("Копия не сделана (%s): %s", copy_dir, exc)

    if publish_dir is not None:
        if not publish_dir.is_dir():
            # каталог создаёт Антон ранбуком (Scripts/deploy/README.md) — сами не лезем в /var
            log.warning("Публикация пропущена: каталога нет (%s)", publish_dir)
        else:
            try:
                publish_dashboard(target, publish_dir)
            except OSError as exc:
                # нет прав / диск полон — рендер уже состоялся, синк не валим
                log.warning("Публикация не удалась (%s): %s", publish_dir, exc)

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
