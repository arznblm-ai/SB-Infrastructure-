#!/usr/bin/env python3
"""Журнал Роки: еда, тренировки, вес, профили и агрегаты. Только чистые функции + тонкий IO.

Запись в журнал делает ТОЛЬКО код: модель присылает блок ```rocky-actions```,
бот его вырезает, а здесь каждое действие валидируется и применяется.

Хранилище (на VPS, не в vault):
  data/journal/YYYY-MM-DD.jsonl  - одна запись на строку, день по Москве;
  data/profiles.json             - {"anton": {...}, "ralina": {...}};
  data/reports/YYYY-Www.md       - недельные отчёты (пишет бот).

Записи не удаляются физически: delete_entry ставит `deleted: true` (tombstone),
чтобы id никогда не переиспользовались и история правок была видна.
"""

import json
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

WHO = ("anton", "ralina")
WHO_RU = {"anton": "Антон", "ralina": "Ралина"}

KIND_PREFIX = {"meal": "m", "workout": "w", "weight": "g"}
PREFIX_KIND = {v: k for k, v in KIND_PREFIX.items()}
ID_RE = re.compile(r"^([mwg])-(\d{8})-(\d+)$")

EDIT_WINDOW_DAYS = 7      # update/delete - только записи последних 7 дней
NORM_TOLERANCE = 0.10     # «день в норме» = ккал в пределах ±10% от target_kcal

OPS = ("add_meal", "add_workout", "add_weight", "update_entry", "delete_entry", "set_profile")
ADD_OPS = {"add_meal": "meal", "add_workout": "workout", "add_weight": "weight"}

# Спецификации полей: ("num", lo, hi) | ("str", maxlen) | ("list", max_items) | ("enum", {...})
FIELD_SPECS = {
    "meal": {
        "items": ("list", 30),
        "kcal": ("num", 0, 10000),
        "kcal_lo": ("num", 0, 10000),
        "kcal_hi": ("num", 0, 10000),
        "p": ("num", 0, 1000),
        "f": ("num", 0, 1000),
        "c": ("num", 0, 2000),
        "photo": ("str", 300),
        "note": ("str", 500),
    },
    "workout": {
        "type": ("str", 100),
        "minutes": ("num", 0, 1440),
        "kcal": ("num", 0, 10000),
        "hr_avg": ("num", 20, 250),
        "note": ("str", 500),
    },
    "weight": {
        "kg": ("num", 20, 400),
        "note": ("str", 500),
    },
    "profile": {
        "age": ("num", 10, 100),
        "sex": ("enum", {"m", "f"}),
        "height_cm": ("num", 100, 250),
        "weight_kg": ("num", 20, 400),
        "goal": ("str", 300),
        "activity": ("str", 300),
        "restrictions": ("str", 500),
        "target_kcal": ("num", 800, 6000),
        "target_p": ("num", 0, 1000),
        "target_f": ("num", 0, 1000),
        "target_c": ("num", 0, 2000),
        "notes": ("str", 1000),
    },
}
REQUIRED = {"meal": ("kcal",), "workout": ("type",), "weight": ("kg",)}

PROFILE_ORDER = (
    "age", "sex", "height_cm", "weight_kg", "goal", "activity", "restrictions",
    "target_kcal", "target_p", "target_f", "target_c", "notes",
)
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# ---------------------------------------------------------------------------
# Время и пути
# ---------------------------------------------------------------------------
def msk(dt):
    """Любой aware datetime -> Москва. Наивный считаем уже московским."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def day_of(dt):
    return msk(dt).strftime("%Y-%m-%d")


def journal_dir(data_dir):
    return os.path.join(data_dir, "journal")


def journal_path(day, data_dir):
    return os.path.join(journal_dir(data_dir), "%s.jsonl" % day)


def profiles_path(data_dir):
    return os.path.join(data_dir, "profiles.json")


def reports_dir(data_dir):
    return os.path.join(data_dir, "reports")


def write_json_atomic(path, data):
    """tmp + os.replace: частично записанного файла на диске не бывает."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_text_atomic(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Чтение и запись журнала дня
# ---------------------------------------------------------------------------
def read_entries(day, data_dir, include_deleted=False):
    """Записи дня. Битые строки пропускаются (журнал не должен ронять бота)."""
    path = journal_path(day, data_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if item.get("deleted") and not include_deleted:
            continue
        out.append(item)
    return out


def write_entries(day, entries, data_dir):
    text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    _write_text_atomic(journal_path(day, data_dir), text)


def _append_entry_line(day, entry, data_dir):
    path = journal_path(day, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def next_id(kind, day, existing):
    """`m-20261022-3`: номер = максимум по дню и типу + 1 (с учётом удалённых)."""
    prefix = KIND_PREFIX[kind]
    compact = day.replace("-", "")
    top = 0
    for entry in existing or ():
        m = ID_RE.match(str(entry.get("id") or ""))
        if m and m.group(1) == prefix and m.group(2) == compact:
            top = max(top, int(m.group(3)))
    return "%s-%s-%d" % (prefix, compact, top + 1)


def day_from_id(entry_id):
    """'m-20261022-3' -> '2026-10-22' или None."""
    m = ID_RE.match(str(entry_id or ""))
    if not m:
        return None
    raw = m.group(2)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Валидация действий модели
# ---------------------------------------------------------------------------
def _coerce(spec, value):
    """(значение, ошибка). Числа допускаем строкой «620» - модель иногда так пишет."""
    kind = spec[0]
    if kind == "num":
        if isinstance(value, bool):
            return None, "не число"
        if isinstance(value, str):
            try:
                value = float(value.strip().replace(",", "."))
            except ValueError:
                return None, "не число"
        if not isinstance(value, (int, float)) or math.isnan(value) or math.isinf(value):
            return None, "не число"
        lo, hi = spec[1], spec[2]
        if value < lo or value > hi:
            return None, "вне диапазона %s..%s" % (lo, hi)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value, None
    if kind == "str":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            return None, "не строка"
        value = value.strip()
        if not value:
            return None, "пустая строка"
        return value[: spec[1]], None
    if kind == "list":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return None, "не список"
        items = [str(x).strip()[:200] for x in value if str(x).strip()]
        return items[: spec[1]], None
    if kind == "enum":
        val = str(value).strip().lower()
        aliases = {"м": "m", "муж": "m", "male": "m", "ж": "f", "жен": "f", "female": "f"}
        val = aliases.get(val, val)
        if val not in spec[1]:
            return None, "допустимо %s" % sorted(spec[1])
        return val, None
    return None, "неизвестный тип поля"


def _validate_fields(kind, raw_fields, allow_who=False):
    """Поля по спецификации. Неизвестные и null - молча выкидываем, кривые - ошибка."""
    specs = FIELD_SPECS[kind]
    fields = {}
    for key, value in (raw_fields or {}).items():
        if value is None:
            continue
        if key == "who" and allow_who:
            if value not in WHO:
                return None, "who=%r" % (value,)
            fields["who"] = value
            continue
        spec = specs.get(key)
        if spec is None:
            continue  # «неизвестное поле - не пиши его»: модель ошиблась, не страшно
        val, err = _coerce(spec, value)
        if err:
            return None, "%s: %s" % (key, err)
        fields[key] = val
    return fields, None


def validate_action(raw):
    """Сырое действие -> (нормализованное, None) или (None, "ошибка").

    Нормализованная форма:
      add_*        {"op", "kind", "who", "fields"}
      update_entry {"op", "id", "kind", "fields"}
      delete_entry {"op", "id"}
      set_profile  {"op", "who", "fields"}
    """
    if not isinstance(raw, dict):
        return None, "действие не объект"
    op = raw.get("op")
    if op not in OPS:
        return None, "неизвестный op %r" % (op,)

    if op in ADD_OPS:
        kind = ADD_OPS[op]
        who = raw.get("who")
        if who not in WHO:
            return None, "%s: who=%r" % (op, who)
        body = {k: v for k, v in raw.items() if k not in ("op", "who")}
        fields, err = _validate_fields(kind, body)
        if err:
            return None, "%s: %s" % (op, err)
        missing = [k for k in REQUIRED[kind] if k not in fields]
        if missing:
            return None, "%s: нет поля %s" % (op, ",".join(missing))
        return {"op": op, "kind": kind, "who": who, "fields": fields}, None

    if op in ("update_entry", "delete_entry"):
        entry_id = str(raw.get("id") or "").strip()
        m = ID_RE.match(entry_id)
        if not m or day_from_id(entry_id) is None:
            return None, "%s: кривой id %r" % (op, raw.get("id"))
        if op == "delete_entry":
            return {"op": op, "id": entry_id}, None
        kind = PREFIX_KIND[m.group(1)]
        raw_fields = raw.get("fields")
        if not isinstance(raw_fields, dict) or not raw_fields:
            return None, "update_entry: нет fields"
        fields, err = _validate_fields(kind, raw_fields, allow_who=True)
        if err:
            return None, "update_entry: %s" % err
        if not fields:
            return None, "update_entry: нечего менять"
        return {"op": op, "id": entry_id, "kind": kind, "fields": fields}, None

    # set_profile
    who = raw.get("who")
    if who not in WHO:
        return None, "set_profile: who=%r" % (who,)
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        return None, "set_profile: нет fields"
    fields, err = _validate_fields("profile", raw_fields)
    if err:
        return None, "set_profile: %s" % err
    if not fields:
        return None, "set_profile: нечего менять"
    return {"op": op, "who": who, "fields": fields}, None


# ---------------------------------------------------------------------------
# Применение действий
# ---------------------------------------------------------------------------
def add_entry(kind, who, fields, now, data_dir):
    day = day_of(now)
    existing = read_entries(day, data_dir, include_deleted=True)
    entry = {
        "id": next_id(kind, day, existing),
        "kind": kind,
        "who": who,
        "ts": msk(now).isoformat(timespec="seconds"),
    }
    entry.update(fields)
    _append_entry_line(day, entry, data_dir)
    return entry


def _within_window(entry_day, now):
    try:
        delta = (msk(now).date() - date.fromisoformat(entry_day)).days
    except ValueError:
        return False
    return 0 <= delta <= EDIT_WINDOW_DAYS


def update_entry(entry_id, fields, now, data_dir):
    """(запись, None) или (None, ошибка)."""
    day = day_from_id(entry_id)
    if day is None:
        return None, "кривой id"
    if not _within_window(day, now):
        return None, "запись старше %d дней или из будущего" % EDIT_WINDOW_DAYS
    entries = read_entries(day, data_dir, include_deleted=True)
    for entry in entries:
        if entry.get("id") == entry_id:
            if entry.get("deleted"):
                return None, "запись удалена"
            entry.update(fields)
            entry["updated_ts"] = msk(now).isoformat(timespec="seconds")
            write_entries(day, entries, data_dir)
            return entry, None
    return None, "нет записи %s" % entry_id


def delete_entry(entry_id, now, data_dir):
    day = day_from_id(entry_id)
    if day is None:
        return None, "кривой id"
    if not _within_window(day, now):
        return None, "запись старше %d дней или из будущего" % EDIT_WINDOW_DAYS
    entries = read_entries(day, data_dir, include_deleted=True)
    for entry in entries:
        if entry.get("id") == entry_id:
            if entry.get("deleted"):
                return None, "уже удалена"
            entry["deleted"] = True
            entry["deleted_ts"] = msk(now).isoformat(timespec="seconds")
            write_entries(day, entries, data_dir)
            return entry, None
    return None, "нет записи %s" % entry_id


def load_profiles(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for who in WHO:
        if not isinstance(data.get(who), dict):
            data[who] = {}
    return data


def merge_profile(profiles, who, fields, now):
    """Новый dict профилей: поля мерджатся поверх, остальное как было. Чистая."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in (profiles or {}).items()}
    current = dict(out.get(who) or {})
    current.update(fields)
    current["updated_at"] = msk(now).isoformat(timespec="seconds")
    out[who] = current
    for w in WHO:
        out.setdefault(w, {})
    return out


def apply_actions(actions, now, data_dir):
    """Применить список сырых действий. Невалидное действие пропускается (в errors),
    валидные применяются. Возвращает {"applied": [...], "errors": [...]}."""
    result = {"applied": [], "errors": []}
    if not isinstance(actions, list):
        result["errors"].append("actions не список")
        return result
    ppath = profiles_path(data_dir)
    for raw in actions:
        action, err = validate_action(raw)
        if err:
            result["errors"].append(err)
            continue
        op = action["op"]
        if op in ADD_OPS:
            entry = add_entry(action["kind"], action["who"], action["fields"], now, data_dir)
            result["applied"].append("%s %s" % (op, entry["id"]))
        elif op == "update_entry":
            entry, err = update_entry(action["id"], action["fields"], now, data_dir)
            if err:
                result["errors"].append("update_entry %s: %s" % (action["id"], err))
            else:
                result["applied"].append("update_entry %s" % entry["id"])
        elif op == "delete_entry":
            entry, err = delete_entry(action["id"], now, data_dir)
            if err:
                result["errors"].append("delete_entry %s: %s" % (action["id"], err))
            else:
                result["applied"].append("delete_entry %s" % entry["id"])
        elif op == "set_profile":
            profiles = merge_profile(load_profiles(ppath), action["who"], action["fields"], now)
            write_json_atomic(ppath, profiles)
            result["applied"].append("set_profile %s (%s)" % (action["who"], ",".join(sorted(action["fields"]))))
    return result


# ---------------------------------------------------------------------------
# Агрегаты
# ---------------------------------------------------------------------------
def _num(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return value
    return 0


def day_totals(entries, who):
    """Суммы дня по человеку. Удалённые записи не считаются."""
    live = [e for e in entries or () if e.get("who") == who and not e.get("deleted")]
    meals = [e for e in live if e.get("kind") == "meal"]
    workouts = [e for e in live if e.get("kind") == "workout"]
    weights = [e for e in live if e.get("kind") == "weight" and e.get("kg") is not None]
    totals = {
        "kcal": round(sum(_num(e.get("kcal")) for e in meals)),
        "p": round(sum(_num(e.get("p")) for e in meals)),
        "f": round(sum(_num(e.get("f")) for e in meals)),
        "c": round(sum(_num(e.get("c")) for e in meals)),
        "meals": len(meals),
        "workouts": len(workouts),
        "workout_minutes": round(sum(_num(e.get("minutes")) for e in workouts)),
        "workout_kcal": round(sum(_num(e.get("kcal")) for e in workouts)),
        "weight": weights[-1].get("kg") if weights else None,
    }
    return totals


def profile_filled(profile):
    """Профиль считается заполненным, когда есть норма ккал."""
    return isinstance(profile, dict) and profile.get("target_kcal") is not None


def remaining(totals, profile):
    """Остаток до нормы по ккал/БЖУ (отрицательный = перебор). Нет нормы - ключа нет."""
    out = {}
    for key in ("kcal", "p", "f", "c"):
        target = (profile or {}).get("target_" + key)
        if isinstance(target, (int, float)) and not isinstance(target, bool):
            out[key] = round(target - totals.get(key, 0))
    return out


def in_norm(kcal, target, tolerance=NORM_TOLERANCE):
    if not target:
        return False
    return abs(kcal - target) <= tolerance * target


def _fmt_num(value):
    if isinstance(value, float):
        return ("%.1f" % value).rstrip("0").rstrip(".")
    return str(value)


def format_entry(entry):
    """Строка записи для промпта и /today (с id - по нему модель правит)."""
    ts = str(entry.get("ts") or "")
    hhmm = ts[11:16] if len(ts) >= 16 else "--:--"
    kind = entry.get("kind")
    eid = entry.get("id", "?")
    if kind == "meal":
        items = ", ".join(entry.get("items") or []) or "еда"
        line = "- %s %s %s: %s ккал" % (eid, hhmm, items, _fmt_num(entry.get("kcal", 0)))
        if entry.get("kcal_lo") is not None and entry.get("kcal_hi") is not None:
            line += " (%s–%s)" % (_fmt_num(entry["kcal_lo"]), _fmt_num(entry["kcal_hi"]))
        macros = [
            "%s %s" % (label, _fmt_num(entry[key]))
            for key, label in (("p", "Б"), ("f", "Ж"), ("c", "У"))
            if entry.get(key) is not None
        ]
        if macros:
            line += " · " + " ".join(macros)
    elif kind == "workout":
        line = "- %s %s тренировка: %s" % (eid, hhmm, entry.get("type") or "?")
        extra = []
        if entry.get("minutes") is not None:
            extra.append("%s мин" % _fmt_num(entry["minutes"]))
        if entry.get("kcal") is not None:
            extra.append("%s ккал" % _fmt_num(entry["kcal"]))
        if entry.get("hr_avg") is not None:
            extra.append("пульс %s" % _fmt_num(entry["hr_avg"]))
        if extra:
            line += " · " + ", ".join(extra)
    elif kind == "weight":
        line = "- %s %s вес: %s кг" % (eid, hhmm, _fmt_num(entry.get("kg")))
    else:
        line = "- %s %s ?" % (eid, hhmm)
    if entry.get("note"):
        line += " (%s)" % entry["note"]
    return line


def format_totals(totals, profile):
    """`Итого: 1240 из 2100 ккал (осталось 860) · Б 95/130 · ...`."""
    filled = profile_filled(profile)
    parts = []
    if filled:
        rest = remaining(totals, profile)
        kcal = "Итого: %d из %s ккал" % (totals["kcal"], _fmt_num(profile["target_kcal"]))
        if rest.get("kcal", 0) >= 0:
            kcal += " (осталось %d)" % rest["kcal"]
        else:
            kcal += " (перебор %d)" % -rest["kcal"]
        parts.append(kcal)
        for key, label in (("p", "Б"), ("f", "Ж"), ("c", "У")):
            target = profile.get("target_" + key)
            if target is not None:
                parts.append("%s %d/%s" % (label, totals[key], _fmt_num(target)))
            else:
                parts.append("%s %d" % (label, totals[key]))
    else:
        parts.append("Итого: %d ккал" % totals["kcal"])
        parts.append("Б %d Ж %d У %d" % (totals["p"], totals["f"], totals["c"]))
        parts.append("норма не задана")
    line = " · ".join(parts)
    extra = []
    if totals["workouts"]:
        extra.append("тренировок %d (%d мин)" % (totals["workouts"], totals["workout_minutes"]))
    if totals["weight"] is not None:
        extra.append("вес %s кг" % _fmt_num(totals["weight"]))
    if extra:
        line += "; " + ", ".join(extra)
    return line


def format_profile(profile):
    if not profile:
        return "профиль не заполнен"
    parts = ["%s: %s" % (k, _fmt_num(profile[k])) for k in PROFILE_ORDER if profile.get(k) not in (None, "")]
    text = "; ".join(parts) if parts else "пусто"
    if not profile_filled(profile):
        text += " (профиль не заполнен: нормы ещё нет)"
    return text


# ---------------------------------------------------------------------------
# Недельная сводка
# ---------------------------------------------------------------------------
def previous_week_monday(dt):
    """Понедельник ПРОШЛОЙ недели относительно dt (по Москве)."""
    local = msk(dt).date()
    return local - timedelta(days=local.weekday() + 7)


def week_days(monday):
    return [(monday + timedelta(days=i)).isoformat() for i in range(7)]


def week_key(monday):
    iso = monday.isocalendar()
    return "%d-W%02d" % (iso[0], iso[1])


def read_days(days, data_dir):
    return {day: read_entries(day, data_dir) for day in days}


def weekly_summary(entries_by_day, profiles, days):
    """Сводка недели по каждому. Чистая: на входе уже прочитанные записи."""
    summary = {}
    for who in WHO:
        profile = (profiles or {}).get(who) or {}
        target = profile.get("target_kcal")
        rows = []
        workouts_types = {}
        workouts_count = workouts_minutes = workouts_kcal = 0
        weights = []
        for day in days:
            entries = entries_by_day.get(day) or []
            t = day_totals(entries, who)
            rows.append({
                "day": day,
                "kcal": t["kcal"], "p": t["p"], "f": t["f"], "c": t["c"],
                "meals": t["meals"],
                "in_norm": bool(t["meals"]) and in_norm(t["kcal"], target),
            })
            for e in entries:
                if e.get("who") != who or e.get("deleted"):
                    continue
                if e.get("kind") == "workout":
                    workouts_count += 1
                    workouts_minutes += _num(e.get("minutes"))
                    workouts_kcal += _num(e.get("kcal"))
                    name = str(e.get("type") or "?")
                    workouts_types[name] = workouts_types.get(name, 0) + 1
                elif e.get("kind") == "weight" and e.get("kg") is not None:
                    weights.append((day, e.get("kg")))
        logged = [r for r in rows if r["meals"]]
        n = len(logged)

        def avg(key):
            return round(sum(r[key] for r in logged) / n) if n else None

        summary[who] = {
            "rows": rows,
            "logged_days": n,
            "days_in_norm": sum(1 for r in rows if r["in_norm"]),
            "avg_kcal": avg("kcal"),
            "avg_p": avg("p"),
            "avg_f": avg("f"),
            "avg_c": avg("c"),
            "targets": {k: profile.get("target_" + k) for k in ("kcal", "p", "f", "c")},
            "workouts": {
                "count": workouts_count,
                "minutes": round(workouts_minutes),
                "kcal": round(workouts_kcal),
                "types": workouts_types,
            },
            "weights": weights,
            "weight_trend": round(weights[-1][1] - weights[0][1], 1) if len(weights) >= 2 else None,
        }
    return summary


def format_weekly_text(summary, days):
    """Таблица недели текстом: её видит модель и она же - фолбэк без модели."""
    first = date.fromisoformat(days[0])
    last = date.fromisoformat(days[-1])
    lines = ["Неделя %s – %s" % (first.strftime("%d.%m"), last.strftime("%d.%m.%Y"))]
    for who in WHO:
        s = summary[who]
        tg = s["targets"]
        lines.append("")
        lines.append("%s:" % WHO_RU[who])
        for row in s["rows"]:
            d = date.fromisoformat(row["day"])
            label = "%s %s" % (WEEKDAYS_SHORT[d.weekday()], d.strftime("%d.%m"))
            if not row["meals"]:
                lines.append("  %s: нет записей еды" % label)
                continue
            mark = " ✓" if row["in_norm"] else ""
            lines.append(
                "  %s: %d ккал · Б %d · Ж %d · У %d%s"
                % (label, row["kcal"], row["p"], row["f"], row["c"], mark)
            )
        if s["logged_days"]:
            avg_line = "  Среднее за %d дн.: %d ккал, Б %d" % (s["logged_days"], s["avg_kcal"], s["avg_p"])
            if tg.get("kcal"):
                avg_line += " (норма %s ккал, Б %s)" % (_fmt_num(tg["kcal"]), _fmt_num(tg.get("p") or "?"))
            lines.append(avg_line)
            if tg.get("kcal"):
                lines.append("  Дней в норме (±10%%): %d из %d" % (s["days_in_norm"], s["logged_days"]))
            else:
                lines.append("  Норма не задана - дни в норме не считаю")
        else:
            lines.append("  За неделю ни одной записи еды")
        w = s["workouts"]
        if w["count"]:
            types = ", ".join("%s ×%d" % (k, v) for k, v in sorted(w["types"].items()))
            lines.append("  Тренировок: %d, %d мин (%s)" % (w["count"], w["minutes"], types))
        else:
            lines.append("  Тренировок: 0")
        if s["weights"]:
            ws = ", ".join("%s %s" % (date.fromisoformat(d).strftime("%d.%m"), _fmt_num(kg)) for d, kg in s["weights"])
            trend = s["weight_trend"]
            lines.append(
                "  Вес: %s%s"
                % (ws, "" if trend is None else "; тренд %+.1f кг" % trend)
            )
        else:
            lines.append("  Вес: замеров нет")
    return "\n".join(lines)
