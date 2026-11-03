"""Тести вкладки «Діагностика»: перевірка параметрів і розбір виводу. Без мережі й без запуску утиліт."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from molfar import diag  # noqa: E402

PING = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=14.1 ms
64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=16.3 ms
64 bytes from 8.8.8.8: icmp_seq=4 ttl=117 time=15.2 ms

--- 8.8.8.8 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 603ms
rtt min/avg/max/mdev = 14.1/15.2/16.3/0.9 ms
"""
TRACE = """traceroute to 1.1.1.1 (1.1.1.1), 30 hops max, 60 byte packets
 1  192.168.1.1  0.612 ms  0.580 ms  0.571 ms
 2  * * *
 3  core.isp.example (195.24.128.33)  4.101 ms  4.230 ms *
 4  1.1.1.1  9.402 ms  9.380 ms  9.511 ms
"""
TRACE_FAIL = """traceroute to 10.9.9.9 (10.9.9.9), 3 hops max, 60 byte packets
 1  192.168.1.1  0.612 ms  0.580 ms  0.571 ms
 2  192.168.1.1  3001.2 ms !H  *  *
 3  * * *
"""
NS_OK = """Server:\t\t192.168.1.1
Address:\t192.168.1.1#53

Non-authoritative answer:
google.com\tmail exchanger = 10 smtp.google.com.

"""
NS_FAIL = """Server:\t\t8.8.8.8
Address:\t8.8.8.8#53

** server can't find nope.invalid: NXDOMAIN
"""
TELNET_OPEN = """Trying 192.168.1.10...
Connected to 192.168.1.10.
Escape character is '^]'.
SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3
"""
TELNET_REFUSED = "Trying 127.0.0.1...\ntelnet: Unable to connect to remote host: Connection refused\n"


def iperf_json(**over):
    d = {
        "start": {"connected": [{"local_host": "192.168.1.50", "remote_host": "192.168.1.10"}],
                  "test_start": {"protocol": "TCP", "reverse": 1}},
        "intervals": [{"sum": {"end": 1.0, "bits_per_second": 9.4e8}}, {"sum": {"end": 2.0, "bits_per_second": 9.38e8}}],
        "end": {"sum_sent": {"bits_per_second": 9.41e8, "retransmits": 5},
                "sum_received": {"bits_per_second": 9.39e8, "bytes": 2.3e8},
                "cpu_utilization_percent": {"host_total": 23.4, "remote_total": 7.9}},
    }
    d.update(over)
    return json.dumps(d)


class Validation(unittest.TestCase):
    def test_hosts(self):
        for good in ("1.1.1.1", "::1", "[2001:db8::1]", "google.com", "router.lan.", "my_host-1"):
            self.assertTrue(diag.clean_host(good))
        for bad in ("", "-x", "a b", "$(reboot)", "; rm -rf /", "host/../x", "x" * 300, "--help"):
            with self.assertRaises(diag.DiagError, msg=bad):
                diag.clean_host(bad)

    def test_ping_args(self):
        args, timeout, p, target = diag.build("ping", {"host": "8.8.8.8", "count": 5, "family": "4"})
        self.assertEqual(args[:3], ["ping", "-n", "-c"])
        self.assertIn("-4", args)
        self.assertEqual(args[-2:], ["--", "8.8.8.8"])
        self.assertEqual(target, "8.8.8.8")
        with self.assertRaises(diag.DiagError) as e:
            diag.build("ping", {"host": "8.8.8.8", "count": 1000})
        self.assertEqual(e.exception.code, "range_count")

    def test_traceroute_modes(self):
        self.assertIn("-I", diag.build("traceroute", {"host": "1.1.1.1", "mode": "icmp"})[0])
        a = diag.build("traceroute", {"host": "1.1.1.1", "mode": "tcp", "port": 80})[0]
        self.assertEqual(a[a.index("-T"):a.index("-T") + 3], ["-T", "-p", "80"])
        self.assertIn("-n", diag.build("traceroute", {"host": "1.1.1.1"})[0])
        with self.assertRaises(diag.DiagError):
            diag.build("traceroute", {"host": "1.1.1.1", "mode": "raw"})

    def test_nslookup(self):
        a = diag.build("nslookup", {"host": "google.com", "type": "mx", "server": "1.1.1.1"})[0]
        self.assertEqual(a, ["nslookup", "-type=MX", "-timeout=3", "-retry=2", "google.com", "1.1.1.1"])
        with self.assertRaises(diag.DiagError):
            diag.build("nslookup", {"host": "google.com", "type": "AXFR"})

    def test_telnet_needs_port(self):
        with self.assertRaises(diag.DiagError) as e:
            diag.build("telnet", {"host": "1.2.3.4"})
        self.assertEqual(e.exception.code, "need_port")
        self.assertEqual(diag.build("telnet", {"host": "1.2.3.4", "port": 22})[0], ["telnet", "1.2.3.4", "22"])

    def test_iperf_args(self):
        a = diag.build("iperf3", {"host": "10.0.0.2", "direction": "down", "streams": 4})[0]
        self.assertIn("-R", a)
        self.assertIn("-J", a)
        self.assertEqual(a[a.index("-P") + 1], "4")
        a = diag.build("iperf3", {"host": "10.0.0.2", "direction": "bidir", "protocol": "udp", "bandwidth": 50})[0]
        self.assertIn("--bidir", a)
        self.assertEqual(a[a.index("-b") + 1], "50M")
        with self.assertRaises(diag.DiagError):
            diag.build("iperf3", {"host": "10.0.0.2", "duration": 3600})


class Parsing(unittest.TestCase):
    def test_ping(self):
        r = diag.parse_ping(PING)
        self.assertEqual((r["sent"], r["received"], r["loss"]), (4, 3, 25.0))
        self.assertEqual((r["min"], r["max"]), (14.1, 16.3))
        self.assertAlmostEqual(r["jitter"], 1.65, places=2)

    def test_traceroute(self):
        r = diag.parse_traceroute(TRACE)
        self.assertTrue(r["reached"])
        self.assertEqual([h["hop"] for h in r["hops"]], [1, 2, 3, 4])
        self.assertEqual(r["hops"][1]["lost"], 3)
        self.assertEqual(r["hops"][2]["addrs"], ["195.24.128.33"])
        self.assertEqual(r["hops"][2]["names"], ["core.isp.example"])
        self.assertEqual((r["hops"][2]["lost"], r["hops"][2]["sent"]), (1, 3))
        f = diag.parse_traceroute(TRACE_FAIL)
        self.assertFalse(f["reached"])
        self.assertEqual(f["hops"][1]["flags"], ["!H"])

    def test_nslookup(self):
        r = diag.parse_nslookup(NS_OK)
        self.assertEqual(r["server"], "192.168.1.1")
        self.assertEqual(r["answers"], ["google.com\tmail exchanger = 10 smtp.google.com."])
        self.assertEqual(r["error"], "")
        f = diag.parse_nslookup(NS_FAIL)
        self.assertIn("NXDOMAIN", f["error"])
        self.assertEqual(f["answers"], [])

    def test_telnet(self):
        r = diag.parse_telnet(TELNET_OPEN, timed_out=True)
        self.assertEqual(r["state"], "open")
        self.assertEqual(r["banner"], ["SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3"])
        self.assertEqual(diag.parse_telnet(TELNET_REFUSED, False)["state"], "refused")
        self.assertEqual(diag.parse_telnet("Trying 10.1.1.1...\n", True)["state"], "timeout")
        self.assertEqual(diag.parse_telnet("telnet: could not resolve x/22: Name or service not known", False)["state"], "dns")

    def test_iperf_tcp(self):
        r = diag.parse_iperf(iperf_json())
        self.assertFalse(r["udp"])
        self.assertTrue(r["reverse"])
        self.assertEqual((r["sent_mbps"], r["recv_mbps"], r["retransmits"]), (941.0, 939.0, 5))
        self.assertEqual(r["intervals"], [[1.0, 940.0], [2.0, 938.0]])
        self.assertEqual(r["remote"], "192.168.1.10")
        s = diag.summary_of("iperf3", r)
        self.assertNotIn("intervals", s)

    def test_iperf_udp_and_error(self):
        d = json.loads(iperf_json())
        d["start"]["test_start"]["protocol"] = "UDP"
        d["end"] = {"sum": {"bits_per_second": 5e7, "jitter_ms": 0.21, "lost_percent": 1.5, "lost_packets": 9, "packets": 600}}
        r = diag.parse_iperf(json.dumps(d))
        self.assertTrue(r["udp"])
        self.assertEqual((r["mbps"], r["lost_pct"], r["jitter_ms"]), (50.0, 1.5, 0.21))
        e = diag.parse_iperf(json.dumps({"start": {}, "intervals": [], "end": {},
                                         "error": "unable to connect to server: Connection refused"}))
        self.assertIn("Connection refused", e["error"])
        self.assertEqual(diag.parse_iperf("garbage")["error"], "no_json")


if __name__ == "__main__":
    unittest.main()
