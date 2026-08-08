#!/usr/bin/env python3
"""Одноразовая авторизация Google Sheets для радара вакансий.

Открывает браузер, просит доступ к Google Sheets и сохраняет токен в
~/.config/second-brain/google-sheets-token.json (права 600).

Запуск:
    python3 sheets_auth.py
"""

import os
import sys
from pathlib import Path

CLIENT_SECRETS = Path("/Users/anton/.config/second-brain/google-calendar-credentials.json")
TOKEN_PATH = Path.home() / ".config" / "second-brain" / "google-sheets-token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main() -> int:
    print("Авторизация Google Sheets для радара вакансий.")

    if not CLIENT_SECRETS.exists():
        print(
            f"Ошибка: не найден файл client secrets: {CLIENT_SECRETS}",
            file=sys.stderr,
        )
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(
            f"Ошибка: не установлена библиотека google-auth-oauthlib ({exc}).\n"
            "Установи её: pip3 install google-auth-oauthlib",
            file=sys.stderr,
        )
        return 2

    print(f"Использую client secrets: {CLIENT_SECRETS}")
    print("Сейчас откроется браузер — выбери аккаунт a.rznblm@gmail.com "
          "и разреши доступ к Google Таблицам.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), scopes=SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(TOKEN_PATH, 0o600)

    print(f"Готово. Токен сохранён: {TOKEN_PATH} (права 600)")
    print("Теперь daily_vacancy_scan.py сможет писать в таблицу «Anton job research».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
