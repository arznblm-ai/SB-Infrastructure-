#!/usr/bin/env python3
"""Второй мак (Mac mini) как то же рабочее место, что и ноутбук.

Запускается НА НОУТБУКЕ. Всё, что нужно на mini, делается по ssh.

  python3 mac_mini_setup.py sessions-hub        # папка claude-sessions: ноутбук <-> VPS (идемпотентно)
  python3 mac_mini_setup.py mini HOST           # развернуть mini (HOST = anton@mac-mini.local)
  python3 mac_mini_setup.py tunnel HOST         # обратный туннель mini -> VPS, ssh-алиасы mini / mini-remote
  python3 mac_mini_setup.py migrate LABEL|all HOST   # перевезти автоматизацию с ноутбука на mini
  python3 mac_mini_setup.py status [HOST]       # связи Syncthing и где какие автоматизации

Контракт и правила — ../CLAUDE.md, раздел «Второй мак».
"""
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HOME = os.path.expanduser("~")
VAULT = f"{HOME}/AI AGENT FOLDER/Second Brain"
PROJECT_KEY = "-Users-anton-AI-AGENT-FOLDER-Second-Brain"
SESSIONS_DIR = f"{HOME}/.claude/projects/{PROJECT_KEY}"
SHARED_MEMORY = f"{HOME}/.claude/shared-memory"
ST_HOME = f"{HOME}/Library/Application Support/Syncthing"
ST_PLIST = f"{HOME}/Library/LaunchAgents/com.user.syncthing.plist"

VPS = "root@163.5.29.10"
MINI_KEY = f"{HOME}/.ssh/id_ed25519_mini"  # ноутбук -> mini; кладётся на mini через ssh-copy-id
VPS_KEY = f"{HOME}/.ssh/id_ed25519_vps"
VPS_ST_CONFIG = "/root/.local/state/syncthing/config.xml"
VPS_SESSIONS_DIR = "/root/.claude/mac-sessions"

LAPTOP_ID = "QJZKYVW-MA6XTMZ-FTJUXFF-P26QDFM-IAMNESB-WB2A4ZT-JVURIOP-D3GW4QW"
VPS_ID = "4P5TNJ5-OWX4AYR-YBD4V4Q-AFKYEQD-MEWZIZT-7XUNAL6-AUMIM5S-6RLUMQC"

# Папки, которые живут на всех трёх устройствах: id -> (label, путь на маках, путь на VPS)
FOLDERS = {
    "second-brain": ("Second Brain", VAULT, "/root/second-brain"),
    "claude-memory": ("Claude Shared Memory", SHARED_MEMORY, "/root/.claude/shared-memory"),
    "claude-sessions": ("Claude Sessions (mac)", SESSIONS_DIR, VPS_SESSIONS_DIR),
}
# memory/ внутри каталога сессий — симлинк на shared-memory, у него своя папка
SESSIONS_IGNORE = ["/memory", "(?d).DS_Store"]

# Секреты копируются один раз; состояние процессов (offsets, seen, ledgers) — нет:
# процессы живут на одной машине, чужое состояние на второй только мешает.
SECRETS_EXCLUDE = ["*.offset", "*state*.json", "*seen*", "*ledger*", "*history*",
                   "*backup*", "*.bak*", "*.obsolete", "mic-prompt-result.txt", "machine-role"]
SSH_FILES = ["id_ed25519_vps", "id_ed25519_vps.pub", "id_ed25519_github",
             "id_ed25519_github.pub", "config", "known_hosts"]
USER_MCP = ["miro", "iiagency-kb", "playwright"]

# Mac mini — главная машина (решение Антона 27.10): автоматизации ноутбука переезжают на него.
# label -> что ещё кроме vault нужно на mini (код и state переезжают вместе с агентом).
# Каждый label в каждый момент загружен ровно на одной машине.
SB = "~/.config/second-brain"
MIGRATE = {
    "com.user.krisp-to-obsidian": ["~/.krisp-obsidian/", "~/.claude/krisp_token.json"],
    "com.user.krisp-nightly-backfill": ["~/.krisp-obsidian/", "~/.claude/krisp_token.json"],
    "com.user.krisp-audio-capture": ["~/.krisp-obsidian/"],
    "com.user.claude-session-export": [],
    "com.user.personal-context-updater": [],
    "com.user.vacancy-scan": [f"{SB}/vacancy-scan-seen.json"],
    "com.user.voice-memos": [f"{SB}/voice-memos-state.json"],
    "com.anton.link-inbox": ["~/.config/link-inbox/"],
    "com.anton.link-inbox-bot": ["~/.config/link-inbox/"],
    "com.anton.link-inbox-quality-audit": ["~/.config/link-inbox/"],
    "com.anton.weekly-git-update": ["~/.codex/skills/"],
    # TorrServer: переезд только вместе с DHCP-привязкой 192.168.1.14 на MAC mini (ТВ ходит на этот IP)
    "torrserver": ["/Users/Shared/TorrServer/"],
    "lampaplugins.local": ["/Users/Shared/LampaPlugins/"],
    "com.user.torrserver-cleanup": ["/Users/Shared/TorrServer/"],
    "com.user.torrserver-watchdog": ["/Users/Shared/TorrServer/"],
}
# Свои на каждой машине: синк, локальные индексы. Туннель — только на mini.
BOTH = ["com.user.syncthing", "rs.ctx.daemon", "com.anton.vault-semantic-search"]
TUNNEL_LABEL = "com.user.vps-tunnel"
TUNNEL_PORT = 2222  # на VPS слушает только 127.0.0.1 (GatewayPorts no)


def log(msg):
    print(f"[mini-setup] {msg}", flush=True)


def run(cmd, check=True, input=None, capture=True):
    if cmd[0] == "rsync":
        cmd = cmd[:1] + ["-e", f"ssh -i {MINI_KEY} -o BatchMode=yes"] + cmd[1:]
    r = subprocess.run(cmd, input=input, text=True, capture_output=capture)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd)}\n{r.stderr}")
    return r


def ssh(host, script, check=True, input=None, key=None):
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    if key:
        cmd += ["-i", key]
    return run(cmd + [host, script], check=check, input=input)


# --- Syncthing REST: локально, на VPS, на mini -------------------------------------------

class ST:
    def __init__(self, name, host=None, key=None, config=None):
        self.name, self.host, self.sshkey = name, host, key
        self.apikey = self._apikey(config)

    def _apikey(self, config):
        if self.host is None:
            return ET.parse(config).getroot().find("gui/apikey").text
        out = ssh(self.host, f"grep -o '<apikey>[^<]*' {shlex.quote(config)}", key=self.sshkey).stdout
        return out.strip().split(">", 1)[1]

    def call(self, method, path, body=None):
        data = json.dumps(body) if body is not None else None
        if self.host is None:
            req = urllib.request.Request(f"http://127.0.0.1:8384{path}", method=method,
                                         data=data.encode() if data else None,
                                         headers={"X-API-Key": self.apikey})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    txt = r.read().decode()
                    return 200, (json.loads(txt) if txt.strip() else None)
            except urllib.error.HTTPError as e:
                return e.code, None
        curl = (f"curl -s -o /tmp/st.out -w '%{{http_code}}' -X {method} "
                f"-H 'X-API-Key: {self.apikey}' -H 'Content-Type: application/json' "
                f"{'--data-binary @- ' if data else ''}'http://127.0.0.1:8384{path}'; echo; cat /tmp/st.out; rm -f /tmp/st.out")
        out = ssh(self.host, curl, input=data, key=self.sshkey).stdout
        code, _, txt = out.partition("\n")
        try:
            return int(code), (json.loads(txt) if txt.strip() else None)
        except ValueError:
            return int(code), txt

    def ensure_device(self, device_id, name, addresses):
        code, _ = self.call("GET", f"/rest/config/devices/{device_id}")
        if code == 200:
            return
        code, _ = self.call("POST", "/rest/config/devices",
                            {"deviceID": device_id, "name": name, "addresses": addresses,
                             "autoAcceptFolders": False})
        assert code == 200, f"{self.name}: add device {name} -> {code}"
        log(f"{self.name}: добавлено устройство {name}")

    def ensure_folder(self, folder_id, label, path, devices, versioning=None):
        code, cur = self.call("GET", f"/rest/config/folders/{folder_id}")
        if code == 200:
            have = {d["deviceID"] for d in cur["devices"]}
            missing = [d for d in devices if d not in have]
            if missing:
                cur["devices"] += [{"deviceID": d} for d in missing]
                code, _ = self.call("PUT", f"/rest/config/folders/{folder_id}", cur)
                assert code == 200, f"{self.name}: share {folder_id} -> {code}"
                log(f"{self.name}: папка {folder_id} расшарена ещё {len(missing)} устройству(ам)")
            return
        body = {"id": folder_id, "label": label, "path": path, "type": "sendreceive",
                "devices": [{"deviceID": d} for d in devices], "fsWatcherEnabled": True}
        if versioning:
            body["versioning"] = versioning
        code, _ = self.call("POST", "/rest/config/folders", body)
        assert code == 200, f"{self.name}: add folder {folder_id} -> {code}"
        log(f"{self.name}: создана папка {folder_id} → {path}")

    def set_ignores(self, folder_id, lines):
        code, _ = self.call("POST", f"/rest/db/ignores?folder={folder_id}", {"ignore": lines})
        assert code == 200, f"{self.name}: ignores {folder_id} -> {code}"


def laptop():
    return ST("ноутбук", config=f"{ST_HOME}/config.xml")


def vps():
    return ST("VPS", host=VPS, key=VPS_KEY, config=VPS_ST_CONFIG)


# --- шаги ------------------------------------------------------------------------------

def sessions_hub():
    """Транскрипты сессий Claude ноутбука уезжают на VPS (только как пересадочный узел).

    Нужно, чтобы mini получил свежую сессию, даже если ноутбук уже закрыт в рюкзаке.
    Без versioning: jsonl дописывается постоянно, версии съели бы диск."""
    L, V = laptop(), vps()
    ssh(VPS, f"mkdir -p {VPS_SESSIONS_DIR} && printf '%s\\n' {' '.join(shlex.quote(x) for x in SESSIONS_IGNORE)} "
             f"> {VPS_SESSIONS_DIR}/.stignore", key=VPS_KEY)
    with open(f"{SESSIONS_DIR}/.stignore", "w") as f:
        f.write("\n".join(SESSIONS_IGNORE) + "\n")
    label, mac_path, vps_path = FOLDERS["claude-sessions"]
    V.ensure_folder("claude-sessions", label, vps_path, [VPS_ID, LAPTOP_ID])
    L.ensure_folder("claude-sessions", label, mac_path, [LAPTOP_ID, VPS_ID])
    log("sessions-hub готов: ноутбук ⇄ VPS")


def mini(host):
    def m(script, check=True, input=None):
        return ssh(host, script, check=check, input=input, key=MINI_KEY)

    # 0. Предпроверки: тот же логин и та же архитектура, иначе пути vault/памяти/хуков разъедутся
    info = m("whoami; uname -m; sw_vers -productVersion; xcode-select -p 2>/dev/null || echo NO_CLT; "
             "ls /usr/local/bin/python3.1[0-9] /Library/Frameworks/Python.framework/Versions/3.1*/bin/python3 2>/dev/null | head -1 || true").stdout.split("\n")
    user, arch, osver, clt = info[0], info[1], info[2], info[3]
    py = info[4].strip() if len(info) > 4 else ""
    log(f"mini: user={user} arch={arch} macOS={osver} CLT={clt} python≥3.10={py or 'нет'}")
    if user != "anton":
        raise SystemExit("На mini должен быть пользователь anton: пути vault, памяти и хуков привязаны к /Users/anton")
    if arch != "arm64":
        raise SystemExit("Ожидался Apple Silicon: syncthing и ctx копируются как arm64-бинарники")

    # 1. ssh-ключи к VPS и GitHub (тот же владелец, канал — ssh; не через vault/GitHub)
    m("mkdir -p ~/.ssh && chmod 700 ~/.ssh && [ -f ~/.ssh/config ] && cp ~/.ssh/config ~/.ssh/config.bak-mini-setup || true")
    run(["rsync", "-a"] + [f"{HOME}/.ssh/{f}" for f in SSH_FILES] + [f"{host}:.ssh/"])
    m("chmod 600 ~/.ssh/id_ed25519_* ~/.ssh/config; chmod 644 ~/.ssh/*.pub")
    log("ssh-ключи скопированы")

    # 2. секреты ~/.config/second-brain без состояния процессов
    m("mkdir -p ~/.config/second-brain && chmod 700 ~/.config/second-brain")
    run(["rsync", "-a"] + [f"--exclude={x}" for x in SECRETS_EXCLUDE]
        + [f"{HOME}/.config/second-brain/", f"{host}:.config/second-brain/"])
    m("chmod 600 ~/.config/second-brain/*; echo mac-mini > ~/.config/second-brain/machine-role")
    with open(f"{HOME}/.config/second-brain/machine-role", "w") as f:
        f.write("laptop\n")
    log("секреты скопированы (state-файлы не копировались)")

    # 3. пользовательский конфиг Claude: settings, личные скиллы, плагины, Node для playwright
    m(f"mkdir -p ~/.claude/skills ~/.claude/projects/{PROJECT_KEY} {shlex.quote(SHARED_MEMORY)} "
      f"{shlex.quote(VAULT)} ~/.local/bin ~/.local/share ~/bin ~/Library/LaunchAgents")
    run(["rsync", "-a", f"{HOME}/.claude/settings.json", f"{HOME}/.claude/anthropic_api.json", f"{host}:.claude/"])
    run(["rsync", "-a", "--delete", f"{HOME}/.claude/skills/", f"{host}:.claude/skills/"])
    run(["rsync", "-a", f"{HOME}/.claude/plugins/", f"{host}:.claude/plugins/"])
    run(["rsync", "-a", f"{HOME}/.hermes/node", f"{host}:.hermes/"], check=False)
    m("cd ~/.local/bin && for b in node npm npx; do ln -sf ~/.hermes/node/bin/$b $b; done")
    # память проекта — тот же симлинк, что на ноутбуке
    m(f"cd ~/.claude/projects/{PROJECT_KEY} && [ -L memory ] || ln -s {shlex.quote(SHARED_MEMORY)} memory")
    m(f"printf '%s\\n' {' '.join(shlex.quote(x) for x in SESSIONS_IGNORE)} > ~/.claude/projects/{PROJECT_KEY}/.stignore")
    m(f"cat > {shlex.quote(VAULT)}/.stignore", input=open(f"{VAULT}/.stignore").read())
    log("конфиг Claude, скиллы, плагины скопированы; каталоги vault/памяти/сессий созданы")

    # 4. Syncthing: тот же бинарник и LaunchAgent, приватный режим как на ноутбуке
    run(["rsync", "-a", f"{HOME}/bin/syncthing", f"{host}:bin/"])
    run(["rsync", "-a", ST_PLIST, f"{host}:Library/LaunchAgents/"])
    st_home = shlex.quote(ST_HOME)
    m(f"[ -f {st_home}/config.xml ] || STNODEFAULTFOLDER=1 ~/bin/syncthing generate --home={st_home} >/dev/null")
    mini_id = m(f"~/bin/syncthing device-id --home={st_home}").stdout.strip()
    log(f"mini device id: {mini_id}")
    m("launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.syncthing.plist 2>/dev/null; "
      "launchctl kickstart gui/$(id -u)/com.user.syncthing")
    M = None
    for _ in range(30):
        try:
            M = ST("mini", host=host, key=MINI_KEY, config=f"{ST_HOME}/config.xml")
            if M.call("GET", "/rest/system/ping")[0] == 200:
                break
        except SystemExit:
            pass
        time.sleep(2)
    else:
        raise SystemExit("Syncthing на mini не поднялся: смотри ~/Library/Logs/syncthing.log на mini")
    M.call("PATCH", "/rest/config/options", {"globalAnnounceEnabled": False, "localAnnounceEnabled": True,
                                             "relaysEnabled": False, "natEnabled": False,
                                             "urAccepted": -1, "crashReportingEnabled": False})
    if M.call("GET", "/rest/config/folders/default")[0] == 200:
        M.call("DELETE", "/rest/config/folders/default")

    L, V = laptop(), vps()
    # дома маки находят друг друга по LAN (local discovery), вне дома — через VPS
    L.call("PATCH", "/rest/config/options", {"localAnnounceEnabled": True})
    for api in (L, V):
        api.ensure_device(mini_id, "Mac-mini", ["dynamic"])
    M.ensure_device(LAPTOP_ID, "MacBook-Pro", ["dynamic"])
    M.ensure_device(VPS_ID, "vps-hostkey", ["tcp://163.5.29.10:22000"])

    for fid, (label, mac_path, vps_path) in FOLDERS.items():
        if fid == "claude-sessions":
            sessions_hub()
        _, lf = L.call("GET", f"/rest/config/folders/{fid}")
        V.ensure_folder(fid, label, vps_path, [VPS_ID, LAPTOP_ID, mini_id])
        L.ensure_folder(fid, label, mac_path, [LAPTOP_ID, VPS_ID, mini_id])
        M.ensure_folder(fid, label, mac_path, [mini_id, LAPTOP_ID, VPS_ID], versioning=lf.get("versioning"))
    M.set_ignores("claude-sessions", SESSIONS_IGNORE)
    log("Syncthing: mini в сетке ноутбук ⇄ VPS ⇄ mini, три папки")

    # 5. Claude CLI (официальный установщик Anthropic) и пользовательские MCP
    if m("[ -x ~/.local/bin/claude ] && echo yes || echo no").stdout.strip() != "yes":
        m("curl -fsSL https://claude.ai/install.sh | bash", check=False)
    user_mcp = json.load(open(f"{HOME}/.claude.json")).get("mcpServers", {})
    have = m("~/.local/bin/claude mcp list 2>/dev/null || true", check=False).stdout
    for name in USER_MCP:
        if name in user_mcp and f"{name}:" not in have:
            m(f"~/.local/bin/claude mcp add-json -s user {name} {shlex.quote(json.dumps(user_mcp[name]))}", check=False)
    log("Claude CLI и пользовательские MCP (miro, iiagency-kb, playwright) на месте")

    # 6. ctx: свой индекс на каждой машине (индексирует синхронизированные транскрипты обеих)
    run(["rsync", "-a", f"{HOME}/.local/bin/ctx", f"{HOME}/.local/bin/ctx.install-integrations", f"{host}:.local/bin/"])
    m("~/.local/bin/ctx setup --quiet", check=False)
    log("ctx установлен, индекс строится в фоне")

    # 7. Todoist MCP (.mcp.json vault) — нужен Python ≥3.10
    if py:
        req = run([f"{HOME}/.local/share/todoist-mcp-venv/bin/pip", "freeze"]).stdout
        m(f"{shlex.quote(py)} -m venv ~/.local/share/todoist-mcp-venv && "
          "~/.local/share/todoist-mcp-venv/bin/pip install -q -r /dev/stdin", input=req, check=False)
        log("Todoist MCP venv собран")
    else:
        log("⚠ Python ≥3.10 на mini нет: Todoist MCP не заработает. Поставь Python 3.11 с python.org и перезапусти шаг")
    if clt == "NO_CLT":
        log("⚠ Нет Command Line Tools: git и хук bash_streak_guard (python3) не работают. На mini: xcode-select --install")

    tunnel(host)
    status(host)


def tunnel(host):
    """Ноутбук попадает на mini откуда угодно: mini держит обратный ssh-туннель на VPS.

    Ключ туннеля на VPS ограничен: только проброс на 127.0.0.1:2222, без shell."""
    lan = host.split("@")[-1]
    m = lambda s, **kw: ssh(host, s, key=MINI_KEY, **kw)
    m("[ -f ~/.ssh/id_ed25519_tunnel ] || ssh-keygen -t ed25519 -N '' -q -C mac-mini-tunnel -f ~/.ssh/id_ed25519_tunnel")
    pub = m("cat ~/.ssh/id_ed25519_tunnel.pub").stdout.strip()
    line = (f'restrict,port-forwarding,permitlisten="127.0.0.1:{TUNNEL_PORT}",command="/bin/false" {pub}')
    ssh(VPS, f"grep -qF {shlex.quote(pub.split()[1])} ~/.ssh/authorized_keys || echo {shlex.quote(line)} >> ~/.ssh/authorized_keys; "
             # мёртвый туннель освобождает порт за ~90 с, иначе новый не поднимется
             "f=/etc/ssh/sshd_config.d/60-client-alive.conf; [ -f $f ] || { printf 'ClientAliveInterval 30\\nClientAliveCountMax 3\\n' > $f "
             "&& sshd -t && systemctl reload ssh; }", key=VPS_KEY)
    m("ssh-keyscan -H 163.5.29.10 >> ~/.ssh/known_hosts 2>/dev/null")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{TUNNEL_LABEL}</string>
<key>ProgramArguments</key><array><string>/usr/bin/ssh</string><string>-N</string>
<string>-i</string><string>{HOME}/.ssh/id_ed25519_tunnel</string>
<string>-o</string><string>ExitOnForwardFailure=yes</string><string>-o</string><string>ServerAliveInterval=30</string>
<string>-o</string><string>ServerAliveCountMax=3</string><string>-o</string><string>BatchMode=yes</string>
<string>-R</string><string>127.0.0.1:{TUNNEL_PORT}:127.0.0.1:22</string><string>root@163.5.29.10</string></array>
<key>KeepAlive</key><true/><key>RunAtLoad</key><true/><key>ThrottleInterval</key><integer>15</integer>
<key>StandardErrorPath</key><string>{HOME}/Library/Logs/vps-tunnel.log</string>
</dict></plist>
"""
    m(f"cat > ~/Library/LaunchAgents/{TUNNEL_LABEL}.plist && "
      f"(launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{TUNNEL_LABEL}.plist 2>/dev/null; "
      f"launchctl kickstart -k gui/$(id -u)/{TUNNEL_LABEL})", input=plist)

    # ssh-алиасы на ноутбуке: `ssh mini` дома, `ssh mini-remote` откуда угодно
    cfg = f"{HOME}/.ssh/config"
    text = open(cfg).read()
    if "# >>> mac-mini" not in text:
        with open(cfg, "a") as f:
            f.write(f"""
# >>> mac-mini (infrastructure/Vault Sync/Scripts/mac_mini_setup.py)
Host vps
  HostName 163.5.29.10
  User root
  IdentityFile ~/.ssh/id_ed25519_vps
  IdentitiesOnly yes
Host mini
  HostName {lan}
  User anton
  IdentityFile ~/.ssh/id_ed25519_mini
  IdentitiesOnly yes
Host mini-remote
  HostName 127.0.0.1
  Port {TUNNEL_PORT}
  User anton
  ProxyJump vps
  IdentityFile ~/.ssh/id_ed25519_mini
  IdentitiesOnly yes
  HostKeyAlias mini
# <<< mac-mini
""")
    time.sleep(5)
    r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "mini-remote", "hostname"], check=False)
    log(f"туннель: ssh mini-remote → {r.stdout.strip() if r.returncode == 0 else 'НЕ работает: ' + r.stderr.strip()[:200]}")


def migrate(label, host):
    """Переезд одной автоматизации ноутбук -> mini: стоп на ноутбуке, код/state на mini, старт на mini.

    plist на ноутбуке не удаляется, а уходит в ~/Library/LaunchAgents/archive/ (откат = вернуть и bootstrap)."""
    if label not in MIGRATE:
        raise SystemExit(f"{label} нет в MIGRATE")
    m = lambda s, **kw: ssh(host, s, key=MINI_KEY, **kw)
    plist = f"{HOME}/Library/LaunchAgents/{label}.plist"
    uid = os.getuid()
    run(["launchctl", "bootout", f"gui/{uid}", plist], check=False)  # стоп ДО копирования state
    for p in MIGRATE[label]:
        src = os.path.expanduser(p)
        if os.path.exists(src.rstrip("/")):
            # домашние пути — относительно ~ на mini, остальные (/Users/Shared) — как есть
            dst = src[len(HOME) + 1:] if src.startswith(HOME + "/") else src
            m(f"mkdir -p {shlex.quote(os.path.dirname(dst.rstrip('/')) or '.')}")
            run(["rsync", "-a", src, f"{host}:{dst}"])
    run(["rsync", "-a", plist, f"{host}:Library/LaunchAgents/"])
    m(f"launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist")
    os.makedirs(f"{HOME}/Library/LaunchAgents/archive", exist_ok=True)
    os.replace(plist, f"{HOME}/Library/LaunchAgents/archive/{label}.plist")
    ok = m(f"launchctl list {label} >/dev/null && echo loaded || echo MISSING").stdout.strip()
    log(f"{label}: ноутбук → mini, на mini {ok}")


def status(host=None):
    L = laptop()
    _, conns = L.call("GET", "/rest/system/connections")
    _, devs = L.call("GET", "/rest/config/devices")
    names = {d["deviceID"]: d["name"] for d in devs}
    for did, c in conns["connections"].items():
        log(f"ноутбук → {names.get(did, did[:7])}: {'online ' + c.get('address', '') if c['connected'] else 'offline'}")
    for fid in FOLDERS:
        code, st = L.call("GET", f"/rest/db/status?folder={fid}")
        if code == 200:
            log(f"папка {fid}: {st['state']}, расхождение {st['needBytes'] // 1024} КБ")
    def loaded(h):
        out = ssh(h, "launchctl list", key=MINI_KEY).stdout if h else run(["launchctl", "list"]).stdout
        return {line.split("\t")[-1] for line in out.split("\n") if "\t" in line}
    on_laptop = loaded(None) & set(MIGRATE)
    on_mini = loaded(host) & set(MIGRATE) if host else set()
    log(f"ноутбук: {len(on_laptop)} автоматизаций; mini: {len(on_mini) if host else '?'}; "
        f"переехало {len(on_mini)}/{len(MIGRATE)}")
    dup = sorted(on_laptop & on_mini)
    if dup:
        log(f"⚠ ДУБЛИ на обеих машинах: {', '.join(dup)} — выгрузить на ноутбуке")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["sessions-hub"]:
        sessions_hub()
    elif args[:1] == ["mini"] and len(args) == 2:
        mini(args[1])
    elif args[:1] == ["tunnel"] and len(args) == 2:
        tunnel(args[1])
    elif args[:1] == ["migrate"] and len(args) == 3:
        # all = всё, кроме TorrServer: тому сначала нужна DHCP-привязка .14 на mini
        labels = [x for x in MIGRATE if "torr" not in x and "lampa" not in x] if args[1] == "all" else [args[1]]
        for label in labels:
            migrate(label, args[2])
    elif args[:1] == ["status"]:
        status(args[1] if len(args) > 1 else None)
    else:
        print(__doc__)
        sys.exit(1)
