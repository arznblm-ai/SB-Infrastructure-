#!/usr/bin/env python3
"""Тесты разбора звонков (calls_runner). Без сети, без claude, без Telegram."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import calls_runner as cr  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
JOURNAL_TEMPLATE = (
    "# Общая память\n\n## Сейчас в работе\n\n## Журнал\n\n<!-- комментарий -->\n\n"
    "- 2026-10-22 16:16 · tg · старое\n"
)


def full_transcript(n=40, call_id="abc-1"):
    lines = ["---", "date: 2026-10-23", "krisp_call_id: %s" % call_id, "---", "", "# 10:30 AM - Telemost", "",
             "## Транскрипт", ""]
    for i in range(n):
        lines += ["**Антон Розенблюм | 00:%02d**" % (i % 60), "реплика %d" % i, ""]
    return "\n".join(lines)


EMPTY = "---\ndate: 2026-10-23\n---\n\n# x\n\n## Транскрипт\n\n_Транскрипт не получен. Откройте Krisp._\n"


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    (vault / "transcripts").mkdir(parents=True)
    (vault / "meetings").mkdir()
    mem = vault / "infrastructure" / "Asya" / "memory"
    mem.mkdir(parents=True)
    (vault / cr.JOURNAL_REL).write_text(JOURNAL_TEMPLATE, encoding="utf-8")
    prompt = tmp_path / "calls_prompt.md"
    prompt.write_text("# Задание\nразбери звонок", encoding="utf-8")
    cfg = cr.Config(
        vault=str(vault), home=str(tmp_path / "home"), model="sonnet", claude_bin="/bin/false",
        max_per_tick=1, mcp_config=str(tmp_path / "mcp.json"), prompt_file=str(prompt),
        queue_file="", chatlog_dir=str(tmp_path / "chatlog"), chat_id="324186708",
    )
    return cfg


def add_transcript(cfg, name, text, age_min=60):
    path = os.path.join(cfg.vault, "transcripts", name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    stamp = NOW.timestamp() - age_min * 60
    os.utime(path, (stamp, stamp))
    return path


def model_reply(track="luch", confidence=0.9, report="Луч, звонок про тендер"):
    obj = {"track": track, "confidence": confidence, "secondary_tracks": [],
           "summary_path": "meetings/{meeting} {summary} тест – 2026-10-23.md",
           "kb_updates": [{"kb": track, "section": "1.0", "text": "x", "source": "[[s]]", "warn": False}],
           "tasks": [], "report_text": report}
    return "Готово.\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


class Fake:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.prompts, self.sent = [], []

    def claude(self, prompt, cfg):
        self.prompts.append(prompt)
        r = self.replies.pop(0) if self.replies else model_reply()
        return r if isinstance(r, tuple) else (r, None, 0.12)

    def send(self, cfg, text):
        self.sent.append(text)
        return True


def tick(cfg, fake, now=NOW, **kw):
    return cr.Tick(cfg, now=now, claude_fn=fake.claude, send_fn=fake.send, **kw).run()


NAME = "{meeting} 10 30 AM - Telemost – 2026-10-23.md"


# ---------------------------------------------------------------------------
# Кандидаты
# ---------------------------------------------------------------------------
def test_candidates_window_glob_and_mtime(env):
    add_transcript(env, NAME, full_transcript())
    add_transcript(env, "{meeting} fresh – 2026-10-24.md", full_transcript(call_id="f"), age_min=5)
    add_transcript(env, "{meeting} old – 2026-10-01.md", full_transcript(call_id="o"))
    add_transcript(env, "{course} {transcript} lecture – 2026-10-23.md", full_transcript(call_id="c"))
    names = [c["name"] for c in cr.scan_candidates(env, {"calls": {}}, NOW)]
    assert names == [NAME]
    names = [c["name"] for c in cr.scan_candidates(env, {"calls": {}}, NOW, since=datetime(2026, 9, 30).date())]
    assert names == ["{meeting} old – 2026-10-01.md", NAME]


def test_mtime_threshold_20_minutes(env):
    add_transcript(env, NAME, full_transcript(), age_min=19)
    assert cr.scan_candidates(env, {"calls": {}}, NOW) == []
    add_transcript(env, NAME, full_transcript(), age_min=21)
    assert len(cr.scan_candidates(env, {"calls": {}}, NOW)) == 1


def test_same_call_id_under_other_name_skipped(env):
    add_transcript(env, NAME, full_transcript(call_id="dup"))
    state = {"calls": {"other.md": {"status": "done", "krisp_call_id": "dup"}}}
    assert cr.scan_candidates(env, state, NOW) == []


def test_transcript_ready():
    assert cr.transcript_ready(EMPTY)[0] is False
    assert cr.transcript_ready(full_transcript(10))[0] is False
    assert cr.transcript_ready(full_transcript(30))[0] is True


# ---------------------------------------------------------------------------
# Пустой транскрипт: retry, потом «нет транскрипта»
# ---------------------------------------------------------------------------
def test_empty_transcript_retry_then_expiry(env):
    add_transcript(env, NAME, EMPTY)
    fake = Fake()
    tick(env, fake)
    state = cr.load_state(env)
    assert state["calls"][NAME]["status"] == cr.ST_RETRY
    assert fake.prompts == [] and fake.sent == []
    tick(env, fake, now=NOW + timedelta(hours=5))
    assert cr.load_state(env)["calls"][NAME]["status"] == cr.ST_RETRY
    tick(env, fake, now=NOW + timedelta(hours=24, minutes=1))
    state = cr.load_state(env)
    assert state["calls"][NAME]["status"] == cr.ST_NO_TRANSCRIPT
    assert any("нет транскрипта" in b["text"] for b in state["backlog"])
    # строка уходит со следующим отчётом
    add_transcript(env, "{meeting} next – 2026-10-24.md", full_transcript(call_id="n"))
    tick(env, fake, now=NOW + timedelta(hours=25))
    assert len(fake.sent) == 1 and "нет транскрипта" in fake.sent[0]
    assert cr.load_state(env)["backlog"] == []


def test_transcript_arrives_during_retry(env):
    add_transcript(env, NAME, EMPTY)
    fake = Fake()
    tick(env, fake)
    add_transcript(env, NAME, full_transcript())
    tick(env, fake, now=NOW + timedelta(hours=1))
    assert cr.load_state(env)["calls"][NAME]["status"] == cr.ST_DONE
    assert len(fake.prompts) == 1


# ---------------------------------------------------------------------------
# JSON модели
# ---------------------------------------------------------------------------
def test_extract_last_json_object():
    text = 'план {"a": 1}\nитог:\n```json\n{"track": "zekiro", "confidence": 0.8, "kb_updates": [{"kb": "zekiro"}], "report_text": "x"}\n```'
    obj = cr.extract_result_json(text)
    assert obj["track"] == "zekiro" and obj["kb_updates"] == [{"kb": "zekiro"}]
    two = '{"track": "luch", "report_text": "a"} потом {"track": "producer", "report_text": "b"}'
    assert cr.extract_result_json(two)["track"] == "producer"
    assert cr.extract_result_json("нет json") is None
    assert cr.extract_result_json('{"track": ') is None


def test_validate_and_normalize():
    r = cr.validate_result({"track": "Фриланс", "confidence": "0.95", "report_text": "ok", "secondary_tracks": ["луч", "x"]})
    assert r["track"] == "producer" and r["confidence"] == 0.95 and r["secondary_tracks"] == ["luch"]
    assert cr.normalize_track("мимо") == "none" and cr.normalize_track("Луч") == "luch"
    assert cr.normalize_track("что-то") is None


# ---------------------------------------------------------------------------
# Успех, идемпотентность, журнал, лог
# ---------------------------------------------------------------------------
def test_success_report_journal_log_and_idempotency(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake()
    tick(env, fake)
    state = cr.load_state(env)
    assert state["calls"][NAME]["status"] == cr.ST_DONE and state["calls"][NAME]["track"] == "luch"
    assert fake.sent == ["Луч, звонок про тендер"]
    journal = open(env.journal_path(), encoding="utf-8").read().splitlines()
    i = journal.index("<!-- комментарий -->")
    new = [ln for ln in journal if "📞" in ln]
    assert len(new) == 1 and " · tg · 📞 разбор звонка" in new[0] and "Луч" in new[0]
    assert journal.index(new[0]) < journal.index("- 2026-10-22 16:16 · tg · старое") and journal.index(new[0]) > i
    log = [json.loads(ln) for ln in open(env.log_file, encoding="utf-8")]
    assert log[-1]["cost_usd"] == 0.12 and log[-1]["track"] == "luch"
    # прompt содержит пути
    p = fake.prompts[0]
    for needle in (NAME, cr.TRACK_MAP_REL, cr.SUMMARIZER_REL, cr.KB_REL["zekiro"], "разбери звонок"):
        assert needle in p
    # повторный тик ничего не делает
    tick(env, fake, now=NOW + timedelta(minutes=10))
    assert len(fake.prompts) == 1 and len(fake.sent) == 1


def test_max_per_tick(env):
    for i in range(3):
        add_transcript(env, "{meeting} c%d – 2026-10-2%d.md" % (i, i), full_transcript(call_id=str(i)))
    fake = Fake()
    tick(env, fake)
    assert len(fake.prompts) == 1
    tick(env, fake, max_calls=5)
    assert len(fake.prompts) == 3


def test_errors_retry_twice_then_failure_message(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake([("", "timeout", None), ("", "empty", 0.05), ("", "error", None)])
    tick(env, fake)
    assert cr.load_state(env)["calls"][NAME]["status"] == cr.ST_ERROR and fake.sent == []
    tick(env, fake, now=NOW + timedelta(minutes=10))
    assert cr.load_state(env)["calls"][NAME]["error"] == "empty"
    tick(env, fake, now=NOW + timedelta(minutes=20))
    assert cr.load_state(env)["calls"][NAME]["status"] == cr.ST_FAILED
    assert len(fake.sent) == 1 and "Не смогла разобрать" in fake.sent[0]
    tick(env, fake, now=NOW + timedelta(minutes=30))
    assert len(fake.prompts) == 3


# ---------------------------------------------------------------------------
# Низкая уверенность: очередь, ответ Антона, 24 ч
# ---------------------------------------------------------------------------
def test_low_confidence_queue_then_forced_rerun(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake([model_reply("zekiro", 0.5, "Звонок про пилот, Зекиро или Луч")])
    tick(env, fake)
    assert cr.load_state(env)["calls"][NAME]["status"] == cr.ST_TRACK
    queue = json.load(open(env.queue_path(), encoding="utf-8"))
    item = queue["pending"][0]
    assert item["status"] == "pending" and item["track"] is None and item["guess"] == "zekiro"
    assert cr.QUESTION_TAIL in fake.sent[0]
    # без ответа ничего не перезапускается
    tick(env, fake, now=NOW + timedelta(minutes=10))
    assert len(fake.prompts) == 1
    # Ася записала ответ Антона
    item["track"] = "Луч"
    json.dump(queue, open(env.queue_path(), "w", encoding="utf-8"), ensure_ascii=False)
    fake.replies = [model_reply("luch", 1.0, "Луч: пилот")]
    tick(env, fake, now=NOW + timedelta(minutes=20))
    assert "ТРЕК ЗАДАН АНТОНОМ: luch" in fake.prompts[-1]
    assert item["summary_path"] in fake.prompts[-1]
    state = cr.load_state(env)
    assert state["calls"][NAME]["status"] == cr.ST_DONE and state["calls"][NAME]["track"] == "luch"
    assert json.load(open(env.queue_path(), encoding="utf-8"))["pending"][0]["status"] == "resolved"
    assert fake.sent[-1] == "Луч: пилот"
    tick(env, fake, now=NOW + timedelta(minutes=30))
    assert len(fake.prompts) == 2


def test_low_confidence_answer_none_closes_without_rerun(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake([model_reply("none", 0.3, "Похоже на личное")])
    tick(env, fake)
    queue = json.load(open(env.queue_path(), encoding="utf-8"))
    queue["pending"][0]["track"] = "мимо"
    json.dump(queue, open(env.queue_path(), "w", encoding="utf-8"), ensure_ascii=False)
    tick(env, fake, now=NOW + timedelta(minutes=10))
    assert len(fake.prompts) == 1
    assert cr.load_state(env)["calls"][NAME]["status"] == cr.ST_DONE


def test_low_confidence_24h_expiry_goes_to_next_report(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake([model_reply("zekiro", 0.4, "?")])
    tick(env, fake)
    tick(env, fake, now=NOW + timedelta(hours=24, minutes=5))
    state = cr.load_state(env)
    assert state["calls"][NAME]["status"] == cr.ST_DONE and state["calls"][NAME]["track"] == "none"
    assert json.load(open(env.queue_path(), encoding="utf-8"))["pending"][0]["status"] == "expired"
    assert any("мимо" in b["text"] for b in state["backlog"])
    add_transcript(env, "{meeting} next – 2026-10-25.md", full_transcript(call_id="n2"))
    tick(env, fake, now=NOW + timedelta(hours=24, minutes=15))
    assert "мимо" in fake.sent[-1] and "Также:" in fake.sent[-1]


def test_old_backlog_sent_standalone_after_6h(env):
    add_transcript(env, NAME, EMPTY)
    fake = Fake()
    tick(env, fake)
    tick(env, fake, now=NOW + timedelta(hours=25))
    assert fake.sent == []
    tick(env, fake, now=NOW + timedelta(hours=31, minutes=1))
    assert len(fake.sent) == 1 and "нет транскрипта" in fake.sent[0]


def test_merge_queue_keeps_answer_written_during_tick():
    ours = {"pending": [{"transcript": "a", "status": "pending", "track": None}]}
    disk = {"pending": [{"transcript": "a", "status": "pending", "track": "luch"}, {"transcript": "b", "status": "pending"}]}
    merged = cr.merge_queue(ours, disk)
    assert merged["pending"][0]["track"] == "luch" and [q["transcript"] for q in merged["pending"]] == ["a", "b"]


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
def snapshot(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[p] = open(p, "rb").read()
    return out


def test_dry_run_makes_no_writes_no_calls_no_sends(env, tmp_path, capsys):
    add_transcript(env, NAME, full_transcript())
    add_transcript(env, "{meeting} empty – 2026-10-23.md", EMPTY)
    before = snapshot(str(tmp_path))
    fake = Fake()
    actions = tick(env, fake, dry_run=True)
    assert snapshot(str(tmp_path)) == before
    assert fake.prompts == [] and fake.sent == []
    assert any("разбор" in a for a in actions) and any("не готов" in a for a in actions)
    assert NAME in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Права и конфиги
# ---------------------------------------------------------------------------
def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def test_allowed_write_paths_exact():
    allowed = cr.allowed_tools("/root/second-brain")
    writes = sorted(a for a in allowed if a.startswith(("Edit", "Write")))
    expected = []
    for p in ("meetings/**", "tasks/Луч*/*база*знаний*", "tasks/Зекиро/*база*знаний*", "tasks/Фриланс/*база*знаний*"):
        expected += ["Edit(//root/second-brain/%s)" % p, "Write(//root/second-brain/%s)" % p]
    assert writes == sorted(expected)
    assert [a for a in allowed if a.startswith("mcp__")] == ["mcp__todoist__todoist_add_task", "mcp__todoist__todoist_set_track"]
    assert not {"Read", "Glob", "Grep", "Edit", "Write", "Bash", "mcp__todoist"} & set(allowed)
    for rule in allowed + cr.deny_rules("/root/second-brain"):
        assert " " not in rule and "," not in rule


def test_kb_globs_match_kb_files():
    import fnmatch
    for track, rel in cr.KB_REL.items():
        assert any(fnmatch.fnmatch(rel, g) for g in cr.KB_WRITE_GLOBS_REL), track
    vault = os.path.dirname(os.path.dirname(ROOT))
    for rel in cr.KB_REL.values():
        assert os.path.exists(os.path.join(vault, rel)), rel
    assert os.path.exists(os.path.join(vault, cr.TRACK_MAP_REL))
    assert os.path.exists(os.path.join(vault, cr.SUMMARIZER_REL))


def test_claude_cmd_for_calls():
    cfg = cr.Config(vault="/root/second-brain", home="/var/lib/asya-calls", model="sonnet",
                    claude_bin="/root/.local/bin/claude", mcp_config="/var/lib/asya-calls/workspace/mcp.json")
    cmd = cr.build_claude_cmd("p", cfg)
    assert _flag(cmd, "--model") == "sonnet"
    assert _flag(cmd, "--tools") == "Read,Glob,Grep,Edit,Write"
    assert _flag(cmd, "--permission-mode") == "dontAsk"
    assert _flag(cmd, "--add-dir") == "/root/second-brain"
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    deny = _flag(cmd, "--disallowedTools").split(",")
    for rule in ("Bash", "WebFetch", "WebSearch", "mcp__gcal", "Read(//root/second-brain/context/psychology/**)", "Read(//**/.env)"):
        assert rule in deny
    with pytest.raises(ValueError):
        cr.allowed_tools("/Users/a/AI AGENT FOLDER")


def test_calls_settings_and_mcp_match_code():
    with open(os.path.join(ROOT, "config", "calls-settings.json"), encoding="utf-8") as f:
        assert json.load(f) == cr.settings_payload("/root/second-brain", "/root")
    with open(os.path.join(ROOT, "config", "calls-mcp.json"), encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    assert list(servers) == ["todoist"]


def test_units_and_prompt_and_footer():
    unit = open(os.path.join(HERE, "asya-calls.service"), encoding="utf-8").read()
    assert "Type=oneshot" in unit and "EnvironmentFile=/root/.config/asya/.env" in unit
    assert "ExecStart=/opt/asya/venv/bin/python /opt/asya/calls_runner.py" in unit
    assert "skip-permissions" not in unit
    timer = open(os.path.join(HERE, "asya-calls.timer"), encoding="utf-8").read()
    assert "OnCalendar=*:0/10" in timer and "Persistent=true" in timer
    prompt = open(os.path.join(ROOT, "config", "calls_prompt.md"), encoding="utf-8").read()
    for needle in ("report_text", "confidence", "⚠️", "названию встречи", "context/", "«Мои»", "без «ё»", "Связи"):
        assert needle in prompt
    footer = open(os.path.join(ROOT, "config", "workspace-footer.md"), encoding="utf-8").read()
    assert "calls-queue.json" in footer and '"track"' in footer


def test_runner_messages_have_no_yo_and_long_dash():
    for text in (cr.QUESTION_TAIL,):
        assert "ё" not in text and "—" not in text


# ---------------------------------------------------------------------------
# Устойчивый JSON, raw, done_unparsed (сбой 24.10 на звонке 17.10)
# ---------------------------------------------------------------------------
def _full_obj(**kw):
    obj = {"track": "zekiro", "confidence": 0.9, "secondary_tracks": [],
           "summary_path": "meetings/x.md", "kb_updates": [],
           "tasks": [{"title": "прислать прайс", "track": "zekiro", "due": ""}],
           "report_text": "Зекиро, цена за место"}
    obj.update(kw)
    return obj


def test_extract_fenced_json_with_nested_track_in_tasks():
    text = "Готово, записала.\n```json\n" + json.dumps(_full_obj(), ensure_ascii=False, indent=2) + "\n```\n"
    obj = cr.extract_result_json(text)
    assert obj["report_text"] == "Зекиро, цена за место" and obj["track"] == "zekiro"


def test_extract_text_around_json_and_braces_in_prose():
    text = ("Саммари: meetings/{meeting} {summary} тест – 2026-10-17.md\n"
            + json.dumps(_full_obj(), ensure_ascii=False) + "\nЕсли что, пиши. {не json}")
    assert cr.extract_result_json(text)["report_text"] == "Зекиро, цена за место"


def test_extract_from_cli_result_wrapper():
    inner = "Итог:\n```json\n" + json.dumps(_full_obj(), ensure_ascii=False) + "\n```"
    wrapper = json.dumps({"type": "result", "is_error": False, "result": inner, "total_cost_usd": 0.4})
    assert cr.extract_result_json(wrapper)["track"] == "zekiro"


def test_garbage_done_unparsed_no_rerun_fallback_sent_and_raw_saved(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake([("мусор без json", None, 0.44, "rc=0\n--- stdout ---\nRAW-MARKER")])
    tick(env, fake)
    entry = cr.load_state(env)["calls"][NAME]
    assert entry["status"] == cr.ST_UNPARSED
    assert len(fake.prompts) == 1
    assert len(fake.sent) == 1 and "не смогла собрать отчет" in fake.sent[0] and "meetings/" in fake.sent[0]
    assert entry["raw"] and entry["raw"] in fake.sent[0]
    assert open(entry["raw"], encoding="utf-8").read().endswith("RAW-MARKER")
    assert os.path.dirname(entry["raw"]) == env.raw_dir
    log = [json.loads(ln) for ln in open(env.log_file, encoding="utf-8")]
    assert log[-1]["error"] == "bad_json" and log[-1]["raw"] == entry["raw"]
    # следующие тики не трогают звонок
    tick(env, fake, now=NOW + timedelta(minutes=10))
    tick(env, fake, now=NOW + timedelta(minutes=20))
    assert len(fake.prompts) == 1 and len(fake.sent) == 1


def test_raw_saved_on_success(env):
    add_transcript(env, NAME, full_transcript())
    fake = Fake()
    tick(env, fake)
    files = os.listdir(env.raw_dir)
    assert len(files) == 1 and files[0].startswith("2026-10-24-") and files[0].endswith(".txt")
    assert "10-30-am-telemost" in files[0]


def test_fallback_message_style():
    assert "ё" not in "Разобрала звонок, но не смогла собрать отчет. Проверь саммари в meetings/ и базу проекта."
