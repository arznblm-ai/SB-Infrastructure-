#!/usr/bin/env python3
"""Синхронизация group/calendar.json в один выделенный Google-календарь.

Паттерн тот же, что у reminders.json и pin.md: модель ведёт файл, код
детерминированно исполняет. Модель не ходит в сеть и не знает про OAuth —
она только пишет список событий, а этот модуль раскладывает его в календарь.

Формат group/calendar.json — список объектов:

    [
      {
        "id": "flight-ey844",                       # стабильный slug, задаёт модель
        "title": "✈️ Вылет EY 844 SVO→AUH",
        "start": "2026-09-21T23:55",
        "end": "2026-09-22T06:40",
        "tz": "Europe/Moscow",
        "end_tz": "Asia/Dubai",                     # опц., по умолчанию = tz
        "all_day": false,
        "location": "Шереметьево, терминал C",      # опц.
        "description": "Бронь ABC123",              # опц.
        "remind_minutes": [180]                     # опц., popup-напоминания
      }
    ]

all_day=true -> start/end задаются датами YYYY-MM-DD, end в файле включительный
(в Google уезжает end.date = +1 день, как требует API).

Секреты: ключ сервисного аккаунта читается из файла и НИКОГДА не логируется.
В сообщения попадает только client_email — он не секрет и нужен для шаринга.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

try:  # pragma: no cover - зависит от версии python
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API_BASE = "https://www.googleapis.com/calendar/v3"
HTTP_TIMEOUT = 20
DEFAULT_TZ = "Europe/Moscow"
ID_PREFIX = "marco"
DEFAULT_KEY_FILE = "/root/.config/marco-group/gcal-key.json"

# Google разрешает в event id только символы base32hex (a-v, 0-9), длина 5..1024.
# "marco" + sha1-hex укладывается: hex даёт 0-9a-f, префикс — буквы до 'v'.
ALLOWED_ID_CHARS = set("0123456789abcdefghijklmnopqrstuv")


class GCalError(Exception):
    """Сетевая/протокольная ошибка Google Calendar. Без секретов в тексте."""


# ---------------------------------------------------------------------------
# Чистые функции: пути, валидация, тело запроса, план синка
# ---------------------------------------------------------------------------
def calendar_path(group_dir):
    return os.path.join(group_dir, "calendar.json")


def _zone(name):
    """ZoneInfo или None, если пояс неизвестен."""
    if not isinstance(name, str) or not name.strip():
        return None
    if ZoneInfo is None:  # pragma: no cover - старый python
        return None
    try:
        return ZoneInfo(name.strip())
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError и всё, что похоже
        return None


def _parse_date(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_naive_dt(value):
    """'YYYY-MM-DDTHH:MM[:SS]' без смещения -> datetime, иначе None."""
    raw = str(value or "").strip()
    if not raw or "T" not in raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return None
    return parsed


def _clean_text(value):
    return str(value).strip() if isinstance(value, str) else ""


def validate_event(ev):
    """(ok, error). error - человеческая строка для лога и отчёта."""
    if not isinstance(ev, dict):
        return False, "событие не объект"

    slug = _clean_text(ev.get("id"))
    if not slug:
        return False, "нет id"
    title = _clean_text(ev.get("title"))
    if not title:
        return False, "нет title"
    if ev.get("start") in (None, ""):
        return False, "нет start"

    all_day = bool(ev.get("all_day"))

    tz_name = _clean_text(ev.get("tz")) or DEFAULT_TZ
    if _zone(tz_name) is None:
        return False, "неизвестный часовой пояс tz=%s" % tz_name
    end_tz_name = _clean_text(ev.get("end_tz"))
    if end_tz_name and _zone(end_tz_name) is None:
        return False, "неизвестный часовой пояс end_tz=%s" % end_tz_name

    if all_day:
        start = _parse_date(ev.get("start"))
        if start is None:
            return False, "all_day: start должен быть датой YYYY-MM-DD"
        end_raw = ev.get("end")
        if end_raw not in (None, ""):
            end = _parse_date(end_raw)
            if end is None:
                return False, "all_day: end должен быть датой YYYY-MM-DD"
            if end < start:
                return False, "end раньше start"
    else:
        start_dt = _parse_naive_dt(ev.get("start"))
        if start_dt is None:
            return False, "start должен быть YYYY-MM-DDTHH:MM без смещения"
        end_raw = ev.get("end")
        if end_raw not in (None, ""):
            end_dt = _parse_naive_dt(end_raw)
            if end_dt is None:
                return False, "end должен быть YYYY-MM-DDTHH:MM без смещения"
            # Разные пояса у вылета и прилёта - сравниваем в абсолютном времени.
            start_abs = start_dt.replace(tzinfo=_zone(tz_name))
            end_abs = end_dt.replace(tzinfo=_zone(end_tz_name or tz_name))
            if end_abs < start_abs:
                return False, "end раньше start"

    reminders = ev.get("remind_minutes")
    if reminders is not None:
        if not isinstance(reminders, list) or not reminders:
            return False, "remind_minutes должен быть непустым списком минут"
        for item in reminders:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                return False, "remind_minutes: только целые минуты >= 0"

    return True, None


def google_event_id(slug):
    """Стабильный id события в Google из slug модели."""
    digest = hashlib.sha1(str(slug).encode("utf-8")).hexdigest()
    return ID_PREFIX + digest


def to_google_body(ev):
    """Тело события для Calendar API v3. Событие должно быть валидным."""
    tz_name = _clean_text(ev.get("tz")) or DEFAULT_TZ
    end_tz_name = _clean_text(ev.get("end_tz")) or tz_name
    all_day = bool(ev.get("all_day"))

    body = {"summary": _clean_text(ev.get("title"))}
    location = _clean_text(ev.get("location"))
    if location:
        body["location"] = location
    description = _clean_text(ev.get("description"))
    if description:
        body["description"] = description

    if all_day:
        start = _parse_date(ev.get("start"))
        end_raw = ev.get("end")
        end = _parse_date(end_raw) if end_raw not in (None, "") else start
        body["start"] = {"date": start.isoformat()}
        # Google трактует end.date как исключающий, в файле он включительный.
        body["end"] = {"date": (end + timedelta(days=1)).isoformat()}
    else:
        start_dt = _parse_naive_dt(ev.get("start"))
        end_raw = ev.get("end")
        end_dt = _parse_naive_dt(end_raw) if end_raw not in (None, "") else start_dt
        body["start"] = {
            "dateTime": start_dt.replace(microsecond=0).isoformat(),
            "timeZone": tz_name,
        }
        body["end"] = {
            "dateTime": end_dt.replace(microsecond=0).isoformat(),
            "timeZone": end_tz_name,
        }

    reminders = ev.get("remind_minutes")
    if reminders:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": int(m)} for m in reminders
            ],
        }
    else:
        body["reminders"] = {"useDefault": True}
    return body


def event_hash(ev):
    """Хэш по каноническому телу: косметика в файле синк не дёргает."""
    payload = json.dumps(to_google_body(ev), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_sync(events, synced_state):
    """Что отправить и что удалить. Ничего не мутирует.

    synced_state: {slug: hash} из state.json (ключ gcal_synced).
    Возвращает {"upsert": [...], "delete": [...], "skipped_invalid": [(slug, err)]}.
    """
    synced = dict(synced_state or {})
    upsert = []
    skipped = []
    seen = set()

    if not isinstance(events, list):
        return {"upsert": [], "delete": [], "skipped_invalid": [(None, "calendar.json не список")]}

    for index, ev in enumerate(events):
        ok, error = validate_event(ev)
        slug = _clean_text(ev.get("id")) if isinstance(ev, dict) else ""
        if not ok:
            skipped.append((slug or "#%d" % index, error))
            continue
        if slug in seen:
            skipped.append((slug, "дубликат id"))
            continue
        seen.add(slug)
        digest = event_hash(ev)
        if synced.get(slug) == digest:
            continue  # ничего не изменилось - в сеть не ходим
        upsert.append(
            {
                "slug": slug,
                "event_id": google_event_id(slug),
                "body": to_google_body(ev),
                "hash": digest,
            }
        )

    # Битое событие не повод стирать его из календаря: пропускаем и ждём правки.
    broken = {slug for slug, _ in skipped}
    delete = [
        {"slug": slug, "event_id": google_event_id(slug)}
        for slug in synced
        if slug not in seen and slug not in broken
    ]
    return {"upsert": upsert, "delete": delete, "skipped_invalid": skipped}


def load_calendar_file(path):
    """(события, ошибка). Нет файла -> (None, 'нет файла ...')."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "нет файла %s" % path
    except (OSError, ValueError) as exc:
        return None, "%s: %s" % (path, exc)


def empty_report():
    return {
        "upserted": 0,
        "deleted": 0,
        "skipped_invalid": [],
        "errors": [],
        "events": 0,
    }


def sync(calendar_path_or_file, state, client):
    """Синк файла в календарь. state мутируется только по успешным событиям.

    Возвращает отчёт: upserted, deleted, skipped_invalid, errors, events.
    Сбойное событие остаётся вне gcal_synced, значит повторится на следующем тике.
    """
    report = empty_report()
    events, error = load_calendar_file(calendar_path_or_file)
    if error is not None:
        if "нет файла" not in error:
            report["errors"].append(error)
        # Нет файла - не повод сносить уже созданные события.
        return report

    synced = dict((state or {}).get("gcal_synced") or {})
    plan = plan_sync(events, synced)
    report["skipped_invalid"] = list(plan["skipped_invalid"])
    report["events"] = (
        len(events) - len(plan["skipped_invalid"]) if isinstance(events, list) else 0
    )
    if report["events"] < 0:
        report["events"] = 0

    for item in plan["upsert"]:
        try:
            client.upsert(item["event_id"], item["body"])
        except Exception as exc:  # noqa: BLE001 - одно событие не роняет остальные
            report["errors"].append("%s: %s" % (item["slug"], exc))
            continue
        synced[item["slug"]] = item["hash"]
        report["upserted"] += 1

    for item in plan["delete"]:
        try:
            client.delete(item["event_id"])
        except Exception as exc:  # noqa: BLE001
            report["errors"].append("%s (удаление): %s" % (item["slug"], exc))
            continue
        synced.pop(item["slug"], None)
        report["deleted"] += 1

    if state is not None:
        state["gcal_synced"] = synced
    return report


def enabled(calendar_id, key_file):
    """Синк включён только если задан календарь и ключ реально лежит на диске."""
    return bool((calendar_id or "").strip()) and os.path.isfile(key_file or "")


# ---------------------------------------------------------------------------
# IO: клиент Google Calendar (ленивые импорты - тесты живут без google-libs)
# ---------------------------------------------------------------------------
def read_client_email(key_file):
    """client_email из ключа. Нужен в подсказках про шаринг, секретом не является."""
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    email = data.get("client_email") if isinstance(data, dict) else None
    return email if isinstance(email, str) and email else None


class GCalClient:
    """Тонкая обёртка над REST v3 поверх AuthorizedSession сервисного аккаунта."""

    def __init__(self, calendar_id, key_file=DEFAULT_KEY_FILE, timeout=HTTP_TIMEOUT):
        self.calendar_id = (calendar_id or "").strip()
        self.key_file = key_file
        self.timeout = timeout
        self._session = None

    # -- внутреннее ---------------------------------------------------------
    def _url(self, suffix=""):
        return "%s/calendars/%s%s" % (API_BASE, quote(self.calendar_id, safe=""), suffix)

    def session(self):
        if self._session is not None:
            return self._session
        if not os.path.isfile(self.key_file):
            raise GCalError("нет файла ключа сервисного аккаунта")
        try:  # ленивый импорт: без google-libs модуль всё равно импортируется
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
        except ImportError as exc:
            raise GCalError(
                "нет google-auth в окружении (поставь google-auth requests): %s" % exc
            ) from exc
        try:
            creds = service_account.Credentials.from_service_account_file(
                self.key_file, scopes=SCOPES
            )
        except Exception as exc:  # noqa: BLE001 - текст ключа наружу не тащим
            raise GCalError("ключ сервисного аккаунта не читается (%s)" % type(exc).__name__)
        self._session = AuthorizedSession(creds)
        return self._session

    def _request(self, method, url, json_body=None):
        session = self.session()
        try:
            return session.request(
                method, url, json=json_body, timeout=self.timeout
            )
        except Exception as exc:  # noqa: BLE001 - сеть/таймаут
            raise GCalError("сеть недоступна (%s)" % type(exc).__name__) from exc

    @staticmethod
    def _fail(response, what):
        raise GCalError("%s: HTTP %s" % (what, getattr(response, "status_code", "?")))

    # -- публичное ----------------------------------------------------------
    def upsert(self, event_id, body):
        """PUT, при 404 - insert с явным id, при 409 на insert - снова PUT."""
        response = self._request("PUT", self._url("/events/" + quote(event_id, safe="")), body)
        if response.status_code == 404:
            payload = dict(body)
            payload["id"] = event_id
            insert = self._request("POST", self._url("/events"), payload)
            if insert.status_code == 409:  # событие уже есть (гонка/восстановление)
                response = self._request(
                    "PUT", self._url("/events/" + quote(event_id, safe="")), body
                )
            else:
                response = insert
        if 200 <= response.status_code < 300:
            return True
        self._fail(response, "запись события")

    def delete(self, event_id):
        """404/410 считаем успехом: события уже нет, цель достигнута."""
        response = self._request(
            "DELETE", self._url("/events/" + quote(event_id, safe=""))
        )
        if 200 <= response.status_code < 300 or response.status_code in (404, 410):
            return True
        self._fail(response, "удаление события")

    def check(self):
        """(ok, сообщение по-русски) - для CLI --check."""
        email = read_client_email(self.key_file) or "сервисный аккаунт"
        if not self.calendar_id:
            return False, "GCAL_CALENDAR_ID пуст — синк календаря выключен."
        try:
            response = self._request("GET", self._url())
            if response.status_code == 403:
                # У scope calendar.events может не быть прав на сам календарь.
                response = self._request("GET", self._url("/events?maxResults=1"))
        except GCalError as exc:
            return False, "Не получилось достучаться до Google: %s" % exc

        code = getattr(response, "status_code", 0)
        if 200 <= code < 300:
            return True, "Календарь %s доступен, ключ рабочий (%s)." % (
                self.calendar_id,
                email,
            )
        if code in (403, 404):
            return False, (
                "Календарь %s недоступен (HTTP %s). Скорее всего он не расшарен на %s "
                "с правом «Внесение изменений в мероприятия»." % (self.calendar_id, code, email)
            )
        if code == 401:
            return False, "Ключ сервисного аккаунта отклонён (HTTP 401). Перевыпусти ключ."
        return False, "Google ответил HTTP %s." % code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):  # pragma: no cover - ручной инструмент
    parser = argparse.ArgumentParser(description="Синк group/calendar.json в Google Calendar")
    parser.add_argument("--check", action="store_true", help="проверить ключ и доступ")
    parser.add_argument("--sync", action="store_true", help="разовый синк")
    parser.add_argument(
        "--calendar", default=os.environ.get("GCAL_CALENDAR_ID", ""), help="id календаря"
    )
    parser.add_argument(
        "--key", default=os.environ.get("GCAL_KEY_FILE", DEFAULT_KEY_FILE), help="файл ключа"
    )
    parser.add_argument(
        "--group-dir",
        default=os.environ.get(
            "MARCO_GROUP_DIR", "/root/second-brain/infrastructure/Travel Agent/group"
        ),
        help="каталог состояния поездки",
    )
    parser.add_argument(
        "--state",
        default=os.path.join(
            os.environ.get("MARCO_STATE_DIR", "/root/.config/marco-group"), "state.json"
        ),
        help="файл состояния (там живёт gcal_synced)",
    )
    args = parser.parse_args(argv)

    if not args.check and not args.sync:
        parser.error("нужен --check или --sync")

    if not os.path.isfile(args.key):
        print("Нет файла ключа: %s" % args.key)
        return 1

    client = GCalClient(args.calendar, args.key)

    if args.check:
        ok, message = client.check()
        print(message)
        if not ok:
            return 1

    if args.sync:
        state = {}
        try:
            with open(args.state, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            print("state.json не прочитан, синкую с нуля")
        report = sync(calendar_path(args.group_dir), state, client)
        print(
            "Записано: %d, удалено: %d, пропущено невалидных: %d, ошибок: %d"
            % (
                report["upserted"],
                report["deleted"],
                len(report["skipped_invalid"]),
                len(report["errors"]),
            )
        )
        for slug, error in report["skipped_invalid"]:
            print("  пропущено %s: %s" % (slug, error))
        for error in report["errors"]:
            print("  ошибка %s" % error)
        try:
            with open(args.state, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            print("Не смог записать state.json: %s" % exc)
            return 1
        if report["errors"]:
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
