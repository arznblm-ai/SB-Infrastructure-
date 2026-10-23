#!/usr/bin/env python3
"""busy_sync.py — синк занятости: личные календари → приглашения в рабочий anton@looch.ai.

План и решения: tasks/Calendar Busy Sync/{automation} {plan} синк занятости в рабочий календарь – 2026-10-21.md
(ревизия 2026-10-22: приглашения вместо OAuth looch.ai — админ looch.ai заблокировал OAuth).

Раз в 10 минут (busy-sync.timer на VPS), от имени tony@portalcg.us (токен Аси, gcal-mcp):
  1. free/busy источников (primary = tony@portalcg.us, a.rznblm@gmail.com) — названий
     событий скрипт не запрашивает;
  2. desired = union(источники), фрагменты < MIN_MINUTES — прочь;
  3. existing = наши блоки в служебном календаре BLOCK_CALENDAR («Busy → Луч»),
     метка extendedProperties.private.busySync=1;
  4. лишние блоки удалить, недостающие создать. Каждый блок — событие с одним участником
     INVITEE (anton@looch.ai): приглашение появляется в рабочем календаре частным и «Занят».
     Все записи с sendUpdates=none — писем нет. State-файла нет: состояние = сами блоки.

Токен-файл (google-calendar-token.json) общий с gcal-mcp Аси: скрипт его НИКОГДА не
перезаписывает, access token обновляется только в памяти процесса.

Fail-safe: если хоть один источник вернул ошибку — ничего не удаляем (только создаём
по живым источникам), статус degraded и алерт.

Защита от петли: BLOCK_CALENDAR и INVITEE не могут быть среди SOURCES
(freebusy primary служебный календарь не включает — так и надо).

Health: $STATE_DIRECTORY (или /var/lib/busy-sync)/health.json. Одно сообщение в личку
Аси при поломке и одно при восстановлении, дедуп по ключу «статус|причина» (как в
asya_watchdog.sh). Токен Аси читается из /root/.config/asya/.env и нигде не печатается.

Только stdlib (на VPS нет googleapiclient). Названия событий не запрашиваются
(fields=...) и не логируются.

Запуск:
  python3 busy_sync.py              # синк (так его зовёт systemd)
  python3 busy_sync.py --dry-run    # план изменений, ничего не пишет и не алертит
  python3 busy_sync.py --check      # проверка доступа: токен, free/busy по источникам, BLOCK_CALENDAR
                                    # rc 0 — всё ok, 2 — degraded (главный источник ok, другой нет), 1 — сломано
  python3 busy_sync.py --purge      # удалить ВСЕ наши блоки в окне (откат; сперва выключи таймер)
  python3 busy_sync.py --purge --dry-run   # сколько блоков удалил бы --purge

env: BUSY_SYNC_ENV — путь к env-файлу; BUSY_SYNC_<KEY> перекрывает ключ конфига;
     TOKEN_FILE — путь к токену (как и BUSY_SYNC_TOKEN_FILE);
     BUSY_SYNC_NO_ALERT=1 — не слать алерты; ASYA_ENV_FILE — откуда брать токен бота.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# ── константы ───────────────────────────────────────────────────────────────
MSK = timezone(timedelta(hours=3), "MSK")
API = "https://www.googleapis.com/calendar/v3"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
MARK_KEY, MARK_VAL = "busySync", "1"
CHAT_ID = "324186708"
ENV_FILE_DEFAULT = "/root/.config/second-brain/busy-sync.env"
ASYA_ENV_DEFAULT = "/root/.config/asya/.env"
CHECK_CMD = "python3 /opt/busy-sync/busy_sync.py --check"
TOKEN_FIX = ("токен tony@portalcg.us общий с gcal-mcp Аси (календарь у Аси, скорее всего, тоже не работает): "
             "перевыпусти его тем же способом, каким выпускался; файл не правь руками. Потом " + CHECK_CMD)
SHARE_FIX = ("расшарь free/busy {src} на tony@portalcg.us: настройки календаря {src} → «Доступ» → "
             "tony@portalcg.us с правом «только свободно/занято» (шаг H1'). Пока источник сломан, "
             "блоки не удаляются (fail-safe)")
TRANSIENT_ALERT_AFTER = 3        # сеть/5xx: алерт только после 3 провалов подряд (~30 мин)
WRITE_PAUSE = 0.15               # пауза между записями в API (квота ~10 rps на пользователя)
MAX_WRITES = 300                 # потолок записей за прогон, остальное — на следующем тике
EVENT_FIELDS = ("timeZone,nextPageToken,items(id,status,start,end,extendedProperties/private)")

DEFAULTS = {
    "BLOCK_CALENDAR": "c_306448966e57feda06b750938dc35b2fce3e0914a99852482b67272fe950e7c5@group.calendar.google.com",
    "INVITEE": "anton@looch.ai",
    "SOURCES": "primary,a.rznblm@gmail.com",
    "HORIZON_DAYS": "30",
    "MIN_MINUTES": "5",
    "TITLE": "Busy",
    "TOKEN_FILE": "/root/.config/second-brain/google-calendar-token.json",
}


# ── конфиг ──────────────────────────────────────────────────────────────────
@dataclass
class Config:
    block_calendar: str
    invitee: str
    sources: list[str]
    horizon_days: int
    min_minutes: int
    title: str
    token_file: str

    @property
    def main_source(self) -> str:
        """Главный источник (его падение блокирует включение таймера): primary, иначе первый."""
        return "primary" if "primary" in self.sources else self.sources[0]


def parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k] = v
    except FileNotFoundError:
        pass
    return out


def load_config(env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    raw = dict(DEFAULTS)
    raw.update({k: v for k, v in parse_env_file(env.get("BUSY_SYNC_ENV", ENV_FILE_DEFAULT)).items() if k in DEFAULTS})
    if env.get("TOKEN_FILE"):
        raw["TOKEN_FILE"] = env["TOKEN_FILE"]
    for k in DEFAULTS:
        if env.get("BUSY_SYNC_" + k):
            raw[k] = env["BUSY_SYNC_" + k]
    sources = [s.strip() for s in raw["SOURCES"].split(",") if s.strip()]
    if not sources:
        raise SyncError("config", "no_sources", "в конфиге пустой SOURCES", "заполни SOURCES в " + ENV_FILE_DEFAULT)
    low = {s.lower() for s in sources}
    if raw["BLOCK_CALENDAR"].lower() in low:
        raise SyncError("config", "block_in_sources", "BLOCK_CALENDAR указан среди SOURCES — это петля",
                        "убери служебный календарь «Busy → Луч» из SOURCES")
    if raw["INVITEE"].lower() in low:
        raise SyncError("config", "invitee_in_sources", "INVITEE указан среди SOURCES — это петля",
                        "убери рабочий адрес из SOURCES")
    return Config(block_calendar=raw["BLOCK_CALENDAR"], invitee=raw["INVITEE"], sources=sources,
                  horizon_days=int(raw["HORIZON_DAYS"]), min_minutes=int(raw["MIN_MINUTES"]),
                  title=raw["TITLE"], token_file=raw["TOKEN_FILE"])


# ── ошибки ──────────────────────────────────────────────────────────────────
class SyncError(Exception):
    """kind: auth | api | network | config. key — стабильный код для дедупа алертов."""

    def __init__(self, kind: str, key: str, detail: str, fix: str = ""):
        super().__init__(detail)
        self.kind, self.key, self.detail, self.fix = kind, key, detail, fix

    @property
    def transient(self) -> bool:
        return self.kind == "network"


# ── интервалы (epoch-секунды, полуоткрытые [s, e)) ─────────────────────────────
Interval = tuple[int, int]


def merge(intervals) -> list[Interval]:
    """Сортирует и склеивает пересекающиеся и соприкасающиеся интервалы."""
    out: list[list[int]] = []
    for s, e in sorted((int(s), int(e)) for s, e in intervals if e > s):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def drop_short(intervals, min_sec: int) -> list[Interval]:
    return [(s, e) for s, e in intervals if e - s >= min_sec]


def clip(intervals, lo: int, hi: int) -> list[Interval]:
    return [(max(s, lo), min(e, hi)) for s, e in intervals if min(e, hi) > max(s, lo)]


def diff(desired, existing) -> tuple[list[Interval], list[str]]:
    """desired: [(s,e)], existing: [(event_id, s, e)] → (создать, удалить id).

    Совпадение — по (s,e). Дубли среди existing (два блока на один интервал) удаляются.
    """
    want = set(desired)
    kept: set[Interval] = set()
    to_delete: list[str] = []
    for ev_id, s, e in sorted(existing, key=lambda x: (x[1], x[2], x[0])):
        key = (s, e)
        if key in want and key not in kept:
            kept.add(key)
        else:
            to_delete.append(ev_id)
    to_create = sorted(k for k in want if k not in kept)
    return to_create, to_delete


# ── время ────────────────────────────────────────────────────────────────────
def tz_by_name(name: str | None):
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return MSK


def parse_when(obj: dict, tz) -> int:
    """start/end события Google → epoch. all-day (date) — полночь в поясе календаря."""
    if obj.get("dateTime"):
        dt = datetime.fromisoformat(obj["dateTime"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz_by_name(obj.get("timeZone")) if obj.get("timeZone") else tz)
        return int(dt.timestamp())
    d = date.fromisoformat(obj["date"])
    return int(datetime(d.year, d.month, d.day, tzinfo=tz).timestamp())


def iso_z(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_msk(ts: int) -> str:
    return datetime.fromtimestamp(ts, MSK).strftime("%d.%m %H:%M")


def compute_window(now: int, horizon_days: int) -> Interval:
    """Окно, выровненное по полуночи МСК: [сегодня 00:00, сегодня + horizon + 1 день).

    Края двигаются раз в сутки, а не каждый прогон: иначе интервал, срезанный краем
    окна, менял бы (start,end) каждые 10 минут и блок пересоздавался бы на каждом тике.
    """
    today = datetime.fromtimestamp(now, MSK).date()
    lo = datetime(today.year, today.month, today.day, tzinfo=MSK)
    hi = lo + timedelta(days=horizon_days + 1)
    return int(lo.timestamp()), int(hi.timestamp())


# ── HTTP-слой ────────────────────────────────────────────────────────────────
def http_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: int = 30):
    """→ (status, bytes). Сетевые сбои — OSError (URLError/timeout) наружу."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _err_reason(data: dict) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        errs = err.get("errors") or []
        if errs and isinstance(errs[0], dict) and errs[0].get("reason"):
            return str(errs[0]["reason"])
        return str(err.get("status") or err.get("code") or "?")
    return str(err or "?")


# ── токен (формат google-auth authorized_user; файл только ЧИТАЕМ) ──────────────
def atomic_write_json(path: str, data: dict, mode: int = 0o600) -> None:
    """Для health.json. Токен-файл этой функцией не пишется никогда."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class TokenStore:
    """Читает токен-файл один раз; access token живёт только в памяти процесса.

    Файл общий с gcal-mcp Аси, поэтому он не перезаписывается ни при refresh, ни при
    ротации refresh_token (если Google пришлёт новый — используем в памяти, на диск не пишем).
    Access token из файла не используется: срок его жизни неизвестен (его обновляет Ася),
    поэтому первый же вызов делает свой refresh.
    """

    def __init__(self, path: str, transport=http_transport, clock=time.time):
        self.path, self.transport, self.clock = path, transport, clock
        self.data: dict | None = None
        self._access: str | None = None
        self._expires_at: float = 0.0

    def _load(self) -> dict:
        if self.data is None:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except FileNotFoundError:
                raise SyncError("auth", "token_missing", f"нет файла токена {self.path}",
                                "это токен tony@portalcg.us от gcal-mcp Аси — проверь путь TOKEN_FILE "
                                "и что Ася на VPS на месте") from None
            except ValueError:
                raise SyncError("auth", "token_broken", f"битый JSON в {self.path}", TOKEN_FIX) from None
        return self.data

    def access_token(self) -> str:
        if not self._access or self._expires_at - self.clock() < 120:
            self.refresh()
        return self._access  # type: ignore[return-value]

    def refresh(self) -> None:
        data = self._load()
        need = [k for k in ("refresh_token", "client_id", "client_secret") if not data.get(k)]
        if need:
            raise SyncError("auth", "token_incomplete", f"в {self.path} нет полей: {', '.join(need)}", TOKEN_FIX)
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
        }).encode()
        try:
            status, raw = self.transport("POST", data.get("token_uri") or DEFAULT_TOKEN_URI,
                                         {"Content-Type": "application/x-www-form-urlencoded"}, body)
        except OSError as exc:
            raise SyncError("network", "token_network", f"не достучался до OAuth Google: {type(exc).__name__}",
                            "проверь сеть VPS; само пройдёт, если это короткий сбой") from None
        resp = _json(raw)
        if status != 200 or not resp.get("access_token"):
            err = str(resp.get("error") or status)
            if err in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise SyncError("auth", "token_revoked:" + err,
                                f"refresh token tony@portalcg.us отозван или истёк ({err})", TOKEN_FIX)
            kind = "network" if status >= 500 else "auth"
            raise SyncError(kind, f"token_http_{status}", f"обновление токена не прошло: HTTP {status} {err}",
                            f"проверь на VPS: {CHECK_CMD}")
        self._access = resp["access_token"]
        self._expires_at = self.clock() + int(resp.get("expires_in", 3600))
        if resp.get("refresh_token"):            # ротация: только в памяти, файл не трогаем
            data["refresh_token"] = resp["refresh_token"]


# ── Calendar API ─────────────────────────────────────────────────────────────
class CalendarAPI:
    def __init__(self, tokens: TokenStore, transport=http_transport, sleep=time.sleep):
        self.tokens, self.transport, self.sleep = tokens, transport, sleep

    def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        url = API + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        payload = json.dumps(body).encode() if body is not None else None
        refreshed = retried = False
        while True:
            headers = {"Authorization": "Bearer " + self.tokens.access_token(), "Accept": "application/json"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            try:
                status, raw = self.transport(method, url, headers, payload)
            except OSError as exc:
                raise SyncError("network", "api_network", f"Google Calendar API не отвечает: {type(exc).__name__}",
                                "проверь сеть VPS; само пройдёт, если это короткий сбой") from None
            data = _json(raw)
            if status == 401 and not refreshed:
                refreshed = True
                self.tokens.refresh()
                continue
            reason = _err_reason(data) if status >= 400 else ""
            if not retried and (status == 429 or status >= 500 or
                                (status == 403 and reason in ("rateLimitExceeded", "userRateLimitExceeded"))):
                retried = True
                self.sleep(2)
                continue
            return status, data

    def freebusy(self, sources: list[str], lo: int, hi: int) -> dict[str, tuple[list[Interval] | None, str]]:
        """→ {источник: (интервалы | None, причина ошибки)}."""
        status, data = self.call("POST", "/freeBusy", body={
            "timeMin": iso_z(lo), "timeMax": iso_z(hi), "timeZone": "Europe/Moscow",
            "items": [{"id": s} for s in sources]})
        if status != 200:
            raise _api_error("freebusy", status, data)
        cals = data.get("calendars") or {}
        # Google может вернуть «primary» под адресом аккаунта: если ровно один запрошенный id
        # не нашёлся и ровно один ключ ответа не запрашивался — это он
        missing = [s for s in sources if s not in cals]
        extra = [k for k in cals if k not in sources]
        alias = {missing[0]: extra[0]} if len(missing) == 1 and len(extra) == 1 else {}
        out: dict[str, tuple[list[Interval] | None, str]] = {}
        for src in sources:
            c = cals.get(src, cals.get(alias.get(src, ""), None))
            if not isinstance(c, dict):
                out[src] = (None, "missing")
                continue
            errs = c.get("errors") or []
            if errs:
                out[src] = (None, ",".join(sorted({str(e.get("reason", "?")) for e in errs if isinstance(e, dict)})) or "error")
                continue
            busy = []
            for b in c.get("busy") or []:
                busy.append((parse_when({"dateTime": b["start"]}, MSK), parse_when({"dateTime": b["end"]}, MSK)))
            out[src] = (busy, "")
        return out

    def list_events(self, calendar: str, lo: int, hi: int) -> tuple[list[dict], str | None]:
        """Только наши блоки (privateExtendedProperty busySync=1), без названий (fields)."""
        params = {"timeMin": iso_z(lo), "timeMax": iso_z(hi), "singleEvents": "true", "showDeleted": "false",
                  "maxResults": "2500", "fields": EVENT_FIELDS,
                  "privateExtendedProperty": f"{MARK_KEY}={MARK_VAL}"}
        items: list[dict] = []
        tz_name = None
        while True:
            status, data = self.call("GET", f"/calendars/{urllib.parse.quote(calendar)}/events", params=params)
            if status != 200:
                raise _api_error("events_list", status, data)
            tz_name = tz_name or data.get("timeZone")
            items.extend(data.get("items") or [])
            tok = data.get("nextPageToken")
            if not tok:
                return items, tz_name
            params = dict(params, pageToken=tok)

    def insert_event(self, calendar: str, body: dict) -> None:
        status, data = self.call("POST", f"/calendars/{urllib.parse.quote(calendar)}/events",
                                 params={"sendUpdates": "none"}, body=body)
        if status != 200:
            raise _api_error("insert", status, data)

    def delete_event(self, calendar: str, event_id: str) -> None:
        status, data = self.call("DELETE", f"/calendars/{urllib.parse.quote(calendar)}/events/{urllib.parse.quote(event_id)}",
                                 params={"sendUpdates": "none"})
        if status not in (200, 204, 404, 410):
            raise _api_error("delete", status, data)


def _api_error(op: str, status: int, data: dict) -> SyncError:
    reason = _err_reason(data)
    if status >= 500 or status == 429:
        return SyncError("network", f"{op}_http_{status}", f"Google API {op}: HTTP {status} ({reason})",
                         "временный сбой Google — само пройдёт; если висит долго — " + CHECK_CMD)
    if status in (401, 403):
        return SyncError("auth", f"{op}_http_{status}:{reason}",
                         f"нет доступа ({op}: HTTP {status} {reason})",
                         "проверь, что токен tony@portalcg.us со scope calendar и что служебный календарь "
                         "«Busy → Луч» (BLOCK_CALENDAR) существует и принадлежит этому аккаунту; " + CHECK_CMD)
    if status == 404 and op != "freebusy":
        return SyncError("api", f"{op}_http_404:{reason}", f"служебный календарь не найден ({op}: HTTP 404)",
                         "проверь BLOCK_CALENDAR: календарь «Busy → Луч» в аккаунте tony@portalcg.us не удалён?")
    return SyncError("api", f"{op}_http_{status}:{reason}", f"Google API {op}: HTTP {status} ({reason})",
                     f"проверь на VPS: {CHECK_CMD}")


# ── блоки ────────────────────────────────────────────────────────────────────
def is_ours(ev: dict) -> bool:
    return ((ev.get("extendedProperties") or {}).get("private") or {}).get(MARK_KEY) == MARK_VAL


def event_interval(ev: dict, tz) -> Interval | None:
    try:
        return parse_when(ev["start"], tz), parse_when(ev["end"], tz)
    except (KeyError, ValueError, TypeError):
        return None


def block_body(title: str, invitee: str, s: int, e: int) -> dict:
    return {
        "summary": title,
        "visibility": "private",
        "transparency": "opaque",
        "start": {"dateTime": iso_z(s)},
        "end": {"dateTime": iso_z(e)},
        "attendees": [{"email": invitee}],
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {"private": {MARK_KEY: MARK_VAL}},
        "guestsCanSeeOtherGuests": False,
        "guestsCanInviteOthers": False,
        "guestsCanModify": False,
    }


# ── синк ─────────────────────────────────────────────────────────────────────
@dataclass
class Result:
    status: str                      # ok | degraded | error
    key: str = ""                    # стабильный ключ причины (дедуп алертов)
    detail: str = ""
    fix: str = ""
    transient: bool = False
    stats: dict = field(default_factory=dict)


def plan_sync(cfg: Config, api: CalendarAPI, now: int) -> dict:
    """Читает источники и служебный календарь, считает план. Ничего не пишет."""
    lo, hi = compute_window(now, cfg.horizon_days)
    fb = api.freebusy(cfg.sources, lo, hi)
    src_busy: list[Interval] = []
    failed: list[tuple[str, str]] = []
    for src in cfg.sources:
        busy, err = fb.get(src, (None, "missing"))
        if busy is None:
            failed.append((src, err))
        else:
            src_busy.extend(busy)

    items, tz_name = api.list_events(cfg.block_calendar, lo, hi)
    tz = tz_by_name(tz_name)
    existing: list[tuple[str, int, int]] = []
    for ev in items:
        if not is_ours(ev) or ev.get("status") == "cancelled":
            continue
        iv = event_interval(ev, tz)
        if iv is None or iv[1] <= now:
            continue
        # сравниваем по срезу окном: блок, начатый до полуночи, совпадёт с desired,
        # который freebusy тоже срезал по краю окна
        c = clip([iv], lo, hi)
        s, e = c[0] if c else iv
        existing.append((ev["id"], s, e))

    desired = drop_short(clip(merge(src_busy), lo, hi), cfg.min_minutes * 60)
    desired = [d for d in desired if d[1] > now]          # прошлое не трогаем
    to_create, to_delete = diff(desired, existing)
    skipped_deletes = 0
    if failed:                                           # fail-safe: при сбое источника не удаляем
        skipped_deletes, to_delete = len(to_delete), []
    return {"window": (lo, hi), "failed": failed, "desired": desired, "existing": existing,
            "create": to_create, "delete": to_delete, "skipped_deletes": skipped_deletes}


def apply_plan(cfg: Config, api: CalendarAPI, plan: dict, sleep=time.sleep, log=print) -> tuple[int, int, list[str]]:
    """Создаёт, потом удаляет (чтобы не было окна без блока). → (создано, удалено, ошибки)."""
    created = deleted = 0
    errors: list[str] = []
    budget = MAX_WRITES
    for s, e in plan["create"]:
        if budget <= 0:
            break
        budget -= 1
        try:
            api.insert_event(cfg.block_calendar, block_body(cfg.title, cfg.invitee, s, e))
            created += 1
        except SyncError as exc:
            errors.append(exc.key)
            log(f"  ✗ создать {fmt_msk(s)}–{fmt_msk(e)}: {exc.detail}")
        sleep(WRITE_PAUSE)
    for ev_id in plan["delete"]:
        if budget <= 0:
            break
        budget -= 1
        try:
            api.delete_event(cfg.block_calendar, ev_id)
            deleted += 1
        except SyncError as exc:
            errors.append(exc.key)
            log(f"  ✗ удалить блок {ev_id}: {exc.detail}")
        sleep(WRITE_PAUSE)
    if budget <= 0 and created + deleted < len(plan["create"]) + len(plan["delete"]):
        log(f"  ⚠ упёрся в потолок {MAX_WRITES} записей за прогон — остальное на следующем тике")
    return created, deleted, errors


def degraded_result(failed: list[tuple[str, str]], stats: dict) -> Result:
    names = ", ".join(f"{s} ({r})" for s, r in failed)
    fixes = "; ".join(SHARE_FIX.format(src=s) for s, _ in failed if s != "primary")
    if any(s == "primary" for s, _ in failed):
        fixes = ("primary (tony@portalcg.us) не отвечает на free/busy — проверь токен: " + CHECK_CMD
                 + ("; " + fixes if fixes else ""))
    return Result("degraded", key="sources:" + ";".join(f"{s}={r}" for s, r in sorted(failed)),
                  detail=f"нет доступа к free/busy {names}", fix=fixes, stats=stats)


def run_sync(cfg: Config, api: CalendarAPI, now: int, dry_run: bool = False, sleep=time.sleep, log=print) -> Result:
    plan = plan_sync(cfg, api, now)
    lo, hi = plan["window"]
    log(f"окно {fmt_msk(lo)} — {fmt_msk(hi)} МСК; источники: {', '.join(cfg.sources)}")
    for src, reason in plan["failed"]:
        log(f"  ✗ источник {src}: {reason}")
    log(f"  нужно блоков {len(plan['desired'])}, есть {len(plan['existing'])}")
    log(f"  план: создать {len(plan['create'])}, удалить {len(plan['delete'])}"
        + (f" (ещё {plan['skipped_deletes']} удалений пропущено — fail-safe)" if plan["skipped_deletes"] else ""))
    stats = {"desired": len(plan["desired"]), "existing": len(plan["existing"]),
             "create": len(plan["create"]), "delete": len(plan["delete"]),
             "skipped_deletes": plan["skipped_deletes"]}
    if dry_run:
        for s, e in plan["create"]:
            log(f"    + {fmt_msk(s)} – {fmt_msk(e)}")
        by_id = {i: (s, e) for i, s, e in plan["existing"]}
        for i in plan["delete"]:
            s, e = by_id.get(i, (0, 0))
            log(f"    − {fmt_msk(s)} – {fmt_msk(e)}")
        log("  dry-run: ничего не записано")
    else:
        created, deleted, errors = apply_plan(cfg, api, plan, sleep=sleep, log=log)
        stats.update(created=created, deleted=deleted, write_errors=len(errors))
        log(f"  сделано: создано {created}, удалено {deleted}, ошибок записи {len(errors)}")
        if errors:
            return Result("error", key="write:" + ",".join(sorted(set(errors))),
                          detail=f"не прошло {len(errors)} записей в служебный календарь ({sorted(set(errors))[0]})",
                          fix=f"посмотри journalctl -u busy-sync -n 50 и {CHECK_CMD}",
                          transient=all(k == "api_network" or "_http_429" in k or "_http_5" in k for k in errors),
                          stats=stats)
    if plan["failed"]:
        return degraded_result(plan["failed"], stats)
    return Result("ok", stats=stats)


def run_purge(cfg: Config, api: CalendarAPI, now: int, dry_run: bool, sleep=time.sleep, log=print) -> int:
    lo, hi = compute_window(now, cfg.horizon_days + 7)
    items, tz_name = api.list_events(cfg.block_calendar, lo, hi)
    tz = tz_by_name(tz_name)
    ids = []
    for ev in items:
        iv = event_interval(ev, tz)
        if is_ours(ev) and iv and iv[1] > now:
            ids.append(ev["id"])
    log(f"purge: наших блоков в окне {fmt_msk(lo)} — {fmt_msk(hi)}: {len(ids)}")
    if dry_run:
        log("  dry-run: ничего не удалено")
        return 0
    fails = 0
    for i in ids:
        try:
            api.delete_event(cfg.block_calendar, i)
        except SyncError as exc:
            fails += 1
            log(f"  ✗ {exc.detail}")
        sleep(WRITE_PAUSE)
    log(f"  удалено {len(ids) - fails}, ошибок {fails}. Если таймер включён — он поставит блоки обратно: "
        "systemctl disable --now busy-sync.timer")
    return 1 if fails else 0


# ── health / алерты ──────────────────────────────────────────────────────────
def read_bot_token(path: str) -> str | None:
    t = parse_env_file(path).get("TELEGRAM_BOT_TOKEN", "").strip()
    return t or None


def redact(text: str) -> str:
    import re
    return re.sub(r"\d{6,}:[A-Za-z0-9_-]{20,}", "<token>", text)


def send_telegram(text: str, env_file: str | None = None, transport=http_transport) -> bool:
    """Шлёт в личку Аси токеном Аси. Токен не печатается и не уходит в argv."""
    env_file = env_file or os.environ.get("ASYA_ENV_FILE", ASYA_ENV_DEFAULT)
    token = read_bot_token(env_file)
    if not token:
        print(f"✗ нет TELEGRAM_BOT_TOKEN в {env_file} — алерт не отправлен")
        return False
    body = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    try:
        status, raw = transport("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                                {"Content-Type": "application/x-www-form-urlencoded"}, body, 20)
    except OSError as exc:
        print(f"✗ sendMessage не прошёл: {redact(type(exc).__name__ + ' ' + str(exc))[:200]}")
        return False
    finally:
        token = None
    if status == 200 and _json(raw).get("ok") is True:
        return True
    print(f"✗ sendMessage не прошёл: HTTP {status} {redact(raw.decode('utf-8', 'replace'))[:300]}")
    return False


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def alert_text(target: str, r: Result) -> str:
    head = "Синк занятости в " + target + (" работает частично" if r.status == "degraded" else " сломан")
    return f"{head}: {r.detail}.\nКак починить: {r.fix or CHECK_CMD}"


def handle_health(state_dir: str, r: Result, target: str, send=send_telegram, now: float | None = None,
                  no_alert: bool = False, log=print) -> None:
    """Одно сообщение на смену статуса/причины, одно на восстановление (дедуп как в asya_watchdog.sh)."""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "health.json")
    st = load_state(path)
    now = time.time() if now is None else now
    alerted = st.get("alerted_key") or ""
    streak = int(st.get("fail_streak") or 0)
    key = f"{r.status}|{r.key}" if r.status != "ok" else ""

    if r.status == "ok":
        streak = 0
        if alerted:
            if no_alert:
                log("алерты выключены (BUSY_SYNC_NO_ALERT=1) — сообщение о восстановлении не шлю")
            elif send("Синк занятости снова работает: блоки «Busy» в " + target + " обновляются."):
                log("снова ok — сообщение о восстановлении отправлено")
                alerted = ""
            else:
                log("сообщение о восстановлении не ушло — повторю на следующем тике")
    else:
        streak += 1
        if r.transient and streak < TRANSIENT_ALERT_AFTER:
            log(f"временный сбой {streak}/{TRANSIENT_ALERT_AFTER} — пока молчу")
        elif key == alerted:
            log("алерт по этой причине уже отправлен — молчу")
        elif no_alert:
            log("алерты выключены (BUSY_SYNC_NO_ALERT=1)")
        elif send(alert_text(target, r)):
            log("алерт отправлен")
            alerted = key
        else:
            log("алерт не ушёл — повторю на следующем тике")

    st.update({"status": r.status, "key": r.key, "detail": r.detail, "stats": r.stats,
               "at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "fail_streak": streak, "alerted_key": alerted or None})
    if r.status == "ok":
        st["last_ok"] = st["at"]
    atomic_write_json(path, st, 0o600)


# ── --check ──────────────────────────────────────────────────────────────────
def run_check(cfg: Config, api: CalendarAPI, now: int, log=print) -> int:
    """rc 0 — всё ok; 2 — degraded: токен, главный источник и служебный календарь ok, другой
    источник с ошибкой (таймер включать можно); 1 — сломано (таймер не включать)."""
    try:
        api.tokens.refresh()
        scopes = api.tokens.data.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        log(f"✓ токен {cfg.token_file}: refresh прошёл (в памяти, файл не тронут); "
            f"scopes: {', '.join(scopes) if scopes else '?'}")
    except SyncError as exc:
        log(f"✗ токен: {exc.detail}\n  → {exc.fix}")
        return 1
    lo, hi = compute_window(now, cfg.horizon_days)
    fatal = degraded = False
    try:
        fb = api.freebusy(cfg.sources, lo, hi)
        for src in cfg.sources:
            busy, err = fb.get(src, (None, "missing"))
            if busy is None:
                if src == cfg.main_source:
                    fatal = True
                    log(f"✗ free/busy {src}: {err} — главный источник, проверь токен/аккаунт")
                else:
                    degraded = True
                    log(f"⚠ free/busy {src}: {err} — " + SHARE_FIX.format(src=src))
            else:
                log(f"✓ free/busy {src}: ok, занятых интервалов {len(busy)}")
    except SyncError as exc:
        fatal = True
        log(f"✗ free/busy: {exc.detail}")
    try:
        items, tz_name = api.list_events(cfg.block_calendar, lo, hi)
        log(f"✓ events.list BLOCK_CALENDAR: ok, наших блоков {sum(1 for e in items if is_ours(e))}, "
            f"пояс {tz_name or '?'}; приглашения уходят на {cfg.invitee}")
    except SyncError as exc:
        fatal = True
        log(f"✗ events.list BLOCK_CALENDAR {cfg.block_calendar}: {exc.detail}\n  → {exc.fix}")
    if fatal:
        log("Итог: ✗ есть проблемы — таймер не включать")
        return 1
    if degraded:
        log("Итог: ⚠ degraded — синк работает по живым источникам, удалений не будет до починки шаринга")
        return 2
    log("Итог: ✓ доступ есть")
    return 0


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Синк занятости: личные календари → приглашения в рабочий looch.ai")
    ap.add_argument("--dry-run", action="store_true", help="напечатать план, ничего не писать и не алертить")
    ap.add_argument("--check", action="store_true", help="только проверка доступа (rc 0 ok / 2 degraded / 1 сломано)")
    ap.add_argument("--purge", action="store_true", help="удалить все наши блоки в окне (откат)")
    args = ap.parse_args(argv)

    state_dir = os.environ.get("STATE_DIRECTORY", "/var/lib/busy-sync").split(":")[0]
    no_alert = os.environ.get("BUSY_SYNC_NO_ALERT") == "1"
    now = int(time.time())

    try:
        cfg = load_config()
    except SyncError as exc:
        print(f"✗ конфиг: {exc.detail} → {exc.fix}")
        return 1
    api = CalendarAPI(TokenStore(cfg.token_file))

    if args.check:
        return run_check(cfg, api, now)

    lock = None
    if not args.dry_run:
        os.makedirs(state_dir, exist_ok=True)
        lock = open(os.path.join(state_dir, ".lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("другой прогон busy-sync ещё идёт — выхожу")
            return 0

    if args.purge:
        try:
            return run_purge(cfg, api, now, args.dry_run)
        except SyncError as exc:
            print(f"✗ purge: {exc.detail} → {exc.fix}")
            return 1

    try:
        r = run_sync(cfg, api, now, dry_run=args.dry_run)
    except SyncError as exc:
        r = Result("error", key=exc.key, detail=exc.detail, fix=exc.fix, transient=exc.transient)
        print(f"✗ {exc.detail}\n  → {exc.fix}")

    print(f"статус: {r.status}" + (f" — {r.detail}" if r.detail else ""))
    if not args.dry_run:
        try:
            handle_health(state_dir, r, cfg.invitee, no_alert=no_alert)
        except OSError as exc:
            print(f"⚠ health-файл не записан: {exc}")
    return 1 if r.status == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
