#!/usr/bin/env python3
"""Тесты группового Марко. Без сети, без claude, без Telegram API.

Проверяется только детерминированная часть: маршрутизация, триггеры, сегменты и
таймзоны, напоминания, закреп, сборка промптов, состояние, ref-копии, yt-dlp.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import marco_group_bot as bot  # noqa: E402
import reel_meta  # noqa: E402

ANTON = 111
RALINA = 222
STRANGER = 999
GROUP = -100500
BOTNAME = "marco_family_bot"


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def classify(**kwargs):
    base = dict(
        chat_type="supergroup",
        chat_id=GROUP,
        user_id=ANTON,
        text=None,
        is_reply_to_bot=False,
        group_chat_id=GROUP,
        bot_username=BOTNAME,
        anton_id=ANTON,
        ralina_id=RALINA,
        passive_enabled=True,
    )
    base.update(kwargs)
    return bot.classify_update(**base)


# ---------------------------------------------------------------------------
# classify_update
# ---------------------------------------------------------------------------
def test_private_chat_dropped():
    assert classify(chat_type="private", chat_id=ANTON)["route"] == bot.ROUTE_DROP


def test_foreign_group_dropped():
    assert classify(chat_id=-4242)["route"] == bot.ROUTE_DROP


def test_setup_mode_answers_ids_in_any_group():
    verdict = classify(chat_id=-4242, group_chat_id=None, has_group_chat_id=False)
    assert verdict["route"] == bot.ROUTE_SETUP


def test_setup_mode_does_not_touch_private():
    verdict = classify(chat_type="private", group_chat_id=None, has_group_chat_id=False)
    assert verdict["route"] == bot.ROUTE_DROP


def test_plain_message_goes_to_passive():
    verdict = classify(text="взяли воду в дорогу")
    assert verdict["route"] == bot.ROUTE_PASSIVE
    assert verdict["triggers"] == []
    assert verdict["author"] == "Антон"


def test_plain_message_is_log_only_when_passive_disabled():
    verdict = classify(text="взяли воду в дорогу", passive_enabled=False)
    assert verdict["route"] == bot.ROUTE_LOG


def test_passive_flag_defaults_to_global(monkeypatch):
    monkeypatch.setattr(bot, "PASSIVE_ENABLED", False)
    assert classify(text="просто болтаем", passive_enabled=None)["route"] == bot.ROUTE_LOG
    monkeypatch.setattr(bot, "PASSIVE_ENABLED", True)
    assert classify(text="просто болтаем", passive_enabled=None)["route"] == bot.ROUTE_PASSIVE


def test_passive_needs_non_empty_text():
    for text in (None, "", "   "):
        assert classify(text=text)["route"] == bot.ROUTE_LOG, repr(text)


def test_passive_only_for_allowed_users():
    assert classify(text="привет всем", user_id=STRANGER)["route"] == bot.ROUTE_LOG


def test_passive_works_for_ralina():
    verdict = classify(text="я бы поела", user_id=RALINA)
    assert verdict["route"] == bot.ROUTE_PASSIVE and verdict["author"] == "Ралина"


def test_mention_triggers_model():
    verdict = classify(text="@%s а что по такси" % BOTNAME, user_id=RALINA)
    assert verdict["route"] == bot.ROUTE_MODEL
    assert "mention" in verdict["triggers"]
    assert verdict["author"] == "Ралина"


def test_mention_is_case_insensitive():
    assert classify(text="эй @MARCO_FAMILY_BOT")["route"] == bot.ROUTE_MODEL


def test_mention_of_other_bot_is_not_trigger():
    assert classify(text="@some_other_bot привет")["route"] == bot.ROUTE_PASSIVE
    assert classify(text="@some_other_bot привет", passive_enabled=False)["route"] == (
        bot.ROUTE_LOG
    )


def test_reply_to_bot_triggers_model():
    verdict = classify(text="а подробнее?", is_reply_to_bot=True)
    assert verdict["route"] == bot.ROUTE_MODEL
    assert "reply" in verdict["triggers"]


def test_name_prefix_triggers_model():
    for text in ("Марко, где ужин", "марко добавляй", '"Марко" глянь'):
        assert classify(text=text)["route"] == bot.ROUTE_MODEL, text


def test_name_in_the_middle_is_not_trigger():
    verdict = classify(text="спросим у Марко потом")
    assert verdict["route"] == bot.ROUTE_PASSIVE and verdict["triggers"] == []
    assert classify(text="спросим у Марко потом", passive_enabled=False)["route"] == (
        bot.ROUTE_LOG
    )


def test_reel_link_triggers_model_without_tag():
    url = "https://www.instagram.com/reel/Cx1_ab-cD/"
    verdict = classify(text="смотри " + url, user_id=RALINA)
    assert verdict["route"] == bot.ROUTE_MODEL
    assert verdict["reel_url"].startswith("https://www.instagram.com/reel/")


def test_stranger_is_logged_but_never_calls_model():
    verdict = classify(text="Марко, посчитай", user_id=STRANGER)
    assert verdict["route"] == bot.ROUTE_LOG
    assert verdict["author"].startswith("гость")
    assert verdict["allowed"] is False
    assert verdict["triggers"] == ["name"]


def test_guest_author_carries_name_and_id():
    verdict = classify(text="привет", user_id=STRANGER, user_name="Вася Пупкин")
    assert verdict["author"] == "гость Вася Пупкин (id 999)"
    assert classify(text="привет", user_id=STRANGER)["author"] == "гость (id 999)"


def test_guest_label_without_id_or_name():
    assert bot.guest_label(None, None) == "гость"
    assert bot.guest_label(None, "  Вася  ") == "гость Вася"
    assert bot.guest_label(7, None) == "гость (id 7)"


def test_allowed_flag_true_for_own_people():
    assert classify(text="привет")["allowed"] is True
    assert classify(text="привет", user_id=RALINA)["allowed"] is True


# ---------------------------------------------------------------------------
# Детект ссылок
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "https://instagram.com/reel/AbC123/",
        "https://www.instagram.com/reels/AbC123",
        "https://www.instagram.com/p/AbC-12_3/?igsh=xx",
        "https://www.instagram.com/some.author/reel/AbC123/",
    ],
)
def test_find_reel_url_positive(text):
    assert bot.find_reel_url(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "https://instagram.com/some.author/",
        "https://tiktok.com/@x/video/123",
        "просто текст",
    ],
)
def test_find_reel_url_negative(text):
    assert bot.find_reel_url(text) is None


def test_find_reel_url_strips_trailing_punctuation():
    assert bot.find_reel_url("вот (https://instagram.com/reel/AbC123)").endswith("AbC123")


# ---------------------------------------------------------------------------
# Сегменты и таймзоны
# ---------------------------------------------------------------------------
BALI = {"from": "2026-09-21", "to": "2026-10-05", "tz": "Asia/Makassar", "base": "Убуд"}
BKK = {"from": "2026-10-06", "to": "2026-10-15", "tz": "Asia/Bangkok", "base": "Бангкок"}
SEGMENTS = [BALI, BKK]


def test_active_segment_inside():
    assert bot.active_segment(SEGMENTS, utc(2026, 9, 25, 3))["base"] == "Убуд"


def test_active_segment_first_day_by_local_date():
    # 20.09 20:00 UTC = 21.09 04:00 на Бали: сегмент уже начался.
    assert bot.active_segment(SEGMENTS, utc(2026, 9, 20, 20))["base"] == "Убуд"


def test_active_segment_last_day_inclusive():
    assert bot.active_segment(SEGMENTS, utc(2026, 10, 5, 10))["base"] == "Убуд"


def test_active_segment_switches_timezone():
    seg = bot.active_segment(SEGMENTS, utc(2026, 10, 7, 6))
    assert seg["tz"] == "Asia/Bangkok"
    assert bot.segment_local_now(seg, utc(2026, 10, 7, 6)).hour == 13


def test_active_segment_none_outside_trip():
    assert bot.active_segment(SEGMENTS, utc(2026, 11, 1)) is None
    assert bot.active_segment([], utc(2026, 9, 25)) is None


def test_valid_segments_drops_broken_entries():
    raw = [BALI, {"from": "нет", "to": "2026-10-05", "tz": "Asia/Makassar"}, {"tz": "X"}, "мусор"]
    assert bot.valid_segments(raw) == [BALI]


def test_load_segments_falls_back_to_state_on_broken_json(tmp_path, monkeypatch):
    gdir = tmp_path / "group"
    gdir.mkdir()
    (gdir / "segments.json").write_text("{битый", encoding="utf-8")
    monkeypatch.setattr(bot, "GROUP_DIR", str(gdir))
    segments, fallback = bot.load_segments(state={"last_segments": SEGMENTS})
    assert fallback is True
    assert segments == SEGMENTS


def test_load_segments_reads_valid_file(tmp_path, monkeypatch):
    gdir = tmp_path / "group"
    gdir.mkdir()
    (gdir / "segments.json").write_text(json.dumps(SEGMENTS), encoding="utf-8")
    monkeypatch.setattr(bot, "GROUP_DIR", str(gdir))
    segments, fallback = bot.load_segments(state={})
    assert fallback is False and len(segments) == 2


def test_tz_of_unknown_timezone_is_utc():
    assert bot.tz_of({"tz": "Mars/Olympus"}) is timezone.utc


def test_tz_of_without_segment_is_home_tz():
    # Вне поездки - домашний пояс (Москва), не UTC.
    assert str(bot.tz_of(None)) == bot.HOME_TZ == "Europe/Moscow"


def test_time_block_has_weekday_and_home_time():
    block = bot.format_time_block(dt=utc(2026, 9, 18, 9, 5), segment=None)
    assert "2026-09-18 12:05" in block and "пятница" in block
    seg = {"from": "2026-09-23", "to": "2026-09-25", "tz": "Asia/Makassar", "base": "Убуд"}
    block = bot.format_time_block(dt=utc(2026, 9, 23, 17, 30), segment=seg)
    assert "2026-09-24 01:30" in block and "четверг" in block


# ---------------------------------------------------------------------------
# Утреннее окно
# ---------------------------------------------------------------------------
def test_is_morning_window():
    local = bot.segment_local_now(BALI, utc(2026, 9, 25, 2, 5))  # 10:05 на Бали
    assert local.hour == 10 and bot.is_morning_window(local)


@pytest.mark.parametrize("hh,mm", [(2, 20), (1, 55), (14, 0)])
def test_is_morning_window_negative(hh, mm):
    assert not bot.is_morning_window(bot.segment_local_now(BALI, utc(2026, 9, 25, hh, mm)))


def test_is_morning_window_none():
    assert bot.is_morning_window(None) is False


# ---------------------------------------------------------------------------
# Напоминания
# ---------------------------------------------------------------------------
REMINDERS = [
    {"id": "tdac", "at_utc": "2026-10-03T02:00:00+00:00", "text": "подать TDAC", "sent": False},
    {"id": "evoa", "at_utc": "2026-09-19T06:00:00+00:00", "text": "e-VoA", "sent": True},
    {"id": "flight", "at_utc": "2026-12-01T00:00:00+00:00", "text": "рейс", "sent": False},
    {"id": "broken", "at_utc": "завтра", "text": "мусор", "sent": False},
    {"id": "empty", "at_utc": "2026-01-01T00:00:00+00:00", "text": "  ", "sent": False},
]


def test_due_reminders_picks_only_overdue_unsent():
    due = bot.due_reminders(REMINDERS, utc(2026, 10, 3, 5))
    assert [r["id"] for r in due] == ["tdac"]


def test_due_reminders_empty_before_time():
    assert bot.due_reminders(REMINDERS, utc(2026, 9, 25)) == []


def test_due_reminders_handles_garbage_input():
    assert bot.due_reminders(None) == [] and bot.due_reminders("x") == []


def test_mark_reminders_sent_does_not_mutate_source():
    updated = bot.mark_reminders_sent(REMINDERS, ["tdac"])
    assert updated[0]["sent"] is True
    assert REMINDERS[0]["sent"] is False
    assert updated[2]["sent"] is False


def test_save_reminders_is_atomic_and_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "GROUP_DIR", str(tmp_path))
    bot.save_reminders(bot.mark_reminders_sent(REMINDERS, ["tdac"]))
    data = json.loads((tmp_path / "reminders.json").read_text(encoding="utf-8"))
    assert data[0]["sent"] is True
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".tmp-")]


def test_due_or_future_counts_unsent():
    assert len(bot.due_or_future(REMINDERS)) == 4


# ---------------------------------------------------------------------------
# Закреп
# ---------------------------------------------------------------------------
def test_pin_needs_update_by_hash():
    text = "# План\n- 21.09 вылет"
    assert bot.pin_needs_update(text, None) is True
    assert bot.pin_needs_update(text, bot.pin_hash(text)) is False
    assert bot.pin_needs_update(text + " правка", bot.pin_hash(text)) is True


def test_pin_needs_update_ignores_empty_pin():
    assert bot.pin_needs_update("   ", None) is False
    assert bot.pin_needs_update(None, "abc") is False


def test_pin_payload_truncates_long_text():
    body, truncated = bot.pin_payload("x" * 5000, limit=4000)
    assert truncated is True and len(body) <= 4000 and body.endswith("(обрезано)")


def test_pin_payload_keeps_short_text():
    body, truncated = bot.pin_payload("  короткий план  ")
    assert body == "короткий план" and truncated is False


def test_read_pin_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "GROUP_DIR", str(tmp_path))
    assert bot.read_pin() is None
    (tmp_path / "pin.md").write_text("план", encoding="utf-8")
    assert bot.read_pin() == "план"


# ---------------------------------------------------------------------------
# Промпты
# ---------------------------------------------------------------------------
def test_build_turn_prompt_contains_warning_author_and_time():
    prompt = bot.build_turn_prompt(
        "Ралина", "где завтракаем", dt=utc(2026, 9, 25, 2), segment=BALI,
        chatlog_tail=["- 09:50 Антон: доброе"], group_dir="/g",
    )
    assert bot.UNTRUSTED_WARNING in prompt
    assert "Автор сообщения: Ралина" in prompt
    assert "Asia/Makassar" in prompt and "2026-09-25 10:00" in prompt
    assert "- 09:50 Антон: доброе" in prompt
    assert "где завтракаем" in prompt
    assert "/g" in prompt


def test_build_turn_prompt_without_segment_says_no_trip():
    prompt = bot.build_turn_prompt("Антон", "привет", dt=utc(2026, 11, 1), segment=None)
    assert "Активного отрезка поездки сейчас нет." in prompt


def test_build_turn_prompt_with_reel_meta():
    meta = {"uploader": "warungx", "description": "лучший завтрак в Убуде", "location": "Ubud"}
    prompt = bot.build_turn_prompt(
        "Ралина", "смотри", reel_url="https://instagram.com/reel/AbC123", meta=meta
    )
    assert "## Рилс: https://instagram.com/reel/AbC123" in prompt
    assert "лучший завтрак в Убуде" in prompt
    assert reel_meta.NO_META_HINT not in prompt


def test_build_turn_prompt_with_reel_without_meta_asks_for_name():
    prompt = bot.build_turn_prompt(
        "Ралина", "смотри", reel_url="https://instagram.com/reel/AbC123", meta=None
    )
    assert reel_meta.NO_META_HINT in prompt


def test_build_morning_prompt():
    prompt = bot.build_morning_prompt(dt=utc(2026, 9, 25, 2), segment=BALI, group_dir="/g")
    assert bot.UNTRUSTED_WARNING in prompt
    assert "/g/plan.md" in prompt
    assert "800" in prompt


def test_status_text_with_and_without_segment():
    text = bot.status_text({"last_morning_date": "2026-09-25"}, SEGMENTS, REMINDERS,
                           utc(2026, 9, 25, 2))
    assert "Убуд" in text and "2026-09-25" in text and "4" in text
    assert "Активного отрезка поездки нет" in bot.status_text({}, SEGMENTS, [], utc(2026, 11, 1))


def test_status_text_reports_passive_mode():
    on = bot.status_text({}, SEGMENTS, [], utc(2026, 11, 1),
                         passive_enabled=True, passive_debounce=25, passive_pending=3)
    assert "Пассивный режим: вкл, дебаунс 25 с, в очереди 3" in on
    off = bot.status_text({}, SEGMENTS, [], utc(2026, 11, 1),
                          passive_enabled=False, passive_debounce=10, passive_pending=0)
    assert "Пассивный режим: выкл, дебаунс 10 с, в очереди 0" in off


# ---------------------------------------------------------------------------
# Пассивный режим: пачка, промпт, разбор ответа
# ---------------------------------------------------------------------------
def test_passive_batch_accumulates_and_takes_once():
    batch = bot.PassiveBatch()
    batch.add("Антон", "хочу кофе", 1)
    batch.add("Ралина", "  я   тоже  ", 2)
    assert len(batch) == 2 and batch.last_message_id == 2
    entries = batch.take()
    assert [e["author"] for e in entries] == ["Антон", "Ралина"]
    assert entries[1]["text"] == "я тоже"  # пробелы схлопнуты
    assert len(batch) == 0 and batch.take() == []


def test_passive_batch_skips_empty_text():
    batch = bot.PassiveBatch()
    assert batch.add("Антон", "   ", 1) is None
    assert batch.add("Антон", None, 2) is None
    assert len(batch) == 0


def test_passive_batch_clear_on_direct_trigger():
    batch = bot.PassiveBatch()
    batch.add("Антон", "раз", 1)
    batch.add("Антон", "два", 2)
    assert batch.clear() == 2
    assert len(batch) == 0 and batch.last_message_id is None


def test_passive_batch_last_message_id_ignores_none():
    batch = bot.PassiveBatch()
    batch.add("Антон", "раз", 7)
    batch.add("Ралина", "два", None)
    assert batch.last_message_id == 7


@pytest.mark.parametrize(
    "reply",
    ["NOTHING", "nothing", " NOTHING. ", "«NOTHING»", "```\nNOTHING\n```",
     "```text\nnothing\n```", "", "   ", None],
)
def test_interpret_passive_reply_silence(reply):
    assert bot.interpret_passive_reply(reply) is None


def test_interpret_passive_reply_returns_text():
    assert bot.interpret_passive_reply("  Кафе рядом работает до 22:00  ") == (
        "Кафе рядом работает до 22:00"
    )


def test_interpret_passive_reply_keeps_nothing_inside_phrase():
    text = "there is nothing to add, но такси лучше вызвать заранее"
    assert bot.interpret_passive_reply(text) == text


def test_build_passive_prompt_contains_batch_and_rules():
    entries = [
        {"author": "Антон", "text": "может в Убуд завтра"},
        {"author": "Ралина", "text": "а во сколько выезжаем"},
    ]
    prompt = bot.build_passive_prompt(
        entries, dt=utc(2026, 9, 25, 2), segment=BALI,
        chatlog_tail=["- 09:50 Антон: доброе"], group_dir="/g",
    )
    assert bot.UNTRUSTED_WARNING in prompt
    assert "Новые сообщения в чате (тебя напрямую не звали)" in prompt
    assert "- Антон: может в Убуд завтра" in prompt
    assert "- Ралина: а во сколько выезжаем" in prompt
    assert "NOTHING" in prompt
    assert "Asia/Makassar" in prompt and "2026-09-25 10:00" in prompt
    assert "- 09:50 Антон: доброе" in prompt
    assert "/g" in prompt


def test_build_passive_prompt_does_not_inline_behaviour_rules():
    prompt = bot.build_passive_prompt([{"author": "Антон", "text": "ок"}])
    assert "CLAUDE.md" in prompt  # правила живут в файле, а не в промпте
    assert "Активного отрезка поездки сейчас нет." in prompt


def test_build_passive_prompt_with_empty_batch():
    assert "(пусто)" in bot.build_passive_prompt([])


class FakeJob:
    def __init__(self, name):
        self.name = name
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    """Мини-заглушка JobQueue: снятие по имени и run_once без telegram."""

    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, name):
        return [j for j in self.jobs if j.name == name and not j.removed]

    def run_once(self, callback, when, name=None):
        job = FakeJob(name)
        self.jobs.append(job)
        self.last = (callback, when, name)
        return job


def test_schedule_passive_job_replaces_previous(monkeypatch):
    monkeypatch.setattr(bot, "PASSIVE_DEBOUNCE_SECONDS", 25)
    jq = FakeJobQueue()
    first = bot.schedule_passive_job(jq)
    second = bot.schedule_passive_job(jq)
    assert first.removed is True and second.removed is False
    assert jq.last[1] == 25 and jq.last[2] == bot.PASSIVE_JOB_NAME
    assert len(jq.get_jobs_by_name(bot.PASSIVE_JOB_NAME)) == 1


def test_schedule_passive_job_without_queue_is_safe():
    assert bot.schedule_passive_job(None) is None
    assert bot.cancel_passive_job(None) == 0


def test_drop_passive_batch_clears_batch_and_job():
    jq = FakeJobQueue()
    bot.passive_batch.clear()
    bot.passive_batch.add("Антон", "раз", 1)
    job = bot.schedule_passive_job(jq)
    assert bot.drop_passive_batch(jq) == 1
    assert len(bot.passive_batch) == 0 and job.removed is True


# ---------------------------------------------------------------------------
# Чатлог
# ---------------------------------------------------------------------------
def test_format_chatlog_line_with_attachment():
    line = bot.format_chatlog_line("Ралина", "вот тут", ["photo"], utc(2026, 9, 25, 2), BALI)
    assert line == "- 10:00 Ралина: [вложение: фото] вот тут"


def test_format_chatlog_line_empty_message_is_skipped():
    assert bot.format_chatlog_line("Антон", "", None, utc(2026, 9, 25)) == ""


def test_append_and_read_chatlog_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "GROUP_DIR", str(tmp_path))
    dt = utc(2026, 9, 25, 2)
    for i in range(45):
        bot.append_chatlog(
            bot.format_chatlog_line("Антон", "строка %d" % i, None, dt, BALI),
            day=bot.chatlog_day(dt, BALI), segment=BALI,
        )
    head = (tmp_path / "chat-log" / "2026-09-25.md").read_text(encoding="utf-8")
    assert head.startswith("# Чат поездки 2026-09-25")
    assert "### [[2026-09-25]]" in head
    tail = bot.read_chatlog_tail(40, segment=BALI, dt=dt)
    assert len(tail) == 40 and tail[-1].endswith("строка 44")


def test_read_chatlog_tail_without_files(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "GROUP_DIR", str(tmp_path))
    assert bot.read_chatlog_tail(segment=BALI) == []


# ---------------------------------------------------------------------------
# Состояние
# ---------------------------------------------------------------------------
def test_save_state_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    state = bot.default_state()
    state["pin_message_id"] = 42
    bot.save_state(state)
    assert bot.load_state()["pin_message_id"] == 42
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".tmp-")]


def test_load_state_defaults_on_broken_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("не json", encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "STATE_FILE", str(path))
    assert bot.load_state() == bot.default_state()


def test_load_state_fills_missing_keys(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"pin_hash": "abc"}), encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE", str(path))
    state = bot.load_state()
    assert state["pin_hash"] == "abc" and state["last_morning_date"] is None


# ---------------------------------------------------------------------------
# refresh_ref
# ---------------------------------------------------------------------------
def test_refresh_ref_copies_dirs_and_files(tmp_path):
    memory = tmp_path / "memory"
    (memory / "sub").mkdir(parents=True)
    (memory / "sub" / "a.md").write_text("память", encoding="utf-8")
    single = tmp_path / "plan.md"
    single.write_text("план", encoding="utf-8")
    ref = tmp_path / "ws" / "ref"

    copied = bot.refresh_ref([str(memory), str(single), str(tmp_path / "нет")], ref_dir=str(ref))
    assert len(copied) == 2
    assert (ref / "memory" / "sub" / "a.md").read_text(encoding="utf-8") == "память"
    assert (ref / "plan.md").read_text(encoding="utf-8") == "план"


def test_refresh_ref_overwrites_previous_copies(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "a.md").write_text("v1", encoding="utf-8")
    ref = tmp_path / "ref"
    bot.refresh_ref([str(memory)], ref_dir=str(ref))
    (ref / "memory" / "a.md").write_text("правка модели", encoding="utf-8")
    (ref / "memory" / "мусор.md").write_text("x", encoding="utf-8")
    (memory / "a.md").write_text("v2", encoding="utf-8")

    bot.refresh_ref([str(memory)], ref_dir=str(ref))
    assert (ref / "memory" / "a.md").read_text(encoding="utf-8") == "v2"
    assert not (ref / "memory" / "мусор.md").exists()
    assert (memory / "a.md").read_text(encoding="utf-8") == "v2"  # оригинал не тронут


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------
def test_split_message_short_text():
    assert bot.split_message("привет") == ["привет"]


def test_split_message_empty():
    assert bot.split_message("") == [] and bot.split_message(None) == []


def test_split_message_respects_limit():
    text = "\n\n".join("абзац %d" % i + "я" * 100 for i in range(50))
    chunks = bot.split_message(text, limit=500)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)


def test_split_message_splits_hard_when_no_breaks():
    chunks = bot.split_message("х" * 9000, limit=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]


# ---------------------------------------------------------------------------
# Команда доступа
# ---------------------------------------------------------------------------
def test_command_allowed(monkeypatch):
    monkeypatch.setattr(bot, "GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(bot, "USER_ANTON_ID", ANTON)
    monkeypatch.setattr(bot, "USER_RALINA_ID", RALINA)
    assert bot.command_allowed("supergroup", GROUP, RALINA) is True
    assert bot.command_allowed("supergroup", GROUP, STRANGER) is False
    assert bot.command_allowed("private", ANTON, ANTON) is False
    assert bot.command_allowed("supergroup", -4242, ANTON) is False


# ---------------------------------------------------------------------------
# reel_meta: subprocess замокан, сети нет
# ---------------------------------------------------------------------------
class FakeProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


DUMP = json.dumps(
    {
        "description": "завтрак в Убуде",
        "uploader": "warungx",
        "title": "Reel",
        "location": "Ubud",
        "thumbnail": "https://x/y.jpg",
        "duration": 31,
        "webpage_url": "https://instagram.com/reel/AbC123",
        "formats": ["мусор, который не нужен"],
    }
).encode("utf-8")


@pytest.fixture
def ytdlp_present(monkeypatch):
    monkeypatch.setattr(reel_meta.shutil, "which", lambda _b: "/usr/bin/yt-dlp")


def test_reel_fetch_success(monkeypatch, ytdlp_present):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["timeout"] = kwargs.get("timeout")
        return FakeProc(0, DUMP)

    monkeypatch.setattr(reel_meta.subprocess, "run", fake_run)
    meta = reel_meta.fetch("https://instagram.com/reel/AbC123")
    assert meta["description"] == "завтрак в Убуде"
    assert meta["uploader"] == "warungx" and meta["duration"] == 31
    assert "formats" not in meta
    assert "--skip-download" in calls["cmd"] and "--dump-json" in calls["cmd"]
    assert not [c for c in calls["cmd"] if "cookie" in str(c).lower()]
    assert calls["timeout"] == reel_meta.FETCH_TIMEOUT


def test_reel_fetch_nonzero_exit(monkeypatch, ytdlp_present):
    monkeypatch.setattr(
        reel_meta.subprocess, "run", lambda cmd, **kw: FakeProc(1, b"", b"login required")
    )
    assert reel_meta.fetch("https://instagram.com/reel/AbC123") is None


def test_reel_fetch_timeout(monkeypatch, ytdlp_present):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 45)

    monkeypatch.setattr(reel_meta.subprocess, "run", boom)
    assert reel_meta.fetch("https://instagram.com/reel/AbC123") is None


def test_reel_fetch_broken_json(monkeypatch, ytdlp_present):
    monkeypatch.setattr(reel_meta.subprocess, "run", lambda cmd, **kw: FakeProc(0, b"<html>"))
    assert reel_meta.fetch("https://instagram.com/reel/AbC123") is None


def test_reel_fetch_os_error(monkeypatch, ytdlp_present):
    def boom(cmd, **kwargs):
        raise OSError("нет такого файла")

    monkeypatch.setattr(reel_meta.subprocess, "run", boom)
    assert reel_meta.fetch("https://instagram.com/reel/AbC123") is None


def test_reel_fetch_without_binary(monkeypatch):
    monkeypatch.setattr(reel_meta.shutil, "which", lambda _b: None)
    monkeypatch.setattr(reel_meta.os.path, "exists", lambda _p: False)
    assert reel_meta.fetch("https://instagram.com/reel/AbC123") is None


def test_reel_fetch_empty_url():
    assert reel_meta.fetch("") is None


def test_reel_parse_dump_takes_first_line():
    raw = DUMP.decode("utf-8") + "\n" + json.dumps({"title": "второй"})
    assert reel_meta.parse_dump(raw)["uploader"] == "warungx"


def test_reel_prompt_block_marks_untrusted():
    block = reel_meta.prompt_block("https://x", {"description": "текст"})
    assert "недоверенный" in block


# ---------------------------------------------------------------------------
# Команда claude
# ---------------------------------------------------------------------------
def test_build_claude_cmd_has_no_resume_and_no_skip_permissions():
    cmd = bot.build_claude_cmd("промпт", group_dir="/g")
    assert "--resume" not in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--add-dir") + 1] == "/g"
    assert "Bash" not in cmd[cmd.index("--allowedTools") + 1]
    assert "WebSearch" in cmd[cmd.index("--allowedTools") + 1]


# ---------------------------------------------------------------------------
# Атрибуция пересылок (PTB 22: message.forward_origin)
# ---------------------------------------------------------------------------
def msg(**kwargs):
    """Заглушка сообщения: только те поля, которые ставит Telegram."""
    return SimpleNamespace(**kwargs)


def test_forward_source_origin_user():
    origin = msg(sender_user=msg(full_name="Леша Языков"))
    assert bot.forward_source(msg(forward_origin=origin)) == "Леша Языков"


def test_forward_source_origin_user_without_full_name():
    origin = msg(sender_user=msg(first_name="Леша", last_name="Языков"))
    assert bot.forward_source(msg(forward_origin=origin)) == "Леша Языков"


def test_forward_source_origin_hidden_user():
    origin = msg(sender_user_name="Скрытый друг")
    assert bot.forward_source(msg(forward_origin=origin)) == "Скрытый друг"


def test_forward_source_origin_chat():
    origin = msg(sender_chat=msg(title="Чат про Бали"))
    assert bot.forward_source(msg(forward_origin=origin)) == "Чат про Бали"


def test_forward_source_origin_channel():
    origin = msg(chat=msg(title="Канал про Бали"))
    assert bot.forward_source(msg(forward_origin=origin)) == "Канал про Бали"


def test_forward_source_none_when_not_forwarded():
    assert bot.forward_source(msg(text="обычное сообщение")) is None
    assert bot.forward_source(msg(forward_origin=None)) is None
    assert bot.forward_source(None) is None


def test_forward_source_unknown_when_origin_is_empty():
    # Незнакомый/новый тип origin: атрибуцию не теряем, но имени нет.
    assert bot.forward_source(msg(forward_origin=msg())) == bot.FORWARD_UNKNOWN


def test_forward_source_legacy_fields():
    assert bot.forward_source(msg(forward_from=msg(full_name="Старый API"))) == "Старый API"
    assert bot.forward_source(msg(forward_sender_name="Скрытый")) == "Скрытый"
    assert bot.forward_source(msg(forward_date=1)) == bot.FORWARD_UNKNOWN


def test_format_author_variants():
    assert bot.format_author("Антон", None) == "Антон"
    assert bot.format_author("Антон", "Леша Языков") == "Антон (переслал от «Леша Языков»)"
    assert bot.format_author("Антон", bot.FORWARD_UNKNOWN) == "Антон (переслал)"


def test_format_chatlog_line_marks_forward():
    line = bot.format_chatlog_line(
        "Антон", "там дешёвые байки", None, utc(2026, 9, 25, 2), BALI,
        forward_from="Леша Языков",
    )
    assert line == "- 10:00 Антон (переслал от «Леша Языков»): там дешёвые байки"


def test_format_chatlog_line_without_forward_is_unchanged():
    line = bot.format_chatlog_line("Антон", "привет", None, utc(2026, 9, 25, 2), BALI)
    assert line == "- 10:00 Антон: привет"


# ---------------------------------------------------------------------------
# build_record / format_record_line
# ---------------------------------------------------------------------------
def test_build_record_normalizes_and_finds_reel():
    rec = bot.build_record("Антон", "  смотри  https://instagram.com/reel/AbC123 ", 5)
    assert rec["text"].startswith("смотри https://")
    assert rec["reel_url"].endswith("AbC123")
    assert rec["message_id"] == 5 and rec["forward_from"] is None


def test_build_record_keeps_attachment_only_message():
    rec = bot.build_record("Ралина", "", 7, None, ["photo"])
    assert rec is not None and rec["attachments"] == ["photo"]
    assert bot.build_record("Ралина", "   ", 7, None, None) is None


def test_format_record_line_with_forward_and_attachment():
    rec = bot.build_record("Антон", "вот тут", 1, "Леша Языков", ["photo"])
    assert bot.format_record_line(rec) == (
        "- Антон (переслал от «Леша Языков»): [вложение: фото] вот тут"
    )


# ---------------------------------------------------------------------------
# Пассивная пачка: пересылки и ссылки без текста автора
# ---------------------------------------------------------------------------
def test_passive_batch_keeps_forward_attribution():
    batch = bot.PassiveBatch()
    batch.add("Антон", "в Убуде есть вулкан", 1, None, "Леша Языков")
    entry = batch.entries[0]
    assert entry["forward_from"] == "Леша Языков"
    prompt = bot.build_passive_prompt(batch.take())
    assert "- Антон (переслал от «Леша Языков»): в Убуде есть вулкан" in prompt


def test_forward_only_message_still_goes_to_passive_batch():
    """Задача 3: пересылка без собственного текста автора - штатная пачка."""
    verdict = classify(text="друг пишет: в Убуде дожди")
    assert verdict["route"] == bot.ROUTE_PASSIVE
    batch = bot.PassiveBatch()
    record = bot.build_record(
        verdict["author"], "друг пишет: в Убуде дожди", 11, "Леша Языков"
    )
    assert batch.add_record(record) is not None and len(batch) == 1


def test_link_only_message_goes_to_passive_batch():
    verdict = classify(text="https://maps.app.goo.gl/xyz")
    assert verdict["route"] == bot.ROUTE_PASSIVE
    batch = bot.PassiveBatch()
    assert batch.add_record(bot.build_record("Антон", "https://maps.app.goo.gl/xyz", 3))
    assert len(batch) == 1


def test_passive_batch_add_record_skips_empty():
    batch = bot.PassiveBatch()
    assert batch.add_record(None) is None
    assert batch.add_record(bot.build_record("Антон", "", 1, None, ["photo"])) is None
    assert len(batch) == 0


# ---------------------------------------------------------------------------
# Grace-окно прямого триггера
# ---------------------------------------------------------------------------
T0 = utc(2026, 9, 25, 2)


def rec(author, text, mid=None, forward_from=None, attachments=None):
    return bot.build_record(author, text, mid, forward_from, attachments)


def test_direct_window_single_trigger_fires_after_grace():
    w = bot.DirectWindow(grace=4, max_wait=15)
    assert w.start(rec("Антон", "Марко, где ужин", 1), T0) is True
    assert w.is_open() and len(w) == 1
    assert w.seconds_left(T0) == 4
    assert w.due(T0) is False
    assert w.due(T0 + timedelta(seconds=3.9)) is False
    assert w.due(T0 + timedelta(seconds=4)) is True


def test_direct_window_tail_extends_deadline():
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко вот ещё инфа от друга", 1), T0)
    w.add(rec("Антон", "в Убуде дожди", 2, "Леша Языков"), T0 + timedelta(seconds=2))
    # Дедлайн уехал на «последнее + 4», а не остался на «первое + 4».
    assert w.due(T0 + timedelta(seconds=4)) is False
    assert w.seconds_left(T0 + timedelta(seconds=2)) == 4
    assert w.due(T0 + timedelta(seconds=6)) is True


def test_direct_window_respects_max_cap():
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко смотри", 1), T0)
    for i in range(20):  # хвост каждую секунду: без потолка окно бы не закрылось
        w.add(rec("Антон", "сообщение %d" % i, 10 + i), T0 + timedelta(seconds=i + 1))
    assert w.seconds_left(T0 + timedelta(seconds=14)) == 1
    assert w.due(T0 + timedelta(seconds=15)) is True


def test_direct_window_collects_tail_and_takes_once():
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко вот ещё инфа от друга", 1), T0)
    w.add(rec("Антон", "в Убуде дожди", 2, "Леша Языков"), T0 + timedelta(seconds=1))
    w.add(rec("Антон", "", 3, None, ["photo"]), T0 + timedelta(seconds=2))
    assert len(w) == 3
    payload = w.take()
    assert payload["trigger"]["message_id"] == 1
    assert [r["message_id"] for r in payload["tail"]] == [2, 3]
    assert w.is_open() is False and len(w) == 0
    assert w.take() is None


def test_direct_window_second_trigger_goes_to_tail():
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко глянь", 1), T0)
    assert w.start(rec("Ралина", "Марко и ещё вопрос", 2), T0 + timedelta(seconds=1)) is True
    payload = w.take()
    assert payload["trigger"]["message_id"] == 1
    assert [r["message_id"] for r in payload["tail"]] == [2]


def test_direct_window_add_to_closed_window_is_noop():
    w = bot.DirectWindow(grace=4, max_wait=15)
    assert w.add(rec("Антон", "хвост", 1), T0) is False
    assert w.start(None, T0) is False
    assert w.fire_at() is None and w.seconds_left(T0) == 0.0 and w.due(T0) is False


def test_direct_window_clear():
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко", 1), T0)
    w.add(rec("Антон", "хвост", 2), T0)
    assert w.clear() == 2 and w.is_open() is False


def test_direct_window_fractional_grace():
    w = bot.DirectWindow(grace=0.5, max_wait=15)
    w.start(rec("Антон", "Марко", 1), T0)
    assert w.seconds_left(T0) == 0.5
    assert w.due(T0 + timedelta(seconds=0.5)) is True


def test_collect_reel_url_takes_first_in_window():
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко смотри", 1), T0)
    w.add(rec("Антон", "https://instagram.com/reel/FIRST1", 2), T0)
    w.add(rec("Антон", "https://instagram.com/reel/SECOND", 3), T0)
    assert bot.collect_reel_url(w.take()).endswith("FIRST1")
    assert bot.collect_reel_url(None) is None


def test_window_messages_do_not_land_in_passive_batch():
    """Сообщения окна не идут в пачку и не взводят пассивный таймер."""
    jq = FakeJobQueue()
    batch = bot.PassiveBatch()
    w = bot.DirectWindow(grace=4, max_wait=15)
    w.start(rec("Антон", "Марко вот инфа", 1), T0)
    # Хвост: в окно, мимо пачки и мимо passive-job.
    for i, text in enumerate(["в Убуде дожди", "и байк дорогой"], start=2):
        assert w.add(rec("Антон", text, i, "Леша Языков"), T0) is True
    assert len(batch) == 0
    assert jq.get_jobs_by_name(bot.PASSIVE_JOB_NAME) == []
    assert len(w) == 3


def test_schedule_direct_job_replaces_previous():
    jq = FakeJobQueue()
    bot.direct_window.clear()
    bot.direct_window.start(rec("Антон", "Марко", 1))
    first = bot.schedule_direct_job(jq, 4)
    second = bot.schedule_direct_job(jq, 2)
    assert first.removed is True and second.removed is False
    assert jq.last[1] == 2 and jq.last[2] == bot.DIRECT_JOB_NAME
    assert len(jq.get_jobs_by_name(bot.DIRECT_JOB_NAME)) == 1
    bot.direct_window.clear()


def test_schedule_direct_job_without_queue_is_safe():
    assert bot.schedule_direct_job(None) is None
    assert bot.cancel_direct_job(None) == 0


# ---------------------------------------------------------------------------
# Промпт прямого прогона: блок «Следом пришли сообщения»
# ---------------------------------------------------------------------------
def test_build_turn_prompt_with_followups():
    followups = [
        bot.build_record("Антон", "в Убуде дожди весь октябрь", 2, "Леша Языков"),
        bot.build_record("Антон", "", 3, "Леша Языков", ["photo"]),
    ]
    prompt = bot.build_turn_prompt(
        "Антон", "Марко вот ещё инфа от друга", dt=utc(2026, 9, 25, 2), segment=BALI,
        followups=followups,
    )
    assert "Следом пришли сообщения:" in prompt
    assert "- Антон (переслал от «Леша Языков»): в Убуде дожди весь октябрь" in prompt
    assert "- Антон (переслал от «Леша Языков»): [вложение: фото]" in prompt
    assert "Сообщение:\nМарко вот ещё инфа от друга" in prompt


def test_build_turn_prompt_without_followups_has_no_block():
    prompt = bot.build_turn_prompt("Антон", "Марко привет")
    assert "Следом пришли сообщения" not in prompt


def test_both_prompts_explain_third_party_forwards():
    turn = bot.build_turn_prompt("Антон", "Марко привет")
    passive = bot.build_passive_prompt([{"author": "Антон", "text": "ок"}])
    assert bot.FORWARD_NOTE in turn
    assert bot.FORWARD_NOTE in passive
    assert "не Антона и не Ралины" in bot.FORWARD_NOTE


def test_env_float_parsing(monkeypatch):
    monkeypatch.setenv("MARCO_DIRECT_GRACE", "2.5")
    assert bot._env_float("MARCO_DIRECT_GRACE", 4) == 2.5
    monkeypatch.setenv("MARCO_DIRECT_GRACE", "мусор")
    assert bot._env_float("MARCO_DIRECT_GRACE", 4) == 4.0
    monkeypatch.setenv("MARCO_DIRECT_GRACE", "-3")
    assert bot._env_float("MARCO_DIRECT_GRACE", 4) == 4.0


# ---------------------------------------------------------------------------
# Хендлер целиком: сценарий из прода «подпись + пачка пересылок следом»
# ---------------------------------------------------------------------------
class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))

    async def send_chat_action(self, **kwargs):
        return True


class FakeContext:
    def __init__(self, job_queue):
        self.bot = FakeBot()
        self.job_queue = job_queue


def incoming(text, mid, user_id=ANTON, forward_origin=None, photo=None):
    user = SimpleNamespace(id=user_id, full_name="Антон Розенблюм")
    message = SimpleNamespace(
        message_id=mid,
        text=text,
        caption=None,
        from_user=user,
        reply_to_message=None,
        forward_origin=forward_origin,
        photo=photo,
    )
    chat = SimpleNamespace(id=GROUP, type="supergroup")
    return SimpleNamespace(effective_message=message, effective_chat=chat)


@pytest.fixture
def handler_env(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "GROUP_DIR", str(tmp_path / "group"))
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state" / "state.json"))
    monkeypatch.setattr(bot, "GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(bot, "USER_ANTON_ID", ANTON)
    monkeypatch.setattr(bot, "USER_RALINA_ID", RALINA)
    monkeypatch.setattr(bot, "BOT_USERNAME", BOTNAME)
    monkeypatch.setattr(bot, "start_direct_typing", lambda _bot: "typing")
    monkeypatch.setattr(bot, "stop_direct_typing", lambda: True)
    bot.direct_window.clear()
    bot.passive_batch.clear()
    yield
    bot.direct_window.clear()
    bot.passive_batch.clear()


def run(coro):
    import asyncio as _asyncio

    return _asyncio.run(coro)


def test_handler_trigger_opens_window_and_swallows_forwards(handler_env):
    jq = FakeJobQueue()
    ctx = FakeContext(jq)
    origin = SimpleNamespace(sender_user=SimpleNamespace(full_name="Леша Языков"))

    async def scenario():
        await bot.on_group_message(incoming("Марко вот ещё инфа от друга", 1), ctx)
        assert bot.direct_window.is_open()
        # Пересылки доезжают через доли секунды - они должны попасть в то же окно.
        await bot.on_group_message(
            incoming("в Убуде дожди весь октябрь", 2, forward_origin=origin), ctx
        )
        await bot.on_group_message(
            incoming("байк 150к рупий", 3, forward_origin=origin), ctx
        )

    run(scenario())

    assert len(bot.direct_window) == 3
    assert len(bot.passive_batch) == 0  # пересылки не ушли в пассивную пачку
    assert jq.get_jobs_by_name(bot.PASSIVE_JOB_NAME) == []
    assert len(jq.get_jobs_by_name(bot.DIRECT_JOB_NAME)) == 1
    assert ctx.bot.sent == []  # модель ещё не звали: ждём закрытия окна

    payload = bot.direct_window.take()
    assert payload["trigger"]["message_id"] == 1
    assert [r["forward_from"] for r in payload["tail"]] == ["Леша Языков"] * 2

    log = (tmp_log(bot.GROUP_DIR)).read_text(encoding="utf-8")
    assert "Антон (переслал от «Леша Языков»): в Убуде дожди весь октябрь" in log
    assert "Антон: Марко вот ещё инфа от друга" in log

    prompt = bot.build_turn_prompt(
        bot.format_author(payload["trigger"]["author"], None),
        payload["trigger"]["text"],
        followups=payload["tail"],
    )
    assert "Следом пришли сообщения:" in prompt
    assert "байк 150к рупий" in prompt


def tmp_log(group_dir):
    from pathlib import Path

    files = sorted(Path(group_dir, "chat-log").glob("*.md"))
    assert files, "чатлог не создан"
    return files[-1]


def test_handler_plain_message_without_window_goes_to_passive(handler_env):
    jq = FakeJobQueue()
    ctx = FakeContext(jq)
    run(bot.on_group_message(incoming("взяли воду в дорогу", 1), ctx))
    assert len(bot.passive_batch) == 1
    assert bot.direct_window.is_open() is False
    assert len(jq.get_jobs_by_name(bot.PASSIVE_JOB_NAME)) == 1


def test_handler_logs_stranger_with_id(handler_env, caplog):
    jq = FakeJobQueue()
    ctx = FakeContext(jq)
    with caplog.at_level("INFO", logger="marco-group"):
        run(bot.on_group_message(incoming("всем привет", 1, user_id=STRANGER), ctx))
    assert "Сообщение от неразрешённого автора: user_id=999" in caplog.text
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert "гость Антон Розенблюм (id 999): всем привет" in log
    assert len(bot.passive_batch) == 0


# ---------------------------------------------------------------------------
# Волна 2: вложения (media_intake) - классификация
# ---------------------------------------------------------------------------
import media_intake  # noqa: E402


def photo_sizes():
    return [
        SimpleNamespace(file_id="small", file_size=1000, width=90, height=90),
        SimpleNamespace(file_id="big", file_size=90000, width=1280, height=1280),
    ]


def media_msg(**kwargs):
    base = dict(
        photo=None,
        document=None,
        voice=None,
        audio=None,
        video_note=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_classify_photo_takes_largest_size():
    info = media_intake.classify_attachment(media_msg(photo=photo_sizes()))
    assert info["kind"] == media_intake.KIND_IMAGE
    assert info["file_id"] == "big"
    assert info["file_name"] == "photo.jpg"


def test_classify_photo_without_file_size_falls_back_to_area():
    photos = [
        SimpleNamespace(file_id="small", file_size=None, width=90, height=90),
        SimpleNamespace(file_id="big", file_size=None, width=1280, height=720),
    ]
    assert media_intake.classify_attachment(media_msg(photo=photos))["file_id"] == "big"


def test_classify_pdf_document():
    doc = SimpleNamespace(
        file_id="f1",
        file_name="Бронь отеля.pdf",
        mime_type="application/pdf",
        file_size=200000,
    )
    info = media_intake.classify_attachment(media_msg(document=doc))
    assert info["kind"] == media_intake.KIND_PDF
    assert info["file_name"] == "Бронь_отеля.pdf"


def test_classify_pdf_by_extension_when_mime_is_generic():
    doc = SimpleNamespace(
        file_id="f1",
        file_name="ticket.PDF",
        mime_type="application/octet-stream",
        file_size=10,
    )
    assert media_intake.classify_attachment(media_msg(document=doc))["kind"] == media_intake.KIND_PDF


def test_classify_image_document_by_mime():
    doc = SimpleNamespace(
        file_id="f1", file_name="scan", mime_type="image/png", file_size=10
    )
    assert media_intake.classify_attachment(media_msg(document=doc))["kind"] == media_intake.KIND_IMAGE


def test_classify_image_document_by_extension():
    doc = SimpleNamespace(file_id="f1", file_name="boarding.webp", mime_type=None, file_size=10)
    assert media_intake.classify_attachment(media_msg(document=doc))["kind"] == media_intake.KIND_IMAGE


def test_classify_heic_is_unsupported():
    doc = SimpleNamespace(
        file_id="f1", file_name="IMG_0001.HEIC", mime_type="image/heic", file_size=10
    )
    assert media_intake.classify_attachment(media_msg(document=doc))["kind"] == media_intake.KIND_UNSUPPORTED


def test_classify_zip_is_unsupported():
    doc = SimpleNamespace(
        file_id="f1", file_name="photos.zip", mime_type="application/zip", file_size=10
    )
    assert media_intake.classify_attachment(media_msg(document=doc))["kind"] == media_intake.KIND_UNSUPPORTED


def test_classify_voice():
    voice = SimpleNamespace(file_id="v1", file_size=30000, duration=12, mime_type="audio/ogg")
    info = media_intake.classify_attachment(media_msg(voice=voice))
    assert info["kind"] == media_intake.KIND_VOICE
    assert info["file_name"] == "voice.ogg"
    assert info["duration"] == 12


def test_classify_video_note_is_voice_mp4():
    note = SimpleNamespace(file_id="c1", file_size=40000, duration=8)
    info = media_intake.classify_attachment(media_msg(video_note=note))
    assert info["kind"] == media_intake.KIND_VOICE
    assert info["file_name"] == "circle.mp4"


def test_classify_audio_is_voice():
    audio = SimpleNamespace(
        file_id="a1", file_name="запись.m4a", mime_type="audio/mp4", file_size=5000, duration=30
    )
    info = media_intake.classify_attachment(media_msg(audio=audio))
    assert info["kind"] == media_intake.KIND_VOICE
    assert info["file_name"].endswith(".m4a")


def test_classify_too_big_file_is_not_downloaded():
    doc = SimpleNamespace(
        file_id="f1",
        file_name="album.pdf",
        mime_type="application/pdf",
        file_size=media_intake.MAX_FILE_BYTES + 1,
    )
    assert media_intake.classify_attachment(media_msg(document=doc))["kind"] == media_intake.KIND_TOO_BIG


def test_classify_returns_none_without_attachment():
    assert media_intake.classify_attachment(media_msg()) is None
    assert media_intake.classify_attachment(None) is None


def test_voice_too_long_guard():
    long_voice = {"kind": media_intake.KIND_VOICE, "duration": 301}
    short_voice = {"kind": media_intake.KIND_VOICE, "duration": 299}
    assert media_intake.voice_too_long(long_voice) is True
    assert media_intake.voice_too_long(short_voice) is False
    assert media_intake.voice_too_long({"kind": "image", "duration": 9999}) is False
    assert media_intake.voice_too_long(None) is False


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("/tmp/evil.pdf", "evil.pdf"),
        ("C:\\Users\\x\\bill.pdf", "bill.pdf"),
        ("Бронь отеля Убуд.pdf", "Бронь_отеля_Убуд.pdf"),
        ("..", "file"),
        ("", "file"),
        (None, "file"),
    ],
)
def test_safe_filename_variants(raw, expected):
    assert media_intake.safe_filename(raw) == expected


def test_safe_filename_limits_length_and_keeps_extension():
    name = media_intake.safe_filename("a" * 200 + ".pdf")
    assert len(name) <= 80
    assert name.endswith(".pdf")


def test_safe_filename_drops_semicolons_and_quotes():
    assert media_intake.safe_filename('bill";rm -rf .pdf') == "bill_rm_-rf_.pdf"
    # Всё до последнего слеша - путь, он отрезается целиком.
    assert media_intake.safe_filename('x";rm -rf /.pdf') == "pdf"


# ---------------------------------------------------------------------------
# prompt_block
# ---------------------------------------------------------------------------
def test_prompt_block_lists_files_and_marks_untrusted():
    block = media_intake.prompt_block(
        [
            {"kind": "pdf", "rel": "inbox/20260918-120000-7/bron.pdf", "author": "Антон"},
            {"kind": "image", "rel": "inbox/20260918-120001-8/photo.jpg", "author": "Ралина"},
            {"kind": "voice", "rel": "inbox/x/voice.ogg", "author": "Антон"},
        ]
    )
    assert "inbox/20260918-120000-7/bron.pdf (pdf, от Антон)" in block
    assert "inbox/20260918-120001-8/photo.jpg (картинка, от Ралина)" in block
    assert "открой инструментом Read" in block
    assert "voice.ogg" not in block  # голосовое уже стало текстом
    assert media_intake.UNTRUSTED_NOTE in block


def test_prompt_block_empty_without_readable_files():
    assert media_intake.prompt_block([]) == ""
    assert media_intake.prompt_block(None) == ""
    assert media_intake.prompt_block([{"kind": "voice", "rel": "a.ogg"}]) == ""


def test_media_record_makes_relative_path(tmp_path):
    info = {"kind": "pdf", "mime": "application/pdf"}
    path = tmp_path / "workspace" / "inbox" / "20260918-1-2" / "b.pdf"
    rec = media_intake.media_record(info, str(path), "Антон", str(tmp_path / "workspace"))
    assert rec["rel"] == "inbox/20260918-1-2/b.pdf"
    assert rec["name"] == "b.pdf"
    assert rec["author"] == "Антон"


# ---------------------------------------------------------------------------
# inbox: раскладка и уборка
# ---------------------------------------------------------------------------
def test_target_path_uses_stamp_and_message_id(tmp_path):
    info = {"file_name": "Бронь.pdf"}
    path = media_intake.target_path(
        info, 42, dt=utc(2026, 9, 18, 12, 30), inbox=str(tmp_path)
    )
    assert path.endswith("/20260918-123000-42/Бронь.pdf")
    assert os.path.isdir(os.path.dirname(path))


def test_cleanup_inbox_removes_only_old_dirs(tmp_path):
    now = 1_800_000_000.0
    old = tmp_path / "20260101-000000-1"
    fresh = tmp_path / "20260918-000000-2"
    for d in (old, fresh):
        d.mkdir()
        (d / "f.bin").write_bytes(b"x")
    os.utime(old, (now - 31 * 86400, now - 31 * 86400))
    os.utime(fresh, (now - 2 * 86400, now - 2 * 86400))
    (tmp_path / "stray.txt").write_text("не каталог")

    removed = media_intake.cleanup_inbox(max_age_days=30, now=now, inbox=str(tmp_path))
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert (tmp_path / "stray.txt").exists()


def test_cleanup_inbox_missing_dir_is_noop(tmp_path):
    assert media_intake.cleanup_inbox(inbox=str(tmp_path / "нет")) == 0


def test_count_inbox_counts_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "1.pdf").write_text("x")
    (tmp_path / "a" / "2.jpg").write_text("x")
    assert media_intake.count_inbox(str(tmp_path)) == 2
    assert media_intake.count_inbox(str(tmp_path / "нет")) == 0


# ---------------------------------------------------------------------------
# transcribe: subprocess замокан, faster_whisper не импортируется
# ---------------------------------------------------------------------------
def fake_run(stdout=b"", stderr=b"", rc=0, raise_exc=None):
    def _run(cmd, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    return _run


def test_transcribe_success(monkeypatch):
    payload = json.dumps({"text": " Марко,  во сколько вылет "}, ensure_ascii=False)
    monkeypatch.setattr(
        media_intake.subprocess, "run", fake_run(stdout=payload.encode("utf-8"))
    )
    assert media_intake.transcribe("/tmp/v.ogg") == "Марко, во сколько вылет"


def test_transcribe_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        media_intake.subprocess, "run", fake_run(rc=1, stderr=b"no ffmpeg")
    )
    assert media_intake.transcribe("/tmp/v.ogg") is None


def test_transcribe_timeout(monkeypatch):
    monkeypatch.setattr(
        media_intake.subprocess,
        "run",
        fake_run(raise_exc=subprocess.TimeoutExpired(cmd="x", timeout=180)),
    )
    assert media_intake.transcribe("/tmp/v.ogg") is None


def test_transcribe_broken_json(monkeypatch):
    monkeypatch.setattr(media_intake.subprocess, "run", fake_run(stdout=b"not json at all"))
    assert media_intake.transcribe("/tmp/v.ogg") is None


def test_transcribe_os_error(monkeypatch):
    monkeypatch.setattr(
        media_intake.subprocess, "run", fake_run(raise_exc=OSError("нет python"))
    )
    assert media_intake.transcribe("/tmp/v.ogg") is None


def test_transcribe_empty_text_is_none(monkeypatch):
    monkeypatch.setattr(media_intake.subprocess, "run", fake_run(stdout=b'{"text": "  "}'))
    assert media_intake.transcribe("/tmp/v.ogg") is None
    assert media_intake.transcribe(None) is None


# ---------------------------------------------------------------------------
# Пересчёт маршрута по транскрипту
# ---------------------------------------------------------------------------
def route_voice(text, **kwargs):
    base = dict(
        chat_type="supergroup",
        chat_id=GROUP,
        user_id=ANTON,
        group_chat_id=GROUP,
        bot_username=BOTNAME,
        anton_id=ANTON,
        ralina_id=RALINA,
        passive_enabled=True,
    )
    base.update(kwargs)
    return bot.route_for_text(text, **base)


def test_route_for_text_voice_with_name_is_direct():
    verdict, text = route_voice("Марко, во сколько вылет")
    assert verdict["route"] == bot.ROUTE_MODEL
    assert verdict["triggers"] == ["name"]
    assert text == "Марко, во сколько вылет"


def test_route_for_text_plain_voice_is_passive():
    verdict, text = route_voice("купил билеты на завтра")
    assert verdict["route"] == bot.ROUTE_PASSIVE
    assert text == "купил билеты на завтра"


def test_route_for_text_failed_voice_is_passive_with_mark():
    verdict, text = route_voice(None)
    assert verdict["route"] == bot.ROUTE_PASSIVE
    assert text == bot.VOICE_FAILED_TEXT


def test_route_for_text_too_long_voice_uses_fallback():
    verdict, text = route_voice("", fallback=bot.VOICE_TOO_LONG_TEXT)
    assert verdict["route"] == bot.ROUTE_PASSIVE
    assert text == bot.VOICE_TOO_LONG_TEXT


def test_route_for_text_failed_voice_never_becomes_direct():
    verdict, _text = route_voice(None, is_reply_to_bot=True)
    assert verdict["route"] == bot.ROUTE_PASSIVE


def test_route_for_text_voice_from_stranger_is_log_only():
    verdict, _text = route_voice("Марко, привет", user_id=STRANGER)
    assert verdict["route"] == bot.ROUTE_LOG


# ---------------------------------------------------------------------------
# Чатлог и промпты с вложениями
# ---------------------------------------------------------------------------
def test_format_chatlog_line_marks_voice():
    line = bot.format_chatlog_line(
        "Антон", "во сколько вылет", [], dt=utc(2026, 9, 18, 9, 5), voice=True
    )
    assert line == "- 12:05 Антон [голосовое]: во сколько вылет"  # 09:05 UTC = 12:05 Москва


def test_format_record_line_marks_voice():
    record = bot.build_record("Ралина", "купила воду", 7, voice=True)
    assert bot.format_record_line(record) == "- Ралина [голосовое]: купила воду"


def test_build_record_keeps_media_for_photo_without_text():
    media = [{"kind": "image", "rel": "inbox/a/photo.jpg", "author": "Антон"}]
    record = bot.build_record("Антон", None, 5, attachments=["photo"], media=media)
    assert record["media"] == media
    assert bot.collect_media([record]) == media


def test_build_turn_prompt_lists_attachments():
    prompt = bot.build_turn_prompt(
        "Антон",
        "вот бронь",
        media=[{"kind": "pdf", "rel": "inbox/a/bron.pdf", "author": "Антон"}],
    )
    assert "## Вложения" in prompt
    assert "inbox/a/bron.pdf" in prompt
    assert media_intake.UNTRUSTED_NOTE in prompt


def test_build_passive_prompt_collects_media_from_entries():
    record = bot.build_record(
        "Ралина",
        "смотри",
        3,
        attachments=["photo"],
        media=[{"kind": "image", "rel": "inbox/b/photo.jpg", "author": "Ралина"}],
    )
    prompt = bot.build_passive_prompt([record])
    assert "inbox/b/photo.jpg" in prompt
    assert "картинка, от Ралина" in prompt


def test_status_text_reports_inbox(tmp_path):
    text = bot.status_text({}, [], [], inbox_files=3)
    assert "Вложений в inbox: 3" in text


# ---------------------------------------------------------------------------
# Хендлер с вложениями (Telegram и ASR замоканы)
# ---------------------------------------------------------------------------
class FakeTGFile:
    def __init__(self, payload=b"data"):
        self.payload = payload

    async def download_to_drive(self, custom_path=None):
        with open(custom_path, "wb") as f:
            f.write(self.payload)
        return custom_path


class MediaBot(FakeBot):
    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail
        self.requested = []

    async def get_file(self, file_id):
        self.requested.append(file_id)
        if self.fail:
            raise RuntimeError("telegram недоступен")
        return FakeTGFile()


class MediaContext:
    def __init__(self, job_queue, bot_obj):
        self.bot = bot_obj
        self.job_queue = job_queue


def media_incoming(mid, caption=None, user_id=ANTON, **attach):
    user = SimpleNamespace(id=user_id, full_name="Антон Розенблюм")
    fields = dict(photo=None, document=None, voice=None, audio=None, video_note=None)
    fields.update(attach)
    message = SimpleNamespace(
        message_id=mid,
        text=None,
        caption=caption,
        from_user=user,
        reply_to_message=None,
        forward_origin=None,
        **fields,
    )
    chat = SimpleNamespace(id=GROUP, type="supergroup")
    return SimpleNamespace(effective_message=message, effective_chat=chat)


@pytest.fixture
def media_env(handler_env, tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setattr(bot, "WORKSPACE", str(workspace))
    monkeypatch.setattr(bot, "INBOX_DIR", str(inbox))
    return inbox


def test_handler_downloads_pdf_and_puts_it_in_passive_batch(media_env):
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    doc = SimpleNamespace(
        file_id="pdf1", file_name="Бронь.pdf", mime_type="application/pdf", file_size=1234
    )
    run(bot.on_group_message(media_incoming(1, caption="вот бронь", document=doc), ctx))

    assert ctx.bot.requested == ["pdf1"]
    saved = list(media_env.rglob("*.pdf"))
    assert len(saved) == 1 and saved[0].name == "Бронь.pdf"
    assert len(bot.passive_batch) == 1
    record = bot.passive_batch.entries[0]
    assert record["media"][0]["kind"] == "pdf"
    assert record["media"][0]["rel"].startswith("inbox/")
    prompt = bot.build_passive_prompt(list(bot.passive_batch.entries))
    assert "Бронь.pdf" in prompt


def test_handler_photo_with_trigger_caption_opens_window(media_env):
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    run(
        bot.on_group_message(
            media_incoming(1, caption="Марко, что скажешь?", photo=photo_sizes()), ctx
        )
    )
    assert bot.direct_window.is_open()
    payload = bot.direct_window.take()
    assert payload["trigger"]["media"][0]["kind"] == "image"
    prompt = bot.build_turn_prompt(
        "Антон", "Марко, что скажешь?", media=bot.collect_media([payload["trigger"]])
    )
    assert "photo.jpg" in prompt


def test_handler_download_failure_marks_message(media_env):
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot(fail=True))
    doc = SimpleNamespace(
        file_id="pdf1", file_name="Бронь.pdf", mime_type="application/pdf", file_size=10
    )
    run(bot.on_group_message(media_incoming(1, caption="бронь", document=doc), ctx))
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert "не удалось скачать" in log
    assert len(bot.passive_batch) == 1


def test_handler_too_big_file_is_not_downloaded(media_env):
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    doc = SimpleNamespace(
        file_id="big",
        file_name="album.pdf",
        mime_type="application/pdf",
        file_size=media_intake.MAX_FILE_BYTES + 1,
    )
    run(bot.on_group_message(media_incoming(1, caption="архив", document=doc), ctx))
    assert ctx.bot.requested == []
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert "файл больше 20 МБ, не скачан" in log


def test_handler_unsupported_document_is_marked(media_env):
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    doc = SimpleNamespace(
        file_id="z1", file_name="photos.zip", mime_type="application/zip", file_size=10
    )
    run(bot.on_group_message(media_incoming(1, caption="архив", document=doc), ctx))
    assert ctx.bot.requested == []
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert "формат не поддерживается" in log


def test_handler_voice_with_name_becomes_direct_trigger(media_env, monkeypatch):
    monkeypatch.setattr(
        media_intake, "transcribe", lambda path: "Марко, во сколько вылет"
    )
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    voice = SimpleNamespace(file_id="v1", file_size=3000, duration=7, mime_type="audio/ogg")
    run(bot.on_group_message(media_incoming(1, voice=voice), ctx))

    assert bot.direct_window.is_open()
    trigger = bot.direct_window.trigger
    assert trigger["text"] == "Марко, во сколько вылет"
    assert trigger["voice"] is True
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert "Антон [голосовое]: Марко, во сколько вылет" in log
    assert "[вложение: голосовое]" not in log


def test_handler_voice_without_trigger_goes_to_passive(media_env, monkeypatch):
    monkeypatch.setattr(media_intake, "transcribe", lambda path: "купил билеты на завтра")
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    voice = SimpleNamespace(file_id="v1", file_size=3000, duration=7, mime_type="audio/ogg")
    run(bot.on_group_message(media_incoming(1, voice=voice), ctx))

    assert bot.direct_window.is_open() is False
    assert len(bot.passive_batch) == 1
    assert bot.passive_batch.entries[0]["text"] == "купил билеты на завтра"
    prompt = bot.build_passive_prompt(list(bot.passive_batch.entries))
    assert "Антон [голосовое]: купил билеты на завтра" in prompt


def test_handler_voice_not_recognized_is_passive_with_mark(media_env, monkeypatch):
    monkeypatch.setattr(media_intake, "transcribe", lambda path: None)
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    voice = SimpleNamespace(file_id="v1", file_size=3000, duration=7, mime_type="audio/ogg")
    run(bot.on_group_message(media_incoming(1, voice=voice), ctx))

    assert bot.direct_window.is_open() is False
    assert bot.passive_batch.entries[0]["text"] == bot.VOICE_FAILED_TEXT
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert bot.VOICE_FAILED_TEXT in log


def test_handler_long_voice_is_not_transcribed(media_env, monkeypatch):
    def boom(path):
        raise AssertionError("длинное голосовое расшифровывать нельзя")

    monkeypatch.setattr(media_intake, "transcribe", boom)
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    voice = SimpleNamespace(file_id="v1", file_size=3000, duration=301, mime_type="audio/ogg")
    run(bot.on_group_message(media_incoming(1, voice=voice), ctx))

    assert ctx.bot.requested == []  # даже не качаем
    log = tmp_log(bot.GROUP_DIR).read_text(encoding="utf-8")
    assert bot.VOICE_TOO_LONG_TEXT in log
    assert len(bot.passive_batch) == 1


def test_handler_voice_joins_open_direct_window(media_env, monkeypatch):
    monkeypatch.setattr(media_intake, "transcribe", lambda path: "и ещё отель на Убуде")
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    voice = SimpleNamespace(file_id="v1", file_size=3000, duration=5, mime_type="audio/ogg")

    async def scenario():
        await bot.on_group_message(incoming("Марко, собери план", 1), ctx)
        await bot.on_group_message(media_incoming(2, voice=voice), ctx)

    run(scenario())
    assert len(bot.direct_window) == 2
    assert bot.direct_window.tail[0]["text"] == "и ещё отель на Убуде"
    assert len(bot.passive_batch) == 0


def test_handler_ignores_attachments_from_stranger(media_env):
    jq = FakeJobQueue()
    ctx = MediaContext(jq, MediaBot())
    doc = SimpleNamespace(
        file_id="pdf1", file_name="Бронь.pdf", mime_type="application/pdf", file_size=10
    )
    run(
        bot.on_group_message(
            media_incoming(1, caption="вот", document=doc, user_id=STRANGER), ctx
        )
    )
    assert ctx.bot.requested == []
    assert list(media_env.rglob("*.pdf")) == []
