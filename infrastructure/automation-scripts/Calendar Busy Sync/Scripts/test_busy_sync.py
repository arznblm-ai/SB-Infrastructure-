#!/usr/bin/env python3
"""Тесты busy_sync.py — без сети: HTTP-слой подменён FakeGoogle.

    python3 -m unittest "infrastructure/Calendar Busy Sync/Scripts/test_busy_sync.py" -v
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import busy_sync as bs  # noqa: E402

H = 3600
M = 60


def ts(s: str) -> int:
    """'2026-10-22 10:00' МСК → epoch."""
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=bs.MSK).timestamp())


NOW = ts("2026-10-22 09:00")


# ── чистая логика ───────────────────────────────────────────────────────────
class TestIntervals(unittest.TestCase):
    def test_merge(self):
        self.assertEqual(bs.merge([]), [])
        self.assertEqual(bs.merge([(5, 10), (1, 3), (2, 4)]), [(1, 4), (5, 10)])
        self.assertEqual(bs.merge([(1, 3), (3, 5)]), [(1, 5)], "соприкасающиеся склеиваются")
        self.assertEqual(bs.merge([(1, 10), (2, 3)]), [(1, 10)])
        self.assertEqual(bs.merge([(5, 5), (7, 6)]), [], "пустые/обратные выкидываются")

    def test_drop_short(self):
        self.assertEqual(bs.drop_short([(0, 4 * M), (10 * M, 15 * M), (20 * M, 21 * M)], 5 * M), [(10 * M, 15 * M)])

    def test_diff(self):
        desired = [(1, 2), (3, 4), (5, 6)]
        existing = [("a", 1, 2), ("b", 3, 5), ("c", 5, 6), ("d", 5, 6)]
        create, delete = bs.diff(desired, existing)
        self.assertEqual(create, [(3, 4)])
        self.assertEqual(sorted(delete), ["b", "d"], "лишний и дубль удаляются")
        self.assertEqual(bs.diff([], []), ([], []))

    def test_window_day_aligned(self):
        lo, hi = bs.compute_window(NOW, 30)
        self.assertEqual(lo, ts("2026-10-22 00:00"))
        self.assertEqual(hi, ts("2026-11-22 00:00"))
        self.assertEqual(bs.compute_window(NOW + 10 * M, 30), (lo, hi), "края не двигаются внутри дня")


# ── фейковый Google ─────────────────────────────────────────────────────────
GMAIL, PRIMARY, PORTAL = "a.rznblm@gmail.com", "primary", "tony@portalcg.us"
WORK = "anton@looch.ai"
BLOCK_CAL = bs.DEFAULTS["BLOCK_CALENDAR"]


class FakeGoogle:
    """Транспорт: token endpoint, freeBusy, events list/insert/delete. Пишет журнал вызовов."""

    def __init__(self, busy=None, fb_errors=None, events=None, token_status=200, token_error=None,
                 primary_as=None, rotate_refresh=False, list_status=200):
        self.busy = busy or {}                 # src → [(s,e)]
        self.fb_errors = fb_errors or {}       # src → reason
        self.events = {e["id"]: e for e in (events or [])}
        self.token_status, self.token_error = token_status, token_error
        self.primary_as = primary_as           # Google вернул «primary» под адресом аккаунта
        self.rotate_refresh = rotate_refresh
        self.list_status = list_status
        self.calls: list[tuple[str, str]] = []
        self.auth_headers: list[str] = []
        self.token_calls = 0
        self.inserted: list[dict] = []
        self.deleted: list[str] = []
        self.seq = 0

    def __call__(self, method, url, headers, body, timeout=30):
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((method, u.path))
        if "oauth2" in u.netloc:
            self.token_calls += 1
            if self.token_status != 200:
                return self.token_status, json.dumps({"error": self.token_error}).encode()
            resp = {"access_token": "new-access", "expires_in": 3599}
            if self.rotate_refresh:
                resp["refresh_token"] = "rotated"
            return 200, json.dumps(resp).encode()
        assert headers.get("Authorization", "").startswith("Bearer "), "нет Bearer"
        self.auth_headers.append(headers["Authorization"])
        if u.path.endswith("/freeBusy"):
            req = json.loads(body)
            cals = {}
            for it in req["items"]:
                sid = it["id"]
                key = self.primary_as if (sid == PRIMARY and self.primary_as) else sid
                if sid in self.fb_errors:
                    cals[key] = {"errors": [{"domain": "global", "reason": self.fb_errors[sid]}], "busy": []}
                else:
                    cals[key] = {"busy": [{"start": bs.iso_z(s), "end": bs.iso_z(e)} for s, e in self.busy.get(sid, [])]}
            return 200, json.dumps({"calendars": cals}).encode()
        cal = urllib.parse.unquote(u.path.split("/calendars/", 1)[1].split("/", 1)[0]) if "/calendars/" in u.path else ""
        assert cal == BLOCK_CAL, f"запись/чтение не в служебный календарь: {cal}"
        if u.path.endswith("/events") and method == "GET":
            if self.list_status != 200:
                return self.list_status, json.dumps({"error": {"errors": [{"reason": "notFound"}]}}).encode()
            assert "summary" not in q.get("fields", ""), "названия не запрашиваются"
            items = list(self.events.values())
            if q.get("privateExtendedProperty") == "busySync=1":
                items = [e for e in items if bs.is_ours(e)]
            # пагинация: по 2 события на страницу
            start = int(q.get("pageToken", "0"))
            page = items[start:start + 2]
            resp = {"timeZone": "Europe/Moscow", "items": page}
            if start + 2 < len(items):
                resp["nextPageToken"] = str(start + 2)
            return 200, json.dumps(resp).encode()
        if u.path.endswith("/events") and method == "POST":
            assert q.get("sendUpdates") == "none"
            ev = json.loads(body)
            self.seq += 1
            ev["id"] = f"new{self.seq}"
            self.events[ev["id"]] = ev
            self.inserted.append(ev)
            return 200, json.dumps(ev).encode()
        if method == "DELETE":
            assert q.get("sendUpdates") == "none"
            ev_id = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            self.deleted.append(ev_id)
            self.events.pop(ev_id, None)
            return 204, b""
        return 404, b"{}"


def block(ev_id, s, e):
    return {"id": ev_id, "status": "confirmed", "summary": "Busy",
            "start": {"dateTime": bs.iso_z(s)}, "end": {"dateTime": bs.iso_z(e)},
            "attendees": [{"email": WORK}],
            "extendedProperties": {"private": {"busySync": "1"}}}


def foreign(ev_id, s, e, **kw):
    """Событие в служебном календаре без нашей метки (например, ручное)."""
    ev = {"id": ev_id, "status": "confirmed", "summary": "секрет",
          "start": {"dateTime": bs.iso_z(s)}, "end": {"dateTime": bs.iso_z(e)}}
    ev.update(kw)
    return ev


TOKEN = {"token": "asya-access", "refresh_token": "r", "client_id": "c", "client_secret": "s",
         "token_uri": "https://oauth2.googleapis.com/token",
         "scopes": ["https://www.googleapis.com/auth/calendar"],
         "expiry": "2099-01-01T00:00:00Z"}


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.token_path = os.path.join(self.tmp, "google-calendar-token.json")
        with open(self.token_path, "w") as fh:
            json.dump(TOKEN, fh)
        os.chmod(self.token_path, 0o644)
        self.cfg = bs.load_config({"BUSY_SYNC_ENV": os.path.join(self.tmp, "none.env"),
                                   "TOKEN_FILE": self.token_path})
        self.log: list[str] = []

    def api(self, fake):
        return bs.CalendarAPI(bs.TokenStore(self.token_path, transport=fake, clock=lambda: NOW),
                              transport=fake, sleep=lambda s: None)

    def run_sync(self, fake, dry_run=False):
        return bs.run_sync(self.cfg, self.api(fake), NOW, dry_run=dry_run, sleep=lambda s: None,
                           log=self.log.append)

    def check(self, fake):
        return bs.run_check(self.cfg, self.api(fake), NOW, log=self.log.append)


class TestSync(SyncCase):
    def test_creates_moves_deletes_with_invite(self):
        m1 = (ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))
        m2 = (ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))
        fake = FakeGoogle(
            busy={GMAIL: [m1], PRIMARY: [m2]},
            events=[block("keep", *m1),                                            # совпал — оставить
                    block("moved", ts("2026-10-23 14:00"), ts("2026-10-23 15:00")),  # встречу перенесли
                    block("past", ts("2026-10-22 07:00"), ts("2026-10-22 08:00"))])  # прошлое — не трогать
        r = self.run_sync(fake)
        self.assertEqual(r.status, "ok")
        self.assertEqual(fake.deleted, ["moved"])
        self.assertEqual(len(fake.inserted), 1)
        ev = fake.inserted[0]
        self.assertEqual((ev["start"]["dateTime"], ev["end"]["dateTime"]), (bs.iso_z(m2[0]), bs.iso_z(m2[1])))
        self.assertEqual(ev["attendees"], [{"email": WORK}], "один участник — рабочий адрес")
        self.assertEqual(ev["summary"], "Busy")
        self.assertEqual(ev["visibility"], "private")
        self.assertEqual(ev["transparency"], "opaque")
        self.assertEqual(ev["reminders"], {"useDefault": False, "overrides": []})
        self.assertEqual(ev["extendedProperties"], {"private": {"busySync": "1"}})
        for k in ("guestsCanSeeOtherGuests", "guestsCanInviteOthers", "guestsCanModify"):
            self.assertIs(ev[k], False, k)
        for k in ("description", "location"):
            self.assertNotIn(k, ev)
        # второй прогон — идемпотентен
        fake.inserted.clear(); fake.deleted.clear()
        self.assertEqual(self.run_sync(fake).status, "ok")
        self.assertEqual((fake.inserted, fake.deleted), ([], []))

    def test_sources_union_no_subtraction(self):
        # пересечение источников склеивается в один блок; рабочую занятость больше не вычитаем
        fake = FakeGoogle(busy={GMAIL: [(ts("2026-10-22 10:00"), ts("2026-10-22 11:00"))],
                                PRIMARY: [(ts("2026-10-22 10:30"), ts("2026-10-22 12:00")),
                                          (ts("2026-10-22 15:00"), ts("2026-10-22 15:03"))]})  # < 5 мин
        self.run_sync(fake)
        got = [(e["start"]["dateTime"], e["end"]["dateTime"]) for e in fake.inserted]
        self.assertEqual(got, [(bs.iso_z(ts("2026-10-22 10:00")), bs.iso_z(ts("2026-10-22 12:00")))])

    def test_primary_alias_in_response(self):
        m = (ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))
        fake = FakeGoogle(busy={PRIMARY: [m]}, primary_as=PORTAL)
        r = self.run_sync(fake)
        self.assertEqual(r.status, "ok")
        self.assertEqual(len(fake.inserted), 1)

    def test_foreign_events_in_block_calendar_untouched(self):
        fake = FakeGoogle(events=[foreign("manual", ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))])
        self.run_sync(fake)
        self.assertEqual(fake.deleted, [])
        self.assertFalse(any("секрет" in line for line in self.log), "названия не логируются")

    def test_in_progress_block_kept(self):
        # многодневная занятость с позавчера: freebusy срезан по окну, блок начат раньше — не пересоздавать
        start, end = ts("2026-10-20 00:00"), ts("2026-10-25 00:00")
        lo, _ = bs.compute_window(NOW, 30)
        fake = FakeGoogle(busy={GMAIL: [(lo, end)]}, events=[block("long", start, end)])
        self.run_sync(fake)
        self.assertEqual((fake.inserted, fake.deleted), ([], []))

    def test_degraded_gmail_creates_but_no_deletes(self):
        m = (ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))
        fake = FakeGoogle(busy={PRIMARY: [m]}, fb_errors={GMAIL: "notFound"},
                          events=[block("gmail-block", ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))])
        r = self.run_sync(fake)
        self.assertEqual(r.status, "degraded")
        self.assertIn(GMAIL, r.detail)
        self.assertIn("tony@portalcg.us", r.fix, "алерт: расшарь free/busy gmail на tony@portalcg.us")
        self.assertEqual(fake.deleted, [], "fail-safe: ничего не удалено")
        self.assertEqual(len(fake.inserted), 1, "по живому источнику создаём")
        self.assertEqual(fake.inserted[0]["attendees"], [{"email": WORK}])
        self.assertEqual(r.stats["skipped_deletes"], 1)

    def test_dry_run_writes_nothing(self):
        fake = FakeGoogle(busy={GMAIL: [(ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))]},
                          events=[block("x", ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))])
        r = self.run_sync(fake, dry_run=True)
        self.assertEqual(r.stats["create"], 1)
        self.assertEqual(r.stats["delete"], 1)
        self.assertFalse(any(m in ("POST", "DELETE") and "/events" in p for m, p in fake.calls))

    def test_token_file_never_written(self):
        with open(self.token_path, "rb") as fh:
            before = fh.read()
        st_before = os.stat(self.token_path)
        fake = FakeGoogle(busy={GMAIL: [(ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))]}, rotate_refresh=True)
        self.run_sync(fake)
        self.run_sync(fake)
        with open(self.token_path, "rb") as fh:
            self.assertEqual(fh.read(), before, "токен-файл Аси не перезаписан")
        st_after = os.stat(self.token_path)
        self.assertEqual((st_after.st_mtime_ns, st_after.st_ino, stat.S_IMODE(st_after.st_mode)),
                         (st_before.st_mtime_ns, st_before.st_ino, 0o644), "ни mtime, ни inode, ни права")
        self.assertEqual(os.listdir(self.tmp), ["google-calendar-token.json"], "временных файлов рядом нет")
        self.assertTrue(fake.auth_headers and all(h == "Bearer new-access" for h in fake.auth_headers),
                        "в API идёт свой обновлённый access token, не токен Аси из файла")

    def test_refresh_once_per_run(self):
        fake = FakeGoogle(busy={GMAIL: [(ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))]})
        self.run_sync(fake)
        self.assertEqual(fake.token_calls, 1, "access token кэшируется в памяти")

    def test_revoked_token(self):
        fake = FakeGoogle(token_status=400, token_error="invalid_grant")
        with self.assertRaises(bs.SyncError) as cm:
            self.run_sync(fake)
        self.assertEqual(cm.exception.kind, "auth")
        self.assertIn("gcal-mcp", cm.exception.fix)

    def test_purge(self):
        fake = FakeGoogle(events=[block("b1", ts("2026-10-22 12:00"), ts("2026-10-22 13:00")),
                                  block("b2", ts("2026-10-25 12:00"), ts("2026-10-25 13:00")),
                                  foreign("w", ts("2026-10-22 14:00"), ts("2026-10-22 15:00"))])
        rc = bs.run_purge(self.cfg, self.api(fake), NOW, dry_run=True, sleep=lambda s: None, log=self.log.append)
        self.assertEqual((rc, fake.deleted), (0, []))
        bs.run_purge(self.cfg, self.api(fake), NOW, dry_run=False, sleep=lambda s: None, log=self.log.append)
        self.assertEqual(sorted(fake.deleted), ["b1", "b2"], "события без метки не трогает")


class TestCheck(SyncCase):
    def test_all_ok(self):
        self.assertEqual(self.check(FakeGoogle()), 0)

    def test_gmail_not_shared_is_degraded(self):
        self.assertEqual(self.check(FakeGoogle(fb_errors={GMAIL: "notFound"})), 2)
        self.assertTrue(any("tony@portalcg.us" in l and GMAIL in l for l in self.log))

    def test_primary_failed_is_fatal(self):
        self.assertEqual(self.check(FakeGoogle(fb_errors={PRIMARY: "notFound"})), 1)

    def test_block_calendar_missing_is_fatal(self):
        self.assertEqual(self.check(FakeGoogle(list_status=404)), 1)

    def test_bad_token_is_fatal(self):
        self.assertEqual(self.check(FakeGoogle(token_status=400, token_error="invalid_grant")), 1)

    def test_check_does_not_write_token(self):
        with open(self.token_path, "rb") as fh:
            before = fh.read()
        self.check(FakeGoogle(rotate_refresh=True))
        with open(self.token_path, "rb") as fh:
            self.assertEqual(fh.read(), before)


class TestHealth(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sent: list[str] = []
        self.ok = True

    def send(self, text):
        self.sent.append(text)
        return self.ok

    def h(self, r, **kw):
        bs.handle_health(self.dir, r, "anton@looch.ai", send=self.send, now=NOW, log=lambda *_: None, **kw)

    def test_alert_dedup_and_recovery(self):
        bad = bs.Result("degraded", key="sources:tony=notFound", detail="нет доступа к free/busy tony", fix="шаринг")
        self.h(bs.Result("ok"))
        self.assertEqual(self.sent, [], "ok без прошлого алерта — молчим")
        self.h(bad); self.h(bad); self.h(bad)
        self.assertEqual(len(self.sent), 1, "одно сообщение на поломку")
        self.assertIn("free/busy tony", self.sent[0])
        other = bs.Result("error", key="token_revoked:invalid_grant", detail="refresh token отозван", fix="перевыпусти токен")
        self.h(other)
        self.assertEqual(len(self.sent), 2, "новая причина — новое сообщение")
        self.h(bs.Result("ok")); self.h(bs.Result("ok"))
        self.assertEqual(len(self.sent), 3)
        self.assertIn("снова работает", self.sent[2])

    def test_failed_send_retried(self):
        bad = bs.Result("error", key="k", detail="d")
        self.ok = False
        self.h(bad)
        self.ok = True
        self.h(bad)
        self.assertEqual(len(self.sent), 2, "не ушло — повтор на следующем тике")
        self.h(bad)
        self.assertEqual(len(self.sent), 2)

    def test_transient_needs_streak(self):
        net = bs.Result("error", key="api_network", detail="сеть", transient=True)
        self.h(net); self.h(net)
        self.assertEqual(self.sent, [])
        self.h(net)
        self.assertEqual(len(self.sent), 1)

    def test_no_alert(self):
        self.h(bs.Result("error", key="k", detail="d"), no_alert=True)
        self.assertEqual(self.sent, [])
        mode = stat.S_IMODE(os.stat(os.path.join(self.dir, "health.json")).st_mode)
        self.assertEqual(mode, 0o600)


class TestTelegram(unittest.TestCase):
    def test_token_not_printed(self):
        tmp = tempfile.mkdtemp()
        env = os.path.join(tmp, ".env")
        tok = "1234567890:" + "A" * 35
        with open(env, "w") as fh:
            fh.write(f'TELEGRAM_BOT_TOKEN="{tok}"\n')

        def fail(method, url, headers, body, timeout=30):
            return 400, json.dumps({"ok": False, "description": f"bad {tok}"}).encode()

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertFalse(bs.send_telegram("x", env_file=env, transport=fail))
        self.assertNotIn(tok, buf.getvalue())

        seen = {}

        def good(method, url, headers, body, timeout=30):
            seen["chat"] = urllib.parse.parse_qs(body.decode())["chat_id"][0]
            return 200, b'{"ok": true}'
        self.assertTrue(bs.send_telegram("x", env_file=env, transport=good))
        self.assertEqual(seen["chat"], "324186708")


class TestConfig(unittest.TestCase):
    def test_env_file_and_override(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "b.env")
        with open(p, "w") as fh:
            fh.write("# комментарий\nHORIZON_DAYS=14\nSOURCES='x@y.z, a@b.c'\nJUNK=1\n")
        cfg = bs.load_config({"BUSY_SYNC_ENV": p, "BUSY_SYNC_MIN_MINUTES": "10"})
        self.assertEqual((cfg.horizon_days, cfg.min_minutes, cfg.sources), (14, 10, ["x@y.z", "a@b.c"]))
        self.assertEqual(cfg.block_calendar, BLOCK_CAL)

    def test_defaults(self):
        cfg = bs.load_config({"BUSY_SYNC_ENV": "/nonexistent"})
        self.assertEqual(cfg.sources, [PRIMARY, GMAIL])
        self.assertEqual(cfg.invitee, WORK)
        self.assertEqual(cfg.main_source, PRIMARY)
        self.assertEqual(cfg.token_file, "/root/.config/second-brain/google-calendar-token.json")
        self.assertEqual(bs.load_config({"BUSY_SYNC_ENV": "/nonexistent", "TOKEN_FILE": "/x.json"}).token_file,
                         "/x.json", "env TOKEN_FILE")

    def test_loops_rejected(self):
        for src in (BLOCK_CAL, WORK, "Anton@Looch.ai"):
            with self.assertRaises(bs.SyncError, msg=src):
                bs.load_config({"BUSY_SYNC_ENV": "/nonexistent", "BUSY_SYNC_SOURCES": f"primary,{src}"})


if __name__ == "__main__":
    unittest.main()
