#!/usr/bin/env python3
"""Тонкий клиент Google Drive для Крис (ADR-027).

Аккаунт студии admin@portalcg.xyz, OAuth installed-app flow (type Desktop).
Токен и client secrets лежат вне vault, на VPS в `/root/.config/kris/`:
  KRIS_GOOGLE_TOKEN  (дефолт /root/.config/kris/google-token.json)  — refresh token
  KRIS_GOOGLE_CLIENT (дефолт /root/.config/kris/google-client.json) — OAuth client secrets

Авторизация — два режима:
  --auth --local  основной: локальный сервер + браузер (запускается НА МАКЕ), токен
                  потом переносится на VPS скопом (scp). Не зависит от OOB-редиректа.
  --auth          запасной: ручной console-flow через urn:ietf:wg:oauth:2.0:oob. Google
                  свернул OOB для новых клиентов — может вернуть invalid_request.
Ранбук — Scripts/deploy/README-gdrive.md.

Зависимости (ленивый импорт внутри функций, чтобы модуль импортировался в тестах без сети):
    google-auth google-auth-oauthlib google-api-python-client

CLI:
    python3 gdrive.py --auth --local
    python3 gdrive.py --auth
    python3 gdrive.py --whoami
    python3 gdrive.py --upload FILE [--folder ESTIMATES]
    python3 gdrive.py --update FILE_ID FILE
    python3 gdrive.py --export FILE_ID MIME OUT
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import sys
import time
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_TOKEN = "/root/.config/kris/google-token.json"
DEFAULT_CLIENT = "/root/.config/kris/google-client.json"
REDIRECT_OOB = "urn:ietf:wg:oauth:2.0:oob"

# Признаки того, что Google свернул OOB-редирект для этого клиента
OOB_ERROR_MARKERS = ("redirect_uri", "invalid_request")
OOB_HINT = (
    "Похоже, Google больше не пускает ручной OOB-редирект для этого OAuth-клиента.\n"
    "Запусти на маке (там есть браузер):\n"
    "  python3 Scripts/gdrive.py --auth --local\n"
    "и перенеси токен на VPS:\n"
    "  scp /tmp/google-token.json root@163.5.29.10:/root/.config/kris/google-token.json\n"
    "  ssh root@163.5.29.10 'chmod 600 /root/.config/kris/google-token.json'"
)


def looks_like_oob_failure(message: str) -> bool:
    """Ошибка похожа на отказ Google в OOB-редиректе?"""
    low = (message or "").lower()
    return any(marker in low for marker in OOB_ERROR_MARKERS)

FOLDER_MIME = "application/vnd.google-apps.folder"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_STATUSES = {429, 500, 502, 503, 504}


class DriveError(Exception):
    """Понятная человеку ошибка работы с Drive."""


def _status_of(exc: Exception) -> int | None:
    """HTTP-код из googleapiclient.errors.HttpError, если он там есть."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _retry(call, what: str):
    """3 попытки с backoff на 5xx/429; остальные ошибки — сразу наверх."""
    last: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — решаем по HTTP-коду
            status = _status_of(exc)
            if status not in RETRY_STATUSES or attempt == RETRY_ATTEMPTS - 1:
                if status in RETRY_STATUSES:
                    raise DriveError(
                        f"Google Drive: {what} не удалось за {RETRY_ATTEMPTS} попытки "
                        f"(HTTP {status}). Попробуй позже."
                    ) from exc
                raise
            last = exc
            time.sleep(RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, 0.3))
    raise DriveError(f"Google Drive: {what} не удалось") from last  # pragma: no cover


def _escape(value: str) -> str:
    """Экранирование строки для Drive query (одинарные кавычки и слэш)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    def __init__(self, token_path=None, client_path=None):
        self.token_path = Path(token_path or DEFAULT_TOKEN)
        self.client_path = Path(client_path or DEFAULT_CLIENT)
        self._service = None

    @classmethod
    def from_env(cls) -> "Drive":
        return cls(
            token_path=os.environ.get("KRIS_GOOGLE_TOKEN", DEFAULT_TOKEN),
            client_path=os.environ.get("KRIS_GOOGLE_CLIENT", DEFAULT_CLIENT),
        )

    # --- авторизация -------------------------------------------------

    def is_configured(self) -> bool:
        """Токен существует и читается как JSON."""
        try:
            with self.token_path.open("r", encoding="utf-8") as fh:
                json.load(fh)
        except (OSError, ValueError):
            return False
        return True

    def _credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self.is_configured():
            raise DriveError(
                f"Нет токена Google Drive ({self.token_path}) — запусти gdrive.py --auth"
            )
        creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as exc:  # noqa: BLE001
                    raise DriveError(
                        "Токен Google Drive не обновился (отозван доступ?) — "
                        "запусти gdrive.py --auth"
                    ) from exc
                self._save_token(creds)
            else:
                raise DriveError(
                    "Токен Google Drive невалиден — запусти gdrive.py --auth"
                )
        return creds

    def _save_token(self, creds) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "drive", "v3", credentials=self._credentials(), cache_discovery=False
            )
        return self._service

    def _require_client(self) -> None:
        if not self.client_path.exists():
            raise DriveError(
                f"Нет client secrets ({self.client_path}). Скачай OAuth client "
                "(type Desktop) из Google Cloud Console — см. Scripts/deploy/README-gdrive.md"
            )

    def authorize_local(self, port: int = 0, open_browser: bool = True) -> None:
        """Локальный сервер + браузер. Запускать на маке, токен потом на VPS."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        self._require_client()
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_path), SCOPES)
        print("Открою браузер — войди под аккаунтом студии (admin@portalcg.xyz).")
        try:
            creds = flow.run_local_server(
                port=port,
                open_browser=open_browser,
                prompt="consent",
                access_type="offline",
            )
        except Exception as exc:  # noqa: BLE001
            raise DriveError(f"Локальная авторизация не завершилась: {exc}") from exc
        self._save_token(creds)
        self._service = None
        print(f"Токен сохранён: {self.token_path}")

    def authorize_interactive(self) -> None:
        """Ручной console-flow (запасной): печатаем URL, ждём код из браузера Антона."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        self._require_client()
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.client_path), SCOPES, redirect_uri=REDIRECT_OOB
        )
        try:
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        except Exception as exc:  # noqa: BLE001
            raise DriveError(f"Google не выдал ссылку авторизации: {exc}") from exc
        print("Открой ссылку под аккаунтом студии (admin@portalcg.xyz):\n")
        print(auth_url + "\n")
        code = input("Вставь код авторизации: ").strip()
        if not code:
            raise DriveError("Пустой код авторизации — авторизация не завершена")
        try:
            flow.fetch_token(code=code)
        except Exception as exc:  # noqa: BLE001
            raise DriveError(f"Google не принял код авторизации: {exc}") from exc
        self._save_token(flow.credentials)
        self._service = None
        print(f"Токен сохранён: {self.token_path}")

    def whoami(self) -> dict:
        about = _retry(
            lambda: self.service.about().get(fields="user").execute(), "about.get"
        )
        return about.get("user", {})

    # --- операции ----------------------------------------------------

    def ensure_folder(self, name: str, parent_id: str | None = None) -> str:
        """Ищет папку по имени (не в корзине), создаёт если нет. Возвращает id."""
        query = (
            f"name = '{_escape(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        if parent_id:
            query += f" and '{_escape(parent_id)}' in parents"
        found = _retry(
            lambda: self.service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name)",
                pageSize=10,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute(),
            f"поиск папки «{name}»",
        )
        files = found.get("files") or []
        if files:
            return files[0]["id"]

        body = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        created = _retry(
            lambda: self.service.files()
            .create(body=body, fields="id", supportsAllDrives=True)
            .execute(),
            f"создание папки «{name}»",
        )
        return created["id"]

    def upload(self, path, folder_id: str, name=None, mime=None) -> dict:
        """Заливает файл в папку. Возвращает {id, name, webViewLink}."""
        from googleapiclient.http import MediaFileUpload

        path = Path(path)
        if not path.exists():
            raise DriveError(f"Файл не найден: {path}")
        mime = mime or guess_mime(path)
        media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
        body = {"name": name or path.name, "parents": [folder_id]}
        return _retry(
            lambda: self.service.files()
            .create(
                body=body,
                media_body=media,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute(),
            f"загрузка «{path.name}»",
        )

    def update_version(self, file_id: str, path) -> dict:
        """Новая версия того же файла — ссылка и id сохраняются."""
        from googleapiclient.http import MediaFileUpload

        path = Path(path)
        if not path.exists():
            raise DriveError(f"Файл не найден: {path}")
        media = MediaFileUpload(str(path), mimetype=guess_mime(path), resumable=False)
        return _retry(
            lambda: self.service.files()
            .update(
                fileId=file_id,
                media_body=media,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute(),
            f"обновление файла {file_id}",
        )

    def download(self, file_id: str, dest) -> Path:
        from googleapiclient.http import MediaIoBaseDownload

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
        self._stream(request, dest, f"скачивание файла {file_id}")
        return dest

    def export(self, file_id: str, mime: str, dest=None) -> Path:
        """Экспорт Google Docs/Sheets/Slides в заданный mime."""
        if dest is None:
            dest = Path(f"{file_id}{_ext_for(mime)}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().export_media(fileId=file_id, mimeType=mime)
        self._stream(request, dest, f"экспорт файла {file_id}")
        return dest

    def _stream(self, request, dest: Path, what: str) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        def run():
            with dest.open("wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

        _retry(run, what)

    @staticmethod
    def sheet_url(file_id: str) -> str:
        return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"


def guess_mime(path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return XLSX_MIME
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _ext_for(mime: str) -> str:
    known = {
        XLSX_MIME: ".xlsx",
        "application/pdf": ".pdf",
        "text/csv": ".csv",
        "text/plain": ".txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    }
    return known.get(mime) or mimetypes.guess_extension(mime) or ".bin"


# --- CLI ---------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Google Drive клиент Крис")
    parser.add_argument("--auth", action="store_true", help="интерактивная авторизация")
    parser.add_argument(
        "--local",
        action="store_true",
        help="с --auth: локальный сервер + браузер (запускать на маке)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="порт локального сервера для --auth --local (0 = свободный)",
    )
    parser.add_argument("--whoami", action="store_true", help="чей токен сейчас лежит")
    parser.add_argument("--upload", metavar="FILE", help="залить файл")
    parser.add_argument(
        "--folder",
        default=os.environ.get("KRIS_DRIVE_FOLDER", "ESTIMATES"),
        help="имя папки на Drive (дефолт из KRIS_DRIVE_FOLDER, иначе ESTIMATES)",
    )
    parser.add_argument(
        "--update", nargs=2, metavar=("FILE_ID", "FILE"), help="новая версия файла"
    )
    parser.add_argument(
        "--export", nargs=3, metavar=("FILE_ID", "MIME", "OUT"), help="экспорт документа"
    )
    args = parser.parse_args(argv)

    drive = Drive.from_env()
    try:
        if args.auth:
            if args.local:
                drive.authorize_local(port=args.port)
            else:
                try:
                    drive.authorize_interactive()
                except DriveError as exc:
                    if looks_like_oob_failure(str(exc)):
                        print(f"Ошибка: {exc}", file=sys.stderr)
                        print(OOB_HINT, file=sys.stderr)
                        return 2
                    raise
            user = drive.whoami()
            print(f"Аккаунт: {user.get('emailAddress', '?')} ({user.get('displayName', '')})")
            return 0
        if args.whoami:
            user = drive.whoami()
            print(f"{user.get('emailAddress', '?')} ({user.get('displayName', '')})")
            return 0
        if args.upload:
            folder_id = drive.ensure_folder(args.folder)
            res = drive.upload(Path(args.upload), folder_id)
            print(f"{res['id']}\t{res.get('webViewLink', '')}")
            return 0
        if args.update:
            file_id, path = args.update
            res = drive.update_version(file_id, Path(path))
            print(f"{res['id']}\t{res.get('webViewLink', '')}")
            return 0
        if args.export:
            file_id, mime, out = args.export
            print(drive.export(file_id, mime, Path(out)))
            return 0
    except DriveError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
