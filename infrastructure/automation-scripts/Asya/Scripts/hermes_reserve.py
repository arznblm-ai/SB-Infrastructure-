"""Резерв Hermes для VPS-ботов (ADR-034, разворот 26.10; план
`tasks/Hermes/{automation} {plan} Hermes резерв ботов и агент vault – 2026-10-26.md`).

Эталон этого файла — `infrastructure/Asya/Scripts/hermes_reserve.py`. В Роки, Марко,
Крисе и бухгалтере лежат байт-в-байт копии (как media_intake.py); правишь эталон —
копируй во все пять, тест Аси сверяет хэши.

Как работает:
- Основной движок — `claude -p` бота, как раньше. Квота или нет логина (quota/auth) →
  этот и следующие вызовы идут в Hermes one-shot (`hermes chat -q -Q`) с тем же
  промптом; в чат уходит одна строка «перешёл на резерв». Разовая ошибка (error) →
  только этот вызов через Hermes, без перехода.
- Раз в 30 минут на резерве бот снова пробует настоящий вызов Claude (отдельного
  пробного запроса нет: на пустой квоте claude отвечает отказом за секунды, а
  живой ответ сразу идёт в дело). Ответил → назад на Claude, строка «вернулся».
- Hermes живёт в своём HERMES_HOME в каталоге бота (700): config.yaml, SOUL.md и
  страж путей guard.py пишутся отсюда при каждом вызове (идемпотентно). Память и
  Honcho выключены, gateway не поднимается, terminal нет. Права — тулсеты +
  страж (pre_tool_call хук): читать только read_roots, писать только write_roots,
  deny-префиксы и маски сверху. Писать мешает и встроенный HERMES_WRITE_SAFE_ROOT.
- Персона: Hermes сам подхватывает CLAUDE.md из cwd (workspace бота).
- Тест-крючки: <PREFIX>_FORCE_ENGINE=hermes|claude, <PREFIX>_FAKE_QUOTA=1
  (Claude не вызывается, сразу квотная ошибка).
"""

import asyncio
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone

HERMES_BIN = "/usr/local/bin/hermes"
DEFAULT_PROVIDER = "nous"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
PROBE_SECONDS = 30 * 60
STICKY_KINDS = ("quota", "auth")      # переход на резерв до возврата Claude
FALLBACK_KINDS = ("quota", "auth", "error")  # разовый уход в Hermes
SHARED_AUTH_DIR = "/root/.hermes/shared"  # общий Nous OAuth машины (vps-bots)

NOTICE_RESERVE = "Claude сейчас недоступен, отвечаю через запасную модель. Могу быть проще обычного"
NOTICE_BACK = "Claude снова на связи, вернулся на основную модель"

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
FILE_TOOLS = ("read_file", "write_file", "patch", "search_files", "vision_analyze")
WRITE_TOOLS = ("write_file", "patch")

PREAMBLE = (
    "[Резервный режим: ты работаешь не в Claude Code, а в Hermes. Правила из CLAUDE.md "
    "действуют полностью. Названия инструментов там от Claude Code, у тебя им "
    "соответствуют: Read = read_file, Glob/Grep = search_files, Edit = patch, "
    "Write = write_file, mcp__<сервер>__<тулза> = тулза MCP-сервера с тем же именем. "
    "Картинки смотри тулзой vision_analyze (первая картинка уже приложена к сообщению). "
    "Командной строки нет. Ответ пиши так же, как ответил бы бот.]\n\n"
)

SOUL = (
    "Ты движок Telegram-бота в резервном режиме. Своей личности у тебя нет: кто ты, "
    "как говоришь и что можешь, написано в CLAUDE.md рабочего каталога и в сообщении. "
    "Следуй им. Отвечай только текстом ответа, без пояснений о себе.\n"
)

_PATH_RE = re.compile(r"[^\s'\"()<>«»]+\.(?:jpe?g|png|webp|gif)\b", re.IGNORECASE)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(path, text, mode=0o600):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _write_if_changed(path, text, mode=0o600):
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    _write_atomic(path, text, mode)
    return True


def _yaml(value, indent=0):
    """Мини-YAML без зависимостей: dict/list/str/int/bool. Строки — в JSON-кавычках."""
    pad = "  " * indent
    lines = []
    if isinstance(value, dict):
        for key, val in value.items():
            if isinstance(val, (dict, list)) and val:
                lines.append("%s%s:" % (pad, key))
                lines.append(_yaml(val, indent + 1))
            else:
                lines.append("%s%s: %s" % (pad, key, _scalar(val)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                first = True
                for key, val in item.items():
                    lead = "- " if first else "  "
                    first = False
                    if isinstance(val, (dict, list)) and val:
                        lines.append("%s%s%s:" % (pad, lead, key))
                        lines.append(_yaml(val, indent + 2))
                    else:
                        lines.append("%s%s%s: %s" % (pad, lead, key, _scalar(val)))
            else:
                lines.append("%s- %s" % (pad, _scalar(item)))
    return "\n".join(lines)


def _scalar(val):
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if val is None:
        return "null"
    if isinstance(val, (dict, list)):
        return "{}" if isinstance(val, dict) else "[]"
    return json.dumps(str(val), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Страж путей: отдельный скрипт, Hermes зовёт его на каждый файловый вызов
# ---------------------------------------------------------------------------
GUARD_SOURCE = r'''#!/usr/bin/env python3
"""pre_tool_call страж (сгенерирован hermes_reserve.py, не править руками)."""
import fnmatch, json, os, sys

POLICY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy.json")
WRITE = ("write_file", "patch")


def under(path, roots):
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def paths_of(tool, args, cwd):
    out = []
    if tool == "patch" and (args.get("mode") == "patch" or args.get("patch")):
        for line in str(args.get("patch") or "").splitlines():
            for tag in ("*** Update File:", "*** Add File:", "*** Delete File:", "*** Move to:"):
                if line.startswith(tag):
                    out.append(line[len(tag):].strip())
    for key in ("path", "image_url"):
        val = args.get(key)
        if isinstance(val, str) and val and not val.startswith(("http://", "https://", "data:")):
            out.append(val)
    if tool == "search_files" and not args.get("path"):
        out.append(".")
    return [os.path.realpath(os.path.join(cwd, os.path.expanduser(p))) for p in out]


def verdict(payload, policy):
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    cwd = payload.get("cwd") or policy.get("cwd") or os.getcwd()
    write = tool in WRITE
    if write and not policy.get("write_roots"):
        return "запись файлов в этом боте запрещена"
    paths = paths_of(tool, args, cwd)
    if write and not paths:
        return "не вижу путь записи"
    for p in paths:
        for d in policy.get("deny", []):
            if under(p, [d]):
                return "путь закрыт: %s" % p
        if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.basename(p), g) for g in policy.get("deny_globs", [])):
            return "путь закрыт: %s" % p
        roots = policy.get("write_roots") if write else policy.get("read_roots")
        if not under(p, roots or []):
            return "вне разрешённых каталогов: %s" % p
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        with open(POLICY, encoding="utf-8") as fh:
            policy = json.load(fh)
        reason = verdict(payload, policy)
    except Exception as exc:  # страж не открывает дверь на сбое
        reason = "страж упал: %s" % exc
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


class Reserve:
    """Движок бота: Claude основной, Hermes резерв. Один экземпляр на процесс."""

    def __init__(self, prefix, state_path, hermes_home, workspace, read_roots,
                 write_roots=(), deny=(), deny_globs=(), toolsets=("file",),
                 mcp_servers=None, timeout=600, logger=None):
        self.prefix = prefix
        self.state_path = state_path
        self.hermes_home = hermes_home
        self.workspace = workspace
        self.read_roots = [os.path.realpath(p) for p in read_roots]
        self.write_roots = [os.path.realpath(p) for p in write_roots]
        self.deny = [os.path.realpath(p) for p in deny]
        self.deny_globs = list(deny_globs)
        self.toolsets = list(toolsets)
        self.mcp_servers = mcp_servers or {}
        self.timeout = timeout
        self.log = logger or logging.getLogger(prefix.lower())
        self.notify = None  # async fn(text): бот ставит при старте; нет — метка только в логе
        self.hermes_bin = os.environ.get(prefix + "_HERMES_BIN", HERMES_BIN)
        self.provider = os.environ.get(prefix + "_HERMES_PROVIDER", DEFAULT_PROVIDER)
        self.model = os.environ.get(prefix + "_HERMES_MODEL", DEFAULT_MODEL)
        self.vision_model = os.environ.get(prefix + "_HERMES_VISION_MODEL", "") or self.model

    # --- состояние --------------------------------------------------------
    def load(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            _write_atomic(self.state_path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError as exc:
            self.log.warning("engine state не записан: %s", exc)

    def forced(self):
        val = (os.environ.get(self.prefix + "_FORCE_ENGINE") or "").strip().lower()
        return val if val in ("claude", "hermes") else None

    def fake_quota(self):
        return (os.environ.get(self.prefix + "_FAKE_QUOTA") or "").strip() in ("1", "true", "yes")

    def engine(self):
        return self.load().get("engine") or "claude"

    def status_line(self):
        st = self.load()
        if self.forced():
            return "движок: %s (принудительно)" % self.forced()
        if st.get("engine") == "hermes":
            return "движок: резерв Hermes с %s (%s)" % (st.get("since") or "?", st.get("reason") or "?")
        return "движок: claude"

    # --- главный вход -----------------------------------------------------
    async def call(self, prompt, claude_fn, now=None):
        """(текст, error_kind). claude_fn() -> (текст, kind) — прежний вызов бота."""
        forced = self.forced()
        if forced == "hermes":
            return await self.run_hermes(prompt)
        if forced == "claude":
            return await claude_fn()
        now = time.time() if now is None else now
        st = self.load()
        on_reserve = st.get("engine") == "hermes"
        if on_reserve and now - float(st.get("last_probe") or 0) < PROBE_SECONDS:
            return await self.run_hermes(prompt)
        text, kind = await self._claude(claude_fn)
        if on_reserve:
            st["last_probe"] = now
            if kind in STICKY_KINDS:
                self.save(st)
                self.log.info("engine probe: claude ещё %s, остаюсь на резерве", kind)
                return await self.run_hermes(prompt)
            if kind is None:
                self.save({"engine": "claude", "back_at": now_iso()})
                self.log.info("engine switch: hermes -> claude")
                await self._notify(NOTICE_BACK)
                return text, kind
            self.save(st)
            return await self.run_hermes(prompt) if kind in FALLBACK_KINDS else (text, kind)
        if kind in STICKY_KINDS:
            self.save({"engine": "hermes", "since": now_iso(), "reason": kind, "last_probe": now})
            self.log.warning("engine switch: claude -> hermes (%s)", kind)
            await self._notify(NOTICE_RESERVE)
            return await self.run_hermes(prompt)
        if kind in FALLBACK_KINDS:
            self.log.warning("engine fallback: claude %s, этот вызов через hermes", kind)
            return await self.run_hermes(prompt)
        return text, kind

    async def _claude(self, claude_fn):
        if self.fake_quota():
            self.log.warning("engine=claude fake_quota=1 (тест)")
            return "", "quota"
        try:
            return await claude_fn()
        except Exception as exc:  # noqa: BLE001 - вызов не должен ронять бота
            self.log.exception("claude упал: %s", exc)
            return "", "error"

    async def _notify(self, text):
        if self.notify is None:
            self.log.info("engine notice (без отправки): %s", text)
            return
        try:
            await self.notify(text)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("метка движка не отправилась: %s", exc)

    # --- Hermes -----------------------------------------------------------
    def policy(self):
        return {
            "cwd": self.workspace,
            "read_roots": self.read_roots,
            "write_roots": self.write_roots,
            "deny": self.deny + [os.path.realpath(self.hermes_home)],
            "deny_globs": self.deny_globs,
        }

    def config(self):
        guard = os.path.join(self.hermes_home, "guard.py")
        cfg = {
            "model": {"default": self.model, "provider": self.provider},
            "agent": {"max_turns": 40},
            "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
            "skills": {"creation_nudge_interval": 0},
            "curator": {"enabled": False},
            "compression": {"enabled": True},
            "display": {"show_reasoning": False, "streaming": False},
            "security": {"tirith_enabled": False},  # сканер команд terminal, а terminal нет
            "hooks_auto_accept": True,
            "hooks": {"pre_tool_call": [{
                "matcher": "|".join(FILE_TOOLS),
                "command": "%s %s" % (sys.executable, guard),
                "timeout": 20,
            }]},
        }
        if self.mcp_servers:
            cfg["mcp_servers"] = self.mcp_servers
        return cfg

    def ensure_home(self):
        os.makedirs(self.hermes_home, exist_ok=True)
        os.chmod(self.hermes_home, 0o700)
        _write_if_changed(os.path.join(self.hermes_home, "config.yaml"), _yaml(self.config()) + "\n")
        _write_if_changed(os.path.join(self.hermes_home, "SOUL.md"), SOUL)
        _write_if_changed(os.path.join(self.hermes_home, "guard.py"), GUARD_SOURCE, 0o700)
        _write_if_changed(os.path.join(self.hermes_home, "policy.json"),
                          json.dumps(self.policy(), ensure_ascii=False, indent=2))

    def ensure_auth(self):
        """Nous OAuth машины (метка vps-bots) — импорт из общего стора в профиль, один раз.

        Свежий токен потом подтягивается из общего стора самим Hermes при обновлении.
        """
        if self.provider != "nous":
            return
        try:
            with open(os.path.join(self.hermes_home, "auth.json"), encoding="utf-8") as fh:
                if '"nous"' in fh.read():
                    return
        except OSError:
            pass
        import subprocess
        res = subprocess.run(
            [self.hermes_bin, "auth", "add", "nous", "--type", "oauth", "--label", "vps-bots", "--no-browser"],
            cwd=self.workspace, env=self.env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90, check=False)
        self.log.info("engine=hermes импорт Nous OAuth в профиль: rc=%s", res.returncode)

    def images_in(self, prompt):
        out = []
        for raw in _PATH_RE.findall(prompt or ""):
            path = raw if os.path.isabs(raw) else os.path.join(self.workspace, raw)
            path = os.path.realpath(path)
            if path not in out and os.path.isfile(path) and any(
                    path.startswith(r.rstrip("/") + "/") for r in self.read_roots):
                out.append(path)
        return out

    def toolsets_for(self):
        # Без mcp-<сервер> в -t тулз MCP нет. v0.19.1 при этом печатает в stdout
        # «Warning: Unknown toolsets», хотя тулзы есть (проверено 26.10) — строку режем.
        sets = list(self.toolsets)
        sets += ["mcp-" + name for name in self.mcp_servers if "mcp-" + name not in sets]
        return sets

    def build_cmd(self, prompt, images=()):
        model = self.vision_model if images else self.model
        cmd = [self.hermes_bin, "chat", "-q", PREAMBLE + prompt, "-Q",
               "--provider", self.provider, "-m", model,
               "-t", ",".join(self.toolsets_for()), "--source", "tool"]
        if images:
            cmd += ["--image", images[0]]
        return cmd

    def env(self):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("TELEGRAM_", "CLAUDE_CODE_", "ANTHROPIC_")) and k != "BOT_TOKEN"}
        env["HOME"] = env.get("HOME") or "/root"
        env["HERMES_HOME"] = self.hermes_home
        env["HERMES_SHARED_AUTH_DIR"] = os.environ.get(self.prefix + "_HERMES_SHARED_AUTH_DIR", SHARED_AUTH_DIR)
        env["HERMES_WRITE_SAFE_ROOT"] = os.pathsep.join(self.write_roots) or "/nonexistent-write-root"
        env["HERMES_ACCEPT_HOOKS"] = "1"
        return env

    async def run_hermes(self, prompt):
        """(текст, error_kind). Лог: engine=hermes model=… dur=… cost_usd=…"""
        try:
            self.ensure_home()
            await asyncio.to_thread(self.ensure_auth)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("engine=hermes профиль не собран: %s", exc)
            return "", "error"
        images = [p for p in self.images_in(prompt) if p.lower().endswith(IMAGE_EXTS)]
        cmd = self.build_cmd(prompt, images)
        model = self.vision_model if images else self.model
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=self.workspace, env=self.env(), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)  # MCP-детей убиваем группой
        except OSError as exc:
            self.log.warning("engine=hermes не запустился: %s", exc)
            return "", "error"
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            self.log.warning("engine=hermes model=%s timeout dur=%.1fs", model, time.monotonic() - start)
            return "", "timeout"
        dur = time.monotonic() - start
        out = (out_b or b"").decode("utf-8", errors="replace")
        err = (err_b or b"").decode("utf-8", errors="replace")
        sid = (re.findall(r"session_id:\s*(\S+)", err + "\n" + out) or [None])[-1]
        text = "\n".join(ln for ln in out.splitlines()
                         if not ln.startswith(("session_id:", "Warning: Unknown toolsets"))).strip()
        cost = self.session_cost(sid)
        if proc.returncode != 0 or not text:
            self.log.warning("engine=hermes model=%s rc=%s dur=%.1fs cost_usd=%s tail=%s",
                             model, proc.returncode, dur, cost, (err or text)[-400:])
            return "", "error"
        self.log.info("engine=hermes model=%s dur=%.1fs reply_len=%d images=%d cost_usd=%s sid=%s",
                      model, dur, len(text), len(images), cost, sid)
        return text, None

    def session_cost(self, sid):
        if not sid:
            return "?"
        try:
            con = sqlite3.connect("file:%s?mode=ro" % os.path.join(self.hermes_home, "state.db"), uri=True)
            try:
                row = con.execute("select estimated_cost_usd, actual_cost_usd from sessions where id=?",
                                  (sid,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return "?"
        if not row:
            return "?"
        val = row[1] if row[1] is not None else row[0]
        return "?" if val is None else "%.4f" % float(val)


def guard_verdict(payload, policy):
    """Тот же вердикт, что у guard.py — для тестов без подпроцесса."""
    scope = {"__name__": "guard", "__file__": "guard.py"}
    exec(compile(GUARD_SOURCE, "guard.py", "exec"), scope)  # noqa: S102 - свой код
    return scope["verdict"](payload, policy)
