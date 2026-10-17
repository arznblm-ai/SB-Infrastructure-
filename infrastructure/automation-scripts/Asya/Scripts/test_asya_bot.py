#!/usr/bin/env python3
"""Тесты Аси. Без сети, без claude, без Telegram API.

Проверяется детерминированная часть: маршрут, склейка пачки (окно тишины),
пересылки, чатлог дня с 05:00 MSK, промпт, команда claude и права, разбор
ответа CLI, прогон пачки с ошибкой/успехом, конфиги проекта.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import asya_bot as bot  # noqa: E402
import media_intake  # noqa: E402

ANTON = 324186708
STRANGER = 999


def utc(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def rec(text, mid=None, **kw):
    return bot.build_record(text, message_id=mid, **kw)


# ---------------------------------------------------------------------------
# Маршрут
# ---------------------------------------------------------------------------
def test_route_private_anton_goes_to_model():
    assert bot.classify_update("private", ANTON, anton_id=ANTON) == bot.ROUTE_MODEL


@pytest.mark.parametrize(
    "chat_type,user_id",
    [("private", STRANGER), ("group", ANTON), ("supergroup", ANTON), ("channel", ANTON), ("private", None)],
)
def test_route_everything_else_dropped(chat_type, user_id):
    assert bot.classify_update(chat_type, user_id, anton_id=ANTON) == bot.ROUTE_DROP


def test_route_without_allowlist_drops(monkeypatch):
    monkeypatch.setattr(bot, "USER_ANTON_ID", None)
    assert bot.classify_update("private", ANTON) == bot.ROUTE_DROP


# ---------------------------------------------------------------------------
# Склейка пачки: чистый Batch
# ---------------------------------------------------------------------------
def test_batch_default_quiet_is_4_seconds():
    assert bot.BATCH_SECONDS == 4.0
    assert bot.Batch().quiet == 4.0


def test_batch_single_message_fires_after_quiet():
    b = bot.Batch(quiet=4, max_wait=60)
    t0 = utc(2026, 10, 16, 10)
    b.add(rec("привет", 1), t0)
    assert not b.due(t0 + timedelta(seconds=3.9))
    assert b.due(t0 + timedelta(seconds=4))
    assert [r["text"] for r in b.take()] == ["привет"]
    assert len(b) == 0 and not b.due(t0 + timedelta(seconds=100))


def test_batch_each_message_extends_silence_window():
    b = bot.Batch(quiet=4, max_wait=60)
    t0 = utc(2026, 10, 16, 10)
    b.add(rec("Давайте в сб в 12?", 1), t0)
    b.add(rec("Мне бы в 13", 2), t0 + timedelta(seconds=3))
    assert not b.due(t0 + timedelta(seconds=5))  # было бы закрыто без продления
    b.add(rec("Давай 13, ставим", 3), t0 + timedelta(seconds=6))
    assert not b.due(t0 + timedelta(seconds=9.5))
    assert b.due(t0 + timedelta(seconds=10))
    texts = [r["text"] for r in b.take()]
    assert texts == ["Давайте в сб в 12?", "Мне бы в 13", "Давай 13, ставим"]


def test_batch_gap_longer_than_quiet_splits_batches():
    b = bot.Batch(quiet=4, max_wait=60)
    t0 = utc(2026, 10, 16, 10)
    b.add(rec("первое", 1), t0)
    assert b.due(t0 + timedelta(seconds=4.5))
    first = b.take()
    b.add(rec("второе", 2), t0 + timedelta(seconds=10))
    assert [r["text"] for r in first] == ["первое"]
    assert [r["text"] for r in b.take()] == ["второе"]


def test_batch_cap_limits_endless_stream():
    b = bot.Batch(quiet=4, max_wait=10)
    t0 = utc(2026, 10, 16, 10)
    for i in range(10):
        b.add(rec("m%d" % i, i), t0 + timedelta(seconds=i * 3))  # каждые 3 с — тишины нет
        if i * 3 >= 10:
            break
    assert b.due(t0 + timedelta(seconds=10))


def test_batch_hold_keeps_batch_open_while_voice_transcribes():
    b = bot.Batch(quiet=4, max_wait=60)
    t0 = utc(2026, 10, 16, 10)
    b.add(rec("[переслано] анонс", 1), t0)
    b.hold(t0 + timedelta(seconds=1))  # пошла расшифровка голосового
    assert not b.due(t0 + timedelta(seconds=30))
    b.add(rec("поставь это", 2, voice=True), t0 + timedelta(seconds=20))
    b.release(t0 + timedelta(seconds=20))
    assert not b.due(t0 + timedelta(seconds=23))
    assert b.due(t0 + timedelta(seconds=24))
    assert len(b.take()) == 2


def test_batch_take_orders_by_message_id():
    b = bot.Batch(quiet=4)
    t0 = utc(2026, 10, 16, 10)
    b.add(rec("текст после голосового", 6), t0)
    b.add(rec("голосовое (расшифровано позже)", 5), t0 + timedelta(seconds=2))
    assert [r["message_id"] for r in b.take()] == [5, 6]


def test_batch_ignores_empty_record():
    b = bot.Batch(quiet=4)
    assert b.add(None) is False
    assert len(b) == 0 and b.fire_at() is None


# ---------------------------------------------------------------------------
# Склейка пачки: настоящий asyncio-таймер
# ---------------------------------------------------------------------------
def test_flush_timer_glues_burst_into_one_run(monkeypatch):
    calls = []

    async def fake_process(_bot, records):
        calls.append([r["text"] for r in records])

    monkeypatch.setattr(bot, "process_batch", fake_process)
    monkeypatch.setattr(bot, "batch", bot.Batch(quiet=0.15, max_wait=5))

    async def scenario():
        for i, text in enumerate(["Давайте в сб в 12?", "Мне бы в 13", "Давай 13, ставим"]):
            bot.batch.add(rec(text, i + 1))
            bot.schedule_flush(None)
            await asyncio.sleep(0.05)  # короче окна тишины
        await asyncio.sleep(0.4)
        bot.batch.add(rec("и ещё задача", 10))
        bot.schedule_flush(None)
        await asyncio.sleep(0.4)

    asyncio.run(scenario())
    assert calls == [["Давайте в сб в 12?", "Мне бы в 13", "Давай 13, ставим"], ["и ещё задача"]]


def test_flush_timer_does_not_cancel_running_turn(monkeypatch):
    done = []

    async def slow_process(_bot, records):
        await asyncio.sleep(0.3)
        done.append(len(records))

    monkeypatch.setattr(bot, "process_batch", slow_process)
    monkeypatch.setattr(bot, "batch", bot.Batch(quiet=0.05, max_wait=5))

    async def scenario():
        bot.batch.add(rec("первая пачка", 1))
        bot.schedule_flush(None)
        await asyncio.sleep(0.1)  # прогон уже идёт
        bot.batch.add(rec("вторая пачка", 2))
        bot.schedule_flush(None)  # отменяет таймер, но не прогон
        await asyncio.sleep(0.6)

    asyncio.run(scenario())
    assert done == [1, 1]


def test_on_message_drops_strangers_and_batches_anton(monkeypatch):
    scheduled = []
    monkeypatch.setattr(bot, "USER_ANTON_ID", ANTON)
    monkeypatch.setattr(bot, "batch", bot.Batch(quiet=4))
    monkeypatch.setattr(bot, "schedule_flush", lambda b, delay=None: scheduled.append(1))

    def update(user_id, chat_type="private", text="привет", mid=1):
        msg = SimpleNamespace(
            text=text, caption=None, message_id=mid, from_user=SimpleNamespace(id=user_id),
            forward_origin=None, photo=None, document=None, voice=None, audio=None, video_note=None,
        )
        return SimpleNamespace(effective_message=msg, effective_chat=SimpleNamespace(type=chat_type, id=user_id))

    ctx = SimpleNamespace(bot=None)
    asyncio.run(bot.on_message(update(STRANGER), ctx))
    asyncio.run(bot.on_message(update(ANTON, chat_type="group"), ctx))
    assert len(bot.batch) == 0 and not scheduled
    asyncio.run(bot.on_message(update(ANTON, text="поставь синк", mid=7), ctx))
    assert len(bot.batch) == 1 and scheduled == [1] and bot.batch.holds == 0


# ---------------------------------------------------------------------------
# Пересылки
# ---------------------------------------------------------------------------
FWD_DATE = utc(2026, 10, 15, 15, 20)  # 18:20 MSK, четверг


def msg(**kw):
    base = dict(forward_origin=None, forward_from=None, forward_sender_name=None,
                forward_from_chat=None, forward_date=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_forward_from_user_with_original_date():
    origin = SimpleNamespace(sender_user=SimpleNamespace(full_name="Виталик Петров"), date=FWD_DATE)
    info = bot.forward_info(msg(forward_origin=origin))
    assert info == {"source": "Виталик Петров", "date": FWD_DATE}
    assert bot.format_forward_tag(info) == "[переслано: Виталик Петров, чт 2026-10-15 18:20 MSK]"


def test_forward_hidden_user_chat_and_channel():
    hidden = SimpleNamespace(sender_user=None, sender_user_name="Лёша", date=FWD_DATE)
    chat = SimpleNamespace(sender_user=None, sender_user_name=None, sender_chat=SimpleNamespace(title="Зекиро core"), date=FWD_DATE)
    channel = SimpleNamespace(sender_user=None, chat=SimpleNamespace(title="AI канал"), date=FWD_DATE)
    assert bot.forward_info(msg(forward_origin=hidden))["source"] == "Лёша"
    assert bot.forward_info(msg(forward_origin=chat))["source"] == "Зекиро core"
    assert bot.forward_info(msg(forward_origin=channel))["source"] == "AI канал"


def test_forward_unknown_source_and_naive_date():
    origin = SimpleNamespace(date=datetime(2026, 10, 15, 15, 20))  # naive = UTC
    info = bot.forward_info(msg(forward_origin=origin))
    assert info["source"] == bot.FORWARD_UNKNOWN
    assert info["date"].tzinfo is not None


def test_forward_legacy_fields():
    info = bot.forward_info(msg(forward_from=SimpleNamespace(full_name="Макс"), forward_date=FWD_DATE))
    assert info == {"source": "Макс", "date": FWD_DATE}


def test_not_forwarded_is_none():
    assert bot.forward_info(msg()) is None
    assert bot.format_forward_tag(None) == ""


def test_forward_without_date_is_marked():
    assert bot.format_forward_tag({"source": "X", "date": None}) == "[переслано: X, дата неизвестна]"


# ---------------------------------------------------------------------------
# Запись и строка промпта
# ---------------------------------------------------------------------------
def test_record_lines_for_own_forward_and_voice():
    fwd = {"source": "Виталик", "date": FWD_DATE}
    assert bot.format_record_line(rec("поставь синк")) == "- Антон: поставь синк"
    assert bot.format_record_line(rec("Давай в сб\nв 12?", forward=fwd)) == (
        "- [переслано: Виталик, чт 2026-10-15 18:20 MSK]: Давай в сб в 12?"
    )
    assert bot.format_record_line(rec("купить билеты", voice=True)) == "- Антон [голосовое]: купить билеты"


def test_record_with_attachment_marker_only():
    r = rec("", attachments=["photo"])
    assert bot.format_record_line(r) == "- Антон: [вложение: фото]"
    assert rec("") is None


# ---------------------------------------------------------------------------
# Чатлог дня с 05:00 MSK
# ---------------------------------------------------------------------------
def test_chat_day_starts_at_five_msk():
    # 04:59 MSK 16.10 = 01:59 UTC -> ещё «день» 15.10; 05:00 MSK -> 16.10
    assert bot.chat_day(utc(2026, 10, 16, 1, 59)) == "2026-10-15"
    assert bot.chat_day(utc(2026, 10, 16, 2, 0)) == "2026-10-16"
    assert bot.chat_day(utc(2026, 10, 16, 20, 59)) == "2026-10-16"  # 23:59 MSK


def test_chatlog_append_and_tail_limited_to_today(tmp_path):
    d = str(tmp_path)
    yesterday = utc(2026, 10, 16, 1, 0)  # 04:00 MSK -> день 15.10
    today = utc(2026, 10, 16, 9, 0)
    bot.append_chatlog([bot.format_chatlog_line("Антон", "вчерашнее", dt=yesterday)], dt=yesterday, chatlog_dir=d)
    lines = [bot.format_chatlog_line("Антон", "строка %d" % i, dt=today) for i in range(70)]
    path = bot.append_chatlog(lines, dt=today, chatlog_dir=d)
    assert path.endswith("2026-10-16.md")
    tail = bot.read_chatlog_tail(dt=today, chatlog_dir=d)
    assert len(tail) == 60
    assert tail[-1] == "- 12:00 Антон: строка 69"
    assert all("вчерашнее" not in ln for ln in tail)
    assert bot.read_chatlog_tail(dt=today + timedelta(days=1), chatlog_dir=d) == []


def test_chatlog_line_is_single_line_and_capped():
    line = bot.format_chatlog_line("Ася", "a\nb\n\n" + "x" * 5000, dt=utc(2026, 10, 16, 9, 0))
    assert "\n" not in line and len(line) <= bot.CHATLOG_LINE_MAX
    assert bot.format_chatlog_line("Ася", "", dt=utc(2026, 10, 16)) == ""


def test_record_chatlog_line_keeps_forward_tag():
    r = rec("Мне бы в 13", forward={"source": "Лёша", "date": FWD_DATE}, dt=utc(2026, 10, 16, 9, 1))
    assert bot.record_chatlog_line(r) == "- 12:01 [переслано: Лёша, чт 2026-10-15 18:20 MSK]: Мне бы в 13"


# ---------------------------------------------------------------------------
# Память и промпт
# ---------------------------------------------------------------------------
def test_read_memory_missing_and_truncated(tmp_path):
    assert bot.read_memory(str(tmp_path / "nope.md")) == ""
    p = tmp_path / "m.md"
    p.write_text("x" * 100, encoding="utf-8")
    assert bot.read_memory(str(p), limit=10).startswith("x" * 10 + "\n…")


def test_turn_prompt_contains_time_tail_batch_memory_and_warning():
    fwd = {"source": "Виталик", "date": FWD_DATE}
    records = [rec("Давайте в сб в 12?", 1, forward=fwd), rec("поставь", 2)]
    media = [{"kind": media_intake.KIND_IMAGE, "rel": "inbox/x/photo.jpg", "author": "Антон"}]
    prompt = bot.build_turn_prompt(
        records,
        dt=utc(2026, 10, 16, 11, 5),
        chatlog_tail=["- 10:00 Антон: утро", "- 10:01 Ася: доброе"],
        memory_text="- 2026-10-16: Руслан — это Луч",
        memory_file="/root/second-brain/infrastructure/Asya/memory/m.md",
        media=media,
    )
    assert "Сейчас: 2026-10-16 14:05, пятница (Москва, MSK)." in prompt
    assert bot.UNTRUSTED_WARNING in prompt
    assert "- 10:01 Ася: доброе" in prompt
    assert "- [переслано: Виталик, чт 2026-10-15 18:20 MSK]: Давайте в сб в 12?" in prompt
    assert "- Антон: поставь" in prompt
    assert "Руслан — это Луч" in prompt
    assert "inbox/x/photo.jpg" in prompt
    assert prompt.index("утро") < prompt.index("Новые сообщения")


def test_turn_prompt_empty_tail_and_memory():
    prompt = bot.build_turn_prompt([rec("привет", 1)], dt=utc(2026, 10, 16, 3, 0), memory_file="/m.md")
    assert "Разговор сегодня с 05:00 MSK: пока пусто." in prompt
    assert "(пусто)" in prompt


# ---------------------------------------------------------------------------
# Команда claude и права
# ---------------------------------------------------------------------------
def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def test_claude_cmd_basics():
    cmd = bot.build_claude_cmd("промпт", vault_dir="/root/second-brain", home_dir="/root", mcp_config="/w/mcp.json")
    assert cmd[1:3] == ["-p", "промпт"]
    assert _flag(cmd, "--model") == "opus"
    assert "--resume" not in cmd and "-r" not in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert _flag(cmd, "--permission-mode") == "dontAsk"
    assert _flag(cmd, "--add-dir") == "/root/second-brain"
    assert _flag(cmd, "--mcp-config") == "/w/mcp.json"
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert _flag(cmd, "--setting-sources") == "project"


def test_claude_cmd_tools_without_bash_and_web():
    cmd = bot.build_claude_cmd("p", vault_dir="/root/second-brain")
    tools = _flag(cmd, "--tools").split(",")
    assert tools == ["Read", "Glob", "Grep", "Edit", "Write"]
    allowed = _flag(cmd, "--allowedTools").split(",")
    for banned in ("Bash", "WebSearch", "WebFetch", "mcp__gcal__save_item", "mcp__gcal"):
        assert banned not in allowed
    # голый Read/Glob/Grep в allow открыл бы чтение любого файла на диске
    assert not {"Read", "Glob", "Grep", "Edit", "Write"} & set(allowed)
    assert set("mcp__gcal__" + t for t in ("create_event", "list_events", "update_event", "delete_event")) <= set(allowed)
    assert "mcp__todoist" in allowed
    mem = "//root/second-brain/infrastructure/Asya/memory/**"
    assert [a for a in allowed if a.startswith(("Edit", "Write"))] == ["Edit(%s)" % mem, "Write(%s)" % mem]


def test_claude_cmd_deny_rules_cover_private_zones():
    deny = _flag(bot.build_claude_cmd("p", vault_dir="/root/second-brain", home_dir="/root"), "--disallowedTools").split(",")
    for rule in (
        "Read(//root/second-brain/context/psychology/**)",
        "Read(//root/second-brain/context/archive/**)",
        "Read(//root/second-brain/transcripts/telegram/**)",
        "Read(//root/second-brain/infrastructure/Kris/memory/**)",
        "Read(//**/.env)",
        "Read(//**/*.env)",
        "Read(//root/.config/**)",
        "Bash",
        "WebFetch",
        "WebSearch",
        "mcp__gcal__save_item",
    ):
        assert rule in deny


def test_permission_rules_have_no_spaces_or_commas():
    # CLI режет --allowedTools/--disallowedTools по пробелам и запятым
    for rule in bot.allowed_tools("/root/second-brain") + bot.deny_rules("/root/second-brain", "/root"):
        assert " " not in rule and "," not in rule


def test_settings_json_matches_code():
    with open(os.path.join(ROOT, "config", "settings.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data == bot.settings_payload("/root/second-brain", "/root")


def test_mcp_json_points_to_vps_servers():
    with open(os.path.join(ROOT, "config", "mcp.json"), encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    assert servers["gcal"] == {"command": "/root/tools/gcal-mcp/venv/bin/python", "args": ["/root/tools/gcal-mcp/server.py"]}
    assert servers["todoist"] == {"command": "/root/tools/todoist-mcp/venv/bin/python", "args": ["/root/tools/todoist-mcp/server.py"]}


# ---------------------------------------------------------------------------
# Разбор ответа CLI и run_claude
# ---------------------------------------------------------------------------
def test_parse_claude_output_variants():
    assert bot.parse_claude_output(0, json.dumps({"result": "Готово"}), "") == ("Готово", None)
    assert bot.parse_claude_output(0, json.dumps({"is_error": True, "result": "boom"}), "") == ("", "error")
    assert bot.parse_claude_output(1, "", "Claude usage limit reached") == ("", "quota")
    assert bot.parse_claude_output(1, "", "segfault") == ("", "error")
    assert bot.parse_claude_output(0, "not json", "") == ("", "error")


class FakeProc:
    def __init__(self, rc=0, out=b"", err=b"", delay=0):
        self.returncode, self._out, self._err, self._delay = rc, out, err, delay
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(self._delay)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _patch_exec(monkeypatch, proc, seen=None):
    async def fake_exec(*cmd, **kw):
        if seen is not None:
            seen.update(cmd=cmd, kw=kw)
        return proc

    monkeypatch.setattr(bot.asyncio, "create_subprocess_exec", fake_exec)


def test_run_claude_success_runs_in_workspace_with_devnull(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(bot, "WORKSPACE", str(tmp_path))
    _patch_exec(monkeypatch, FakeProc(out=json.dumps({"result": "ok", "total_cost_usd": 0.1}).encode()), seen)
    assert asyncio.run(bot.run_claude("p")) == ("ok", None)
    assert seen["kw"]["cwd"] == str(tmp_path)
    assert seen["kw"]["stdin"] == asyncio.subprocess.DEVNULL


def test_run_claude_timeout_kills(monkeypatch, tmp_path):
    proc = FakeProc(delay=1)
    monkeypatch.setattr(bot, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(bot, "CLAUDE_TIMEOUT", 0.05)
    _patch_exec(monkeypatch, proc)
    assert asyncio.run(bot.run_claude("p")) == ("", "timeout")
    assert proc.killed


def test_run_claude_missing_binary(monkeypatch, tmp_path):
    async def boom(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(bot, "WORKSPACE", str(tmp_path))
    monkeypatch.setattr(bot.asyncio, "create_subprocess_exec", boom)
    assert asyncio.run(bot.run_claude("p")) == ("", "error")


# ---------------------------------------------------------------------------
# Прогон пачки: ошибка -> короткий ответ, не молчание
# ---------------------------------------------------------------------------
class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))

    async def send_chat_action(self, **kw):
        return True


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "USER_ANTON_ID", ANTON)
    monkeypatch.setattr(bot, "CHATLOG_DIR", str(tmp_path / "chatlog"))
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(bot, "INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setattr(bot, "VAULT_DIR", str(tmp_path / "vault"))
    return tmp_path


@pytest.mark.parametrize("kind", ["error", "timeout", "quota"])
def test_process_batch_error_sends_short_reply(monkeypatch, runtime, kind):
    async def fake_run(prompt):
        return "", kind

    monkeypatch.setattr(bot, "run_claude", fake_run)
    fb = FakeBot()
    asyncio.run(bot.process_batch(fb, [rec("поставь синк", 1)]))
    assert fb.sent == [(ANTON, bot.error_reply(kind))]
    assert len(bot.error_reply(kind)) < 120
    tail = bot.read_chatlog_tail(limit=0)
    assert tail[0].endswith("Антон: поставь синк")
    assert ("Ася [сбой: %s]" % kind) in tail[-1]
    assert bot.load_state()["last_error"] == kind


def test_process_batch_empty_reply_is_not_silence(monkeypatch, runtime):
    async def fake_run(prompt):
        return "   ", None

    monkeypatch.setattr(bot, "run_claude", fake_run)
    fb = FakeBot()
    asyncio.run(bot.process_batch(fb, [rec("?", 1)]))
    assert fb.sent == [(ANTON, bot.error_reply("empty"))]


def test_process_batch_success_prompt_and_chatlog(monkeypatch, runtime):
    prompts = []

    async def fake_run(prompt):
        prompts.append(prompt)
        return "Синк сб 17.10 13:00–14:00 → календарь.", None

    monkeypatch.setattr(bot, "run_claude", fake_run)
    fb = FakeBot()
    asyncio.run(bot.process_batch(fb, [rec("первое", 1)]))
    asyncio.run(bot.process_batch(fb, [rec("второе", 2)]))
    assert fb.sent[-1] == (ANTON, "Синк сб 17.10 13:00–14:00 → календарь.")
    # второй промпт видит первый обмен в хвосте, а свою пачку — только блоком
    second = prompts[1]
    head, batch_block = second.split("Новые сообщения Антона")
    assert "Антон: первое" in head and "Ася: Синк" in head
    assert "второе" not in head and "- Антон: второе" in batch_block
    assert bot.load_state()["last_run_at"]


def test_process_batch_splits_long_reply(monkeypatch, runtime):
    async def fake_run(prompt):
        return "а" * 4500, None

    monkeypatch.setattr(bot, "run_claude", fake_run)
    fb = FakeBot()
    asyncio.run(bot.process_batch(fb, [rec("длинно", 1)]))
    assert len(fb.sent) == 2 and all(len(t) <= bot.TG_LIMIT for _c, t in fb.sent)


def test_split_message():
    assert bot.split_message(None) == [] and bot.split_message("  ") == []
    assert bot.split_message("коротко") == ["коротко"]
    parts = bot.split_message("x" * 9000, limit=4000)
    assert [len(p) for p in parts] == [4000, 4000, 1000]


def test_status_text():
    text = bot.status_text({"last_run_at": "t", "last_error": None, "last_error_at": None, "runs": 3}, 2, True, 5, 1)
    assert "opus" in text and "думаю" in text and "в пачке 2" in text


# ---------------------------------------------------------------------------
# Файлы проекта
# ---------------------------------------------------------------------------
def test_media_intake_is_asya_copy():
    assert media_intake.logger.name == "asya.media"
    assert media_intake.WORKSPACE.startswith(os.environ.get("ASYA_HOME", "/var/lib/asya"))


def test_env_example_has_keys_and_no_token():
    with open(os.path.join(ROOT, "config", "env.example"), encoding="utf-8") as f:
        text = f.read()
    assert "TELEGRAM_BOT_TOKEN=\n" in text
    assert "USER_ANTON_ID=324186708" in text
    for key in ("ASYA_MODEL", "ASYA_CLAUDE_BIN", "ASYA_BATCH_SEC"):
        assert key in text


def test_service_unit_paths():
    with open(os.path.join(HERE, "asya.service"), encoding="utf-8") as f:
        unit = f.read()
    assert "EnvironmentFile=/root/.config/asya/.env" in unit
    assert "ExecStart=/opt/asya/venv/bin/python /opt/asya/asya_bot.py" in unit
    assert "WorkingDirectory=/var/lib/asya/workspace" in unit
    assert "skip-permissions" not in unit


def test_memory_file_template():
    path = os.path.join(ROOT, "memory", bot.MEMORY_FILE_NAME)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("---\n") and "### [[2026-10-16]]" in text
    assert "## Факты от Антона" in text and "## Поправки" in text


def test_persona_and_footer_cover_key_rules():
    with open(os.path.join(ROOT, "persona", "asya.md"), encoding="utf-8") as f:
        persona = f.read()
    with open(os.path.join(ROOT, "config", "workspace-footer.md"), encoding="utf-8") as f:
        footer = f.read()
    for needle in ("list_events", "итоговую договорённость", "«Мои»", "`зекиро`", "Луч или Зекиро", "запомни"):
        assert needle in persona
    for needle in ("карта треков", "track:", "Связи", "context/psychology", "transcripts/telegram", "Kris/memory"):
        assert needle in footer
