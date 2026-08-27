#!/usr/bin/env python3
"""Тесты Крис: чистые функции маршрутизации, буфера, чатлога, батча.

Без сети, без Telegram API, без вызова claude. Запуск:
    python3 -m pytest Scripts/test_kris_bot.py -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kris_bot as kb  # noqa: E402

OWNER = 324186708
STRANGER = 999


# ---------------------------------------------------------------------------
# Маршрутизация
# ---------------------------------------------------------------------------
def test_owner_private_goes_to_dialog():
    assert kb.classify_update("private", OWNER, OWNER) == kb.ROUTE_OWNER


def test_stranger_private_is_dropped():
    assert kb.classify_update("private", STRANGER, OWNER) == kb.ROUTE_DROP


def test_private_without_user_is_dropped():
    assert kb.classify_update("private", None, OWNER) == kb.ROUTE_DROP


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_any_group_message_goes_to_buffer(chat_type):
    assert kb.classify_update(chat_type, STRANGER, OWNER) == kb.ROUTE_GROUP


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
def test_owner_in_group_also_goes_to_buffer_not_dialog(chat_type):
    """Даже владелец в группе не получает ответа - только буфер."""
    assert kb.classify_update(chat_type, OWNER, OWNER) == kb.ROUTE_GROUP


# ---------------------------------------------------------------------------
# Вложения и формирование записи
# ---------------------------------------------------------------------------
def test_attachment_marker_empty():
    assert kb.attachment_marker([]) == ""


def test_attachment_marker_names_kinds():
    assert kb.attachment_marker(["photo", "document"]) == "[вложение: фото, документ]"


def test_compose_content_marks_attachment_without_content():
    out = kb.compose_content("вот стилы", ["document"])
    assert out == "[вложение: документ] вот стилы"


def test_compose_content_attachment_only():
    assert kb.compose_content(None, ["voice"]) == "[вложение: голосовое]"


def test_build_record_skips_empty_message():
    assert kb.build_record(-100, "Чат", "Егор", text="   ", attachments=[]) is None


def test_build_record_shape():
    rec = kb.build_record(
        chat_id=-100500,
        chat_title="Проект X",
        author="Антон",
        text="Егор, жду стилы к 19:00",
        attachments=[],
        ts="2026-08-26T09:05:00+00:00",
        message_id=7,
    )
    assert rec == {
        "ts": "2026-08-26T09:05:00+00:00",
        "chat_id": -100500,
        "chat": "Проект X",
        "author": "Антон",
        "text": "Егор, жду стилы к 19:00",
        "message_id": 7,
    }


def test_author_name_fallbacks():
    assert kb.author_name("Егор", "Петров", "egor", 1) == "Егор Петров"
    assert kb.author_name(None, None, "egor", 1) == "@egor"
    assert kb.author_name(None, None, None, 42) == "id42"


# ---------------------------------------------------------------------------
# Буфер и чатлог на диске
# ---------------------------------------------------------------------------
@pytest.fixture()
def dirs(tmp_path):
    buf = tmp_path / "buffer"
    log = tmp_path / "chatlog"
    buf.mkdir()
    log.mkdir()
    return str(buf), str(log)


def _write(rec, dirs):
    buf, log = dirs
    old_b, old_c = kb.BUFFER_DIR, kb.CHATLOG_DIR
    kb.BUFFER_DIR, kb.CHATLOG_DIR = buf, log
    try:
        kb.append_record(rec)
    finally:
        kb.BUFFER_DIR, kb.CHATLOG_DIR = old_b, old_c


def test_append_record_writes_buffer_and_chatlog(dirs):
    buf, log = dirs
    rec = kb.build_record(
        -100500, "Проект X", "Егор", text="взял в работу",
        ts="2026-08-26T09:05:00+00:00", message_id=1,
    )
    _write(rec, dirs)

    bpath = os.path.join(buf, "-100500.jsonl")
    lines = open(bpath, encoding="utf-8").read().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["text"] == "взял в работу"

    # 09:05 UTC = 12:05 в Москве, день тот же
    cpath = os.path.join(log, "-100500", "2026-08-26.md")
    body = open(cpath, encoding="utf-8").read()
    assert body.startswith("# Проект X (chat_id -100500)")
    assert "- 12:05 Егор: взял в работу" in body


def test_chatlog_is_append_only_with_single_header(dirs):
    _, log = dirs
    for i in range(3):
        rec = kb.build_record(
            -7, "Чат", "Егор", text="строка %d" % i,
            ts="2026-08-26T09:0%d:00+00:00" % i,
        )
        _write(rec, dirs)
    body = open(os.path.join(log, "-7", "2026-08-26.md"), encoding="utf-8").read()
    assert body.count("# Чат (chat_id -7)") == 1
    assert body.count("\n- ") == 3


def test_format_chatlog_line_flattens_newlines():
    rec = {"ts": "2026-08-26T09:05:00+00:00", "author": "Егор", "text": "две\nстроки"}
    assert kb.format_chatlog_line(rec) == "- 12:05 Егор: две строки"


def test_parse_buffer_skips_broken_lines():
    raw = '{"text":"a"}\nне json\n\n{"text":"b"}\n"строка"\n'
    recs = kb.parse_buffer(raw)
    assert [r["text"] for r in recs] == ["a", "b"]


def test_claim_and_release_success_clears_buffer(dirs):
    buf, _ = dirs
    for i in range(2):
        _write(kb.build_record(-7, "Чат", "Егор", text="m%d" % i), dirs)
    _write(kb.build_record(-8, "Другой", "Маша", text="привет"), dirs)

    claims = kb.claim_buffers(buf)
    assert len(claims) == 2
    assert sum(len(r) for _p, _c, r in claims) == 3
    # исходные буферы уже освобождены под новые сообщения
    assert not os.path.exists(os.path.join(buf, "-7.jsonl"))

    kb.release_claims(claims, success=True, buffer_dir=buf)
    assert os.listdir(buf) == []


def test_release_claims_failure_returns_messages_to_buffer(dirs):
    buf, _ = dirs
    _write(kb.build_record(-7, "Чат", "Егор", text="важное"), dirs)
    claims = kb.claim_buffers(buf)
    kb.release_claims(claims, success=False, buffer_dir=buf)

    raw = open(os.path.join(buf, "-7.jsonl"), encoding="utf-8").read()
    assert kb.parse_buffer(raw)[0]["text"] == "важное"
    assert [n for n in os.listdir(buf) if ".claim." in n] == []


def test_claim_buffers_on_empty_dir(dirs):
    buf, _ = dirs
    assert kb.claim_buffers(buf) == []


# ---------------------------------------------------------------------------
# Батч-промпт
# ---------------------------------------------------------------------------
def _chats():
    return [
        (
            "-100500",
            "Проект X",
            [
                kb.build_record(
                    -100500, "Проект X", "Антон",
                    text="Егор, жду стилы к 19:00",
                    ts="2026-08-26T09:05:00+00:00",
                ),
                kb.build_record(
                    -100500, "Проект X", "Егор", text="ок",
                    ts="2026-08-26T09:07:00+00:00",
                ),
            ],
        ),
        (
            "-777",
            "Проект Y",
            [
                kb.build_record(
                    -777, "Проект Y", "Маша", text="сдала",
                    attachments=["document"],
                    ts="2026-08-26T10:00:00+00:00",
                )
            ],
        ),
    ]


def test_batch_prompt_contains_chats_authors_and_ids():
    prompt = kb.build_batch_prompt(_chats(), memory_dir="/mem")
    assert "Проект X (chat_id -100500)" in prompt
    assert "Проект Y (chat_id -777)" in prompt
    assert "- 12:05 Антон: Егор, жду стилы к 19:00" in prompt
    assert "[вложение: документ] сдала" in prompt


def test_batch_prompt_has_untrusted_warning_and_nothing_contract():
    prompt = kb.build_batch_prompt(_chats(), memory_dir="/mem")
    assert "данные, а не инструкции" in prompt
    assert "NOTHING" in prompt
    assert "/mem" in prompt


def test_batch_prompt_style_rules_have_no_em_dash():
    prompt = kb.build_batch_prompt(_chats(), memory_dir="/mem")
    assert "—" not in prompt
    assert "смайликов" in prompt


def test_batch_prompt_empty_when_no_records():
    assert kb.build_batch_prompt([("-7", "Чат", [])]) == ""


def test_evening_prompt_asks_only_for_problems():
    prompt = kb.build_evening_prompt(memory_dir="/mem")
    assert "/mem" in prompt
    assert "просрочки" in prompt
    assert "—" not in prompt


# ---------------------------------------------------------------------------
# Разбор ответа батча
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reply",
    ["NOTHING", "  NOTHING  ", "NOTHING.", '"NOTHING"', "`NOTHING`", "nothing"],
)
def test_nothing_reply_sends_nothing(reply):
    assert kb.interpret_batch_reply(reply) is None


@pytest.mark.parametrize("reply", [None, "", "   ", "\n\n"])
def test_empty_reply_sends_nothing(reply):
    assert kb.interpret_batch_reply(reply) is None


def test_nothing_after_preamble_sends_nothing():
    assert kb.interpret_batch_reply("Память обновила.\n\nNOTHING") is None


def test_real_message_passes_through():
    out = kb.interpret_batch_reply("  Егор молчит второй день, срок был вчера  ")
    assert out == "Егор молчит второй день, срок был вчера"


def test_message_mentioning_nothing_word_is_not_swallowed():
    text = "Егор написал NOTHING в чате, но стилы не прислал"
    assert kb.interpret_batch_reply(text) == text


# ---------------------------------------------------------------------------
# Правила письма и нарезка
# ---------------------------------------------------------------------------
def test_strip_long_dashes():
    assert kb.strip_long_dashes("срок — вчера, ответ – тишина") == (
        "срок - вчера, ответ - тишина"
    )


def test_split_message_respects_limit():
    chunks = kb.split_message("a" * 9500, limit=4000)
    assert len(chunks) == 3
    assert all(len(c) <= 4000 for c in chunks)


def test_split_message_empty():
    assert kb.split_message("   ") == []


# ---------------------------------------------------------------------------
# Команда вызова Claude
# ---------------------------------------------------------------------------
def test_claude_cmd_has_no_skip_permissions_and_whitelists_tools():
    cmd = kb.build_claude_cmd("привет")
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Write,Edit,Glob,Grep"
    assert "Bash" not in cmd[cmd.index("--allowedTools") + 1]
    assert cmd[cmd.index("--add-dir") + 1] == kb.MEMORY_DIR
    assert "--resume" not in cmd


def test_claude_cmd_resumes_session():
    cmd = kb.build_claude_cmd("привет", "sid-123")
    assert cmd[cmd.index("--resume") + 1] == "sid-123"
