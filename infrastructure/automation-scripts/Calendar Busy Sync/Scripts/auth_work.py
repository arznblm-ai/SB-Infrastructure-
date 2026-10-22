#!/usr/bin/env python3
"""auth_work.py — разовая авторизация anton@looch.ai для синка занятости (шаг H3).

Запускает Антон на маке (нужен google-auth-oauthlib):
    python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Calendar Busy Sync/Scripts/auth_work.py"

1. Берёт OAuth-клиент из ~/.config/second-brain/google-calendar-credentials.json
   (проект proven-dialect, тот же клиент, что у календаря Аси).
2. Открывает браузер: войти как anton@looch.ai, дать доступ (calendar.events + calendar.freebusy).
3. Сохраняет токен в ~/.config/second-brain/busy-sync-work-token.json (600).
4. Кладёт его на VPS: root@163.5.29.10:/root/.config/second-brain/busy-sync-work-token.json (600).

Токен в вывод не печатается. --no-upload — только сохранить на маке.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLIENT_SECRETS = Path.home() / ".config" / "second-brain" / "google-calendar-credentials.json"
TOKEN_PATH = Path.home() / ".config" / "second-brain" / "busy-sync-work-token.json"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]
LOGIN = "anton@looch.ai"
VPS = "root@163.5.29.10"
VPS_KEY = Path.home() / ".ssh" / "id_ed25519_vps"
VPS_DIR = "/root/.config/second-brain"
VPS_PATH = f"{VPS_DIR}/busy-sync-work-token.json"


def upload() -> bool:
    ssh = ["-i", str(VPS_KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    try:
        subprocess.run(["ssh", *ssh, VPS, f"mkdir -p {VPS_DIR} && chmod 700 {VPS_DIR}"], check=True)
        # во временное имя и mv — busy_sync на таймере не увидит полузаписанный файл
        subprocess.run(["scp", "-q", *ssh, str(TOKEN_PATH), f"{VPS}:{VPS_PATH}.new"], check=True)
        subprocess.run(["ssh", *ssh, VPS, f"chmod 600 {VPS_PATH}.new && mv -f {VPS_PATH}.new {VPS_PATH}"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"✗ не получилось положить токен на VPS: {exc}", file=sys.stderr)
        return False
    return True


def main() -> int:
    no_upload = "--no-upload" in sys.argv[1:]
    print(f"Авторизация {LOGIN} для синка занятости (личные календари → рабочий).")
    if not CLIENT_SECRETS.exists():
        print(f"✗ нет OAuth-клиента: {CLIENT_SECRETS}", file=sys.stderr)
        return 2
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(f"✗ нет google-auth-oauthlib ({exc}). Поставь: pip3 install google-auth-oauthlib", file=sys.stderr)
        return 2

    print(f"Сейчас откроется браузер — войди как {LOGIN} и разреши доступ к календарю.")
    print("Если админ looch.ai блокирует сторонние приложения — вход упадёт; см. «Нерешённые вопросы» в плане.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), scopes=SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, login_hint=LOGIN,
                                  prompt="consent", access_type="offline")
    if not creds.refresh_token:
        print("✗ Google не выдал refresh_token — повтори запуск (prompt=consent должен его выдать).", file=sys.stderr)
        return 1

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)
    print(f"✓ токен сохранён: {TOKEN_PATH} (600)")

    if no_upload:
        print("--no-upload: на VPS не кладу.")
        return 0
    if not upload():
        print(f"Положи руками: scp -i {VPS_KEY} '{TOKEN_PATH}' {VPS}:{VPS_PATH} && ssh -i {VPS_KEY} {VPS} chmod 600 {VPS_PATH}")
        return 1
    print(f"✓ токен на VPS: {VPS}:{VPS_PATH} (600)")
    print()
    print("Дальше:")
    print(f"  ssh -i {VPS_KEY} {VPS} python3 /opt/busy-sync/busy_sync.py --check")
    print("  или просто перезапусти deploy.sh — он сам прогонит --check и включит таймер:")
    print('  bash "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Calendar Busy Sync/Scripts/deploy.sh"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
