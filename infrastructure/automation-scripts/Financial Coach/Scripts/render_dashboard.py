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
import re
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
#: допуск по сумме при списании факта на запись received_log: |факт − actual| / actual.
#: Специально жёстче DUPE_TOLERANCE (5%): здесь приход снимается с проверки целиком,
#: и широкий допуск проглотил бы настоящий дубль, стоящий рядом по сумме.
RECEIVED_LOG_TOLERANCE = 0.01

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


def base_name(project: str) -> str:
    """'Брусника / Перспектива (юрлицо)' -> 'Брусника' (подпись строки в прогнозе)."""
    return project.split("(")[0].split("/")[0].strip() or "проект"


def short_expense_label(label: str) -> str:
    """Ярлык разовой траты для ячейки расходов помесячной таблицы.

    Правило детерминированное, model.json не трогаем: снимаем ведущие эмодзи, режем по
    тире/скобке; если хвост про команду — «команда <проект>».
      '✈️ Таиланд — билеты (оплачено 15.08)'  -> 'Таиланд'
      'ВТБ Привилегия — команда 30%'          -> 'команда ВТБ Привилегия'
    Одинаковые ярлыки в одном месяце JS складывает в одну строку («Таиланд 324 000»).
    """
    text = re.sub(r"^[\W_]+", "", str(label or "")).strip()
    parts = re.split(r"\s+[—–-]\s+", text, maxsplit=1)
    base = parts[0].split("(")[0].strip()
    tail = parts[1] if len(parts) > 1 else ""
    if "команд" in tail.lower():
        return f"команда {base}".strip()
    return base or "разовая трата"


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


MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def next_month(day: _date) -> _date:
    """Первое число следующего месяца."""
    return _date(day.year + day.month // 12, day.month % 12 + 1, 1)


def days_in_month(day: _date) -> int:
    return (next_month(day) - _date(day.year, day.month, 1)).days


def month_key(day: _date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_label(day: _date, base_year: int) -> str:
    """«Сентябрь», а для соседнего года — «Январь 2027»."""
    name = MONTHS_RU[day.month - 1]
    return name if day.year == base_year else f"{name} {day.year}"


def build_forecast_months(model: dict, as_of: _date) -> list[dict[str, Any]]:
    """Состав и окно месяцев прогноза считаются кодом от даты снапшота.

    Живой остаток уже отражает всё потраченное с начала месяца, поэтому у первого
    (текущего) месяца списывается только хвост: burn × days_left / days_total.
    `forecast.months` в модели остаётся слоем подписей: если ключ там есть, берём
    оттуда label, окно и состав не берём никогда (иначе оно застывает на дате,
    когда модель писали руками, — так в августе 2026 прогноз списывал 20 дней
    вместо 3). Месяцы раньше месяца снапшота в прогноз не попадают.
    """
    forecast = model.get("forecast", {}) or {}
    overrides: dict[str, dict[str, Any]] = {}
    for item in forecast.get("months", []) or []:
        key = item.get("key")
        if key:
            overrides[str(key)] = item

    end: _date | None = None
    raw_end = forecast.get("horizon_end")
    if raw_end:
        try:
            end = _date.fromisoformat(str(raw_end)[:10])
        except ValueError:
            log.warning("forecast.horizon_end нечитаем (%s) — горизонт беру из списка месяцев", raw_end)
    if end is None and overrides:
        end = month_start(sorted(overrides)[-1])
    if end is None or end < as_of:
        end = as_of

    months: list[dict[str, Any]] = []
    cursor = _date(as_of.year, as_of.month, 1)
    last = _date(end.year, end.month, 1)
    while cursor <= last:
        key = month_key(cursor)
        override = overrides.get(key) or {}
        entry: dict[str, Any] = {
            "key": key,
            "label": override.get("label") or month_label(cursor, as_of.year),
        }
        if not months:
            total = days_in_month(cursor)
            entry["days_total"] = total
            # день снапшота не досчитываем: его траты уже сидят в живом остатке
            entry["days_left"] = max(total - as_of.day, 0)
        months.append(entry)
        cursor = next_month(cursor)
    return months


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
            "dupe_note": (item.get("dupe_note") or "").strip(),
            "kind": "confirmed",
        })
    for item in model.get("pipeline", []):
        rows.append({
            "id": item.get("id"),
            "project": item.get("project", "?"),
            "amount": item.get("estimate", 0) or 0,
            "month": item.get("month"),
            "received": bool(item.get("received")),
            "dupe_note": (item.get("dupe_note") or "").strip(),
            "kind": "pipeline",
        })
    return rows


def settled_operation_ids(model: dict, ops: list[dict[str, Any]]) -> set[Any]:
    """id приходов, уже разнесённых по проектам (model.received_log) — их сторож не смотрит.

    Мотив: сторож сверяет прогнозные строки со ВСЕМИ приходами снапшота и не знает,
    какие из них уже привязаны к проекту и сняты в факт. 27.08 это дало две ложные
    тревоги: приход «Джи Джи» 559 130 (это Балтика) поймал строку «Зелёная Линия»
    557 865, приход «Дабл» 784 822 (это Сбер Прайм) — строку «Брусника» 801 875.

    Матчинг записи received_log с приходом: дата ТОЧНАЯ + сумма в пределах
    RECEIVED_LOG_TOLERANCE (1%, не 5%). Одна запись снимает максимум один приход:
    если в тот же день пришли два одинаковых платежа, второй остаётся в проверке —
    именно он и был бы настоящим задвоением.
    """
    entries = model.get("received_log")
    if not isinstance(entries, list) or not entries:
        return set()

    settled: set[Any] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        date_str = str(entry.get("date") or "")[:10]
        try:
            actual = float(entry.get("actual"))
        except (TypeError, ValueError):
            actual = 0.0
        if not date_str or actual <= 0:
            log.debug("received_log: запись пропущена (дата %r, actual %r)",
                      entry.get("date"), entry.get("actual"))
            continue
        best: tuple[float, Any] | None = None
        for op in ops:
            if op["operation_id"] in settled:
                continue
            if op["date"] != date_str:
                continue
            delta = abs(op["value"] - actual) / actual
            if delta > RECEIVED_LOG_TOLERANCE:
                continue
            if best is None or delta < best[0]:
                best = (delta, op["operation_id"])
        if best is None:
            log.debug("received_log: приход не найден в снапшоте (%s, %s, %s ₽)",
                      date_str, entry.get("project"), round(actual))
            continue
        settled.add(best[1])
    return settled


def find_income_duplicates(model: dict, incomes: dict[str, Any], today: str) -> dict[str, Any]:
    """Сверяет прогнозные строки с фактическими приходами.

    Матчинг детерминированный, без имён:
      * сумма: |факт − план| / план <= DUPE_TOLERANCE (5%);
      * дата: operationDate в окне [первое число месяца строки − 45 дней; сегодня].
    Строки с received=true проверяются первыми — для них совпадение означает
    «подтверждено фактом» (зелёный статус), а не подозрение на задвоение.
    Один фактический приход закрывает максимум одну строку.
    Приходы, уже разнесённые по проектам (model.received_log), из выборки исключаются
    целиком — иначе сторож ловит их повторно и даёт ложные тревоги.
    """
    report: dict[str, Any] = {
        "suspects": [], "confirmed": [], "checked_rows": 0,
        "ops_count": len(incomes.get("ops") or []),
        "excluded_count": 0,
        "problem": incomes.get("problem"),
        "since": incomes.get("since"),
        "snapshot": incomes.get("snapshot"),
    }
    ops = incomes.get("ops") or []
    if not ops:
        return report

    settled = settled_operation_ids(model, ops)
    if settled:
        ops = [op for op in ops if op["operation_id"] not in settled]
        report["excluded_count"] = len(settled)
        report["ops_count"] = len(ops)
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
    if report.get("excluded_count"):
        log.info("Сторож задвоения: исключено из сверки как уже разнесённые: %s приходов "
                 "(received_log, дата точная + сумма ±%.0f%%)",
                 report["excluded_count"], RECEIVED_LOG_TOLERANCE * 100)
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
                    "платёж: %s, %s, %s ₽ — проверь задвоение%s",
                    row["project"], round(row["amount"]), op["date"], op["contragent"],
                    round(op["value"]),
                    f' | пометка модели: {row["dupe_note"]}' if row.get("dupe_note") else "")
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


def build_page_data(model: dict, balances: dict[str, Any], as_of: _date) -> dict[str, Any]:
    """JSON-блок для JS: всё, от чего считается прогноз (вёрстка отдельно).

    `as_of` — дата снапшота живых остатков: от неё считается окно первого месяца.
    """
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

    # проекты, где команда гасится графиком (one_off), а не удерживается из прихода:
    # «чистыми» у них несопоставимо с остальными строками — помечаем сноской
    scheduled = {
        d.get("project") for d in model.get("team_debts", []) if d.get("paid_by_schedule")
    }

    for item in model.get("confirmed_income", []):
        tid = push_toggle(item.get("toggle"), "confirmed")
        flows.append({
            "id": item.get("id"),
            "title": base_name(item.get("project", "?")),
            "revenue": item.get("amount", 0),
            "tax": item.get("tax", 0),
            "team": item.get("team_debt", 0),
            "month": item.get("month"),
            "alt_month": item.get("alt_month"),
            "toggle": tid,
            "kind": "confirmed",
            "schedule": item.get("project") in scheduled,
        })
    for item in model.get("pipeline", []):
        tid = push_toggle(item.get("toggle"), "pipeline")
        flows.append({
            "id": item.get("id"),
            "title": base_name(item.get("project", "?")),
            "revenue": item.get("estimate", 0),
            "tax": item.get("tax", 0),
            "team": item.get("team_cost", 0),
            "month": item.get("month"),
            "alt_month": None,
            "toggle": tid,
            "kind": "pipeline",
            "schedule": item.get("project") in scheduled,
        })

    crypto = model.get("crypto_reference", {}) or {}
    return {
        "updated_at": model.get("updated_at"),
        "start_balance": start,
        "start_verified": balances["verified"],
        "salary": model.get("salary_monthly", 400000),
        "opex": model.get("opex", {"default": 50000, "min": 30000, "max": 200000, "step": 10000}),
        "months": build_forecast_months(model, as_of),
        "flows": flows,
        "toggles": toggles,
        # в JS уезжает только то, что нужно счёту и подписи (id, короткий ярлык, месяц,
        # стартовая сумма) — полные подписи и комментарии рендерит Python в детализации
        "one_offs": [
            {
                "id": o.get("id"),
                "short": short_expense_label(o.get("label", "")),
                "month": o.get("month"),
                "amount": o.get("amount", 0),
            }
            for o in model.get("one_off_expenses", [])
        ],
        "tax_one_off": [
            {"label": t.get("label", "налог"), "month": t.get("month"), "amount": t.get("amount", 0)}
            for t in model.get("tax_rules", {}).get("one_off", [])
        ],
        "crypto_rub": crypto.get("rub_estimate", 0),
        "runway_variants": model.get("runway_variants", []),
    }


# --- вёрстка -----------------------------------------------------------------
# Структура страницы (решение Антона 28.08, жалоба «оформлено плохо и непонятно»):
#   1) три крупных числа: на счетах / ближайший приход / остаток на конец года;
#   2) сценарии — компактный ряд тумблеров;
#   3) помесячная таблица — главный блок (приходы ЧИСТЫМИ списком, расходы с разбивкой);
#   4) вердикт;
#   5) всё остальное — свёрнутые <details>.
# Правило подачи сумм (CLAUDE.md департамента): ни одной суммы без пометки «смета»
# или «чистыми»; цепочка смета → команде → налог → чистыми живёт в детализации.
# Считает по-прежнему JS на странице, данные — в <script id="model-data">.


CSS = """
  :root{--bg:#faf9f6;--card:#fff;--soft:#f4f2ec;--text:#17181a;--muted:#6c6e72;--faint:#9b9da1;
    --line:#e9e6dd;--ok-bg:#e9f4ea;--ok:#2b7a33;--warn-bg:#fdf2d6;--warn:#8f5606;
    --bad-bg:#fce6e3;--bad:#b32d20;--info-bg:#e7eff9;--info:#27548c;--r:12px}
  *{box-sizing:border-box}
  body{margin:0;padding:20px 16px 44px;background:var(--bg);color:var(--text);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-text-size-adjust:100%}
  .wrap{max-width:960px;margin:0 auto}
  h1{font-size:19px;font-weight:600;margin:0 0 4px}
  .sub{font-size:13px;color:var(--muted)}
  .stitle{font-size:11.5px;font-weight:600;color:var(--faint);text-transform:uppercase;
    letter-spacing:.08em;margin:26px 0 10px}
  .hint{font-size:12.5px;color:var(--faint);line-height:1.55}
  .note{font-size:13px;color:var(--muted);margin-top:10px;line-height:1.6}
  .num{font-variant-numeric:tabular-nums}
  sup{font-size:10px;color:var(--warn)}
  .tag{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:9px;
    background:var(--soft);color:var(--muted)}
  .hero{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0 6px}
  .hero .box{background:var(--card);border-radius:var(--r);padding:16px 18px 15px}
  .hero .k{font-size:12.5px;color:var(--muted);margin-bottom:7px}
  .hero .v{font-size:32px;font-weight:650;letter-spacing:-.02em;line-height:1.12;
    font-variant-numeric:tabular-nums;white-space:nowrap}
  .hero .n{font-size:12.5px;color:var(--faint);margin-top:7px;line-height:1.5}
  .v-ok{color:var(--ok)}.v-warn{color:var(--warn)}.v-bad{color:var(--bad)}
  .chips{display:flex;flex-wrap:wrap;gap:8px}
  .chip{display:inline-flex;align-items:center;gap:8px;background:var(--card);
    border:1px solid var(--line);border-radius:999px;padding:8px 14px;font-size:13.5px;
    color:var(--muted);cursor:pointer;-webkit-user-select:none;user-select:none}
  .chip input{width:15px;height:15px;margin:0;accent-color:var(--info);flex:none}
  .chip.on{background:var(--info-bg);border-color:#cfdcee;color:var(--text)}
  .chip-alt{border-style:dashed}
  .panel{background:var(--card);border-radius:var(--r);overflow:hidden}
  .months{width:100%;border-collapse:collapse}
  .months th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);
    font-weight:600;text-align:left;padding:12px 14px 8px}
  .months th.r{text-align:right}
  .months td{padding:14px;border-top:1px solid var(--line);vertical-align:top}
  .months td.r{text-align:right}
  .months .mname{font-weight:600;font-size:15.5px}
  .months .amt{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
  .months .zero{color:var(--faint);font-weight:400}
  .months .lst{font-size:13px;color:var(--muted);margin-top:6px;line-height:1.7}
  .months tr.year td{background:var(--soft)}
  details{background:var(--card);border-radius:var(--r);margin-top:10px;overflow:hidden}
  summary{cursor:pointer;list-style:none;padding:14px 16px;font-size:14.5px;font-weight:600;
    display:flex;align-items:center;justify-content:space-between;gap:10px}
  summary::-webkit-details-marker{display:none}
  summary::after{content:"▾";font-size:11px;color:var(--faint);font-weight:400}
  details[open]>summary::after{content:"▴"}
  .dbody{padding:0 16px 18px}
  .scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -16px;padding:0 16px}
  table.data{width:100%;border-collapse:collapse;font-size:14px;min-width:560px}
  table.data th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);
    font-weight:600;text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
  table.data th.r,table.data td.r{text-align:right}
  table.data td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
  table.data td.r{white-space:nowrap}
  table.data td.r .details{white-space:normal}
  table.data tr:last-child td{border-bottom:0}
  table.data tr.total td{background:var(--soft);font-weight:600}
  .details{font-size:11.5px;color:var(--faint);line-height:1.5;margin-top:3px}
  .pill{display:inline-block;font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:10px;
    text-transform:uppercase;letter-spacing:.04em}
  .p-ok{background:var(--ok-bg);color:var(--ok)}
  .p-warn{background:var(--warn-bg);color:var(--warn)}
  .p-crit{background:var(--bad-bg);color:var(--bad)}
  .bal-ok{color:var(--ok)}.bal-warn{color:var(--warn)}.bal-crit{color:var(--bad)}
  .srow{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:11px 0;border-bottom:1px solid var(--line)}
  .srow:last-child{border-bottom:0}
  .slabel{font-size:13.5px;color:var(--muted);flex:1 1 190px;min-width:150px}
  .sval{font-size:14.5px;font-weight:600;text-align:right;min-width:100px;font-variant-numeric:tabular-nums}
  input[type=range]{flex:1 1 100%;min-width:170px;accent-color:var(--info)}
  .verdict{margin-top:12px;border-radius:var(--r);padding:14px 16px;font-size:14.5px;line-height:1.55}
  .v-good{background:var(--ok-bg);color:var(--ok)}
  .v-mid{background:var(--warn-bg);color:var(--warn)}
  .v-crit{background:var(--bad-bg);color:var(--bad)}
  .flag{font-size:13px;line-height:1.6;margin-top:10px;padding:10px 12px;border-radius:8px;
    background:var(--warn-bg);color:var(--warn)}
  .alarm{background:var(--warn-bg)}
  .alarm>summary{color:var(--warn)}
  .dupe{font-size:13.5px;line-height:1.6}
  .dupe ul{margin:6px 0 0;padding-left:18px}
  .dupe li+li{margin-top:6px}
  .dupe-h{font-weight:600;margin-top:10px}
  .dupe-ok{color:var(--ok)}
  .dupe-note{font-size:12px;color:var(--faint);margin-top:8px;line-height:1.55}
  .kv{display:flex;justify-content:space-between;gap:14px;padding:9px 0;border-bottom:1px solid var(--line);font-size:14px}
  .kv:last-child{border-bottom:0}
  .kv span:last-child{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}
  .chart{position:relative;height:180px;padding:14px 6px 24px;margin-top:4px}
  .chart-grid{position:absolute;left:46px;right:6px;top:14px;bottom:24px}
  .chart-x{position:absolute;left:46px;right:6px;bottom:2px;display:flex;justify-content:space-between;
    font-size:10.5px;color:var(--faint)}
  .chart-y{position:absolute;left:0;top:14px;bottom:24px;width:42px;display:flex;flex-direction:column;
    justify-content:space-between;align-items:flex-end;font-size:10.5px;color:var(--faint)}
  footer{margin-top:26px;font-size:12.5px;color:var(--faint);line-height:1.7}
  @media (max-width:720px){
    .hero{grid-template-columns:1fr;gap:10px}
    .panel{background:transparent;border-radius:0;overflow:visible}
    .months,.months tbody,.months tr,.months td{display:block;width:100%}
    .months thead{display:none}
    .months tr{background:var(--card);border-radius:var(--r);padding:12px 14px;margin-bottom:10px}
    .months td{border-top:0;padding:8px 0;text-align:left}
    .months td.r{text-align:left}
    .months td+td{border-top:1px solid var(--line)}
    .months td::before{content:attr(data-l);display:block;font-size:11px;text-transform:uppercase;
      letter-spacing:.05em;color:var(--faint);margin-bottom:3px}
    .months td:first-child::before{display:none}
  }
  @media (max-width:400px){ body{padding:16px 12px 40px} .hero .v{font-size:28px} }
"""


JS = r"""
const D = JSON.parse(document.getElementById('model-data').textContent);

const fmt  = (v) => (v < 0 ? '−' : '') + Math.abs(Math.round(v)).toLocaleString('ru') + ' ₽';
const fmtK = (v) => (v < 0 ? '−' : '') + Math.round(Math.abs(v) / 1000) + 'к';
const fc   = (v) => v >= 800000 ? 'bal-ok' : v >= 200000 ? 'bal-warn' : 'bal-crit';
const hc   = (v) => v >= 800000 ? 'v-ok'   : v >= 0      ? 'v-warn'   : 'v-bad';
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
  D.months.forEach(m => { byMonth[m.key] = { income: 0, out: 0, items: [] }; });

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
    byMonth[month].items.push({
      title: f.title, net: f.revenue - f.tax - f.team,
      schedule: !!f.schedule, kind: f.kind,
    });
  });

  const rows = [];
  let bal = D.start_balance;
  const balances = [bal];
  let minBal = bal, minMonth = 'старт', yearIn = 0, yearOut = 0;

  D.months.forEach(m => {
    const bucket = byMonth[m.key];
    // days_left === 0 (снапшот в последний день месяца) — тоже валидное окно, не «нет окна»
    const factor = (m.days_total && m.days_left != null) ? m.days_left / m.days_total : 1;
    const fixed = Math.round((salary + opex) * factor);
    let out = bucket.out + fixed;

    const exp = [];
    const push = (name, amount) => {
      const hit = exp.find(e => e.name === name);
      if (hit) hit.amount += amount; else exp.push({ name: name, amount: amount });
    };
    push(factor < 1 ? 'burn (ЗП + операционка), ' + m.days_left + ' дн' : 'burn (ЗП + операционка)', fixed);

    D.one_offs.forEach(o => {
      const value = oneOffs[o.id];
      if (o.month === m.key && value > 0) { out += value; push(o.short, value); }
    });
    D.tax_one_off.forEach(t => {
      if (t.month === m.key && t.amount) { out += t.amount; push(t.label, t.amount); }
    });

    bal = bal + bucket.income - out;
    balances.push(bal);
    if (bal < minBal) { minBal = bal; minMonth = m.label; }

    // «Приходы» в таблице показываются ЧИСТЫМИ, поэтому налог и выплаты команде по этим же
    // проектам не дублируются в колонке расходов. Сам баланс от перестановки не меняется.
    const projOut = bucket.out;
    const incomeNet = bucket.income - projOut;
    const outShown = out - projOut;
    yearIn += incomeNet; yearOut += outShown;

    rows.push({
      label: m.label, sub: factor < 1 ? 'остаток месяца: ' + m.days_left + ' дн.' : '',
      items: bucket.items, incomeNet: incomeNet, exp: exp, outShown: outShown, bal: bal,
    });
  });

  return { rows, balances, minBal, minMonth, final: bal, monthlyBurn: salary + opex,
           yearIn: yearIn, yearOut: yearOut };
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
    '<path d="' + path + '" fill="none" stroke="#27548c" stroke-width="0.8" vector-effect="non-scaling-stroke"/>' +
    pts.map(p => '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="1.6" fill="' +
      (p[2] >= 200000 ? '#2b7a33' : (p[2] >= 0 ? '#8f5606' : '#b32d20')) + '"/>').join('') +
    '</svg>';
}

const incomeList = (items) => !items.length ? '' : '<div class="lst">' + items.map(i =>
  '<div>' + i.title + ' ' + fmt(i.net) + ' чистыми' + (i.schedule ? '<sup>*</sup>' : '') +
  (i.kind === 'pipeline' ? ' <span class="tag">сценарий</span>' : '') + '</div>').join('') + '</div>';

const expenseList = (items) => '<div class="lst">' + items.map(e =>
  '<div>' + e.name + ' ' + fmt(e.amount) + '</div>').join('') + '</div>';

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
  D.toggles.forEach(t => {
    const chip = el('chip-' + t.id);
    if (chip) chip.className = 'chip' + (t.kind === 'pipeline' ? ' chip-alt' : '') + (toggles[t.id] ? ' on' : '');
  });

  const r = compute(opex, oneOffs, toggles);

  const tbody = el('tbody');
  tbody.innerHTML = '';
  r.rows.forEach(row => {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td data-l="Месяц"><div class="mname">' + row.label + '</div>' +
        (row.sub ? '<div class="hint">' + row.sub + '</div>' : '') + '</td>' +
      '<td data-l="Приходы, чистыми" class="r"><div class="amt' + (row.incomeNet ? '' : ' zero') + '">' +
        (row.incomeNet ? '+' + fmt(row.incomeNet) : '—') + '</div>' + incomeList(row.items) + '</td>' +
      '<td data-l="Расходы" class="r"><div class="amt">−' + fmt(row.outShown) + '</div>' +
        expenseList(row.exp) + '</td>' +
      '<td data-l="Остаток на конец" class="r"><div class="amt ' + fc(row.bal) + '">' + fmt(row.bal) + '</div>' +
        '<div class="lst">' + pill(row.bal) + '</div></td>';
    tbody.appendChild(tr);
  });

  el('year-in').textContent = '+' + fmt(r.yearIn);
  el('year-out').textContent = '−' + fmt(r.yearOut);
  el('year-bal').textContent = fmt(r.final);
  el('year-bal').className = 'amt ' + fc(r.final);

  const next = r.rows.find(x => x.items.length);
  if (next) {
    const top = next.items.slice().sort((a, b) => b.net - a.net)[0];
    el('hero-next-val').textContent = fmt(top.net);
    el('hero-next-val').className = 'v num';
    let sub = top.title + ' · ' + next.label.toLowerCase() + ' · чистыми' + (top.schedule ? ' (команда графиком)' : '');
    if (next.items.length > 1) sub += ' · всего в месяце ' + fmt(next.incomeNet) + ' чистыми';
    el('hero-next-sub').textContent = sub;
  } else {
    el('hero-next-val').textContent = '—';
    el('hero-next-val').className = 'v num';
    el('hero-next-sub').textContent = 'приходов в модели до конца года нет';
  }
  el('hero-eoy-val').textContent = fmt(r.final);
  el('hero-eoy-val').className = 'v num ' + hc(r.final);

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
    cls = 'v-good';
    msg = 'Год закрывается на ' + fmt(r.final) + '. Минимум за период ' + fmt(r.minBal) + ' (' + r.minMonth +
          '). Runway в 2027 без новых продаж: ~' + runway + ' мес.';
  } else if (r.minBal >= 0) {
    cls = 'v-mid';
    msg = 'Год в плюсе (' + fmt(r.final) + '), но минимум ' + fmt(r.minBal) + ' в ' + r.minMonth +
          '. Runway: ~' + runway + ' мес.';
  } else {
    cls = 'v-crit';
    msg = 'Кассовый разрыв в ' + r.minMonth + ' (' + fmt(r.minBal) + '). Год закрывается на ' + fmt(r.final) + '.';
  }
  el('verdict').className = 'verdict ' + cls;
  el('verdict').innerHTML = '<b>Вердикт.</b> ' + msg;
}

['opex'].concat(D.one_offs.map(o => 'oneoff-' + o.id)).concat(D.toggles.map(t => 't-' + t.id))
  .forEach(id => {
    const node = el(id);
    if (node) node.addEventListener(node.type === 'range' ? 'input' : 'change', render);
  });
render();
"""


# --- блоки страницы ----------------------------------------------------------


def scheduled_projects(model: dict) -> list[str]:
    """Проекты, где команда платится графиком, а не удерживается из прихода."""
    return [d.get("project", "") for d in model.get("team_debts", []) if d.get("paid_by_schedule")]


def section_hero(balances: dict[str, Any], page: dict[str, Any]) -> str:
    """Три крупных числа. Первое статично, два других считает JS от сценариев."""
    if balances["verified"]:
        cash_note = f"снапшот ПланФакта {esc(balances['date'])} · рублёвые счета"
    else:
        cash_note = f"not verified: {esc(balances['problem'])} — цифра из handoff"
    return f"""
<div class="hero">
  <div class="box">
    <div class="k">На счетах сейчас</div>
    <div class="v num">{fmt_rub(page['start_balance'])}</div>
    <div class="n">{cash_note}</div>
  </div>
  <div class="box">
    <div class="k">Ближайший приход</div>
    <div class="v num" id="hero-next-val">—</div>
    <div class="n" id="hero-next-sub">—</div>
  </div>
  <div class="box">
    <div class="k">Остаток на конец года</div>
    <div class="v num" id="hero-eoy-val">—</div>
    <div class="n">при текущих допущениях (сценарии ниже)</div>
  </div>
</div>
"""


def section_scenarios(page: dict[str, Any]) -> str:
    chips = "\n".join(
        f'  <label class="chip{" chip-alt" if t["kind"] == "pipeline" else ""}" id="chip-{esc(t["id"])}">'
        f'<input type="checkbox" id="t-{esc(t["id"])}"{" checked" if t["default"] else ""}>'
        f'<span>{rich(t["label"])}</span></label>'
        for t in page["toggles"]
    ) or '  <span class="hint">сценарных переключателей в модели нет</span>'
    return f"""
<div class="stitle">Сценарии — пересчитывают всю страницу</div>
<div class="chips">
{chips}
</div>
"""


def section_months(model: dict, page: dict[str, Any]) -> str:
    """Главный блок: помесячная таблица. Тело строк рисует JS."""
    sched = scheduled_projects(model)
    footnote = ""
    if sched:
        total = sum(
            d.get("amount", 0) for d in model.get("team_debts", []) if d.get("paid_by_schedule")
        )
        footnote = (
            f'<div class="note"><sup>*</sup> {esc(", ".join(sched))}: команда {fmt_rub(total)} '
            f'платится графиком до прихода денег (в расходах — отдельными строками по месяцам), '
            f'а не удерживается из прихода. «Чистыми» здесь = смета − налог.</div>'
        )
    return f"""
<div class="stitle">Помесячно до конца года</div>
<div class="panel">
<table class="months">
  <thead><tr>
    <th>Месяц</th><th class="r">Приходы, чистыми</th><th class="r">Расходы</th><th class="r">Остаток на конец</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
  <tfoot><tr class="year">
    <td data-l="Итог"><div class="mname">Итог года</div>
      <div class="hint">старт {fmt_rub(page['start_balance'])}</div></td>
    <td data-l="Приходы, чистыми" class="r"><div class="amt" id="year-in">—</div></td>
    <td data-l="Расходы" class="r"><div class="amt" id="year-out">—</div></td>
    <td data-l="Остаток на конец" class="r"><div class="amt" id="year-bal">—</div></td>
  </tr></tfoot>
</table>
</div>
{footnote}
<div id="verdict" class="verdict"></div>
"""


def ddmm(date_str: str) -> str:
    """'2026-08-14' -> '14.08'."""
    parts = str(date_str).split("-")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else str(date_str)


def details_income(model: dict, marks: dict[Any, dict[str, Any]] | None = None) -> str:
    """«Приходы подробно»: цепочка смета → команде → налог → чистыми + CRM."""
    marks = marks or {}
    details = team_detail_map(model)
    default_pct = model.get("tax_rules", {}).get("default_pct", 15)
    sched = set(scheduled_projects(model))
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
            tax_cell = '—<div class="details">мимо ИП</div>'
        elif item.get("tax_pct") not in (default_pct, None):
            tax_cell = f'{fmt_rub(tax, "")}<div class="details">{esc(item["tax_pct"])}%</div>'
        else:
            tax_cell = fmt_rub(tax, "")
        detail = details.get(item.get("project", ""), "")
        if item.get("project") in sched:
            team_cell = (f'по графику<div class="details">{fmt_rub(item.get("team_total", 0), "")} '
                         f'вне прихода</div>')
        elif team:
            team_cell = f'{fmt_rub(team, "")}<div class="details">{esc(detail)}</div>'
        elif not item.get("off_ip"):
            team_cell = '0<div class="details">оплачено ранее</div>'
        else:
            team_cell = "—"
        mark = marks.get(item.get("id"))
        if mark and mark["status"] == "ok":
            mark_cell = (f'<div><span class="pill p-ok">подтверждено фактом {esc(ddmm(mark["op"]["date"]))}</span></div>'
                         f'<div class="details">{esc(mark["op"]["contragent"])} · {fmt_rub(mark["op"]["value"])}</div>')
        elif mark:
            note = mark["row"].get("dupe_note")
            mark_cell = (f'<div><span class="pill p-warn">похоже на задвоение</span></div>'
                         f'<div class="details">{esc(ddmm(mark["op"]["date"]))} · {esc(mark["op"]["contragent"])} · {fmt_rub(mark["op"]["value"])}</div>'
                         + (f'<div class="details">{esc(note)}</div>' if note else ""))
        else:
            mark_cell = ""
        star = "<sup>*</sup>" if item.get("project") in sched else ""
        rows.append(
            f'    <tr><td>{esc(item.get("project"))}{star}{mark_cell}</td>'
            f'<td class="r">{fmt_rub(rev, "")}</td>'
            f'<td class="r">{team_cell}</td>'
            f'<td class="r">{tax_cell}</td>'
            f'<td class="r bal-ok">{fmt_rub(net, "")}</td>'
            f'<td>{esc(item.get("due"))}</td></tr>'
        )
    flags = "\n".join(
        f'<div class="flag">{esc(n)}</div>' for n in model.get("notes", []) if "522" in n
    )

    pipe_rows = "\n".join(
        f'    <tr><td><b>{esc(p.get("project"))}</b></td>'
        f'<td class="r">{fmt_rub(p.get("estimate_min"), "")} — {fmt_rub(p.get("estimate_max"), "")}<div class="details">смета</div></td>'
        f'<td class="r">{esc(p.get("margin_pct"))}%</td>'
        f'<td class="r">~{fmt_rub(p.get("team_cost"), "")}<div class="details">{esc(p.get("team_cost_note", ""))}</div></td>'
        f'<td>{esc(p.get("due"))}</td>'
        f'<td><span class="pill p-warn">{esc(p.get("status"))}</span></td></tr>'
        for p in model.get("pipeline", [])
    ) or '    <tr><td colspan="6">переговоров нет</td></tr>'

    star_note = ""
    if sched:
        star_note = (f'<sup>*</sup> {esc(", ".join(sorted(sched)))}: команда платится графиком до прихода, '
                     f'поэтому в «чистыми» этой строки она не вычтена — сумма несопоставима с остальными строками.<br>')

    return f"""
<details>
<summary><span>Приходы подробно · смета → чистыми</span></summary>
<div class="dbody">
<div class="scroll">
<table class="data">
  <thead><tr><th>Проект</th><th class="r">Смета</th><th class="r">− Команде</th>
    <th class="r">− Налог {esc(default_pct)}%</th><th class="r">= Чистыми</th><th>Срок денег</th></tr></thead>
  <tbody>
{chr(10).join(rows)}
    <tr class="total"><td>Итого подтверждено</td><td class="r">{fmt_rub(sum_rev, "")}</td>
      <td class="r">{fmt_rub(sum_team, "")}</td><td class="r">{fmt_rub(sum_tax, "")}</td>
      <td class="r bal-ok">{fmt_rub(sum_net, "")}</td><td></td></tr>
  </tbody>
</table>
</div>
<div class="note"><b>Смета</b> — сумма договора до вычетов. <b>Чистыми</b> — что остаётся после выплат
команде и налога {esc(default_pct)}%. В помесячной таблице выше стоят именно «чистыми».<br>{star_note}</div>
{flags}
<div class="stitle">CRM · в переговорах (в базу не считается)</div>
<div class="scroll">
<table class="data">
  <thead><tr><th>Проект</th><th class="r">Смета</th><th class="r">Маржа</th>
    <th class="r">Расход (оценка)</th><th>Срок денег</th><th>Статус</th></tr></thead>
  <tbody>
{pipe_rows}
  </tbody>
</table>
</div>
<div class="note">{esc(model.get('pipeline_note', ''))} Включается тумблером в блоке «Сценарии».</div>
</div>
</details>
"""


def details_team_debts(model: dict) -> str:
    rows = "\n".join(
        f'    <tr><td>{esc(d.get("project"))}</td><td>{esc(d.get("item"))}</td>'
        f'<td class="r">{fmt_rub(d.get("amount"))}</td>'
        f'<td>{"графиком, вне прихода" if d.get("paid_by_schedule") else "из прихода проекта"}</td></tr>'
        for d in model.get("team_debts", [])
    )
    total = sum(d.get("amount", 0) for d in model.get("team_debts", []))
    return f"""
<details>
<summary><span>Долги команде</span><span class="tag">{fmt_rub(total)}</span></summary>
<div class="dbody">
<div class="scroll">
<table class="data">
  <thead><tr><th>Проект</th><th>Статья</th><th class="r">Сумма</th><th>Как гасится</th></tr></thead>
  <tbody>
{rows}
    <tr class="total"><td colspan="2">Итого</td><td class="r">{fmt_rub(total)}</td><td></td></tr>
  </tbody>
</table>
</div>
<div class="note">{esc(model.get('team_debts_note', ''))}</div>
</div>
</details>
"""


def slider_min(value: float, low: float, step: float) -> int:
    """Нижняя граница ползунка, при которой стартовое значение достижимо шагом.

    Браузер округляет value до ближайшей валидной ступени (min + k×step), поэтому
    124 000 при min=0/step=10 000 превращалось в 120 000 — прогноз на загрузке
    расходился с моделью. Сдвигаем min на остаток: 124 000 становится точной ступенью.
    """
    try:
        if step <= 0 or value < low:
            return int(low)
        return int(low + (value - low) % step)
    except (TypeError, ValueError):
        return int(low or 0)


def details_spending(model: dict, page: dict[str, Any]) -> str:
    """Разовые траты + все слайдеры (операционка и суммы поездок)."""
    rows = "\n".join(
        f'    <tr><td><b>{esc(o.get("label"))}</b><div class="details">{esc(o.get("note", ""))}</div></td>'
        f'<td class="r" id="oneoff-table-{esc(o.get("id"))}">{fmt_rub(o.get("amount"))}</td>'
        f'<td>{esc(o.get("month"))}</td></tr>'
        for o in model.get("one_off_expenses", [])
    ) or '    <tr><td colspan="3">разовых трат нет</td></tr>'
    tax_rows = "\n".join(
        f'    <tr><td>{esc(t.get("label"))}<div class="details">{esc(t.get("due", ""))}</div></td>'
        f'<td class="r">{fmt_rub(t.get("amount"))}</td><td>{esc(t.get("month"))}</td></tr>'
        for t in model.get("tax_rules", {}).get("one_off", [])
    )

    opex = page["opex"]
    o_min, o_max, o_step = opex.get("min", 30000), opex.get("max", 200000), opex.get("step", 10000)
    o_val = opex.get("default", 50000)
    sliders = [f"""  <div class="srow">
    <span class="slabel">Операционка в месяц (без ЗП)</span>
    <input type="range" min="{slider_min(o_val, o_min, o_step)}" max="{o_max}" step="{o_step}" value="{o_val}" id="opex">
    <span class="sval num" id="opex-val">{fmt_rub(o_val)}</span>
  </div>"""]
    for o in model.get("one_off_expenses", []):
        s = o.get("slider") or {}
        s_min, s_max, s_step = s.get("min", 0), s.get("max", 500000), s.get("step", 50000)
        amount = o.get("amount", 0)
        sliders.append(f"""  <div class="srow">
    <span class="slabel">{esc(o.get('label'))} ({esc(o.get('month'))})</span>
    <input type="range" min="{slider_min(amount, s_min, s_step)}" max="{s_max}" step="{s_step}" value="{amount}" id="oneoff-{esc(o.get('id'))}">
    <span class="sval num" id="oneoff-val-{esc(o.get('id'))}">{fmt_rub(amount)}</span>
  </div>""")

    return f"""
<details>
<summary><span>Разовые траты и слайдеры</span></summary>
<div class="dbody">
<div class="scroll">
<table class="data">
  <thead><tr><th>Статья</th><th class="r">Сумма</th><th>Месяц</th></tr></thead>
  <tbody>
{rows}
{tax_rows}
  </tbody>
</table>
</div>
<div class="stitle">Слайдеры — двигают прогноз</div>
{chr(10).join(sliders)}
<div class="note">ЗП Антона {fmt_rub(page['salary'])}/мес фиксированно. Налог и выплаты команде
удерживаются в момент прихода проекта — в помесячной таблице они уже внутри «чистыми».</div>
</div>
</details>
"""


def details_runway(model: dict, page: dict[str, Any]) -> str:
    crypto = model.get("crypto_reference", {}) or {}
    months_x = "".join(
        f"<span>{esc(m.get('label', '').split()[0][:3])}</span>" for m in page["months"]
    )
    return f"""
<details>
<summary><span>Runway и крипта</span></summary>
<div class="dbody">
<div class="chart">
  <div class="chart-y" id="chart-y"></div>
  <div class="chart-grid" id="chart-grid"></div>
  <div class="chart-x"><span>Старт</span>{months_x}</div>
</div>
<div class="scroll">
<table class="data">
  <thead><tr><th>Сценарий 2027 (без новых продаж)</th><th class="r">Burn/мес</th>
    <th class="r">База</th><th class="r">Хватит на</th></tr></thead>
  <tbody id="runway-body"></tbody>
</table>
</div>
<div class="stitle">Крипта · справочно, вне операционки</div>
<div class="kv"><span>Несгораемая подушка</span>
  <span>~{fmt_rub(crypto.get('usd_estimate', 0), NBSP + '$')} · {fmt_rub(crypto.get('rub_estimate', 0))}</span></div>
<div class="note">{esc(crypto.get('composition', ''))} — {esc(crypto.get('note', ''))}
Крипта в оперативный прогноз не входит, только отдельной строкой runway (Конституция модели, п.5).</div>
</div>
</details>
"""


def details_accounts(model: dict, balances: dict[str, Any], page: dict[str, Any]) -> str:
    if balances["verified"]:
        rows = "\n".join(
            f'<div class="kv"><span>{esc(a["title"])}</span><span>{fmt_rub(a["amount"])}</span></div>'
            for a in balances["rub_accounts"] if a["amount"]
        ) or '<div class="kv"><span>рублёвых остатков нет</span><span>—</span></div>'
        source = f'<div class="note">Источник: снапшот ПланФакта {esc(balances["date"])}.</div>'
    else:
        rows = f'<div class="flag">not verified: {esc(balances["problem"])} — стартовый остаток из handoff.</div>'
        source = ""
    fx = ""
    if balances["fx_accounts"]:
        fx = '<div class="note">Валютные счета (в ликвидное не входят): ' + ", ".join(
            f'{esc(a["title"])} {a["amount"]:,.0f} {esc(a["currency"])}'.replace(",", NBSP)
            for a in balances["fx_accounts"]
        ) + ".</div>"

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
    return f"""
<details>
<summary><span>Счета подробно</span><span class="tag">{fmt_rub(page['start_balance'])}</span></summary>
<div class="dbody">
{rows}
<div class="kv"><span>Итого ликвидные (рубли)</span><span>{fmt_rub(page['start_balance'])}</span></div>
<div class="kv"><span>Долги бизнеса</span><span>{fmt_rub(model.get('business_debt', 0))}</span></div>
<div class="kv"><span>Личные долги</span><span>{fmt_rub(model.get('personal_debt', 0))}</span></div>
{source}{fx}{delta_note}
</div>
</details>
"""


def details_watchdog(report: dict[str, Any]) -> str:
    """Сторож задвоения: срабатывания — секция раскрыта и подсвечена."""
    suspects = report.get("suspects") or []
    confirmed = report.get("confirmed") or []
    body: list[str] = []

    if suspects:
        items = "\n".join(
            "    <li>Строка прогноза «{project}» ({amount}) похожа на уже пришедший платёж: "
            "{date}, {contragent}, {value} — проверь задвоение.{comment}{note}</li>".format(
                project=esc(m["row"]["project"]),
                amount=fmt_rub(m["row"]["amount"]),
                date=esc(ddmm(m["op"]["date"]) + "." + m["op"]["date"][:4]),
                contragent=esc(m["op"]["contragent"]),
                value=fmt_rub(m["op"]["value"]),
                comment=f' <span class="dupe-note">({esc(m["op"]["comment"])})</span>'
                        if m["op"]["comment"] else "",
                note=f'<div class="dupe-note">Пометка модели: {esc(m["row"]["dupe_note"])}</div>'
                     if m["row"].get("dupe_note") else "",
            )
            for m in suspects
        )
        body.append(f"""<div class="dupe"><div class="dupe-h">⚠️ Совпадений по сумме и дате: {len(suspects)}</div>
  <ul>
{items}
  </ul>
  <div class="dupe-note">Матчинг детерминированный: сумма ±{round(DUPE_TOLERANCE * 100)}%, окно
  [месяц строки −{DUPE_WINDOW_DAYS} дн … сегодня], названия не сравниваются. Строку из прогноза
  автоматически никто не убирает — решение за Антоном.</div></div>""")

    if confirmed:
        items = "\n".join(
            "    <li>«{project}» ({amount}) — подтверждено фактом {date}: {contragent}, {value}.</li>".format(
                project=esc(m["row"]["project"]),
                amount=fmt_rub(m["row"]["amount"]),
                date=esc(ddmm(m["op"]["date"])),
                contragent=esc(m["op"]["contragent"]),
                value=fmt_rub(m["op"]["value"]),
            )
            for m in confirmed
        )
        body.append(f"""<div class="dupe"><div class="dupe-h dupe-ok">✅ Найдено в выписке ПланФакта (строки с флагом «получен»)</div>
  <ul>
{items}
  </ul>
  <div class="dupe-note">Деньги уже в остатке — строку в прогнозе стоит закрыть, чтобы не задваивать.
  Автоматически не убираем.</div></div>""")

    if report.get("problem"):
        body.append(f'<div class="flag">not verified — {esc(report["problem"])}.</div>')
    elif not suspects and not confirmed:
        excluded = report.get("excluded_count") or 0
        excluded_note = f" (исключено как уже разнесённые: {excluded})" if excluded else ""
        body.append(
            f'<div class="dupe">Совпадений нет: проверено строк {report.get("checked_rows", 0)} '
            f'против {report.get("ops_count", 0)} фактических приходов с {esc(report.get("since") or "—")}'
            f'{excluded_note}.'
            f'<div class="dupe-note">Допуск по сумме ±{round(DUPE_TOLERANCE * 100)}%, '
            f'окно −{DUPE_WINDOW_DAYS} дн от месяца строки.</div></div>'
        )

    if suspects:
        head = f'<span>⚠️ Сторож задвоения · {len(suspects)} совпадени(е/я)</span>'
        attrs = ' open class="alarm"'
    elif confirmed:
        head = f'<span>Сторож задвоения · подтверждено фактом: {len(confirmed)}</span>'
        attrs = ""
    elif report.get("problem"):
        head = '<span>Сторож задвоения</span><span class="tag">not verified</span>'
        attrs = ""
    else:
        head = '<span>Сторож задвоения</span><span class="tag">совпадений нет</span>'
        attrs = ""

    return f"""
<details{attrs}>
<summary>{head}</summary>
<div class="dbody">
{"".join(body)}
</div>
</details>
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
    src_line = (
        f"данные: ПланФакт снапшот от {esc(balances.get('date') or sources.get('planfact', '—'))}"
        f" · ZenMoney снапшот от {esc(sources.get('zenmoney_date') or '—')}"
        f" · модель обновлена {esc(model.get('updated_at'))}"
        f" · собрано {esc(common.now_iso())}"
    )
    model_notes = "".join(
        f'<div>{esc(n)}</div>' for n in model.get("notes", []) if "522" not in n
    )
    watchdog = details_watchdog(dupes)
    # срабатывание сторожа — наверх страницы, всё остальное состояние — вниз, в общий список
    watchdog_top = watchdog if dupes.get("suspects") else ""
    watchdog_bottom = "" if dupes.get("suspects") else watchdog

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
<div class="sub">единый пул: бизнес + личное · модель от {esc(model.get('updated_at'))} ·
остатки из ПланФакта {esc(balances.get('date') or '—')}</div>
{watchdog_top}
{section_hero(balances, page)}
{section_scenarios(page)}
{section_months(model, page)}

<div class="stitle">Подробности</div>
{details_income(model, dupe_marks(dupes))}
{details_team_debts(model)}
{details_spending(model, page)}
{details_runway(model, page)}
{details_accounts(model, balances, page)}
{watchdog_bottom}

<footer>
  {model_notes}
  {src_line}<br>
  Источник плана: {esc(model.get('source', ''))}. Рендер детерминированный (Scripts/render_dashboard.py),
  цифры не генерируются моделью.
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

    # окно первого месяца прогноза считаем от даты живых остатков: всё до неё
    # уже отражено в самом остатке, досчитывать надо только хвост месяца
    as_of: _date | None = None
    if balances.get("date"):
        try:
            as_of = _date.fromisoformat(str(balances["date"])[:10])
        except ValueError:
            log.warning("Дата снапшота ПланФакта нечитаема (%s) — беру сегодняшнюю", balances["date"])
    if as_of is None:
        as_of = _date.fromisoformat(today)
        log.warning("Даты снапшота ПланФакта нет — окно первого месяца прогноза считаю от %s", today)

    page = build_page_data(model, balances, as_of)
    if page["months"]:
        first = page["months"][0]
        log.info("Прогноз: %d мес. (%s…%s); первый месяц частичный — %s из %s дн. от %s",
                 len(page["months"]), page["months"][0]["key"], page["months"][-1]["key"],
                 first.get("days_left"), first.get("days_total"), as_of.isoformat())
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
