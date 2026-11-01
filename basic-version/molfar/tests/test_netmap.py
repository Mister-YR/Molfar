"""Правила мапи мережі. Запуск з кореня проєкту: python3 -m unittest discover -s tests"""
import os
import time
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from molfar.netmap import DAY, NetMap, NetMapError as ApiError  # noqa: E402

T0 = 1_700_000_000.0
GW, PI = "a0:36:bc:12:0c:01", "02:00:a3:4c:11:5e"


def host(ip, mac, vendor="", hostname=""):
    return {"ip": ip, "mac": mac, "vendor": vendor, "hostname": hostname}


def scan(hosts, ts):
    return {"ts": ts, "gateway_ip": "192.168.50.1", "hosts": hosts,
            "self": {"ip": "192.168.50.10", "mac": PI, "hostname": "orangepizero3"}}


BASE = [host("192.168.50.1", GW, "ASUSTek COMPUTER INC."),
        host("192.168.50.20", "00:11:32:a8:7f:01", "Synology Incorporated", "diskstation"),
        host("192.168.50.41", "5a:1e:c7:3b:90:12", "", "iPhone-Oli"),
        host("192.168.50.77", "d8:f1:5b:6a:02:c4", "Espressif Inc.")]


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = {"net_scan_min": 5, "net_offline_min": 15, "net_stale_days": 7, "net_log_days": 400, "net_iface": ""}
        self.app = NetMap(os.path.join(self.tmp.name, "t.db"), lambda: dict(self.cfg))
        self.app.apply_scan(scan(BASE, T0), T0)

    def tearDown(self):
        self.app.db.conn.close()
        self.tmp.cleanup()

    def dev(self, mac):
        return self.app.db.one("SELECT * FROM net_devices WHERE mac=?", (mac,))

    def types(self, mac):
        d = self.dev(mac)
        return [e["type"] for e in reversed(self.app.events(device_id=d["id"]))]

    def test_discovery_roles_kinds(self):
        gw, pi = self.dev(GW), self.dev(PI)
        self.assertEqual(gw["role"], "gateway")
        self.assertEqual(pi["role"], "self")
        self.assertEqual(pi["parent_id"], gw["id"])
        self.assertEqual(self.dev("00:11:32:a8:7f:01")["kind"], "nas")
        self.assertEqual(self.dev("5a:1e:c7:3b:90:12")["kind"], "phone")
        self.assertEqual(self.dev("5a:1e:c7:3b:90:12")["link"], "wifi")
        self.assertEqual(self.dev("d8:f1:5b:6a:02:c4")["kind"], "iot")
        snap = self.app.snapshot()
        self.assertEqual(snap["root_id"], gw["id"])
        self.assertEqual(snap["counts"]["online"], 5)
        self.assertTrue(all(d["parent_id"] == gw["id"] for d in snap["devices"] if d["id"] != gw["id"]))

    def test_offline_log_then_hide_after_7_days(self):
        nas = "00:11:32:a8:7f:01"
        t = T0 + 600  # 10 хв — ще не офлайн (поріг 15 хв)
        self.app.apply_scan(scan([h for h in BASE if h["mac"] != nas], t), t)
        self.assertEqual(self.dev(nas)["status"], "online")
        t = T0 + 1000
        self.app.apply_scan(scan([h for h in BASE if h["mac"] != nas], t), t)
        self.assertEqual(self.dev(nas)["status"], "offline")
        self.assertEqual(self.types(nas)[-1], "offline")
        self.app.tick(T0 + 6 * DAY)
        self.assertEqual(self.dev(nas)["hidden"], 0)
        self.app.tick(T0 + 7 * DAY + 1)
        self.assertEqual(self.dev(nas)["hidden"], 1)
        self.assertEqual(self.types(nas)[-1], "hidden")
        # повернувся в мережу → знову на мапі
        t = T0 + 9 * DAY
        self.app.apply_scan(scan(BASE, t), t)
        d = self.dev(nas)
        self.assertEqual((d["hidden"], d["status"]), (0, "online"))
        self.assertEqual(self.types(nas)[-1], "restored")

    def test_router_and_pi_never_hidden(self):
        t = T0 + 1000
        self.app.apply_scan(scan([], t), t)
        self.assertEqual(self.dev(GW)["status"], "offline")
        self.assertEqual(self.dev(PI)["status"], "online")
        self.app.tick(T0 + 30 * DAY)
        self.assertEqual(self.dev(GW)["hidden"], 0)
        self.assertEqual(self.dev(PI)["hidden"], 0)
        with self.assertRaises(ApiError):
            self.app.delete_device(self.dev(GW)["id"])
        with self.assertRaises(ApiError):
            self.app.delete_device(self.dev(PI)["id"])

    def test_keep_flag(self):
        cam = self.dev("d8:f1:5b:6a:02:c4")
        self.app.update_device(cam["id"], {"keep": True})
        t = T0 + 1000
        self.app.apply_scan(scan([], t), t)
        self.app.tick(T0 + 30 * DAY)
        self.assertEqual(self.dev("d8:f1:5b:6a:02:c4")["hidden"], 0)

    def test_log_retention_400_days(self):
        n0 = self.app.db.one("SELECT COUNT(*) n FROM net_events")["n"]
        self.assertGreater(n0, 0)
        self.app.tick(T0 + 399 * DAY)
        self.assertEqual(self.app.db.one("SELECT COUNT(*) n FROM net_events")["n"], n0)
        self.app.tick(T0 + 401 * DAY)
        # події сканування T0 видалені, лишились лише нові «hidden»
        left = self.app.db.q("SELECT type FROM net_events")
        self.assertTrue(all(e["type"] == "hidden" for e in left))

    def test_reparent_switch_ap_and_cycles(self):
        sw = self.app.create_device({"name": "Комутатор", "kind": "switch"})
        self.assertEqual(sw["status"], "static")
        ap = self.app.create_device({"name": "Точка", "kind": "ap", "parent_id": sw["id"]})
        phone = self.dev("5a:1e:c7:3b:90:12")
        self.app.update_device(phone["id"], {"parent_id": ap["id"]})
        self.assertEqual(self.dev("5a:1e:c7:3b:90:12")["parent_id"], ap["id"])
        nas = self.dev("00:11:32:a8:7f:01")
        self.app.update_device(nas["id"], {"parent_id": ap["id"]})
        self.assertEqual(self.dev(nas["mac"])["link"], "wifi")          # на точку доступу → Wi-Fi
        self.app.update_device(nas["id"], {"parent_id": sw["id"]})
        self.assertEqual(self.dev(nas["mac"])["link"], "wired")         # на комутатор → кабель
        self.app.update_device(nas["id"], {"parent_id": ap["id"], "link": "wired"})
        self.assertEqual(self.dev(nas["mac"])["link"], "wired")         # явний вибір має пріоритет
        self.assertEqual(self.types("5a:1e:c7:3b:90:12")[-1], "moved")
        with self.assertRaises(ApiError):          # цикл: комутатор під власну точку
            self.app.update_device(sw["id"], {"parent_id": ap["id"]})
        with self.assertRaises(ApiError):          # телефон не може бути батьком
            self.app.update_device(ap["id"], {"parent_id": phone["id"]})
        # видалення точки → телефон переходить до комутатора
        self.app.delete_device(ap["id"])
        self.assertEqual(self.dev("5a:1e:c7:3b:90:12")["parent_id"], sw["id"])

    def test_delete_ignore_and_unignore(self):
        esp = "d8:f1:5b:6a:02:c4"
        self.app.delete_device(self.dev(esp)["id"], ignore=True)
        t = T0 + 300
        self.app.apply_scan(scan(BASE, t), t)
        self.assertIsNone(self.dev(esp))
        self.app.unignore(esp.upper())
        t = T0 + 600
        self.app.apply_scan(scan(BASE, t), t)
        self.assertIsNotNone(self.dev(esp))

    def test_delete_without_ignore_reappears_as_new(self):
        esp = "d8:f1:5b:6a:02:c4"
        self.app.delete_device(self.dev(esp)["id"])
        t = T0 + 300
        self.app.apply_scan(scan(BASE, t), t)
        self.assertEqual(self.types(esp), ["new"])

    def test_edit_validation_and_ip_change(self):
        nas = self.dev("00:11:32:a8:7f:01")
        with self.assertRaises(ApiError):
            self.app.update_device(nas["id"], {"ip": "999.1.1.1"})
        with self.assertRaises(ApiError):
            self.app.update_device(nas["id"], {"mac": GW})
        self.app.update_device(nas["id"], {"name": "nas", "vlan": "srv", "kind": "server"})
        d = self.dev("00:11:32:a8:7f:01")
        self.assertEqual((d["name"], d["vlan"], d["kind"], d["kind_locked"]), ("nas", "srv", "server", 1))
        moved = [dict(h, ip="192.168.50.21") if h["mac"] == nas["mac"] else h for h in BASE]
        self.app.apply_scan(scan(moved, T0 + 300), T0 + 300)
        self.assertEqual(self.dev(nas["mac"])["ip"], "192.168.50.21")
        self.assertEqual(self.dev(nas["mac"])["kind"], "server")   # ручний тип не перезаписується
        self.assertIn("ip_changed", self.types(nas["mac"]))

    def test_manual_ip_device_adopts_mac(self):
        cam = self.app.create_device({"name": "Камера двір", "kind": "camera", "ip": "192.168.50.30"})
        self.assertEqual(cam["status"], "offline")
        hosts = BASE + [host("192.168.50.30", "bc:32:5f:00:4d:9a", "Hikvision")]
        self.app.apply_scan(scan(hosts, T0 + 300), T0 + 300)
        d = self.app._get(cam["id"])
        self.assertEqual((d["mac"], d["status"], d["name"]), ("bc:32:5f:00:4d:9a", "online", "Камера двір"))
        self.assertEqual(self.app.db.one("SELECT COUNT(*) n FROM net_devices WHERE ip='192.168.50.30'")["n"], 1)

    def test_hidden_parent_children_move_up(self):
        ap = self.app.create_device({"name": "Точка", "kind": "ap", "mac": "50:91:e3:7a:22:10"})
        phone = self.dev("5a:1e:c7:3b:90:12")
        self.app.update_device(phone["id"], {"parent_id": ap["id"]})
        self.app.tick(time.time() + 8 * DAY)   # точка «офлайн» з моменту створення
        self.assertEqual(self.app._get(ap["id"])["hidden"], 1)
        self.assertEqual(self.dev(phone["mac"])["parent_id"], self.dev(GW)["id"])

    def test_settings_from_molfar(self):
        nas = "00:11:32:a8:7f:01"
        self.cfg["net_stale_days"] = 3
        t = T0 + 1000
        self.app.apply_scan(scan([h for h in BASE if h["mac"] != nas], t), t)
        self.app.tick(T0 + 3 * DAY + 1)
        self.assertEqual(self.dev(nas)["hidden"], 1)

    def test_new_devices_reported(self):
        new = self.app.apply_scan(scan(BASE + [host("192.168.50.99", "aa:bb:cc:00:11:22", "Espressif Inc.")], T0 + 60), T0 + 60)
        self.assertEqual([d["ip"] for d in new], ["192.168.50.99"])
        self.assertEqual(self.app.apply_scan(scan(BASE, T0 + 120), T0 + 120), [])

    def test_counts(self):
        c = self.app.counts()
        self.assertEqual((c["online"], c["offline"], c["hidden"]), (5, 0, 0))


if __name__ == "__main__":
    unittest.main()
