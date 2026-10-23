#!/usr/bin/env python3
"""Проверка прав Роки на НАСТОЯЩЕМ claude CLI без модели и без квоты.

Адаптация `infrastructure/Asya/Scripts/permcheck.py`: локальный фейковый
Anthropic API (127.0.0.1) вместо модели по сценарию шлёт вызовы тулз, CLI
исполняет их своим движком прав. Муляж: workspace (cwd, inbox с «фото»),
data (журнал, профили, .env-ловушка), home/.config/rocky/.env, файл вне
разрешённых каталогов. Команда - РОВНО `rocky_bot.build_claude_cmd`, пути
подменены на муляж. MCP у Роки нет, поэтому фейковых MCP-серверов тоже нет.

Ожидания:
  Read/Glob/Grep inbox и data            - можно (без утечки секретов);
  Read .env (в data и в ~/.config)       - нельзя;
  Read файла вне workspace/data          - нельзя;
  Write/Edit/Bash/WebFetch/WebSearch     - нельзя (тулз нет в сессии).

Запуск: python3 permcheck.py [--claude /root/.local/bin/claude] [--keep]
Код возврата 0 - все ожидания совпали.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rocky_bot  # noqa: E402

SECRET = "ZEBRA42"
# Валидный PNG 1x1: Read проверяет, что картинка - картинка.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def build_mock(root):
    home = os.path.join(root, "root")
    ws = os.path.join(root, "workspace")
    data = os.path.join(root, "data")
    files = {
        os.path.join(ws, "inbox", "2026-10-22", "1-note.txt"): "inbox-visible",
        os.path.join(data, "journal", "2026-10-22.jsonl"): "journal-visible",
        os.path.join(data, "profiles.json"): "profiles-visible",
        os.path.join(data, ".env"): "dotenv",
        os.path.join(data, "secrets.env"): "envfile",
        os.path.join(home, ".config", "rocky", ".env"): "config",
        os.path.join(home, ".config", "rocky", "state.json"): "configstate",
        os.path.join(root, "outside", "secret.txt"): "outside",
    }
    for path, tag in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("%s %s\n" % (SECRET, tag))
    with open(os.path.join(ws, "inbox", "2026-10-22", "1-photo.png"), "wb") as f:
        f.write(PNG_1X1)
    with open(os.path.join(ws, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write("# permcheck\n")
    return home, ws, data


def scenario(root, home, ws, data):
    """[(тулза, вход, ожидание)]: allow - успех, deny - отказ, без утечки секретных тегов."""
    leak = ("dotenv", "envfile", "config", "configstate", "outside")
    j = os.path.join
    return leak, [
        ("Read", {"file_path": j(ws, "inbox", "2026-10-22", "1-photo.png")}, "allow"),
        ("Read", {"file_path": j(ws, "inbox", "2026-10-22", "1-note.txt")}, "allow"),
        ("Read", {"file_path": j(data, "journal", "2026-10-22.jsonl")}, "allow"),
        ("Read", {"file_path": j(data, "profiles.json")}, "allow"),
        ("Glob", {"pattern": "**/*", "path": data}, "allow-noleak"),
        ("Grep", {"pattern": SECRET, "path": data, "output_mode": "content"}, "allow-noleak"),
        ("Grep", {"pattern": SECRET, "path": ws, "output_mode": "content"}, "allow-noleak"),
        ("Read", {"file_path": j(data, ".env")}, "deny"),
        ("Read", {"file_path": j(data, "secrets.env")}, "deny"),
        ("Read", {"file_path": j(home, ".config", "rocky", ".env")}, "deny"),
        ("Read", {"file_path": j(home, ".config", "rocky", "state.json")}, "deny"),
        ("Read", {"file_path": j(root, "outside", "secret.txt")}, "deny"),
        ("Grep", {"pattern": SECRET, "path": home, "output_mode": "content"}, "deny-or-noleak"),
        ("Write", {"file_path": j(data, "profiles.json"), "content": "x"}, "deny"),
        ("Write", {"file_path": j(ws, "hack.md"), "content": "x"}, "deny"),
        ("Edit", {"file_path": j(data, "profiles.json"), "old_string": SECRET, "new_string": "x"}, "deny"),
        ("Bash", {"command": "echo hi"}, "deny"),
        ("WebFetch", {"url": "https://example.com", "prompt": "x"}, "deny"),
        ("WebSearch", {"query": "x"}, "deny"),
    ]


def make_handler(steps, results):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, payload):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):
            self._json({"data": [], "has_more": False})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
                return self._json({"input_tokens": 10})
            done = []
            for m in body.get("messages") or []:
                if isinstance(m.get("content"), list):
                    done += [c for c in m["content"] if c.get("type") == "tool_result"]
            if body.get("tools") and done and len(done) > len(results):
                last = done[-1]
                content = last.get("content")
                if isinstance(content, list):
                    content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
                results.append((bool(last.get("is_error")), str(content)))
            n = len(done)
            if body.get("tools") and n < len(steps):
                name, inp, _exp = steps[n]
                block = {"type": "tool_use", "id": "toolu_%03d" % n, "name": name, "input": inp}
                stop = "tool_use"
            else:
                block, stop = {"type": "text", "text": "done"}, "end_turn"
            msg = {"id": "msg_%d" % n, "type": "message", "role": "assistant", "model": body.get("model", "x"),
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": 1, "output_tokens": 1}}
            if not body.get("stream"):
                msg.update(content=[block], stop_reason=stop)
                return self._json(msg)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()

            def ev(kind, data):
                self.wfile.write(("event: %s\ndata: %s\n\n" % (kind, json.dumps(data))).encode())

            ev("message_start", {"type": "message_start", "message": msg})
            if block["type"] == "text":
                ev("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}})
                ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": block["text"]}})
            else:
                ev("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": dict(block, input={})})
                ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "input_json_delta",
                                                     "partial_json": json.dumps(block["input"])}})
            ev("content_block_stop", {"type": "content_block_stop", "index": 0})
            ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 1}})
            ev("message_stop", {"type": "message_stop"})

    return Handler


def judge(expect, is_error, content, leak_tags):
    leaked = any(("%s %s" % (SECRET, tag)) in content for tag in leak_tags)
    if expect == "allow":
        return not is_error
    if expect == "allow-noleak":
        return not is_error and not leaked
    if expect == "deny-or-noleak":
        return is_error or not leaked
    return is_error and not leaked  # deny


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", default=rocky_bot.CLAUDE_BIN)
    ap.add_argument("--keep", action="store_true", help="не удалять муляж")
    args = ap.parse_args()

    root = os.path.realpath(tempfile.mkdtemp(prefix="rocky-permcheck-"))
    home, ws, data = build_mock(root)
    leak_tags, steps = scenario(root, home, ws, data)
    results = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(steps, results))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    rocky_bot.CLAUDE_BIN = args.claude
    cmd = rocky_bot.build_claude_cmd("permcheck", model="sonnet", data_dir=data, home_dir=home)
    env = {
        "HOME": os.environ.get("HOME", "/root"),
        "PATH": os.environ.get("PATH", ""),
        "LANG": "en_US.UTF-8",
        "ANTHROPIC_API_KEY": "sk-ant-permcheck-fake",  # не настоящий ключ: API - локальный муляж
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % server.server_address[1],
    }
    try:
        proc = subprocess.run(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=300)
    finally:
        server.shutdown()
    version = subprocess.run([args.claude, "--version"], capture_output=True, text=True).stdout.strip()
    print("claude %s, rc=%s, исходов %d/%d" % (version, proc.returncode, len(results), len(steps)))

    failed = 0
    for i, (name, inp, expect) in enumerate(steps):
        target = (inp.get("file_path") or inp.get("path") or inp.get("command") or inp.get("url")
                  or inp.get("query") or "").replace(root, "<mock>")
        if i >= len(results):
            print("✗ %-10s %s — исхода нет" % (name, target))
            failed += 1
            continue
        is_error, content = results[i]
        ok = judge(expect, is_error, content, leak_tags)
        failed += 0 if ok else 1
        print("%s %-10s %-6s %s" % ("✓" if ok else "✗", name, expect.split("-")[0], target))
        if not ok:
            print("    -> %s" % content.replace(root, "<mock>")[:300])
    if proc.returncode != 0:
        print("stderr: " + proc.stderr[-500:].replace(root, "<mock>"))
    if args.keep:
        print("муляж: " + root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    ok_all = not failed and proc.returncode == 0
    print("ИТОГ: %s" % ("все ожидания совпали" if ok_all else "расхождений %d" % failed))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
