"""Розбір виводу iw та оцінка каналів Wi-Fi. Запуск: python3 -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from molfar import wifi  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def bss(ch, signal, width=20, band="2.4", ssid="n", center=None):
    f = wifi.freq_of(band, ch)
    return {"bssid": f"00:00:00:00:00:{ch:02x}", "ssid": ssid, "freq": f, "band": band, "channel": ch,
            "signal": signal, "width": width, "center": center or f}


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.b = {x["bssid"]: x for x in wifi.parse_scan(fx("iw_scan_sample.txt"))}

    def test_basic_fields(self):
        h = self.b["04:d4:c4:1a:2b:3c"]
        self.assertEqual((h["ssid"], h["freq"], h["channel"], h["band"]), ("Home_Olena", 2437, 6, "2.4"))
        self.assertEqual(h["signal"], -41.0)
        self.assertTrue(h["associated"])
        self.assertEqual(h["sec"], "WPA2/3")
        self.assertEqual(h["gen"], "Wi-Fi 6")
        self.assertEqual((h["width"], h["center"]), (20, 2437))

    def test_cyrillic_ssid_and_ht40(self):
        k = self.b["5c:62:8b:aa:01:02"]
        self.assertEqual(k["ssid"], "Київстар")
        self.assertEqual((k["width"], k["center"]), (40, 2422))
        self.assertEqual(k["sec"], "WPA2")
        self.assertFalse(k["associated"])

    def test_vht80_and_160(self):
        t = self.b["60:a4:b7:00:11:22"]
        self.assertEqual((t["band"], t["channel"], t["width"], t["center"]), ("5", 36, 80, 5210))
        self.assertEqual(t["sec"], "WPA3")
        h = self.b["60:a4:b7:00:11:33"]
        self.assertEqual((h["width"], h["center"], h["ssid"], h["sec"]), (160, 5570, "", "open"))

    def test_survey(self):
        s = wifi.parse_survey(fx("iw_survey_sample.txt"))
        self.assertEqual(s[2412]["busy_pct"], 62.0)
        self.assertTrue(s[2437]["in_use"])
        self.assertEqual(s[2462]["noise"], -95)

    def test_phy_and_link(self):
        p = wifi.parse_phy("""
			* 2412.0 MHz [1] (20.0 dBm)
			* 2484 MHz [14] (disabled)
			* 5260.0 MHz [52] (20.0 dBm) (no IR, radar detection)
""")
        self.assertTrue(p[2484]["disabled"])
        self.assertTrue(p[5260]["radar"] and p[5260]["no_ir"])
        ln = wifi.parse_link("Connected to 04:d4:c4:1a:2b:3c (on wlan0)\n\tSSID: Home\n\tfreq: 5180\n\tsignal: -50 dBm\n")
        self.assertEqual((ln["ssid"], ln["channel"], ln["band"], ln["signal"]), ("Home", 36, "5", -50))
        self.assertFalse(wifi.parse_link("Not connected.")["connected"])


class ScoreTest(unittest.TestCase):
    def test_empty_air_is_clean(self):
        sc = wifi.channel_scores("2.4", [], {}, {})
        self.assertTrue(all(c["score"] == 100 for c in sc.values()))

    def test_busy_channel_loses(self):
        sc = wifi.channel_scores("2.4", [bss(1, -50), bss(1, -60), bss(6, -85)], {}, {})
        self.assertLess(sc[1]["score"], 20)
        self.assertGreater(sc[11]["score"], sc[6]["score"])
        self.assertEqual(sc[1]["same"], 2)
        self.assertGreaterEqual(sc[3]["partial"], 1)
        rec = wifi.recommend("2.4", sc, 3)
        self.assertEqual(rec["ch"], 11)

    def test_partial_overlap_worse_than_cochannel(self):
        co = wifi.channel_scores("2.4", [bss(6, -65)], {}, {})[6]["score"]
        part = wifi.channel_scores("2.4", [bss(4, -65)], {}, {})[6]["score"]
        self.assertLess(part, co)

    def test_survey_busy_counts(self):
        sv = {2462: {"freq": 2462, "in_use": False, "noise": -95, "busy_pct": 90.0}}
        sc = wifi.channel_scores("2.4", [], sv, {})
        self.assertLess(sc[11]["score"], sc[6]["score"])
        self.assertEqual(wifi.recommend("2.4", sc, 0)["ch"], 1)

    def test_5ghz_block_and_dfs_preference(self):
        nets = [bss(36, -55, 80, "5", center=5210), bss(149, -60, 80, "5", center=5775)]
        sc = wifi.channel_scores("5", nets, {}, {})
        rec = wifi.recommend("5", sc, 2)
        self.assertNotIn(rec["ch"], (36, 40, 44, 48, 149, 153, 157, 161))
        self.assertEqual(rec["width"], 80)
        self.assertTrue(rec["dfs"])   # обидва блоки без DFS зайняті — лишаються DFS

    def test_adjacent_block_is_not_cochannel(self):
        sc = wifi.channel_scores("5", [bss(44, -55, 80, "5", center=5210)], {}, {})
        self.assertLess(sc[48]["score"], 20)
        self.assertGreater(sc[52]["score"], sc[48]["score"] + 35)

    def test_disabled_channels_skipped(self):
        phy = {wifi.freq_of("5", c): {"ch": c, "disabled": c >= 52, "radar": False, "no_ir": False}
               for c in wifi.CH5}
        sc = wifi.channel_scores("5", [], {}, phy)
        rec = wifi.recommend("5", sc, 0)
        self.assertEqual(rec["block"], [36, 40, 44, 48])

    def test_own_network_excluded(self):
        nets = [dict(bss(6, -35), ssid="Mine"), bss(1, -55), bss(11, -55)]
        res = wifi.analyze(nets, {}, {}, ["Mine"])
        b = res["bands"]["2.4"]
        self.assertEqual(b["rec"]["ch"], 6)
        self.assertEqual(b["current"]["ch"], 6)


if __name__ == "__main__":
    unittest.main()
