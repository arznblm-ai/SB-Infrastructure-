"""Тесты журнала Роки: валидация, запись, правки, профили, суммы, неделя. Без сети."""

import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import journal  # noqa: E402

NOW = datetime(2026, 10, 22, 13, 5, tzinfo=journal.MSK)


def meal(who="anton", **kw):
    base = {"op": "add_meal", "who": who, "items": ["гречка 150 г"], "kcal": 620,
            "kcal_lo": 550, "kcal_hi": 700, "p": 42, "f": 24, "c": 55}
    base.update(kw)
    return base


# --- валидация -------------------------------------------------------------
def test_validate_add_meal_ok_and_unknown_dropped():
    action, err = journal.validate_action(meal(color="red"))
    assert err is None
    assert action["kind"] == "meal" and action["who"] == "anton"
    assert "color" not in action["fields"]
    assert action["fields"]["kcal"] == 620


def test_validate_rejects_bad_who_op_and_numbers():
    assert journal.validate_action(meal(who="vasya"))[0] is None
    assert journal.validate_action({"op": "drop_table"})[0] is None
    assert journal.validate_action(meal(kcal="много"))[0] is None
    assert journal.validate_action(meal(kcal=-5))[0] is None
    assert journal.validate_action(meal(kcal=True))[0] is None
    assert journal.validate_action(meal(kcal=float("nan")))[0] is None
    assert journal.validate_action("строка")[0] is None


def test_validate_meal_requires_kcal_and_accepts_numeric_string():
    raw = meal()
    del raw["kcal"]
    assert journal.validate_action(raw)[0] is None
    action, _ = journal.validate_action(meal(kcal="620"))
    assert action["fields"]["kcal"] == 620


def test_validate_workout_weight_profile():
    a, err = journal.validate_action({"op": "add_workout", "who": "ralina", "type": "бег", "minutes": 35, "hr_avg": 148})
    assert err is None and a["fields"]["minutes"] == 35
    assert journal.validate_action({"op": "add_workout", "who": "ralina", "minutes": 35})[0] is None
    assert journal.validate_action({"op": "add_weight", "who": "anton", "kg": 72.4})[0]["fields"]["kg"] == 72.4
    assert journal.validate_action({"op": "add_weight", "who": "anton", "kg": 7})[0] is None
    p, err = journal.validate_action({"op": "set_profile", "who": "ralina", "fields": {"age": 27, "sex": "ж", "target_kcal": 1750}})
    assert err is None and p["fields"]["sex"] == "f"
    assert journal.validate_action({"op": "set_profile", "who": "ralina", "fields": {}})[0] is None


def test_validate_update_delete_ids():
    assert journal.validate_action({"op": "delete_entry", "id": "m-20261022-1"})[0] is not None
    assert journal.validate_action({"op": "delete_entry", "id": "../etc"})[0] is None
    assert journal.validate_action({"op": "delete_entry", "id": "m-20261340-1"})[0] is None
    up, err = journal.validate_action({"op": "update_entry", "id": "m-20261022-3", "fields": {"kcal": 480, "who": "ralina"}})
    assert err is None and up["kind"] == "meal" and up["fields"]["who"] == "ralina"
    assert journal.validate_action({"op": "update_entry", "id": "m-20261022-3", "fields": {"who": "x"}})[0] is None
    assert journal.validate_action({"op": "update_entry", "id": "g-20261022-1", "fields": {"kcal": 5}})[0] is None


# --- применение ------------------------------------------------------------
def test_apply_add_assigns_ids_ts_and_writes_jsonl(tmp_path):
    data = str(tmp_path)
    res = journal.apply_actions([
        meal(),
        meal(who="ralina", kcal=300),
        {"op": "add_workout", "who": "ralina", "type": "бег", "minutes": 35},
        {"op": "add_weight", "who": "anton", "kg": 72.4},
    ], NOW, data)
    assert res["errors"] == []
    entries = journal.read_entries("2026-10-22", data)
    ids = [e["id"] for e in entries]
    assert ids == ["m-20261022-1", "m-20261022-2", "w-20261022-1", "g-20261022-1"]
    assert entries[0]["ts"].startswith("2026-10-22T13:05")
    assert entries[1]["who"] == "ralina"
    with open(journal.journal_path("2026-10-22", data), encoding="utf-8") as f:
        assert len(f.read().splitlines()) == 4


def test_apply_skips_invalid_but_applies_valid(tmp_path):
    res = journal.apply_actions([meal(who="nobody"), meal()], NOW, str(tmp_path))
    assert len(res["errors"]) == 1 and len(res["applied"]) == 1
    assert journal.apply_actions({"op": "x"}, NOW, str(tmp_path))["errors"]


def test_update_and_delete(tmp_path):
    data = str(tmp_path)
    journal.apply_actions([meal(), meal(kcal=100)], NOW, data)
    res = journal.apply_actions([{"op": "update_entry", "id": "m-20261022-1", "fields": {"kcal": 480, "f": 14}}], NOW, data)
    assert res["errors"] == []
    e = journal.read_entries("2026-10-22", data)[0]
    assert e["kcal"] == 480 and e["f"] == 14 and e["p"] == 42 and "updated_ts" in e

    res = journal.apply_actions([{"op": "delete_entry", "id": "m-20261022-2"}], NOW, data)
    assert res["errors"] == []
    assert [x["id"] for x in journal.read_entries("2026-10-22", data)] == ["m-20261022-1"]
    assert len(journal.read_entries("2026-10-22", data, include_deleted=True)) == 2
    # удалённую нельзя ни править, ни удалить повторно; id не переиспользуется
    assert journal.apply_actions([{"op": "update_entry", "id": "m-20261022-2", "fields": {"kcal": 1}}], NOW, data)["errors"]
    assert journal.apply_actions([{"op": "delete_entry", "id": "m-20261022-2"}], NOW, data)["errors"]
    journal.apply_actions([meal()], NOW, data)
    assert journal.read_entries("2026-10-22", data)[-1]["id"] == "m-20261022-3"


def test_update_missing_and_outside_window(tmp_path):
    data = str(tmp_path)
    assert journal.apply_actions([{"op": "delete_entry", "id": "m-20261022-9"}], NOW, data)["errors"]
    old = datetime(2026, 10, 10, 12, 0, tzinfo=journal.MSK)
    journal.apply_actions([meal()], old, data)
    res = journal.apply_actions([{"op": "update_entry", "id": "m-20261010-1", "fields": {"kcal": 1}}], NOW, data)
    assert "7" in res["errors"][0]
    # 7 дней назад - ещё можно
    week_ago = datetime(2026, 10, 15, 9, 0, tzinfo=journal.MSK)
    journal.apply_actions([meal()], week_ago, data)
    assert journal.apply_actions([{"op": "delete_entry", "id": "m-20261015-1"}], NOW, data)["errors"] == []
    # будущее - нельзя
    assert journal.apply_actions([{"op": "delete_entry", "id": "m-20261030-1"}], NOW, data)["errors"]


def test_read_entries_skips_broken_lines(tmp_path):
    path = journal.journal_path("2026-10-22", str(tmp_path))
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"id": "m-20261022-1", "kind": "meal", "who": "anton", "kcal": 1}\nне json\n[1]\n\n')
    assert len(journal.read_entries("2026-10-22", str(tmp_path))) == 1


def test_day_boundary_is_moscow(tmp_path):
    from datetime import timezone
    utc_late = datetime(2026, 10, 22, 22, 30, tzinfo=timezone.utc)  # 01:30 МСК 23-го
    journal.apply_actions([meal()], utc_late, str(tmp_path))
    assert journal.read_entries("2026-10-23", str(tmp_path))[0]["id"] == "m-20261023-1"


def test_add_yesterday_and_move_preserve_totals_and_history(tmp_path):
    data = str(tmp_path)
    res = journal.apply_actions([meal(day="2026-10-21")], NOW, data)
    assert not res["errors"]
    assert journal.day_totals(journal.read_entries("2026-10-21", data), "anton")["kcal"] == 620
    assert not journal.read_entries("2026-10-22", data)
    journal.apply_actions([meal(who="ralina", kcal=400)], NOW, data)
    res = journal.apply_actions([{"op": "move_entry", "id": "m-20261022-1", "day": "2026-10-21"}], NOW, data)
    assert not res["errors"]
    moved = journal.read_entries("2026-10-21", data)[1]
    assert moved["who"] == "ralina" and moved["moved_from"] == "m-20261022-1"
    assert "время неизвестно" in journal.format_entry(moved)
    assert not journal.read_entries("2026-10-22", data)
    assert journal.apply_actions([{"op": "move_entry", "id": "m-20261022-1", "day": "2026-10-21"}], NOW, data)["errors"]
    assert len(journal.read_entries("2026-10-21", data)) == 2


def test_historical_dates_reject_future_and_outside_window(tmp_path):
    data = str(tmp_path)
    assert journal.apply_actions([meal(day="2026-10-23")], NOW, data)["errors"]
    assert journal.apply_actions([meal(day="2026-10-01")], NOW, data)["errors"]
    assert not journal.read_entries("2026-10-22", data)


# --- профили ---------------------------------------------------------------
def test_set_profile_merges_atomically(tmp_path):
    data = str(tmp_path)
    journal.apply_actions([{"op": "set_profile", "who": "ralina", "fields": {"age": 27, "goal": "похудеть"}}], NOW, data)
    journal.apply_actions([{"op": "set_profile", "who": "ralina", "fields": {"target_kcal": 1750}}], NOW, data)
    profiles = journal.load_profiles(journal.profiles_path(data))
    assert profiles["ralina"]["age"] == 27 and profiles["ralina"]["target_kcal"] == 1750
    assert profiles["anton"] == {}
    assert not [n for n in os.listdir(data) if n.startswith(".tmp-")]
    with open(journal.profiles_path(data), encoding="utf-8") as f:
        assert json.load(f)["ralina"]["goal"] == "похудеть"


def test_load_profiles_survives_garbage(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("[1,2", encoding="utf-8")
    assert journal.load_profiles(str(path)) == {"anton": {}, "ralina": {}}


def test_merge_profile_is_pure():
    src = {"anton": {"age": 30}, "ralina": {}}
    out = journal.merge_profile(src, "anton", {"weight_kg": 80}, NOW)
    assert src["anton"] == {"age": 30}
    assert out["anton"]["age"] == 30 and out["anton"]["weight_kg"] == 80


# --- суммы -----------------------------------------------------------------
ENTRIES = [
    {"id": "m-1", "kind": "meal", "who": "anton", "kcal": 600, "p": 40, "f": 20, "c": 50},
    {"id": "m-2", "kind": "meal", "who": "anton", "kcal": 400, "p": 30, "f": 10, "c": 40},
    {"id": "m-3", "kind": "meal", "who": "anton", "kcal": 999, "deleted": True},
    {"id": "m-4", "kind": "meal", "who": "ralina", "kcal": 300, "p": 20},
    {"id": "w-1", "kind": "workout", "who": "anton", "type": "зал", "minutes": 60, "kcal": 350},
    {"id": "g-1", "kind": "weight", "who": "anton", "kg": 80.2},
    {"id": "g-2", "kind": "weight", "who": "anton", "kg": 80.0},
]


def test_day_totals_and_remaining():
    t = journal.day_totals(ENTRIES, "anton")
    assert (t["kcal"], t["p"], t["f"], t["c"], t["meals"]) == (1000, 70, 30, 90, 2)
    assert t["workouts"] == 1 and t["workout_minutes"] == 60 and t["weight"] == 80.0
    rest = journal.remaining(t, {"target_kcal": 2100, "target_p": 130})
    assert rest == {"kcal": 1100, "p": 60}
    assert journal.day_totals(ENTRIES, "ralina")["kcal"] == 300


def test_format_totals_with_and_without_norm():
    t = journal.day_totals(ENTRIES, "anton")
    text = journal.format_totals(t, {"target_kcal": 2100, "target_p": 130, "target_f": 70, "target_c": 230})
    assert "1000 из 2100" in text and "осталось 1100" in text and "Б 70/130" in text
    assert "перебор 100" in journal.format_totals(t, {"target_kcal": 900})
    assert "норма не задана" in journal.format_totals(t, {})


def test_format_entry_contains_id():
    line = journal.format_entry({"id": "m-20261022-1", "kind": "meal", "ts": "2026-10-22T13:05:00+03:00",
                                 "items": ["суп"], "kcal": 300, "kcal_lo": 250, "kcal_hi": 350, "p": 10})
    assert line.startswith("- m-20261022-1 13:05 суп: 300 ккал (250–350)") and "Б 10" in line


def test_in_norm():
    assert journal.in_norm(2000, 2100) and journal.in_norm(2310, 2100)
    assert not journal.in_norm(2400, 2100) and not journal.in_norm(2000, None)


# --- неделя ----------------------------------------------------------------
def test_previous_week_and_key():
    monday = journal.previous_week_monday(datetime(2026, 10, 26, 9, 0, tzinfo=journal.MSK))
    assert monday == date(2026, 10, 19)
    days = journal.week_days(monday)
    assert days[0] == "2026-10-19" and days[-1] == "2026-10-25"
    assert journal.week_key(monday) == "2026-W43"


def test_weekly_summary():
    days = journal.week_days(date(2026, 10, 19))
    by_day = {
        "2026-10-19": [
            {"kind": "meal", "who": "anton", "kcal": 2000, "p": 120, "f": 60, "c": 200},
            {"kind": "weight", "who": "anton", "kg": 80.4},
            {"kind": "workout", "who": "anton", "type": "зал", "minutes": 60},
        ],
        "2026-10-20": [{"kind": "meal", "who": "anton", "kcal": 2600, "p": 100, "f": 90, "c": 300}],
        "2026-10-24": [
            {"kind": "weight", "who": "anton", "kg": 79.9},
            {"kind": "workout", "who": "anton", "type": "бег", "minutes": 30, "kcal": 300},
            {"kind": "meal", "who": "anton", "kcal": 50, "deleted": True},
        ],
    }
    profiles = {"anton": {"target_kcal": 2100, "target_p": 130}, "ralina": {}}
    s = journal.weekly_summary(by_day, profiles, days)
    a = s["anton"]
    assert a["logged_days"] == 2 and a["days_in_norm"] == 1
    assert a["avg_kcal"] == 2300 and a["avg_p"] == 110
    assert a["workouts"]["count"] == 2 and a["workouts"]["minutes"] == 90
    assert a["weight_trend"] == -0.5
    assert s["ralina"]["logged_days"] == 0 and s["ralina"]["weight_trend"] is None
    text = journal.format_weekly_text(s, days)
    assert "Антон:" in text and "Ралина:" in text and "тренд -0.5" in text
    assert "Дней в норме (±10%): 1 из 2" in text and "ни одной записи" in text


# --- аудит 10.11: проверки чисел еды и дедуп ------------------------------------
def test_normalize_meal_bounds_and_macros():
    ok = {"items": ["гречка"], "kcal": 620, "kcal_lo": 550, "kcal_hi": 700, "p": 42, "f": 24, "c": 55}
    assert journal.normalize_meal(ok) == (ok, [])
    fixed, notes = journal.normalize_meal({"items": ["паста"], "kcal": 1200, "kcal_lo": 500, "kcal_hi": 700,
                                           "p": 30, "f": 20, "c": 80})
    assert fixed["kcal"] == 620 and fixed["kcal_lo"] == 500 and fixed["kcal_hi"] == 700 and len(notes) == 1
    fixed, notes = journal.normalize_meal({"kcal": 600, "kcal_lo": 700, "kcal_hi": 500})
    assert (fixed["kcal_lo"], fixed["kcal"], fixed["kcal_hi"]) == (500, 600, 700) and notes
    fixed, notes = journal.normalize_meal({"kcal": 900, "kcal_lo": 400, "kcal_hi": 600})
    assert fixed["kcal_hi"] == 900 and fixed["kcal"] == 900 and notes
    # неполные БЖУ и алкоголь по БЖУ не пересчитываются
    assert journal.normalize_meal({"kcal": 500, "p": 10})[1] == []
    beer = {"items": ["пиво 0,5"], "kcal": 215, "p": 2, "f": 0, "c": 18}
    assert journal.normalize_meal(beer) == (beer, [])


def test_apply_fixes_kcal_by_macros_and_warns_not_rejects(tmp_path):
    data = str(tmp_path)
    res = journal.apply_actions([meal(kcal=1200, p=30, f=20, c=80, items=["паста"])], NOW, data)
    assert res["applied"] == ["add_meal m-20261022-1"] and not res["errors"] and len(res["warnings"]) == 1
    entry = journal.read_entries("2026-10-22", data)[0]
    assert entry["kcal"] == 620 and entry["added_ts"].startswith("2026-10-22T")
    res = journal.apply_actions([{"op": "update_entry", "id": "m-20261022-1", "fields": {"kcal": 100}}], NOW, data)
    assert res["applied"] and res["warnings"] and journal.read_entries("2026-10-22", data)[0]["kcal"] == 620


def test_dedup_same_meal_within_ten_minutes(tmp_path):
    data = str(tmp_path)
    bare = dict(kcal_lo=None, kcal_hi=None, p=None, f=None, c=None)  # без БЖУ: kcal не пересчитывается
    m = meal(items=["Гречка 150 г"], kcal=400, **bare)
    assert journal.apply_actions([m], NOW, data)["applied"]
    again = journal.apply_actions([meal(items=["гречка  150 г"], kcal=400, **bare)], NOW + timedelta(minutes=9), data)
    assert again["applied"] == [] and len(again["skipped"]) == 1 and not again["errors"]
    assert journal.apply_actions([meal(who="ralina", items=["гречка 150 г"], kcal=400, **bare)], NOW, data)["applied"]
    assert journal.apply_actions([meal(items=["гречка 150 г"], kcal=450, **bare)], NOW, data)["applied"]
    assert journal.apply_actions([m], NOW + timedelta(minutes=11), data)["applied"]
    # запись на вчера: окно считается от момента добавления, не от условных 12:00
    y = dict(m, day="2026-10-21")
    assert journal.apply_actions([y], NOW, data)["applied"]
    assert journal.apply_actions([y], NOW + timedelta(minutes=2), data)["skipped"]
    assert len(journal.read_entries("2026-10-21", data)) == 1
