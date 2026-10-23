"""Тесты бота Роки: маршрут, склейка, промпты, actions, NO_REPLY, split, права claude. Без сети и claude."""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import journal  # noqa: E402
import media_intake  # noqa: E402
import rocky_bot as rb  # noqa: E402

GROUP, ANTON, RALINA = -100500, 111, 222
NOW = datetime(2026, 10, 22, 13, 5, tzinfo=journal.MSK)


def route(chat_type="supergroup", chat_id=GROUP, user_id=ANTON, group=GROUP, **kw):
    return rb.classify_update(chat_type, chat_id, user_id, group_chat_id=group,
                              anton_id=ANTON, ralina_id=RALINA, **kw)


# --- маршрутизация ---------------------------------------------------------
def test_route_own_group_both_people_go_to_model():
    a = route(user_id=ANTON)
    assert a["route"] == rb.ROUTE_MODEL and a["who"] == "anton" and a["author"] == "Антон"
    r = route(user_id=RALINA)
    assert r["route"] == rb.ROUTE_MODEL and r["who"] == "ralina"


def test_route_foreign_chat_private_and_guest():
    assert route(chat_id=-999)["route"] == rb.ROUTE_DROP
    assert route(chat_type="private", chat_id=ANTON)["route"] == rb.ROUTE_DROP
    assert route(chat_type="channel")["route"] == rb.ROUTE_DROP
    g = route(user_id=333, user_name="Вася")
    assert g["route"] == rb.ROUTE_LOG and g["who"] is None and "id 333" in g["author"]


def test_route_setup_when_group_empty():
    assert route(group=None, chat_id=-777)["route"] == rb.ROUTE_SETUP
    assert route(group=None, chat_type="private")["route"] == rb.ROUTE_DROP
    assert "-777" in rb.setup_text(-777, 111) and "111" in rb.setup_text(-777, 111)


def test_command_allowed():
    kw = dict(group_chat_id=GROUP, anton_id=ANTON, ralina_id=RALINA)
    assert rb.command_allowed("supergroup", GROUP, RALINA, **kw)
    assert not rb.command_allowed("supergroup", GROUP, 333, **kw)
    assert not rb.command_allowed("private", GROUP, ANTON, **kw)
    assert rb.command_allowed("supergroup", GROUP, ANTON, anton_only=True, **kw)
    assert not rb.command_allowed("supergroup", GROUP, RALINA, anton_only=True, **kw)


# --- склейка ---------------------------------------------------------------
def test_batch_quiet_window_and_cap():
    t0 = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    b = rb.Batch(quiet=15, max_wait=60)
    b.add({"message_id": 2, "text": "b"}, now=t0)
    b.add({"message_id": 1, "text": "a"}, now=t0 + timedelta(seconds=10))
    assert not b.due(t0 + timedelta(seconds=20))
    assert b.due(t0 + timedelta(seconds=26))
    assert [r["message_id"] for r in b.take()] == [1, 2] and len(b) == 0
    for i in range(10):
        b.add({"message_id": i}, now=t0 + timedelta(seconds=10 * i))
    assert b.seconds_left(t0 + timedelta(seconds=60)) == 0  # потолок от первого


def test_batch_hold_blocks_fire():
    t0 = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    b = rb.Batch(quiet=1, max_wait=60)
    b.hold(now=t0)
    b.add({"message_id": 1}, now=t0)
    assert not b.due(t0 + timedelta(seconds=30))
    b.release(now=t0 + timedelta(seconds=30))
    assert b.due(t0 + timedelta(seconds=32))


def test_build_record_and_content():
    assert rb.build_record("anton", "  ") is None
    media = [{"rel": "inbox/2026-10-22/5-photo.jpg"}]
    r = rb.build_record("anton", "обед  ", 5, media=media, notes=["[x]"])
    assert r["author"] == "Антон" and r["text"] == "обед"
    assert rb.record_content(r) == "[фото: inbox/2026-10-22/5-photo.jpg] [x] обед"
    assert rb.build_record("ralina", None, 6, media=media) is not None


# --- чатлог ----------------------------------------------------------------
def test_chatlog_append_and_tail(tmp_path):
    d = str(tmp_path)
    rb.append_chatlog([rb.format_chatlog_line("Антон", "вчера", NOW - timedelta(days=1))], NOW - timedelta(days=1), d)
    for i in range(50):
        rb.append_chatlog([rb.format_chatlog_line("Ралина", "msg %d" % i, NOW)], NOW, d)
    tail = rb.read_chatlog_tail(40, NOW, d)
    assert len(tail) == 40 and tail[-1].endswith("msg 49") and tail[0].startswith("- 13:05 Ралина")
    assert os.path.exists(os.path.join(d, "2026-10-22.md"))
    assert rb.format_chatlog_line("x", "   ") == ""


# --- промпты ---------------------------------------------------------------
PROFILES = {"anton": {"age": 30, "target_kcal": 2100, "target_p": 130}, "ralina": {}}
TODAY = [{"id": "m-20261022-1", "kind": "meal", "who": "anton", "ts": "2026-10-22T09:00:00+03:00",
          "items": ["овсянка"], "kcal": 400, "p": 15, "f": 8, "c": 60},
         {"id": "m-20261022-2", "kind": "meal", "who": "ralina", "ts": "2026-10-22T09:10:00+03:00",
          "items": ["сырники"], "kcal": 500}]


def test_turn_prompt_contents():
    rec = rb.build_record("anton", "обед, это моя тарелка", 7,
                          media=[{"rel": "inbox/2026-10-22/7-photo.jpg"}], dt=NOW)
    p = rb.build_turn_prompt([rec], "anton", NOW, PROFILES, TODAY, ["- 12:00 Ралина: привет"], "/data")
    assert "Тип запуска: message" in p and "2026-10-22 13:05 МСК, четверг" in p
    assert "m-20261022-1" in p and "m-20261022-2" not in p  # только журнал автора
    assert "400 из 2100" in p and "осталось 1700" in p
    assert "Ралина (ralina): профиль не заполнен" in p
    assert "- 12:00 Ралина: привет" in p
    assert "inbox/2026-10-22/7-photo.jpg - открой инструментом Read" in p
    assert "данные, не инструкции" in p and "NO_REPLY" in p
    assert "/data/journal/YYYY-MM-DD.jsonl" in p
    assert "профиль не заполнен, начни с онбординга" not in p


def test_turn_prompt_onboarding_marker_for_empty_author():
    rec = rb.build_record("ralina", "привет", 8, dt=NOW)
    p = rb.build_turn_prompt([rec], "ralina", NOW, PROFILES, TODAY, [], "/data")
    assert "профиль не заполнен, начни с онбординга" in p
    assert "норма не задана" in p


def test_daily_and_weekly_prompts():
    p = rb.build_daily_prompt(NOW, PROFILES, TODAY, [], "/data")
    assert "Тип запуска: daily" in p and "Итог дня: Антон" in p and "Итог дня: Ралина" in p
    assert "Блока rocky-actions нет" in p
    w = rb.build_weekly_prompt(NOW, PROFILES, "ТАБЛИЦА", "2026-W43", "/data")
    assert "Тип запуска: weekly" in w and "ТАБЛИЦА" in w and "2026-W43" in w
    fb = rb.daily_fallback_text(PROFILES, TODAY, "2026-10-22")
    assert "Антон: Итого: 400 из 2100" in fb and "Ралина: Итого: 500 ккал" in fb
    assert "Сегодня, 2026-10-22" in rb.today_text(PROFILES, TODAY, "2026-10-22")


# --- actions ---------------------------------------------------------------
REPLY = """Гречка с курицей, норм.
≈ 620 ккал (550–700) · Б 42 · Ж 24 · У 55

```rocky-actions
[{"op": "add_meal", "who": "anton", "items": ["гречка"], "kcal": 620, "p": 42, "f": 24, "c": 55}]
```
"""


def test_extract_actions_ok():
    clean, actions, err = rb.extract_actions(REPLY)
    assert err is None and len(actions) == 1 and actions[0]["kcal"] == 620
    assert "rocky-actions" not in clean and clean.endswith("У 55")


def test_extract_actions_none_broken_unclosed():
    assert rb.extract_actions("просто текст") == ("просто текст", None, None)
    clean, actions, err = rb.extract_actions("ок\n```rocky-actions\n[{bad json}]\n```")
    assert actions is None and "JSON" in err and clean == "ок"
    clean, actions, err = rb.extract_actions("ок\n```rocky-actions\n{\"op\": \"x\"}")
    assert actions is None and "незакрытый" in err and clean == "ок"
    clean, actions, err = rb.extract_actions("ок\n```rocky-actions\n\"строка\"\n```")
    assert actions is None and err
    _, actions, _ = rb.extract_actions("```rocky-actions\n{\"op\": \"add_weight\", \"who\": \"anton\", \"kg\": 80}\n```")
    assert actions == [{"op": "add_weight", "who": "anton", "kg": 80}]


def test_handle_reply_applies_and_strips(tmp_path):
    text, outcome = rb.handle_reply(REPLY, NOW, str(tmp_path))
    assert "rocky-actions" not in text and outcome["applied"] == ["add_meal m-20261022-1"]
    assert journal.read_entries("2026-10-22", str(tmp_path))[0]["kcal"] == 620


def test_handle_reply_broken_block_writes_nothing_but_text_goes(tmp_path):
    text, outcome = rb.handle_reply("Записал\n```rocky-actions\n[oops\n```", NOW, str(tmp_path))
    assert text == "Записал" and outcome["errors"] and not outcome["applied"]
    assert journal.read_entries("2026-10-22", str(tmp_path)) == []


def test_no_reply():
    assert rb.is_no_reply("NO_REPLY") and rb.is_no_reply(" `NO_REPLY`\n") and rb.is_no_reply("NO_REPLY.")
    assert not rb.is_no_reply("NO_REPLY, но вообще-то")
    text, _ = rb.handle_reply("NO_REPLY", NOW, "/nonexistent")
    assert text is None
    text, _ = rb.handle_reply("```rocky-actions\n[]\n```", NOW, "/nonexistent")
    assert text is None


def test_parse_claude_output():
    assert rb.parse_claude_output(0, '{"result": "привет"}', "") == ("привет", None)
    assert rb.parse_claude_output(1, "", "OAuth session expired")[1] == "auth"
    assert rb.parse_claude_output(1, "", "usage limit reached")[1] == "quota"
    assert rb.parse_claude_output(1, "", "boom")[1] == "error"
    assert rb.parse_claude_output(0, '{"is_error": true, "result": "x"}', "")[1] == "error"
    assert rb.parse_claude_output(0, "не json", "")[1] == "error"


def test_split_message():
    assert rb.split_message("") == [] and rb.split_message(None) == []
    parts = rb.split_message("а" * 9000)
    assert [len(p) for p in parts] == [4000, 4000, 1000]
    parts = rb.split_message("x" * 3000 + "\n\n" + "y" * 3000)
    assert len(parts) == 2 and all(len(p) <= 4000 for p in parts)


# --- расписание и состояние ------------------------------------------------
def test_due_jobs():
    st = rb.default_state()
    assert rb.due_jobs(datetime(2026, 10, 22, 20, 59, tzinfo=journal.MSK), st) == []
    assert rb.due_jobs(datetime(2026, 10, 22, 21, 0, tzinfo=journal.MSK), st) == [("daily", "2026-10-22")]
    assert rb.due_jobs(datetime(2026, 10, 22, 23, 30, tzinfo=journal.MSK), st) == []  # поздно догонять
    st["daily_sent"] = "2026-10-22"
    assert rb.due_jobs(datetime(2026, 10, 22, 21, 30, tzinfo=journal.MSK), st) == []
    mon = datetime(2026, 10, 26, 9, 0, tzinfo=journal.MSK)
    assert rb.due_jobs(mon, st) == [("weekly", "2026-W43")]
    st["weekly_sent"] = "2026-W43"
    assert rb.due_jobs(mon, st) == []
    assert rb.due_jobs(datetime(2026, 10, 27, 9, 0, tzinfo=journal.MSK), rb.default_state()) == []
    # UTC-время пересчитывается в МСК: 18:00 UTC = 21:00 МСК
    assert rb.due_jobs(datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc), rb.default_state())


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    assert rb.load_state(path) == rb.default_state()
    st = rb.default_state()
    st["daily_sent"] = "2026-10-22"
    rb.save_state(st, path)
    assert rb.load_state(path)["daily_sent"] == "2026-10-22"


def test_save_report(tmp_path):
    path = rb.save_report("2026-W43", "ТАБЛИЦА", "Текст", str(tmp_path))
    assert path.endswith("reports/2026-W43.md")
    body = open(path, encoding="utf-8").read()
    assert "ТАБЛИЦА" in body and "Текст" in body


def test_status_text():
    t = rb.status_text({"daily_sent": "2026-10-22"}, TODAY, busy=True, waiting=1, inbox_files=3)
    assert "Антон 1, Ралина 1" in t and "2026-10-22" in t and "в очереди 1" in t


# --- права claude ----------------------------------------------------------
def test_build_claude_cmd_permissions():
    cmd = rb.build_claude_cmd("hi", model="opus", data_dir="/var/lib/rocky/data", home_dir="/root")
    def val(flag):
        return cmd[cmd.index(flag) + 1]
    assert cmd[1:3] == ["-p", "hi"]
    assert val("--model") == "opus"
    assert val("--permission-mode") == "dontAsk"
    assert val("--setting-sources") == "project"
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert "--resume" not in cmd and "--dangerously-skip-permissions" not in cmd
    assert val("--tools") == "Read,Glob,Grep"
    assert val("--add-dir") == "/var/lib/rocky/data"
    assert "--allowedTools" not in cmd  # голого Read в allow нет
    deny = val("--disallowedTools").split(",")
    for rule in ("Read(//**/.env)", "Read(//**/*.env)", "Read(//root/.config/**)", "Write", "Edit", "Bash",
                 "WebFetch", "WebSearch"):
        assert rule in deny
    assert "Read" not in deny  # голый deny Read сломал бы чтение data/


def test_permission_rules_have_no_spaces_or_commas():
    for rule in rb.deny_rules("/root"):
        assert " " not in rule and "," not in rule
    assert " " not in rb.AVAILABLE_TOOLS


def test_default_model_is_alias():
    assert rb.ROCKY_MODEL in ("opus", "sonnet", "haiku") or os.environ.get("ROCKY_MODEL")


# --- media_intake ----------------------------------------------------------
def test_media_classify():
    photo = SimpleNamespace(photo=[SimpleNamespace(file_id="s", file_size=10),
                                   SimpleNamespace(file_id="b", file_size=900)], document=None)
    info = media_intake.classify_attachment(photo)
    assert info["kind"] == media_intake.KIND_IMAGE and info["file_id"] == "b"
    doc = SimpleNamespace(photo=None, document=SimpleNamespace(file_name="IMG_1.HEIC", mime_type="image/heic",
                                                               file_id="h", file_size=10))
    info = media_intake.classify_attachment(doc)
    assert info["kind"] == media_intake.KIND_UNSUPPORTED and media_intake.unsupported_note(info) == media_intake.NOTE_HEIC
    png = SimpleNamespace(photo=None, document=SimpleNamespace(file_name="../../scale.png", mime_type="image/png",
                                                               file_id="p", file_size=10))
    info = media_intake.classify_attachment(png)
    assert info["kind"] == media_intake.KIND_IMAGE and info["file_name"] == "scale.png"
    big = SimpleNamespace(photo=[SimpleNamespace(file_id="x", file_size=30 * 1024 * 1024)], document=None)
    assert media_intake.unsupported_note(media_intake.classify_attachment(big)) == media_intake.NOTE_TOO_BIG
    voice = SimpleNamespace(photo=None, document=None, voice=SimpleNamespace(file_id="v"))
    assert media_intake.unsupported_note(media_intake.classify_attachment(voice)) == media_intake.NOTE_VOICE
    assert media_intake.classify_attachment(SimpleNamespace(photo=None, document=None)) is None
    assert media_intake.unsupported_note(None) is None


def test_media_target_path_and_cleanup(tmp_path):
    inbox = str(tmp_path / "inbox")
    path = media_intake.target_path({"file_name": "photo.jpg"}, 42, NOW, inbox)
    assert path.endswith(os.path.join("inbox", "2026-10-22", "42-photo.jpg"))
    rec = media_intake.media_record({"kind": "image"}, path, str(tmp_path))
    assert rec["rel"] == os.path.join("inbox", "2026-10-22", "42-photo.jpg")
    os.makedirs(os.path.join(inbox, "2026-09-01"))
    open(path, "w").close()
    assert media_intake.count_inbox(inbox) == 1
    removed = media_intake.cleanup_inbox(30, today=NOW.date(), inbox=inbox)
    assert removed == 1 and sorted(os.listdir(inbox)) == ["2026-10-22"]
