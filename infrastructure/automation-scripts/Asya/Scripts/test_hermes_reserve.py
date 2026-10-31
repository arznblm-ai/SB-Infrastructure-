"""Тесты резерва Hermes (hermes_reserve.py): переход по квоте, возврат через 30 мин,
метка один раз, разовый фолбэк, force/fake-крючки, страж путей, копии у ботов.
Без сети: hermes и claude подменены."""

import asyncio
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hermes_reserve as hr  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INFRA = os.path.dirname(os.path.dirname(HERE))
COPIES = [
    os.path.join(INFRA, "Rocky", "Scripts", "hermes_reserve.py"),
    os.path.join(INFRA, "Travel Agent", "Scripts", "group_bot", "hermes_reserve.py"),
    os.path.join(INFRA, "Kris", "Scripts", "hermes_reserve.py"),
    os.path.join(INFRA, "Financial Coach", "bot", "accountant", "hermes_reserve.py"),
]


def fake_hermes(tmp_path, reply="ответ резерва", rc=0):
    path = tmp_path / "hermes"
    args_log = tmp_path / "hermes-args.json"
    path.write_text(
        "#!%s\nimport json, sys\njson.dump(sys.argv[1:], open(%r, 'w'))\n"
        "print(%r)\nsys.stderr.write('\\nsession_id: test_sid\\n')\nsys.exit(%d)\n"
        % (sys.executable, str(args_log), reply, rc))
    path.chmod(0o755)
    return str(path), args_log


@pytest.fixture
def reserve(tmp_path, monkeypatch):
    for key in ("T_FORCE_ENGINE", "T_FAKE_QUOTA"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("T_PRIMARY_ENGINE", "claude")  # старая матрица; hermes-режим — ниже
    ws = tmp_path / "ws"
    ws.mkdir()
    r = hr.Reserve("T", str(tmp_path / "state" / "engine.json"), str(tmp_path / "home"), str(ws),
                   read_roots=[str(ws)], toolsets=["file"])
    r.hermes_bin, r.args_log = fake_hermes(tmp_path)
    r.provider = "test"
    r.notices = []

    async def notify(text):
        r.notices.append(text)

    r.notify = notify
    return r


def claude(result):
    calls = []

    async def fn():
        calls.append(1)
        return result

    fn.calls = calls
    return fn


def run(coro):
    return asyncio.run(coro)


def test_claude_ok_stays_on_claude(reserve):
    fn = claude(("привет", None))
    assert run(reserve.call("p", fn)) == ("привет", None)
    assert reserve.engine() == "claude" and reserve.notices == []


def test_quota_switches_to_hermes_and_marks_once(reserve):
    fn = claude(("", "quota"))
    assert run(reserve.call("p", fn, now=1000)) == ("ответ резерва", None)
    assert reserve.engine() == "hermes"
    assert reserve.notices == [hr.NOTICE_RESERVE]
    # дальше 30 минут Claude не трогаем, метки не повторяем
    assert run(reserve.call("p2", fn, now=1000 + 60)) == ("ответ резерва", None)
    assert len(fn.calls) == 1 and reserve.notices == [hr.NOTICE_RESERVE]


def test_probe_after_30_min_returns_to_claude(reserve):
    run(reserve.call("p", claude(("", "quota")), now=1000))
    still = claude(("", "quota"))
    assert run(reserve.call("p", still, now=1000 + hr.PROBE_SECONDS + 1)) == ("ответ резерва", None)
    assert len(still.calls) == 1 and reserve.engine() == "hermes"
    assert reserve.notices == [hr.NOTICE_RESERVE]
    back = claude(("снова claude", None))
    t = 1000 + 2 * hr.PROBE_SECONDS + 2
    assert run(reserve.call("p", back, now=t)) == ("снова claude", None)
    assert reserve.engine() == "claude"
    assert reserve.notices == [hr.NOTICE_RESERVE, hr.NOTICE_BACK]


def test_auth_is_sticky_too(reserve):
    run(reserve.call("p", claude(("", "auth")), now=1))
    assert reserve.engine() == "hermes"


def test_one_off_error_does_not_switch(reserve):
    assert run(reserve.call("p", claude(("", "error")))) == ("ответ резерва", None)
    assert reserve.engine() == "claude" and reserve.notices == []


def test_timeout_is_not_rerouted(reserve):
    assert run(reserve.call("p", claude(("", "timeout")))) == ("", "timeout")


def test_claude_exception_falls_back(reserve):
    async def boom():
        raise RuntimeError("x")

    assert run(reserve.call("p", boom)) == ("ответ резерва", None)


def test_hermes_failure_is_error(reserve, tmp_path):
    reserve.hermes_bin, _ = fake_hermes(tmp_path, reply="", rc=1)
    assert run(reserve.call("p", claude(("", "quota")))) == ("", "error")


def test_force_engine_and_fake_quota(reserve, monkeypatch):
    monkeypatch.setenv("T_FORCE_ENGINE", "hermes")
    fn = claude(("x", None))
    assert run(reserve.call("p", fn)) == ("ответ резерва", None)
    assert fn.calls == [] and reserve.notices == [] and reserve.engine() == "claude"
    monkeypatch.delenv("T_FORCE_ENGINE")
    monkeypatch.setenv("T_FAKE_QUOTA", "1")
    assert run(reserve.call("p", fn, now=5)) == ("ответ резерва", None)
    assert fn.calls == [] and reserve.engine() == "hermes"


def test_command_profile_and_env(reserve, tmp_path):
    img = os.path.join(reserve.workspace, "inbox", "1-photo.jpg")
    os.makedirs(os.path.dirname(img))
    open(img, "wb").close()
    run(reserve.run_hermes("Вложение: inbox/1-photo.jpg (фото)"))
    args = json.load(open(str(reserve.args_log)))
    assert args[:2] == ["chat", "-q"] and "-Q" in args
    assert args[args.index("-t") + 1] == "file"
    assert args[args.index("--image") + 1] == os.path.realpath(img)
    assert args[args.index("--source") + 1] == "tool"
    home = reserve.hermes_home
    assert oct(os.stat(home).st_mode & 0o777) == "0o700"
    cfg = open(os.path.join(home, "config.yaml")).read()
    assert "memory_enabled: false" in cfg and "terminal" not in cfg
    assert "show_reasoning: false" in cfg and "honcho" not in cfg
    assert "inline_diffs: false" in cfg
    env = reserve.env()
    assert env["HERMES_HOME"] == home and "TELEGRAM_BOT_TOKEN" not in env
    assert env["HERMES_WRITE_SAFE_ROOT"]  # без write_roots — несуществующий корень


def test_mcp_toolsets_added(reserve):
    reserve.mcp_servers = {"gcal": {"command": "/x"}, "todoist": {"command": "/y"}}
    assert reserve.toolsets_for() == ["file", "mcp-gcal", "mcp-todoist"]
    assert "mcp_servers:" in hr._yaml(reserve.config())


def test_logs_engine_line(reserve, caplog):
    caplog.set_level("INFO")
    run(reserve.run_hermes("p"))
    assert any("engine=hermes reason=reserve model=" in r.getMessage() and "cost_usd=" in r.getMessage()
               for r in caplog.records)


# --- PRIMARY_ENGINE=hermes (разворот 27.10) ----------------------------------
@pytest.fixture
def hreserve(reserve, monkeypatch):
    monkeypatch.delenv("T_PRIMARY_ENGINE")  # дефолт — hermes
    return reserve


def test_primary_default_is_hermes(hreserve):
    assert hreserve.primary() == "hermes"
    fn = claude(("x", None))
    assert run(hreserve.call("p", fn, user_text="привет")) == ("ответ резерва", None)
    assert fn.calls == [] and hreserve.notices == [] and hreserve.engine() == "claude"
    assert (hreserve.last_engine, hreserve.last_reason, hreserve.last_sid) == ("hermes", "primary", "test_sid")


def test_hermes_failure_escalates_to_claude_once(hreserve, tmp_path):
    hreserve.hermes_bin, _ = fake_hermes(tmp_path, reply="", rc=1)
    fn = claude(("ответ claude", None))
    assert run(hreserve.call("p", fn)) == ("ответ claude", None)
    assert len(fn.calls) == 1 and hreserve.last_reason == "escalate_fail"
    assert hreserve.notices == [] and not os.path.exists(hreserve.state_path)


def test_unknown_toolsets_only_escalates(hreserve, tmp_path):
    hreserve.hermes_bin, _ = fake_hermes(tmp_path, reply="Warning: Unknown toolsets: mcp-x")
    assert run(hreserve.call("p", claude(("c", None)))) == ("c", None)


def test_both_down_gives_claude_quota(hreserve, tmp_path):
    hreserve.hermes_bin, _ = fake_hermes(tmp_path, reply="", rc=1)
    assert run(hreserve.call("p", claude(("", "quota")))) == ("", "quota")


@pytest.mark.parametrize("text", ["Клод, посчитай", "спроси у клоду", "с клодом", "ask CLAUDE", "(claude)"])
def test_word_escalates(hreserve, text):
    fn = claude(("ответ claude", None))
    assert run(hreserve.call("p", fn, user_text=text)) == ("ответ claude", None)
    assert len(fn.calls) == 1 and hreserve.last_reason == "escalate_word"


@pytest.mark.parametrize("text", ["приклодка", "xclaude", "", None])
def test_word_not_matched(hreserve, text):
    fn = claude(("c", None))
    run(hreserve.call("p", fn, user_text=text))
    assert fn.calls == [] and hreserve.last_engine == "hermes"


def test_word_escalation_claude_quota_falls_to_hermes(hreserve):
    assert run(hreserve.call("p", claude(("", "quota")), user_text="клод?")) == ("ответ резерва", None)
    assert hreserve.last_reason == "reserve" and hreserve.notices == []


def test_word_ignored_in_prompt_without_user_text(hreserve):
    fn = claude(("c", None))
    run(hreserve.call("CLAUDE.md говорит клод", fn))
    assert fn.calls == []


def test_hermes_prompt_and_resume_passed(hreserve):
    run(hreserve.call("claude-промпт", claude(("c", None)), hermes_prompt="h-промпт", hermes_resume="sid1"))
    args = json.load(open(str(hreserve.args_log)))
    assert args[args.index("--resume") + 1] == "sid1"
    assert args[2].endswith("h-промпт")


def test_force_claude_overrides_primary(hreserve, monkeypatch):
    monkeypatch.setenv("T_FORCE_ENGINE", "claude")
    fn = claude(("c", None))
    assert run(hreserve.call("p", fn)) == ("c", None) and len(fn.calls) == 1


def test_reason_in_hermes_log(hreserve, caplog):
    caplog.set_level("INFO")
    run(hreserve.call("p", claude(("c", None))))
    assert any("engine=hermes reason=primary model=" in r.getMessage() for r in caplog.records)


# --- страж путей ------------------------------------------------------------
POLICY = {"cwd": "/w", "read_roots": ["/w", "/vault"], "write_roots": ["/vault/mem"],
          "deny": ["/vault/context/psychology"], "deny_globs": ["*.env", "/vault/context/*псих*"]}


def verdict(tool, **args):
    return hr.guard_verdict({"tool_name": tool, "tool_input": args, "cwd": "/w"}, POLICY)


def test_guard_reads():
    assert verdict("read_file", path="/vault/a.md") is None
    assert verdict("read_file", path="inbox/x.jpg") is None
    assert verdict("read_file", path="/root/.ssh/config")
    assert verdict("read_file", path="/vault/context/psychology/t.md")
    assert verdict("read_file", path="/vault/x/.env")
    assert verdict("read_file", path="/vault/context/психологический профиль.md")
    assert verdict("search_files", pattern="x") is None
    assert verdict("search_files", pattern="x", path="/etc")


def test_guard_writes():
    assert verdict("write_file", path="/vault/mem/f.md", content="x") is None
    assert verdict("write_file", path="/vault/a.md", content="x")
    assert verdict("patch", mode="patch", patch="*** Begin Patch\n*** Update File: /vault/a.md\n")
    assert verdict("patch", mode="patch", patch="*** Update File: /vault/mem/f.md\n") is None
    ro = dict(POLICY, write_roots=[])
    assert hr.guard_verdict({"tool_name": "write_file", "tool_input": {"path": "/w/x"}}, ro)


def test_guard_script_blocks(tmp_path):
    r = hr.Reserve("G", str(tmp_path / "s.json"), str(tmp_path / "h"), str(tmp_path),
                   read_roots=[str(tmp_path)])
    r.ensure_home()
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(r.hermes_home, "guard.py")],
                         input=json.dumps({"tool_name": "read_file", "tool_input": {"path": "/etc/passwd"}}),
                         capture_output=True, text=True).stdout
    assert json.loads(out)["decision"] == "block"
    ok = subprocess.run([sys.executable, os.path.join(r.hermes_home, "guard.py")],
                        input=json.dumps({"tool_name": "read_file", "tool_input": {"path": str(tmp_path / "a")}}),
                        capture_output=True, text=True).stdout
    assert ok.strip() == ""


def test_copies_identical():
    ref = hashlib.sha256(open(os.path.join(HERE, "hermes_reserve.py"), "rb").read()).hexdigest()
    for path in COPIES:
        assert os.path.exists(path), path
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == ref, path


def test_unknown_toolsets_warning_stripped(reserve, tmp_path):
    reserve.hermes_bin, _ = fake_hermes(tmp_path, reply="Warning: Unknown toolsets: mcp-gcal\nготово")
    assert run(reserve.run_hermes("p")) == ("готово", None)


MARCO_1030 = [  # сессия 20261030_134053_0af4d6: ответ, потом два хода verify-on-stop
    ("user", "[Резервный режим…] когда билеты на Скриптонита?", None),
    ("assistant", "", '[{"name": "read_file"}]'),
    ("tool", '{"content": "..."}', None),
    ("assistant", "", '[{"name": "patch"}]'),
    ("tool", '{"success": true}', None),
    ("assistant", "Похоже, продажа уже открылась.\n• ShowGo.uz, от 300 000 сумов", None),
    ("assistant", "", '[{"name": "read_file"}]'),
    ("tool", '{"content": "..."}', None),
    ("assistant", "Правка проверена настолько, насколько это возможно без оболочки.", None),
    ("assistant", "", '[{"name": "read_file"}]'),
    ("tool", '{"content": "..."}', None),
    ("assistant", "Повторный прогон невозможен по той же причине.", None),
]


def test_pick_reply_ignores_verify_turns():
    assert hr.pick_reply(MARCO_1030).startswith("Похоже, продажа уже открылась.")


def test_pick_reply_after_last_user_on_resume():
    rows = [("user", "старый", None), ("assistant", "старый ответ", None)] + MARCO_1030
    assert hr.pick_reply(rows).startswith("Похоже")
    assert hr.pick_reply([("user", "x", None), ("assistant", "", "[{}]")]) == ""


def test_reply_taken_from_state_db(reserve, tmp_path):
    import sqlite3
    os.makedirs(reserve.hermes_home, exist_ok=True)
    con = sqlite3.connect(os.path.join(reserve.hermes_home, "state.db"))
    con.execute("create table messages (id integer primary key, session_id text, role text, "
                "content text, tool_calls text, active integer default 1)")
    con.executemany("insert into messages (session_id, role, content, tool_calls) "
                    "values ('test_sid', ?, ?, ?)", MARCO_1030)
    con.commit()
    con.close()
    reserve.hermes_bin, _ = fake_hermes(tmp_path, reply="Повторный прогон невозможен по той же причине.")
    text, err = run(reserve.run_hermes("p"))
    assert err is None and text.startswith("Похоже, продажа уже открылась.")


def test_verify_on_stop_disabled(reserve):
    assert reserve.config()["agent"]["verify_on_stop"] is False
    assert reserve.env()["HERMES_VERIFY_ON_STOP"] == "0"
