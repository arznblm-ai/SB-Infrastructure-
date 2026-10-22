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

    def test_subtract(self):
        self.assertEqual(bs.subtract([(0, 10)], []), [(0, 10)])
        self.assertEqual(bs.subtract([(0, 10)], [(3, 5)]), [(0, 3), (5, 10)])
        self.assertEqual(bs.subtract([(0, 10)], [(0, 10)]), [])
        self.assertEqual(bs.subtract([(0, 10)], [(-5, 3), (8, 20)]), [(3, 8)])
        self.assertEqual(bs.subtract([(0, 10), (20, 30)], [(5, 25)]), [(0, 5), (25, 30)])
        self.assertEqual(bs.subtract([(0, 10)], [(10, 20)]), [(0, 10)], "касание не режет")
        self.assertEqual(bs.subtract([(0, 4), (6, 10)], [(1, 2), (3, 7), (8, 9)]), [(0, 1), (2, 3), (7, 8), (9, 10)])

    def test_drop_short(self):
        self.assertEqual(bs.drop_short([(0, 4 * M), (10 * M, 15 * M), (20 * M, 21 * M)], 5 * M), [(10 * M, 15 * M)])

    def test_min_fragment_after_subtract(self):
        # встреча 10:00–11:00, рабочая 10:03–10:58 → остатки по 3 и 2 минуты отбрасываются
        src = [(ts("2026-10-22 10:00"), ts("2026-10-22 11:00"))]
        work = [(ts("2026-10-22 10:03"), ts("2026-10-22 10:58"))]
        self.assertEqual(bs.drop_short(bs.subtract(src, work), 5 * M), [])

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


class TestFilters(unittest.TestCase):
    def ev(self, **kw):
        base = {"id": "x", "status": "confirmed",
                "start": {"dateTime": "2026-10-22T10:00:00+03:00"}, "end": {"dateTime": "2026-10-22T11:00:00+03:00"}}
        base.update(kw)
        return base

    def test_real_busy(self):
        self.assertTrue(bs.is_real_busy(self.ev()))
        self.assertFalse(bs.is_real_busy(self.ev(transparency="transparent")))
        self.assertFalse(bs.is_real_busy(self.ev(status="cancelled")))
        self.assertFalse(bs.is_real_busy(self.ev(extendedProperties={"private": {"busySync": "1"}})))
        self.assertFalse(bs.is_real_busy(self.ev(attendees=[{"self": True, "responseStatus": "declined"}])))
        self.assertTrue(bs.is_real_busy(self.ev(attendees=[{"self": False, "responseStatus": "declined"},
                                                           {"self": True, "responseStatus": "accepted"}])))

    def test_all_day_opaque_counts(self):
        ev = self.ev(start={"date": "2026-10-23"}, end={"date": "2026-10-24"})
        self.assertTrue(bs.is_real_busy(ev))
        self.assertEqual(bs.event_interval(ev, bs.MSK), (ts("2026-10-23 00:00"), ts("2026-10-24 00:00")))


# ── фейковый Google ─────────────────────────────────────────────────────────
class FakeGoogle:
    """Транспорт: token endpoint, freeBusy, events list/insert/delete. Пишет журнал вызовов."""

    def __init__(self, busy=None, fb_errors=None, events=None, token_status=200, token_error=None):
        self.busy = busy or {}                 # src → [(s,e)]
        self.fb_errors = fb_errors or {}       # src → reason
        self.events = {e["id"]: e for e in (events or [])}
        self.token_status, self.token_error = token_status, token_error
        self.calls: list[tuple[str, str]] = []
        self.inserted: list[dict] = []
        self.deleted: list[str] = []
        self.seq = 0

    def __call__(self, method, url, headers, body, timeout=30):
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((method, u.path))
        if "oauth2" in u.netloc:
            if self.token_status != 200:
                return self.token_status, json.dumps({"error": self.token_error}).encode()
            return 200, json.dumps({"access_token": "new-access", "expires_in": 3599}).encode()
        assert headers.get("Authorization", "").startswith("Bearer "), "нет Bearer"
        if u.path.endswith("/freeBusy"):
            req = json.loads(body)
            cals = {}
            for it in req["items"]:
                sid = it["id"]
                if sid in self.fb_errors:
                    cals[sid] = {"errors": [{"domain": "global", "reason": self.fb_errors[sid]}], "busy": []}
                else:
                    cals[sid] = {"busy": [{"start": bs.iso_z(s), "end": bs.iso_z(e)} for s, e in self.busy.get(sid, [])]}
            return 200, json.dumps({"calendars": cals}).encode()
        if u.path.endswith("/events") and method == "GET":
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
            "extendedProperties": {"private": {"busySync": "1"}}}


def work(ev_id, s, e, **kw):
    ev = {"id": ev_id, "status": "confirmed",
          "start": {"dateTime": bs.iso_z(s)}, "end": {"dateTime": bs.iso_z(e)}}
    ev.update(kw)
    return ev


GMAIL, PORTAL = "a.rznblm@gmail.com", "tony@portalcg.us"


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.token_path = os.path.join(self.tmp, "token.json")
        with open(self.token_path, "w") as fh:
            json.dump({"token": "old", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                       "token_uri": "https://oauth2.googleapis.com/token",
                       "scopes": ["https://www.googleapis.com/auth/calendar.events"],
                       "expiry": "2000-01-01T00:00:00Z"}, fh)
        self.cfg = bs.load_config({"BUSY_SYNC_ENV": os.path.join(self.tmp, "none.env"),
                                   "BUSY_SYNC_TOKEN_FILE": self.token_path})
        self.log: list[str] = []

    def api(self, fake):
        return bs.CalendarAPI(bs.TokenStore(self.token_path, transport=fake, clock=lambda: NOW),
                              transport=fake, sleep=lambda s: None)

    def run_sync(self, fake, dry_run=False):
        return bs.run_sync(self.cfg, self.api(fake), NOW, dry_run=dry_run, sleep=lambda s: None,
                           log=self.log.append)


class TestSync(SyncCase):
    def test_creates_moves_deletes(self):
        m1 = (ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))
        m2 = (ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))
        fake = FakeGoogle(
            busy={GMAIL: [m1], PORTAL: [m2]},
            events=[block("keep", *m1),                                            # совпал — оставить
                    block("moved", ts("2026-10-23 14:00"), ts("2026-10-23 15:00")),  # встречу перенесли
                    block("past", ts("2026-10-22 07:00"), ts("2026-10-22 08:00")),   # прошлое — не трогать
                    work("w1", ts("2026-10-24 10:00"), ts("2026-10-24 11:00"), summary="секрет")])
        r = self.run_sync(fake)
        self.assertEqual(r.status, "ok")
        self.assertEqual(fake.deleted, ["moved"])
        self.assertEqual(len(fake.inserted), 1)
        ev = fake.inserted[0]
        self.assertEqual((ev["start"]["dateTime"], ev["end"]["dateTime"]), (bs.iso_z(m2[0]), bs.iso_z(m2[1])))
        self.assertEqual(ev["summary"], "Busy")
        self.assertEqual(ev["visibility"], "private")
        self.assertEqual(ev["transparency"], "opaque")
        self.assertEqual(ev["reminders"], {"useDefault": False, "overrides": []})
        self.assertEqual(ev["extendedProperties"], {"private": {"busySync": "1"}})
        self.assertFalse(ev["guestsCanSeeOtherGuests"])
        for k in ("attendees", "description", "location"):
            self.assertNotIn(k, ev)
        self.assertFalse(any("секрет" in line for line in self.log), "названия не логируются")
        # второй прогон — идемпотентен
        fake.inserted.clear(); fake.deleted.clear()
        self.assertEqual(self.run_sync(fake).status, "ok")
        self.assertEqual((fake.inserted, fake.deleted), ([], []))

    def test_work_events_subtracted(self):
        fake = FakeGoogle(
            busy={GMAIL: [(ts("2026-10-22 10:00"), ts("2026-10-22 12:00"))]},
            events=[work("w", ts("2026-10-22 10:00"), ts("2026-10-22 11:00")),
                    work("tr", ts("2026-10-22 11:00"), ts("2026-10-22 12:00"), transparency="transparent"),
                    work("dec", ts("2026-10-22 11:00"), ts("2026-10-22 11:30"),
                         attendees=[{"self": True, "responseStatus": "declined"}])])
        self.run_sync(fake)
        got = [(e["start"]["dateTime"], e["end"]["dateTime"]) for e in fake.inserted]
        self.assertEqual(got, [(bs.iso_z(ts("2026-10-22 11:00")), bs.iso_z(ts("2026-10-22 12:00")))])

    def test_all_day_work_blocks_day(self):
        fake = FakeGoogle(busy={GMAIL: [(ts("2026-10-23 10:00"), ts("2026-10-23 11:00"))]},
                          events=[{"id": "ad", "status": "confirmed", "start": {"date": "2026-10-23"},
                                   "end": {"date": "2026-10-24"}}])
        self.run_sync(fake)
        self.assertEqual(fake.inserted, [])

    def test_in_progress_block_kept(self):
        # многодневная занятость с позавчера: freebusy срезан по окну, блок начат раньше — не пересоздавать
        start, end = ts("2026-10-20 00:00"), ts("2026-10-25 00:00")
        lo, _ = bs.compute_window(NOW, 30)
        fake = FakeGoogle(busy={GMAIL: [(lo, end)]}, events=[block("long", start, end)])
        self.run_sync(fake)
        self.assertEqual((fake.inserted, fake.deleted), ([], []))

    def test_failsafe_no_deletes_when_source_fails(self):
        m = (ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))
        fake = FakeGoogle(busy={GMAIL: [m]}, fb_errors={PORTAL: "notFound"},
                          events=[block("portal-block", ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))])
        r = self.run_sync(fake)
        self.assertEqual(r.status, "degraded")
        self.assertIn("tony@portalcg.us", r.detail)
        self.assertEqual(fake.deleted, [], "fail-safe: ничего не удалено")
        self.assertEqual(len(fake.inserted), 1, "по живому источнику создаём")
        self.assertEqual(r.stats["skipped_deletes"], 1)

    def test_dry_run_writes_nothing(self):
        fake = FakeGoogle(busy={GMAIL: [(ts("2026-10-22 12:00"), ts("2026-10-22 13:00"))]},
                          events=[block("x", ts("2026-10-23 15:00"), ts("2026-10-23 16:00"))])
        r = self.run_sync(fake, dry_run=True)
        self.assertEqual(r.stats["create"], 1)
        self.assertEqual(r.stats["delete"], 1)
        self.assertFalse(any(m in ("POST", "DELETE") and "/events" in p for m, p in fake.calls))

    def test_token_refreshed_and_saved_600(self):
        fake = FakeGoogle()
        self.run_sync(fake)
        with open(self.token_path) as fh:
            d = json.load(fh)
        self.assertEqual(d["token"], "new-access")
        self.assertEqual(d["refresh_token"], "r", "refresh_token сохранён")
        self.assertTrue(d["expiry"].endswith("Z"))
        self.assertEqual(stat.S_IMODE(os.stat(self.token_path).st_mode), 0o600)

    def test_revoked_token(self):
        fake = FakeGoogle(token_status=400, token_error="invalid_grant")
        with self.assertRaises(bs.SyncError) as cm:
            self.run_sync(fake)
        self.assertEqual(cm.exception.kind, "auth")
        self.assertIn("auth_work.py", cm.exception.fix)

    def test_purge(self):
        fake = FakeGoogle(events=[block("b1", ts("2026-10-22 12:00"), ts("2026-10-22 13:00")),
                                  block("b2", ts("2026-10-25 12:00"), ts("2026-10-25 13:00")),
                                  work("w", ts("2026-10-22 14:00"), ts("2026-10-22 15:00"))])
        rc = bs.run_purge(self.cfg, self.api(fake), NOW, dry_run=True, sleep=lambda s: None, log=self.log.append)
        self.assertEqual((rc, fake.deleted), (0, []))
        bs.run_purge(self.cfg, self.api(fake), NOW, dry_run=False, sleep=lambda s: None, log=self.log.append)
        self.assertEqual(sorted(fake.deleted), ["b1", "b2"], "рабочие события не трогает")


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
        other = bs.Result("error", key="token_revoked:invalid_grant", detail="refresh token отозван", fix="auth_work.py")
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
        self.assertEqual(cfg.calendar, "primary")

    def test_target_in_sources_rejected(self):
        with self.assertRaises(bs.SyncError):
            bs.load_config({"BUSY_SYNC_ENV": "/nonexistent", "BUSY_SYNC_SOURCES": "anton@looch.ai"})


if __name__ == "__main__":
    unittest.main()
