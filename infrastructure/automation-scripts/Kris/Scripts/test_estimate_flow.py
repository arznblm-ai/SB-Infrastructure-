#!/usr/bin/env python3
"""Тесты пайплайна смет (ADR-027): вход, пост-хук, сверка.

Без сети, без Telegram API, без вызова claude и без Google Drive. Запуск:
    python3 -m pytest Scripts/test_estimate_flow.py -q
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import estimate_build as eb  # noqa: E402
import estimate_diff as ed  # noqa: E402
import kris_bot as kb  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "estimate_sibur_base.json"


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def send_chat_action(self, chat_id, action):
        pass


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """workspace, память и state.json - во временном каталоге. Drive выключен."""
    workspace = tmp_path / "workspace"
    memory = tmp_path / "memory"
    state_dir = tmp_path / "state"
    for p in (workspace, memory, state_dir):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(kb, "WORKSPACE", str(workspace))
    monkeypatch.setattr(kb, "MEMORY_DIR", str(memory))
    monkeypatch.setattr(kb, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(kb, "STATE_FILE", str(state_dir / "state.json"))
    monkeypatch.setattr(kb, "drive_or_none", lambda: None)
    kb.pending_queue.clear()
    yield workspace
    kb.pending_queue.clear()


def put_estimate(workspace: Path, stamp: str, payload) -> Path:
    target = Path(workspace) / "out" / stamp
    target.mkdir(parents=True, exist_ok=True)
    path = target / "estimate.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Сканер
# ---------------------------------------------------------------------------
def test_scan_finds_estimate_without_done(sandbox):
    path = put_estimate(sandbox, "20260910-120000", json.loads(FIXTURE.read_text()))
    assert kb.scan_estimates() == [path]


def test_scan_skips_estimate_with_done(sandbox):
    path = put_estimate(sandbox, "20260910-120000", json.loads(FIXTURE.read_text()))
    kb.write_done(path, path.parent / "EST.xlsx", "https://drive/x", "fid-1")
    assert kb.scan_estimates() == []


def test_scan_empty_workspace_is_safe(sandbox):
    assert kb.scan_estimates() == []


# ---------------------------------------------------------------------------
# 2. Пост-хук: сборка xlsx и машинная строка
# ---------------------------------------------------------------------------
def test_post_hook_builds_xlsx_and_machine_message(sandbox):
    path = put_estimate(sandbox, "20260910-120000", json.loads(FIXTURE.read_text()))
    bot = FakeBot()

    asyncio.run(kb.publish_estimates(bot, kb.ALLOWED_USER))

    assert len(bot.sent) == 1
    _, text = bot.sent[0]
    assert text.startswith("Смета: EST_")
    assert "1 738 800 ₽" in text
    assert "1 440 000" in text
    assert kb.DRIVE_NOT_CONFIGURED in text
    assert "Без ставки" not in text  # у фикстуры все ставки есть

    xlsx = list(path.parent.glob("EST_*.xlsx"))
    assert len(xlsx) == 1
    assert len(xlsx[0].name) <= 60
    assert xlsx[0].name.isascii()
    # копия ушла в память и стоит отметка .done
    assert (Path(kb.MEMORY_DIR) / "estimates" / xlsx[0].name).exists()
    mark = json.loads(kb.done_path(path).read_text(encoding="utf-8"))
    assert mark["xlsx"].endswith(xlsx[0].name)
    assert kb.scan_estimates() == []


def test_estimate_filename_is_ascii_and_short():
    name = kb.estimate_filename(
        "СИБУР", "Мелиорация - продуктовый AI-ролик 60-75 сек длинное название"
    )
    assert name.startswith("EST_sibur_")
    assert name.endswith(".xlsx")
    assert name.isascii() and len(name) <= 60


def test_machine_message_lists_missing_roles():
    text = kb.build_estimate_message(
        "EST_x.xlsx", 1738800, 1440000, "https://drive/x", ["storyboard", "vfx"]
    )
    assert "итого 1 738 800 ₽" in text
    assert "Без ставки: storyboard, vfx - итог неполный" in text


# ---------------------------------------------------------------------------
# 3. Невалидный JSON: повторный вызов модели и лимит
# ---------------------------------------------------------------------------
def test_broken_json_retries_and_stops_at_limit(sandbox, monkeypatch):
    put_estimate(sandbox, "20260910-120000", "{это не json")
    prompts = []

    async def fake_run_claude(prompt, session_id=None):
        prompts.append(prompt)
        return ("починила", "sid-1", None)

    monkeypatch.setattr(kb, "run_claude", fake_run_claude)

    bot = FakeBot()
    asyncio.run(kb.publish_estimates(bot, kb.ALLOWED_USER))

    assert len(prompts) == kb.ESTIMATE_CALLS_MAX
    assert "estimate.json невалиден" in prompts[0]
    assert "Исправь файл, ничего больше не делай" in prompts[0]
    assert "не читается estimate.json" in prompts[0]  # текст ошибки в промпте
    assert kb.load_state()["estimate_calls"] == kb.ESTIMATE_CALLS_MAX
    assert any("Три попытки исчерпаны" in t for _, t in bot.sent)


def test_fixed_json_on_second_pass_is_built(sandbox, monkeypatch):
    path = put_estimate(sandbox, "20260910-120000", "{сломано")

    async def fake_run_claude(prompt, session_id=None):
        path.write_text(FIXTURE.read_text(), encoding="utf-8")
        return ("поправила", "sid-1", None)

    monkeypatch.setattr(kb, "run_claude", fake_run_claude)

    bot = FakeBot()
    asyncio.run(kb.publish_estimates(bot, kb.ALLOWED_USER))

    assert any("1 738 800 ₽" in t for _, t in bot.sent)
    assert kb.load_state()["estimate_calls"] == 1


# ---------------------------------------------------------------------------
# 4. Детекторы входа
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("вот бриф https://docs.google.com/document/d/AbC/edit посмотри", "https://docs.google.com/document/d/AbC/edit"),
        ("http://example.com/brief.pdf", "http://example.com/brief.pdf"),
        ("посмотри (https://example.com/x).", "https://example.com/x"),
        ("просто текст без ссылок", None),
        ("", None),
        (None, None),
    ],
)
def test_find_url(text, expected):
    assert kb.find_url(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("сверь смету", True),
        ("Сверь смету, я поправил дни", True),
        ("  СВЕРЬ СМЕТУ  ", True),
        ("сверь, пожалуйста, смету", False),
        ("посчитай смету", False),
        ("", False),
    ],
)
def test_is_reconcile(text, expected):
    assert kb.is_reconcile(text) is expected


def test_compose_intake_prompt_keeps_owner_note():
    assert kb.compose_intake_prompt("БЛОК", "срочно") == "БЛОК\n\nсрочно"
    assert kb.compose_intake_prompt("БЛОК", "  ") == "БЛОК"


def test_reconcile_prompt_has_rules_file_and_diff():
    text = kb.reconcile_prompt(["POST Camera tracking: было 0 дн → стало 2 дн"])
    assert "Антон поправил смету руками" in text
    assert "правила расчёта" in text
    assert "✅ Антон" in text
    assert "- POST Camera tracking: было 0 дн → стало 2 дн" in text


def test_build_reconcile_prompt_without_drive(sandbox, monkeypatch):
    path = put_estimate(sandbox, "20260910-120000", json.loads(FIXTURE.read_text()))
    kb.write_done(path, path.parent / "EST.xlsx", "link", "fid-1")
    prompt, error = kb.build_reconcile_prompt("сверь смету")
    assert prompt is None
    assert error == kb.DRIVE_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# 5. Диф правленой сметы
# ---------------------------------------------------------------------------
def test_diff_detects_manual_days_and_new_total(tmp_path):
    base = json.loads(FIXTURE.read_text())
    edited = json.loads(FIXTURE.read_text())
    edited["lines"].append({"role": "camera_tracking", "qty": 1, "days": 2})

    out = tmp_path / "edited.xlsx"
    result = eb.build(edited, eb.DEFAULT_TEMPLATE, out)
    assert result.total == 1787100.0

    lines = ed.diff(base, out)
    assert "POST Camera tracking: было 0 дн → стало 2 дн" in lines
    assert "POST Camera tracking: было 0 шт → стало 1 шт" in lines
    assert "итого: 1 738 800 → 1 787 100" in lines


def test_diff_of_untouched_estimate_is_empty(tmp_path):
    base = json.loads(FIXTURE.read_text())
    out = tmp_path / "same.xlsx"
    eb.build(base, eb.DEFAULT_TEMPLATE, out)
    assert ed.diff(base, out) == []


def test_format_money():
    assert ed.format_money(1738800.0) == "1 738 800"
    assert ed.format_money(0) == "0"


# ---------------------------------------------------------------------------
# Вход: крупный документ и диспетчеризация build_owner_prompt
# ---------------------------------------------------------------------------
class FakeDoc:
    def __init__(self, size, name="brief.pdf"):
        self.file_id = "fid"
        self.file_size = size
        self.file_name = name


class FakeMessage:
    def __init__(self, text=None, caption=None, document=None):
        self.text = text
        self.caption = caption
        self.document = document


def test_oversized_document_is_refused_without_model(sandbox, monkeypatch):
    called = {"intake": False}

    async def boom(*a, **kw):
        called["intake"] = True

    monkeypatch.setattr(kb, "intake_document", boom)
    bot = FakeBot()
    msg = FakeMessage(caption="посчитай", document=FakeDoc(21 * 1024 * 1024))

    prompt = asyncio.run(kb.build_owner_prompt(bot, kb.ALLOWED_USER, msg))

    assert prompt is None
    assert called["intake"] is False
    assert "слишком большой" in bot.sent[0][1]


def test_document_goes_through_intake(sandbox, monkeypatch):
    seen = {}

    async def fake_intake(bot, document, note=None):
        seen["name"] = document.file_name
        seen["note"] = note
        return "=== ВХОДЯЩИЙ БРИФ ===\n...\n\nпосчитай"

    monkeypatch.setattr(kb, "intake_document", fake_intake)
    bot = FakeBot()
    msg = FakeMessage(caption="посчитай", document=FakeDoc(1024))

    prompt = asyncio.run(kb.build_owner_prompt(bot, kb.ALLOWED_USER, msg))

    assert seen == {"name": "brief.pdf", "note": "посчитай"}
    assert prompt.startswith("=== ВХОДЯЩИЙ БРИФ ===")
    assert bot.sent == []


def test_link_goes_through_intake_and_plain_text_does_not(sandbox, monkeypatch):
    calls = []

    async def fake_link(url, note=None):
        calls.append((url, note))
        return "БЛОК ССЫЛКИ"

    monkeypatch.setattr(kb, "intake_link", fake_link)
    bot = FakeBot()

    link_msg = FakeMessage(text="вот бриф https://docs.google.com/document/d/AbC/edit")
    assert asyncio.run(kb.build_owner_prompt(bot, kb.ALLOWED_USER, link_msg)) == "БЛОК ССЫЛКИ"
    assert calls == [("https://docs.google.com/document/d/AbC/edit", link_msg.text)]

    plain = FakeMessage(text="привет, что по проекту")
    assert asyncio.run(kb.build_owner_prompt(bot, kb.ALLOWED_USER, plain)) == plain.text
    assert len(calls) == 1
