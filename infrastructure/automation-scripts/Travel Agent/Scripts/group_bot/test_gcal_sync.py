#!/usr/bin/env python3
"""Тесты синка group/calendar.json в Google Calendar.

Без сети и без google-библиотек: клиент везде фейковый, HTTP-слой проверяется
подменённой сессией. Проверяется договор «модель ведёт файл - код исполняет».
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gcal_sync  # noqa: E402
import marco_group_bot as bot  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def timed_event(**over):
    ev = {
        "id": "flight-ey844",
        "title": "✈️ Вылет EY 844 SVO→AUH",
        "start": "2026-09-21T23:55",
        "end": "2026-09-22T06:40",
        "tz": "Europe/Moscow",
        "end_tz": "Asia/Dubai",
    }
    ev.update(over)
    return ev


def allday_event(**over):
    ev = {
        "id": "abu-dhabi",
        "title": "Абу-Даби",
        "start": "2026-09-22",
        "end": "2026-09-25",
        "all_day": True,
        "tz": "Asia/Dubai",
    }
    ev.update(over)
    return ev


# ---------------------------------------------------------------------------
# validate_event
# ---------------------------------------------------------------------------
def test_validate_accepts_timed_and_allday():
    assert gcal_sync.validate_event(timed_event()) == (True, None)
    assert gcal_sync.validate_event(allday_event()) == (True, None)


def test_validate_allday_without_end_is_one_day():
    ok, error = gcal_sync.validate_event(allday_event(end=None))
    assert (ok, error) == (True, None)
    body = gcal_sync.to_google_body(allday_event(end=None))
    assert body["start"]["date"] == "2026-09-22"
    assert body["end"]["date"] == "2026-09-23"


@pytest.mark.parametrize(
    "over, marker",
    [
        ({"id": ""}, "нет id"),
        ({"id": None}, "нет id"),
        ({"title": "  "}, "нет title"),
        ({"start": None}, "нет start"),
        ({"tz": "Europe/Марс"}, "tz="),
        ({"end_tz": "Nowhere/Nope"}, "end_tz="),
        ({"start": "21.09.2026 23:55"}, "start должен"),
        ({"start": "2026-09-21T23:55+03:00"}, "start должен"),
        ({"end": "мусор"}, "end должен"),
        ({"remind_minutes": []}, "remind_minutes"),
        ({"remind_minutes": [-5]}, "remind_minutes"),
        ({"remind_minutes": "180"}, "remind_minutes"),
    ],
)
def test_validate_rejects(over, marker):
    ok, error = gcal_sync.validate_event(timed_event(**over))
    assert ok is False
    assert marker in error


def test_validate_rejects_end_before_start():
    ok, error = gcal_sync.validate_event(
        timed_event(start="2026-09-21T23:55", end="2026-09-21T10:00", end_tz=None)
    )
    assert (ok, error) == (False, "end раньше start")

    ok, error = gcal_sync.validate_event(allday_event(start="2026-09-25", end="2026-09-22"))
    assert (ok, error) == (False, "end раньше start")


def test_validate_end_tz_makes_short_flight_valid():
    """Прилёт «раньше» вылета по цифрам, но позже по абсолютному времени."""
    ev = timed_event(start="2026-09-21T23:55", end="2026-09-22T06:40")
    assert gcal_sync.validate_event(ev)[0] is True
    # Тот же интервал без учёта пояса прилёта - тоже валиден (больше суток нет).
    assert gcal_sync.validate_event(timed_event(end_tz=None))[0] is True
    # А вот обратный порядок в одном поясе - уже ошибка.
    back = timed_event(start="2026-09-22T06:40", end="2026-09-21T23:55", end_tz=None)
    assert gcal_sync.validate_event(back) == (False, "end раньше start")


def test_validate_rejects_non_dict():
    assert gcal_sync.validate_event("строка") == (False, "событие не объект")


def test_validate_allday_needs_dates_not_datetimes():
    ok, error = gcal_sync.validate_event(allday_event(start="2026-09-22T10:00"))
    assert ok is False
    assert "YYYY-MM-DD" in error


# ---------------------------------------------------------------------------
# google_event_id
# ---------------------------------------------------------------------------
def test_google_event_id_is_deterministic_and_legal():
    first = gcal_sync.google_event_id("flight-ey844")
    assert first == gcal_sync.google_event_id("flight-ey844")
    assert first != gcal_sync.google_event_id("flight-ey845")
    assert len(first) >= 5
    assert set(first) <= gcal_sync.ALLOWED_ID_CHARS
    assert first.startswith("marco")


def test_google_event_id_handles_unicode_slug():
    value = gcal_sync.google_event_id("отель-убуд")
    assert set(value) <= gcal_sync.ALLOWED_ID_CHARS


# ---------------------------------------------------------------------------
# to_google_body
# ---------------------------------------------------------------------------
def test_body_timed_keeps_two_timezones():
    body = gcal_sync.to_google_body(timed_event(location="SVO, терминал C", description="ABC"))
    assert body["summary"] == "✈️ Вылет EY 844 SVO→AUH"
    assert body["location"] == "SVO, терминал C"
    assert body["description"] == "ABC"
    assert body["start"] == {"dateTime": "2026-09-21T23:55:00", "timeZone": "Europe/Moscow"}
    assert body["end"] == {"dateTime": "2026-09-22T06:40:00", "timeZone": "Asia/Dubai"}
    assert body["reminders"] == {"useDefault": True}


def test_body_defaults_end_tz_to_tz_and_drops_empty_fields():
    body = gcal_sync.to_google_body(timed_event(end_tz=None, location="", description=" "))
    assert body["end"]["timeZone"] == "Europe/Moscow"
    assert "location" not in body
    assert "description" not in body


def test_body_allday_end_is_exclusive():
    body = gcal_sync.to_google_body(allday_event())
    assert body["start"] == {"date": "2026-09-22"}
    assert body["end"] == {"date": "2026-09-26"}  # в файле 25-е включительно


def test_body_reminders_become_popup_overrides():
    body = gcal_sync.to_google_body(timed_event(remind_minutes=[180, 30]))
    assert body["reminders"] == {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": 180},
            {"method": "popup", "minutes": 30},
        ],
    }


def test_event_hash_ignores_cosmetics_but_sees_content():
    base = gcal_sync.event_hash(timed_event())
    assert base == gcal_sync.event_hash(timed_event(title=" ✈️ Вылет EY 844 SVO→AUH "))
    assert base != gcal_sync.event_hash(timed_event(start="2026-09-21T23:56"))
    assert base != gcal_sync.event_hash(timed_event(location="новый терминал"))


# ---------------------------------------------------------------------------
# plan_sync
# ---------------------------------------------------------------------------
def test_plan_new_event():
    plan = gcal_sync.plan_sync([timed_event()], {})
    assert [i["slug"] for i in plan["upsert"]] == ["flight-ey844"]
    assert plan["upsert"][0]["event_id"] == gcal_sync.google_event_id("flight-ey844")
    assert plan["delete"] == []
    assert plan["skipped_invalid"] == []


def test_plan_skips_unchanged_and_sends_changed():
    ev = timed_event()
    synced = {"flight-ey844": gcal_sync.event_hash(ev)}
    assert gcal_sync.plan_sync([ev], synced)["upsert"] == []

    changed = timed_event(start="2026-09-21T22:10")
    plan = gcal_sync.plan_sync([changed], synced)
    assert [i["slug"] for i in plan["upsert"]] == ["flight-ey844"]
    assert plan["delete"] == []


def test_plan_deletes_what_left_the_file():
    synced = {"flight-ey844": "x", "abu-dhabi": "y"}
    plan = gcal_sync.plan_sync([allday_event()], synced)
    assert [i["slug"] for i in plan["delete"]] == ["flight-ey844"]


def test_plan_skips_invalid_but_syncs_the_rest():
    broken = {"id": "hotel", "title": "Отель"}  # нет start
    plan = gcal_sync.plan_sync([broken, allday_event()], {})
    assert [i["slug"] for i in plan["upsert"]] == ["abu-dhabi"]
    assert plan["skipped_invalid"] == [("hotel", "нет start")]


def test_plan_does_not_delete_event_that_became_invalid():
    """Модель сломала запись - в календаре она остаётся до починки."""
    synced = {"flight-ey844": "old"}
    plan = gcal_sync.plan_sync([timed_event(start=None)], synced)
    assert plan["delete"] == []
    assert plan["upsert"] == []
    assert plan["skipped_invalid"][0][0] == "flight-ey844"


def test_plan_catches_duplicate_ids():
    plan = gcal_sync.plan_sync([timed_event(), timed_event(title="дубль")], {})
    assert len(plan["upsert"]) == 1
    assert plan["skipped_invalid"] == [("flight-ey844", "дубликат id")]


def test_plan_on_non_list_file():
    plan = gcal_sync.plan_sync({"id": "x"}, {})
    assert plan["upsert"] == [] and plan["delete"] == []
    assert plan["skipped_invalid"][0][1] == "calendar.json не список"


# ---------------------------------------------------------------------------
# sync с фейковым клиентом
# ---------------------------------------------------------------------------
class FakeClient:
    """Считает вызовы и умеет падать на заданных event_id."""

    def __init__(self, fail_upsert=(), fail_delete=()):
        self.fail_upsert = set(fail_upsert)
        self.fail_delete = set(fail_delete)
        self.upserted = []
        self.deleted = []

    def upsert(self, event_id, body):
        if event_id in self.fail_upsert:
            raise gcal_sync.GCalError("запись события: HTTP 500")
        self.upserted.append((event_id, body))
        return True

    def delete(self, event_id):
        if event_id in self.fail_delete:
            raise gcal_sync.GCalError("удаление события: HTTP 500")
        self.deleted.append(event_id)
        return True


def write_calendar(tmp_path, events):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_sync_writes_events_and_remembers_hashes(tmp_path):
    path = write_calendar(tmp_path, [timed_event(), allday_event()])
    state = {}
    client = FakeClient()
    report = gcal_sync.sync(path, state, client)

    assert report["upserted"] == 2
    assert report["deleted"] == 0
    assert report["errors"] == [] and report["skipped_invalid"] == []
    assert report["events"] == 2
    assert set(state["gcal_synced"]) == {"flight-ey844", "abu-dhabi"}

    # Второй прогон без правок в сеть не ходит.
    again = FakeClient()
    assert gcal_sync.sync(path, state, again)["upserted"] == 0
    assert again.upserted == []


def test_sync_failure_of_one_event_does_not_block_others(tmp_path):
    path = write_calendar(tmp_path, [timed_event(), allday_event()])
    state = {}
    client = FakeClient(fail_upsert=[gcal_sync.google_event_id("flight-ey844")])
    report = gcal_sync.sync(path, state, client)

    assert report["upserted"] == 1
    assert len(report["errors"]) == 1 and "flight-ey844" in report["errors"][0]
    # Сбойное событие не попало в synced - повторится на следующем тике.
    assert "flight-ey844" not in state["gcal_synced"]
    assert "abu-dhabi" in state["gcal_synced"]

    retry = FakeClient()
    assert gcal_sync.sync(path, state, retry)["upserted"] == 1
    assert retry.upserted[0][0] == gcal_sync.google_event_id("flight-ey844")


def test_sync_deletes_removed_event(tmp_path):
    path = write_calendar(tmp_path, [timed_event(), allday_event()])
    state = {}
    gcal_sync.sync(path, state, FakeClient())

    write_calendar(tmp_path, [allday_event()])
    client = FakeClient()
    report = gcal_sync.sync(path, state, client)
    assert report["deleted"] == 1
    assert client.deleted == [gcal_sync.google_event_id("flight-ey844")]
    assert "flight-ey844" not in state["gcal_synced"]


def test_sync_failed_delete_stays_in_state(tmp_path):
    path = write_calendar(tmp_path, [timed_event()])
    state = {}
    gcal_sync.sync(path, state, FakeClient())
    write_calendar(tmp_path, [])
    client = FakeClient(fail_delete=[gcal_sync.google_event_id("flight-ey844")])
    report = gcal_sync.sync(path, state, client)
    assert report["deleted"] == 0
    assert len(report["errors"]) == 1
    assert "flight-ey844" in state["gcal_synced"]


def test_sync_reports_invalid_and_syncs_valid(tmp_path):
    path = write_calendar(tmp_path, [{"id": "hotel", "title": "Отель"}, allday_event()])
    state = {}
    report = gcal_sync.sync(path, state, FakeClient())
    assert report["upserted"] == 1
    assert report["skipped_invalid"] == [("hotel", "нет start")]
    assert report["events"] == 1


def test_sync_missing_file_is_noop(tmp_path):
    state = {"gcal_synced": {"flight-ey844": "h"}}
    client = FakeClient()
    report = gcal_sync.sync(str(tmp_path / "calendar.json"), state, client)
    assert report == gcal_sync.empty_report()
    # Пропавший файл не повод сносить события из календаря.
    assert client.deleted == []
    assert state["gcal_synced"] == {"flight-ey844": "h"}


def test_sync_broken_json_reports_error(tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text("{не json", encoding="utf-8")
    report = gcal_sync.sync(str(path), {}, FakeClient())
    assert len(report["errors"]) == 1
    assert report["upserted"] == 0


# ---------------------------------------------------------------------------
# GCalClient: HTTP-ветки на подменённой сессии (сети нет)
# ---------------------------------------------------------------------------
class FakeSession:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, timeout))
        status = self.statuses.pop(0) if self.statuses else 200
        return SimpleNamespace(status_code=status)


def client_with(statuses, calendar_id="cal@group.calendar.google.com"):
    client = gcal_sync.GCalClient(calendar_id, key_file="/nope/key.json")
    client._session = FakeSession(statuses)
    return client


def test_client_upsert_put_ok():
    client = client_with([200])
    assert client.upsert("marcoabc", {"summary": "x"}) is True
    assert client._session.calls[0][0] == "PUT"
    assert client._session.calls[0][2] == gcal_sync.HTTP_TIMEOUT


def test_client_upsert_falls_back_to_insert_on_404():
    client = client_with([404, 200])
    client.upsert("marcoabc", {"summary": "x"})
    methods = [c[0] for c in client._session.calls]
    assert methods == ["PUT", "POST"]


def test_client_upsert_insert_conflict_retries_put():
    client = client_with([404, 409, 200])
    client.upsert("marcoabc", {"summary": "x"})
    assert [c[0] for c in client._session.calls] == ["PUT", "POST", "PUT"]


def test_client_upsert_raises_on_error():
    client = client_with([500])
    with pytest.raises(gcal_sync.GCalError) as exc:
        client.upsert("marcoabc", {"summary": "x"})
    assert "500" in str(exc.value)


def test_client_delete_treats_404_and_410_as_success():
    assert client_with([404]).delete("marcoabc") is True
    assert client_with([410]).delete("marcoabc") is True
    assert client_with([204]).delete("marcoabc") is True


def test_client_delete_raises_on_server_error():
    with pytest.raises(gcal_sync.GCalError):
        client_with([500]).delete("marcoabc")


def test_client_check_ok_and_not_shared(tmp_path):
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"client_email": "marco@proj.iam.gserviceaccount.com"}), "utf-8")

    client = gcal_sync.GCalClient("cal@group.calendar.google.com", key_file=str(key))
    client._session = FakeSession([200])
    ok, message = client.check()
    assert ok is True and "доступен" in message

    client = gcal_sync.GCalClient("cal@group.calendar.google.com", key_file=str(key))
    client._session = FakeSession([404])
    ok, message = client.check()
    assert ok is False
    assert "не расшарен" in message
    assert "marco@proj.iam.gserviceaccount.com" in message


def test_client_check_without_calendar_id():
    ok, message = gcal_sync.GCalClient("", key_file="/nope").check()
    assert ok is False and "GCAL_CALENDAR_ID" in message


def test_client_session_without_key_file():
    with pytest.raises(gcal_sync.GCalError) as exc:
        gcal_sync.GCalClient("cal", key_file="/nope/key.json").session()
    assert "ключ" in str(exc.value)


def test_read_client_email_missing_file():
    assert gcal_sync.read_client_email("/nope/key.json") is None


# ---------------------------------------------------------------------------
# Интеграция в бота
# ---------------------------------------------------------------------------
@pytest.fixture
def gcal_env(tmp_path, monkeypatch):
    group = tmp_path / "group"
    group.mkdir()
    key = tmp_path / "gcal-key.json"
    key.write_text(json.dumps({"client_email": "marco@proj.iam.gserviceaccount.com"}), "utf-8")
    monkeypatch.setattr(bot, "GROUP_DIR", str(group))
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(bot, "GCAL_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setattr(bot, "GCAL_KEY_FILE", str(key))
    monkeypatch.setattr(bot, "_gcal_client", None)
    return SimpleNamespace(group=group, key=key, tmp=tmp_path)


def test_enabled_requires_id_and_key(gcal_env, monkeypatch):
    assert bot.gcal_enabled() is True
    monkeypatch.setattr(bot, "GCAL_CALENDAR_ID", "")
    assert bot.gcal_enabled() is False
    monkeypatch.setattr(bot, "GCAL_CALENDAR_ID", "cal@group.calendar.google.com")
    monkeypatch.setattr(bot, "GCAL_KEY_FILE", str(gcal_env.tmp / "нет-такого.json"))
    assert bot.gcal_enabled() is False


def test_sync_calendar_disabled_without_key(gcal_env, monkeypatch):
    monkeypatch.setattr(bot, "GCAL_KEY_FILE", str(gcal_env.tmp / "нет-такого.json"))
    client = FakeClient()
    assert run(bot.sync_calendar(client)) is None
    assert client.upserted == []


def test_sync_calendar_writes_state(gcal_env):
    (gcal_env.group / "calendar.json").write_text(
        json.dumps([timed_event(), {"id": "hotel", "title": "Отель"}], ensure_ascii=False),
        encoding="utf-8",
    )
    client = FakeClient()
    report = run(bot.sync_calendar(client))
    assert report["upserted"] == 1
    assert report["skipped_invalid"] == [("hotel", "нет start")]

    state = bot.load_state()
    assert state["gcal_synced"] == {"flight-ey844": gcal_sync.event_hash(timed_event())}
    assert state["gcal_events"] == 1
    assert state["gcal_errors"] == 1
    assert state["gcal_last_sync"]


def test_sync_calendar_swallows_client_explosion(gcal_env):
    (gcal_env.group / "calendar.json").write_text(json.dumps([timed_event()]), encoding="utf-8")

    class Exploding:
        def upsert(self, *a, **kw):
            raise RuntimeError("бум")

        def delete(self, *a, **kw):
            raise RuntimeError("бум")

    report = run(bot.sync_calendar(Exploding()))
    assert report["upserted"] == 0 and len(report["errors"]) == 1


def test_default_state_has_gcal_keys():
    state = bot.default_state()
    assert state["gcal_synced"] == {}
    assert state["gcal_events"] == 0
    assert state["gcal_errors"] == 0
    assert state["gcal_last_sync"] is None


def test_status_text_calendar_off():
    text = bot.status_text({}, [], [], gcal_on=False)
    assert "Календарь: выкл" in text


def test_status_text_calendar_on():
    state = {"gcal_events": 4, "gcal_last_sync": "2026-09-20T09:00:00+00:00", "gcal_errors": 1}
    text = bot.status_text(state, [], [], gcal_on=True)
    assert "Календарь: вкл, событий 4" in text
    assert "2026-09-20T09:00:00+00:00" in text
    assert "ошибок 1" in text
