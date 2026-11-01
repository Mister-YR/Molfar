"""Інциденти: інтернет, простій Мольфара, відвал Wi-Fi (лише коли зникли всі клієнти), роутер, підсумки.
Запуск: python3 -m unittest discover -s tests"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from molfar import incidents as inc  # noqa: E402
from molfar.db import DB  # noqa: E402


class FakeMonitor:
    def __init__(self, db):
        self.db = db
        self.sent = []

    @property
    def settings(self):
        return self.db.get_settings()

    def notify(self, text):
        self.sent.append(text)

    def _spawn(self, coro):
        asyncio.get_event_loop().create_task(coro)


def client(mac, band):
    return {"mac": mac, "band": band, "guest": False, "name": "", "ip": ""}


PI = client("PI:00", "wired")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(os.path.join(self.tmp.name, "t.db"))
        self.db.set_settings({"alert_enabled": True, "tg_token": "x", "tg_chat_id": "1",
                              "router_type": "tplink", "router_host": "192.168.0.1"})
        self.m = FakeMonitor(self.db)
        self.i = inc.Incidents(self.m)
        self.s = self.db.get_settings()

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def rows(self, kind=None):
        q = "SELECT * FROM incidents" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
        out = self.db.query(q, (kind,) if kind else ())
        for r in out:
            r["details"] = json.loads(r["details"])
        return out

    def wifi(self, ts, clients):
        self.i._wifi_check(self.s, ts, {"clients": clients})


class WifiTest(Base):
    def test_partial_drop_is_not_incident(self):
        full = [PI] + [client(f"A:{n}", "5") for n in range(6)]
        self.wifi(100, full)
        self.wifi(200, [PI, client("A:0", "5")])          # 5 з 6 пішли, один лишився
        self.assertEqual(self.rows("wifi"), [])

    def test_all_gone_is_incident_and_recovers(self):
        full = [PI] + [client(f"A:{n}", "5") for n in range(4)] + [client("B:1", "2.4")]
        self.wifi(100, full)
        self.wifi(200, [PI])                                 # кабельні є, Wi-Fi — жодного
        r = self.rows("wifi")
        self.assertEqual(len(r), 1)
        self.assertIsNone(r[0]["ended_ts"])
        self.assertEqual((r[0]["details"]["before"], r[0]["details"]["lost"]), (5, 5))
        self.assertTrue(r[0]["details"]["all"])
        self.wifi(300, [PI])                                 # досі нікого — інцидент триває
        self.assertIsNone(self.rows("wifi")[0]["ended_ts"])
        self.wifi(400, [PI, client("A:0", "5")])            # хтось повернувся — кінець
        r = self.rows("wifi")[0]
        self.assertEqual(r["ended_ts"], 400)
        self.assertEqual(r["details"]["after_recovery"], 1)
        self.assertEqual(sum("Wi-Fi" in t for t in self.m.sent), 2)

    def test_too_few_clients_before(self):
        self.db.set_settings({"wifi_drop_min": 3})
        self.s = self.db.get_settings()
        self.wifi(100, [PI, client("A:0", "5"), client("A:1", "5")])
        self.wifi(200, [PI])
        self.assertEqual(self.rows("wifi"), [])

    def test_empty_poll_is_glitch(self):
        self.wifi(100, [PI, client("A:0", "5"), client("A:1", "5")])
        self.wifi(200, [])                                   # навіть Pi немає — збій опитування
        self.assertEqual(self.rows("wifi"), [])


class WifiMigrationTest(Base):
    def test_partial_records_removed_on_start(self):
        for d in ({"before": 12, "after": 2, "lost": 10}, {"before": 5, "after": 0, "lost": 5, "all": True}):
            self.db.insert("incidents", {"kind": "wifi", "started_ts": 1, "ended_ts": 2, "details": json.dumps(d)})
        asyncio.run(self.i.on_start())
        self.assertEqual([r["details"]["before"] for r in self.rows("wifi")], [5])


class InternetTest(Base):
    def res(self, rx):
        return [{"target": t, "received": rx, "sent": 20, "error": None} for t in ("1.1.1.1", "8.8.8.8", "10.10.105.1")]

    def test_outage_needs_confirm_and_ignores_private(self):
        async def gw():
            return "192.168.0.1"

        async def ping(t, c):
            return {"received": 4}
        old = inc.default_gateway, inc.probes.ping_target
        inc.default_gateway, inc.probes.ping_target = gw, ping
        try:
            async def run():
                await self.i.on_ping(self.res(0), self.s, 1000)
                self.assertEqual(self.rows("internet"), [])   # одна перевірка — ще не обрив
                await self.i.on_ping(self.res(0), self.s, 1060)
                await self.i.on_ping(self.res(20), self.s, 1300)
            asyncio.run(run())
        finally:
            inc.default_gateway, inc.probes.ping_target = old
        r = self.rows("internet")
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["started_ts"], r[0]["ended_ts"]), (1000, 1300))
        self.assertEqual(r[0]["details"]["cause"], "isp")
        self.assertEqual(r[0]["details"]["targets"], ["1.1.1.1", "8.8.8.8"])

    def test_ping_tool_failure_is_not_outage(self):
        bad = [{"target": t, "received": 0, "sent": 20, "error": "ping is not installed"} for t in ("1.1.1.1", "8.8.8.8")]
        asyncio.run(self.i.on_ping(bad, self.s, 1000))
        asyncio.run(self.i.on_ping(bad, self.s, 1060))
        self.assertEqual(self.rows("internet"), [])


class DowntimeTest(Base):
    def start(self):
        i = inc.Incidents(self.m)
        old = inc.clock_synced
        inc.clock_synced = lambda: True
        try:
            asyncio.run(i.on_start())
        finally:
            inc.clock_synced = old
        return i

    def test_power_loss(self):
        self.db.set_state("heartbeat", {"ts": int(time.time()) - 600, "boot_id": "other-boot"})
        self.start()
        r = self.rows("molfar")[0]
        self.assertEqual(r["details"]["reason"], "power")
        self.assertTrue(r["details"]["reboot"])
        self.assertGreaterEqual(r["ended_ts"] - r["started_ts"], 599)

    def test_clean_restart(self):
        self.i.on_shutdown()
        self.start()
        r = self.rows("molfar")[0]
        self.assertEqual(r["details"]["reason"], "restart")
        self.assertFalse(r["details"]["reboot"])


class SummaryTest(unittest.TestCase):
    def test_router_unreachable_counts_only_unreachable(self):
        rows = [
            {"kind": "router", "started_ts": 100, "ended_ts": 400, "details": {"unreachable": True, "reboot": True}},
            {"kind": "router", "started_ts": 500, "ended_ts": 500, "details": {"wan_reset": True}},
            {"kind": "internet", "started_ts": 100, "ended_ts": 200, "details": {"cause": "isp"}},
            {"kind": "internet", "started_ts": 300, "ended_ts": 600, "details": {"cause": "lan"}},
            {"kind": "molfar", "started_ts": 700, "ended_ts": 760, "details": {"reason": "power", "reboot": True}},
            {"kind": "molfar", "started_ts": 800, "ended_ts": 810, "details": {"reason": "restart", "reboot": False}},
        ]
        s = inc.summarize(rows, 0, 1000)
        self.assertEqual((s["router"]["count"], s["router"]["total_sec"]), (1, 300))
        self.assertEqual((s["router"]["reboots"], s["router"]["wan_resets"]), (1, 1))
        self.assertEqual((s["internet"]["count"], s["internet"]["total_sec"], s["internet"]["longest_sec"]), (2, 400, 300))
        self.assertEqual(s["internet"]["causes"]["lan"], 1)
        self.assertEqual(s["molfar"]["count"], 2)
        self.assertEqual((s["molfar"]["reasons"]["power"], s["molfar"]["reasons"]["restart"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
