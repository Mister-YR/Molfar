"""Вкладка «Діагностика»: ping, traceroute, nslookup, telnet (перевірка TCP-порту) та iperf3.

Кожен запуск — «завдання» (job): процес без shell, вивід збирається построчно й віддається
веб-інтерфейсу частинами (GET /api/diag/jobs/{id}?since=N), тож довгий traceroute видно наживо.
Усі параметри перевіряються тут (білий список значень, адреса — лише IP або ім'я хоста),
команди формуються списком аргументів — жодної підстановки в shell.

iperf3:
  • клієнт — тест пропускної здатності до iperf3-сервера (інший ПК, NAS, роутер, VPS);
  • сервер — Мольфар сам слухає порт (за замовчуванням 5201), щоб тест можна було запустити
    з телефона / ноутбука: `iperf3 -c <IP Мольфара>`. Вмикається перемикачем, за бажанням —
    з автовимкненням; стан зберігається й відновлюється після перезапуску сервісу.

Історія запусків — таблиця diag_runs (останні MAX_HISTORY записів).
"""
import asyncio
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import time
from itertools import count

from fastapi import APIRouter, Body, HTTPException

log = logging.getLogger("molfar.diag")

TOOLS = ("ping", "traceroute", "nslookup", "telnet", "iperf3")
NS_TYPES = ("A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "SRV", "CAA")
TR_MODES = ("udp", "icmp", "tcp")
IPERF_DIRS = ("up", "down", "bidir")
MAX_RUNNING = 3            # одночасно запущених завдань
MAX_JOBS = 30              # скільки завдань тримати в пам'яті
MAX_LINES = 3000           # рядків виводу на завдання
MAX_LINE = 2000            # символів у рядку
MAX_HISTORY = 200          # записів у diag_runs
STORE_OUTPUT = 64 * 1024   # скільки виводу зберігати в історії
SERVER_LOG = 300           # рядків журналу iperf3-сервера
AUTO_OFF = (0, 15, 60, 240, 1440)   # хв; 0 — не вимикати
STATE_KEY = "diag_iperf_server"

HOST_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62})(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,62}))*\.?$")
RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms")
SUMMARY_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
TR_HOP_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
TR_MS_RE = re.compile(r"([\d.]+)\s*ms")
TR_ADDR_RE = re.compile(r"(?:(\S+)\s+\(([0-9a-fA-F:.]+)\))|([0-9]{1,3}(?:\.[0-9]{1,3}){3}|[0-9a-fA-F]*:[0-9a-fA-F:]+)")
ACCEPT_RE = re.compile(r"Accepted connection from ([0-9a-fA-F:.]+), port (\d+)")
SRV_SUM_RE = re.compile(r"\[SUM\]|\[\s*\d+\]\s+0\.00-")
RATE_RE = re.compile(r"([\d.]+)\s+([KMG]?)bits/sec")


class DiagError(Exception):
    def __init__(self, code, status=400, **kw):
        super().__init__(code)
        self.code, self.status, self.kw = code, status, kw


# ---------------------------------------------------------------- перевірка параметрів
def clean_host(v, field="host"):
    v = str(v or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    if not v:
        raise DiagError("need_" + field)
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        pass
    if v.startswith("-") or not HOST_RE.match(v):
        raise DiagError("bad_" + field)
    return v.rstrip(".") or v


def clean_int(v, lo, hi, default, field):
    if v in (None, ""):
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise DiagError("bad_" + field)
    if not lo <= n <= hi:
        raise DiagError("range_" + field, lo=lo, hi=hi)
    return n


def clean_choice(v, choices, default, field):
    if v in (None, ""):
        return default
    v = str(v).strip()
    if v not in choices:
        raise DiagError("bad_" + field)
    return v


def family_flag(fam):
    return {"4": ["-4"], "6": ["-6"]}.get(str(fam or ""), [])


def build(tool, p):
    """(аргументи команди, тайм-аут с, нормалізовані параметри, ціль для історії)."""
    fam = clean_choice(p.get("family"), ("", "auto", "4", "6"), "", "family")
    fam = "" if fam == "auto" else fam
    if tool == "ping":
        host = clean_host(p.get("host"))
        n = clean_int(p.get("count"), 1, 100, 10, "count")
        size = clean_int(p.get("size"), 16, 1472, 56, "size")
        args = ["ping", "-n", "-c", str(n), "-i", "0.2", "-W", "2", "-s", str(size), *family_flag(fam), "--", host]
        return args, n * 0.2 + 25, {"host": host, "count": n, "size": size, "family": fam}, host
    if tool == "traceroute":
        host = clean_host(p.get("host"))
        mode = clean_choice(p.get("mode"), TR_MODES, "udp", "mode")
        hops = clean_int(p.get("max_hops"), 1, 64, 30, "max_hops")
        port = clean_int(p.get("port"), 1, 65535, 443, "port")
        names = bool(p.get("resolve"))
        args = ["traceroute", *([] if names else ["-n"]), "-w", "2", "-q", "3", "-m", str(hops), *family_flag(fam)]
        if mode == "icmp":
            args.append("-I")
        elif mode == "tcp":
            args += ["-T", "-p", str(port)]
        args.append(host)
        return args, hops * 7 + 30, {"host": host, "mode": mode, "max_hops": hops, "port": port,
                                     "resolve": names, "family": fam}, host
    if tool == "nslookup":
        host = clean_host(p.get("host"))
        qtype = clean_choice(str(p.get("type") or "A").upper(), NS_TYPES, "A", "type")
        server = str(p.get("server") or "").strip()
        server = clean_host(server, "server") if server else ""
        # «--» nslookup не розуміє; ім'я/адреса вже перевірені й не починаються з «-»
        args = ["nslookup", "-type=" + qtype, "-timeout=3", "-retry=2", host] + ([server] if server else [])
        return args, 30, {"host": host, "type": qtype, "server": server}, host + (" @" + server if server else "")
    if tool == "telnet":
        host = clean_host(p.get("host"))
        port = clean_int(p.get("port"), 1, 65535, None, "port")
        if port is None:
            raise DiagError("need_port")
        wait = clean_int(p.get("wait"), 2, 30, 5, "wait")
        return ["telnet", host, str(port)], wait, {"host": host, "port": port, "wait": wait}, f"{host}:{port}"
    if tool == "iperf3":
        host = clean_host(p.get("host"))
        port = clean_int(p.get("port"), 1, 65535, 5201, "port")
        dur = clean_int(p.get("duration"), 3, 60, 10, "duration")
        streams = clean_int(p.get("streams"), 1, 16, 1, "streams")
        direction = clean_choice(p.get("direction"), IPERF_DIRS, "down", "direction")
        proto = clean_choice(p.get("protocol"), ("tcp", "udp"), "tcp", "protocol")
        bw = clean_int(p.get("bandwidth"), 1, 10000, 100, "bandwidth")
        args = ["iperf3", "-c", host, "-p", str(port), "-t", str(dur), "-P", str(streams), "-J", *family_flag(fam)]
        if direction == "down":
            args.append("-R")
        elif direction == "bidir":
            args.append("--bidir")
        if proto == "udp":
            args += ["-u", "-b", f"{bw}M"]
        return args, dur + 25, {"host": host, "port": port, "duration": dur, "streams": streams,
                                "direction": direction, "protocol": proto, "bandwidth": bw,
                                "family": fam}, f"{host}:{port}"
    raise DiagError("bad_tool")


# ---------------------------------------------------------------- розбір виводу
def parse_ping(text):
    rtts = [float(x) for x in RTT_RE.findall(text)]
    m = SUMMARY_RE.search(text)
    sent = int(m.group(1)) if m else len(rtts)
    recv = int(m.group(2)) if m else len(rtts)
    res = {"sent": sent, "received": recv,
           "loss": round(100.0 * (sent - recv) / sent, 1) if sent else None}
    if rtts:
        res.update(min=round(min(rtts), 2), avg=round(sum(rtts) / len(rtts), 2), max=round(max(rtts), 2),
                   jitter=round(sum(abs(b - a) for a, b in zip(rtts, rtts[1:])) / (len(rtts) - 1), 2)
                   if len(rtts) > 1 else 0.0, rtts=rtts[:100])
    return res


def parse_traceroute(text):
    hops = []
    for line in text.splitlines():
        m = TR_HOP_RE.match(line)
        if not m or line.lstrip().startswith("traceroute"):
            continue
        rest = m.group(2)
        rtts = [float(x) for x in TR_MS_RE.findall(rest)]
        stars = rest.count("*")
        addrs, names = [], []
        for a in TR_ADDR_RE.finditer(TR_MS_RE.sub(" ", rest)):
            name, ip_in, ip = a.group(1), a.group(2), a.group(3)
            addr = ip_in or ip
            if addr and addr not in addrs:
                addrs.append(addr)
                names.append(name if name and name != addr else "")
        flags = re.findall(r"!\w*", rest)
        hops.append({"hop": int(m.group(1)), "addrs": addrs, "names": names, "rtts": rtts,
                     "lost": stars, "sent": stars + len(rtts),
                     "avg": round(sum(rtts) / len(rtts), 2) if rtts else None, "flags": flags})
    reached = bool(hops) and bool(hops[-1]["rtts"])
    return {"hops": hops, "reached": reached}


def parse_nslookup(text):
    server, answers, err = "", [], ""
    lines = text.splitlines()
    in_answer = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("Server:"):
            server = s.split(":", 1)[1].strip()
        elif s.startswith(("Non-authoritative answer", "Authoritative answers")):
            in_answer = True
        elif s.startswith("** ") or "server can't find" in s or "connection timed out" in s or "NXDOMAIN" in s:
            err = s.strip("* ").strip()
        elif in_answer and s and not s.startswith("Name:"):
            answers.append(s)
        elif not in_answer and s.startswith("Address:") and i > 1 and server and "#53" not in s:
            answers.append(s)  # деякі збірки nslookup без «Non-authoritative answer»
    return {"server": server, "answers": answers[:100], "error": err}


def parse_telnet(text, timed_out):
    t = text.lower()
    connected = "connected to" in t
    if connected:
        state = "open"
    elif "connection refused" in t:
        state = "refused"
    elif "unknown host" in t or "name or service not known" in t or "could not resolve" in t \
            or "temporary failure in name resolution" in t:
        state = "dns"
    elif "no route to host" in t or "network is unreachable" in t:
        state = "unreachable"
    elif timed_out or "timed out" in t:
        state = "timeout"
    else:
        state = "error"
    banner = []
    if connected:
        after = text.split("Escape character is", 1)[1] if "Escape character is" in text else ""
        banner = [ln for ln in after.splitlines()[1:] if ln.strip() and "Connection closed" not in ln][:20]
    return {"state": state, "banner": banner}


def parse_iperf(text):
    i = text.find("{")
    try:
        d = json.loads(text[i:]) if i >= 0 else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not d:
        # не JSON: стара версія iperf3 або аварійний вихід — віддаємо останній рядок як помилку
        last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
        return {"error": last[:300] if last and i < 0 and "iperf3" in last.lower() else "no_json"}
    if d.get("error") and not d.get("end"):
        return {"error": d["error"]}
    end = d.get("end") or {}
    start = d.get("start") or {}
    udp = (start.get("test_start") or {}).get("protocol") == "UDP"
    mb = lambda x: round((x or 0) / 1e6, 2)  # noqa: E731
    res = {"udp": udp, "intervals": [], "intervals_rev": []}
    for iv in d.get("intervals") or []:
        s = iv.get("sum") or {}
        res["intervals"].append([round(s.get("end", 0), 1), mb(s.get("bits_per_second"))])
        r = iv.get("sum_bidir_reverse")
        if r:
            res["intervals_rev"].append([round(r.get("end", 0), 1), mb(r.get("bits_per_second"))])
    conn = (start.get("connected") or [{}])[0]
    res["local"] = conn.get("local_host")
    res["remote"] = conn.get("remote_host")
    res["reverse"] = bool((start.get("test_start") or {}).get("reverse"))
    if udp:
        s = end.get("sum") or {}
        res.update(mbps=mb(s.get("bits_per_second")), jitter_ms=round(s.get("jitter_ms") or 0, 3),
                   lost_pct=round(s.get("lost_percent") or 0, 2), lost=s.get("lost_packets"),
                   packets=s.get("packets"))
        r = end.get("sum_bidir_reverse")
        if r:
            res.update(mbps_rev=mb(r.get("bits_per_second")), jitter_rev=round(r.get("jitter_ms") or 0, 3),
                       lost_pct_rev=round(r.get("lost_percent") or 0, 2))
    else:
        snd, rcv = end.get("sum_sent") or {}, end.get("sum_received") or {}
        res.update(sent_mbps=mb(snd.get("bits_per_second")), recv_mbps=mb(rcv.get("bits_per_second")),
                   retransmits=snd.get("retransmits"), bytes=rcv.get("bytes"))
        rs, rr = end.get("sum_sent_bidir_reverse"), end.get("sum_received_bidir_reverse")
        if rs or rr:
            res.update(sent_mbps_rev=mb((rs or {}).get("bits_per_second")),
                       recv_mbps_rev=mb((rr or {}).get("bits_per_second")),
                       retransmits_rev=(rs or {}).get("retransmits"))
    cpu = end.get("cpu_utilization_percent") or {}
    if cpu:
        res["cpu_local"] = round(cpu.get("host_total") or 0, 1)
        res["cpu_remote"] = round(cpu.get("remote_total") or 0, 1)
    if d.get("error"):
        res["error"] = d["error"]
    return res


def summary_of(tool, r):
    """Короткий підсумок для історії (без великих масивів)."""
    if not r:
        return {}
    if tool == "ping":
        return {k: r.get(k) for k in ("sent", "received", "loss", "avg", "jitter")}
    if tool == "traceroute":
        last = r["hops"][-1] if r.get("hops") else {}
        return {"hops": len(r.get("hops") or []), "reached": r.get("reached"), "last_avg": last.get("avg")}
    if tool == "nslookup":
        return {"answers": len(r.get("answers") or []), "first": (r.get("answers") or [""])[0][:120],
                "error": r.get("error")}
    if tool == "telnet":
        return {"state": r.get("state")}
    if tool == "iperf3":
        keys = ("udp", "mbps", "mbps_rev", "sent_mbps", "recv_mbps", "sent_mbps_rev", "recv_mbps_rev",
                "retransmits", "jitter_ms", "lost_pct", "error", "reverse")
        return {k: r.get(k) for k in keys if r.get(k) is not None}
    return {}


# ---------------------------------------------------------------- завдання
class Job:
    _ids = count(1)

    def __init__(self, tool, params, target, args, timeout):
        self.id = next(self._ids)
        self.tool, self.params, self.target, self.args, self.timeout = tool, params, target, args, timeout
        self.lines, self.status, self.rc, self.result, self.error = [], "running", None, None, None
        self.started, self.finished = time.time(), None
        self.proc, self.task, self.stopped = None, None, False

    def add(self, line):
        if len(self.lines) < MAX_LINES:
            self.lines.append(line[:MAX_LINE])
        elif len(self.lines) == MAX_LINES:
            self.lines.append("… (вивід обрізано)")

    def snap(self, since=0):
        since = max(0, min(int(since or 0), len(self.lines)))
        return {"id": self.id, "tool": self.tool, "params": self.params, "target": self.target,
                "cmd": " ".join(self.args), "status": self.status, "rc": self.rc, "error": self.error,
                "started": self.started, "finished": self.finished, "timeout": self.timeout,
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "lines": self.lines[since:], "next": len(self.lines), "result": self.result}


class IperfServer:
    """iperf3 -s як дочірній процес Мольфара (systemd не потрібен — працює й у Docker)."""

    def __init__(self, diag):
        self.diag = diag
        self.proc, self.task = None, None
        self.port, self.auto_off, self.off_at = 5201, 0, None
        self.started, self.error, self.restarts = None, None, 0
        self.log, self.clients = [], []

    @property
    def running(self):
        return self.proc is not None and self.proc.returncode is None

    def snap(self):
        return {"enabled": self.task is not None and not self.task.done(), "running": self.running,
                "port": self.port, "auto_off_min": self.auto_off, "off_at": self.off_at,
                "started": self.started, "error": self.error, "clients": self.clients[-10:][::-1],
                "log": self.log[-60:]}

    def _add_log(self, line):
        self.log.append(line[:MAX_LINE])
        del self.log[:-SERVER_LOG]
        m = ACCEPT_RE.search(line)
        if m:
            self.clients.append({"ip": m.group(1), "ts": time.time(), "rate": None})
            del self.clients[:-50]
        elif self.clients and ("receiver" in line or "sender" in line) and SRV_SUM_RE.search(line):
            r = RATE_RE.search(line)
            if r:
                mult = {"": 1e-6, "K": 1e-3, "M": 1, "G": 1e3}[r.group(2)]
                self.clients[-1]["rate"] = round(float(r.group(1)) * mult, 2)

    async def start(self, port, auto_off=0):
        await self.stop(persist=False)
        self.port, self.auto_off = port, auto_off
        self.off_at = time.time() + auto_off * 60 if auto_off else None
        self.error, self.restarts = None, 0
        self.task = asyncio.create_task(self._supervise())

    async def stop(self, persist=True):
        t, self.task = self.task, None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._kill()
        self.started, self.off_at = None, None
        if persist:
            self.diag.save_server_state(False)

    async def _kill(self):
        p = self.proc
        if p and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
        self.proc = None

    async def _supervise(self):
        try:
            while True:
                if not self.diag.bins["iperf3"]:
                    self.error = "no_iperf3"
                    return
                if self.off_at and time.time() >= self.off_at:
                    break
                try:
                    self.proc = await asyncio.create_subprocess_exec(
                        self.diag.bins["iperf3"], "-s", "-p", str(self.port), "--forceflush",
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT, env={**os.environ, "LC_ALL": "C"})
                except OSError as e:
                    self.error = str(e)[:200]
                    return
                self.started = self.started or time.time()
                self._add_log(f"--- iperf3 -s -p {self.port}")
                reader = asyncio.create_task(self._read())
                waiter = asyncio.create_task(self.proc.wait())
                timeout = max(1.0, self.off_at - time.time()) if self.off_at else None
                done, _ = await asyncio.wait({waiter}, timeout=timeout)
                if not done:              # час автовимкнення
                    await self._kill()
                    await reader
                    break
                await reader
                rc = self.proc.returncode
                tail = " ".join(self.log[-3:]).lower()
                if "address already in use" in tail:
                    self.error = "port_busy"
                    return
                self.error = f"exited rc={rc}"
                self.restarts += 1
                if self.restarts > 5:
                    return
                await asyncio.sleep(min(30, 2 ** self.restarts))
            self.diag.db.log("info", f"iperf3 server stopped (auto-off after {self.auto_off} min)")
            self.diag.save_server_state(False)
            self.started, self.off_at = None, None
        except asyncio.CancelledError:
            raise

    async def _read(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            s = line.decode(errors="replace").rstrip()
            if s:
                self._add_log(s)


class Diag:
    def __init__(self, db):
        self.db = db
        self.jobs = {}
        self.bins = {}
        self.refresh_bins()
        self.server = IperfServer(self)
        db.execute("""CREATE TABLE IF NOT EXISTS diag_runs (
            id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, tool TEXT NOT NULL, target TEXT NOT NULL,
            params TEXT, status TEXT, duration REAL, summary TEXT, output TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS ix_diag_ts ON diag_runs(ts)")

    def refresh_bins(self):
        extra = ("/usr/sbin", "/sbin", "/usr/bin", "/bin", "/usr/local/bin")
        path = os.pathsep.join([os.environ.get("PATH", "")] + list(extra))
        self.bins = {t: shutil.which(t, path=path) for t in TOOLS}

    def iperf_version(self):
        return getattr(self, "_iperf_ver", None)

    async def _detect_iperf_version(self):
        if not self.bins["iperf3"]:
            self._iperf_ver = None
            return
        try:
            p = await asyncio.create_subprocess_exec(self.bins["iperf3"], "--version",
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(p.communicate(), 5)
            m = re.search(r"iperf\s+([\d.]+)", out.decode(errors="replace"))
            self._iperf_ver = m.group(1) if m else None
        except (OSError, asyncio.TimeoutError):
            self._iperf_ver = None

    # ---------- життєвий цикл ----------
    async def start(self):
        await self._detect_iperf_version()
        st = self.db.get_state(STATE_KEY) or {}
        if st.get("enabled") and self.bins["iperf3"]:
            off_at = st.get("off_at")
            if off_at and off_at <= time.time():
                self.save_server_state(False)
                return
            await self.server.start(int(st.get("port") or 5201), int(st.get("auto_off_min") or 0))
            if off_at:
                self.server.off_at = off_at

    async def shutdown(self):
        for j in list(self.jobs.values()):
            if j.status == "running":
                await self.stop_job(j.id)
        t, self.server.task = self.server.task, None
        if t and not t.done():
            t.cancel()
        await self.server._kill()

    def save_server_state(self, enabled):
        s = self.server
        self.db.set_state(STATE_KEY, {"enabled": bool(enabled), "port": s.port, "auto_off_min": s.auto_off,
                                      "off_at": s.off_at if enabled else None})

    # ---------- завдання ----------
    def running(self):
        return [j for j in self.jobs.values() if j.status == "running"]

    async def run(self, tool, params):
        if tool not in TOOLS:
            raise DiagError("bad_tool")
        args, timeout, norm, target = build(tool, params or {})
        if not self.bins[tool] and tool != "telnet":
            self.refresh_bins()
            if not self.bins[tool]:
                raise DiagError("not_installed", tool=tool)
        if len(self.running()) >= MAX_RUNNING:
            raise DiagError("busy", 429)
        if tool == "iperf3" and any(j.tool == "iperf3" for j in self.running()):
            raise DiagError("iperf_busy", 429)
        if self.bins.get(tool):
            args = [self.bins[tool]] + args[1:]
        job = Job(tool, norm, target, args, timeout)
        self.jobs[job.id] = job
        for old in sorted(self.jobs)[:-MAX_JOBS]:
            if self.jobs[old].status != "running":
                del self.jobs[old]
        job.task = asyncio.create_task(self._exec(job))
        return job

    async def _exec(self, job):
        timed_out = False
        try:
            if job.tool == "telnet" and not self.bins["telnet"]:
                timed_out = await self._tcp_probe(job)
            else:
                timed_out = await self._spawn(job)
            text = "\n".join(job.lines)
            if job.tool == "ping":
                job.result = parse_ping(text)
            elif job.tool == "traceroute":
                job.result = parse_traceroute(text)
            elif job.tool == "nslookup":
                job.result = parse_nslookup(text)
                job.result["ms"] = round((time.time() - job.started) * 1000)
            elif job.tool == "telnet":
                job.result = parse_telnet(text, timed_out)
            elif job.tool == "iperf3":
                job.result = parse_iperf(job.raw if hasattr(job, "raw") else text)
            if job.stopped:
                job.status = "stopped"
            elif job.tool == "telnet":
                job.status = "done"
            elif timed_out:
                job.status, job.error = "error", "timeout"
            elif job.tool == "iperf3" and (job.result or {}).get("error") and job.rc != 0:
                job.status, job.error = "error", job.result["error"]
            else:
                job.status = "done" if job.rc == 0 or job.tool in ("traceroute", "ping", "nslookup") else "error"
        except Exception as e:  # noqa: BLE001
            log.exception("diag job failed")
            job.status, job.error = "error", str(e)[:300]
        finally:
            job.finished = time.time()
            job.proc = None
            self._store(job)

    async def _spawn(self, job):
        """Запускає процес, читає вивід построчно. Повертає True, якщо спрацював тайм-аут."""
        keep_stdin = job.tool == "telnet"   # telnet виходить одразу, якщо stdin закрито
        job.proc = await asyncio.create_subprocess_exec(
            *job.args, stdin=asyncio.subprocess.PIPE if keep_stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        raw = []

        async def reader():
            while True:
                line = await job.proc.stdout.readline()
                if not line:
                    return
                s = line.decode(errors="replace").rstrip("\r\n")
                if job.tool == "iperf3":
                    raw.append(s)          # JSON: розбираємо повністю, показуємо людський підсумок
                else:
                    job.add(s)

        rd = asyncio.create_task(reader())
        timed_out = False
        try:
            await asyncio.wait_for(job.proc.wait(), job.timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                job.proc.kill()
            except ProcessLookupError:
                pass
            await job.proc.wait()
        try:
            await asyncio.wait_for(rd, 5)
        except asyncio.TimeoutError:
            rd.cancel()
        job.rc = job.proc.returncode
        if job.tool == "iperf3":
            job.raw = "\n".join(raw)
            res = parse_iperf(job.raw)
            for ln in iperf_text(res, job.params):
                job.add(ln)
        return timed_out

    async def _tcp_probe(self, job):
        """Запасний варіант «telnet», якщо програма telnet не встановлена: TCP-з'єднання + банер."""
        host, port, wait = job.params["host"], job.params["port"], job.params["wait"]
        job.add(f"Trying {host} {port}...")
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(host, port), wait)
        except asyncio.TimeoutError:
            job.add("Connection timed out")
            return True
        except socket.gaierror as e:
            job.add(f"telnet: could not resolve {host}: {e}")
            return False
        except OSError as e:
            job.add(f"telnet: Unable to connect to remote host: {e.strerror or e}")
            if e.errno in (111, 61):
                job.add("Connection refused")
            return False
        peer = w.get_extra_info("peername") or (host, port)
        job.add(f"Connected to {peer[0]}.")
        job.add("Escape character is '^]'.")
        try:
            data = await asyncio.wait_for(r.read(4096), wait)
            for ln in data.decode(errors="replace").splitlines():
                job.add(ln)
        except asyncio.TimeoutError:
            pass
        w.close()
        return True

    async def stop_job(self, jid):
        job = self.jobs.get(jid)
        if not job:
            raise DiagError("no_job", 404)
        if job.status == "running":
            job.stopped = True
            if job.proc and job.proc.returncode is None:
                try:
                    job.proc.terminate()
                except ProcessLookupError:
                    pass
            elif job.task:
                job.task.cancel()
        return job

    def _store(self, job):
        try:
            out = "\n".join(job.lines)
            if len(out) > STORE_OUTPUT:
                out = out[:STORE_OUTPUT] + "\n… (обрізано)"
            self.db.insert("diag_runs", {
                "ts": int(job.started), "tool": job.tool, "target": job.target[:200],
                "params": json.dumps(job.params, ensure_ascii=False), "status": job.status,
                "duration": round((job.finished or time.time()) - job.started, 1),
                "summary": json.dumps({**summary_of(job.tool, job.result), **({"error": job.error} if job.error else {})},
                                      ensure_ascii=False),
                "output": out})
            self.db.execute("DELETE FROM diag_runs WHERE id <= (SELECT id FROM diag_runs ORDER BY id DESC "
                            "LIMIT 1 OFFSET ?)", (MAX_HISTORY,))
        except Exception:  # noqa: BLE001
            log.exception("diag: cannot store run")

    def history(self, limit=15):
        rows = self.db.query("SELECT id, ts, tool, target, params, status, duration, summary FROM diag_runs "
                             "ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            for k in ("params", "summary"):
                try:
                    r[k] = json.loads(r[k] or "{}")
                except ValueError:
                    r[k] = {}
        return rows


def iperf_text(r, p):
    """Людський підсумок iperf3 замість сирого JSON (сам JSON розбирається в result)."""
    if not r:
        return []
    out = [f"iperf3 → {p['host']}:{p['port']}  {p['protocol'].upper()}  {p['duration']} s  "
           f"streams={p['streams']}  direction={p['direction']}"]
    if r.get("remote"):
        out.append(f"Connected: {r.get('local')} → {r.get('remote')}")
    for t, v in r.get("intervals") or []:
        out.append(f"  {t:6.1f} s   {v:10.2f} Mbit/s")
    if r.get("udp"):
        if r.get("mbps") is not None:
            out.append(f"UDP: {r['mbps']} Mbit/s, jitter {r.get('jitter_ms')} ms, lost {r.get('lost_pct')} %")
    elif r.get("sent_mbps") is not None:
        out.append(f"Sender: {r['sent_mbps']} Mbit/s  (retransmits {r.get('retransmits')})")
        out.append(f"Receiver: {r['recv_mbps']} Mbit/s")
        if r.get("recv_mbps_rev") is not None:
            out.append(f"Reverse: sender {r.get('sent_mbps_rev')} / receiver {r.get('recv_mbps_rev')} Mbit/s")
    if r.get("error"):
        out.append("iperf3: " + str(r["error"]))
    return out


# ---------------------------------------------------------------- HTTP API
def make_router(diag: Diag, gateway=None, addresses=None):
    router = APIRouter(prefix="/api/diag")

    def fail(e: DiagError):
        detail = e.code + ((":" + json.dumps(e.kw, ensure_ascii=False)) if e.kw else "")
        return HTTPException(e.status, detail)

    @router.get("")
    async def diag_info():
        diag.refresh_bins()
        gw = None
        if gateway:
            try:
                gw = gateway()
            except Exception:  # noqa: BLE001
                gw = None
        return {"tools": {t: bool(diag.bins[t]) for t in TOOLS}, "iperf_version": diag.iperf_version(),
                "server": diag.server.snap(), "gateway": gw,
                "addresses": addresses() if addresses else [],
                "jobs": [j.snap(len(j.lines)) for j in sorted(diag.jobs.values(), key=lambda j: -j.id)[:5]],
                "history": diag.history(), "limits": {"auto_off": AUTO_OFF, "ns_types": NS_TYPES}}

    @router.post("/run")
    async def diag_run(data: dict = Body(...)):
        try:
            job = await diag.run(str(data.get("tool") or ""), data)
        except DiagError as e:
            raise fail(e)
        return job.snap()

    @router.get("/jobs/{jid}")
    async def diag_job(jid: int, since: int = 0):
        job = diag.jobs.get(jid)
        if not job:
            raise fail(DiagError("no_job", 404))
        return job.snap(since)

    @router.post("/jobs/{jid}/stop")
    async def diag_stop(jid: int):
        try:
            return (await diag.stop_job(jid)).snap()
        except DiagError as e:
            raise fail(e)

    @router.get("/history/{rid}")
    async def diag_history_item(rid: int):
        r = diag.db.one("SELECT * FROM diag_runs WHERE id=?", (rid,))
        if not r:
            raise fail(DiagError("no_job", 404))
        for k in ("params", "summary"):
            r[k] = json.loads(r[k] or "{}")
        return r

    @router.delete("/history")
    async def diag_history_clear():
        diag.db.execute("DELETE FROM diag_runs")
        return {"ok": True}

    @router.post("/iperf/server")
    async def diag_server(data: dict = Body(...)):
        try:
            enabled = bool(data.get("enabled"))
            port = clean_int(data.get("port"), 1024, 65535, diag.server.port, "port")
            auto = clean_int(data.get("auto_off_min"), 0, 1440, 0, "auto_off")
            if auto not in AUTO_OFF:
                raise DiagError("bad_auto_off")
        except DiagError as e:
            raise fail(e)
        if enabled:
            if not diag.bins["iperf3"]:
                diag.refresh_bins()
                if not diag.bins["iperf3"]:
                    raise fail(DiagError("not_installed", tool="iperf3"))
            await diag.server.start(port, auto)
            diag.save_server_state(True)
            diag.db.log("info", f"iperf3 server on, port {port}" + (f", auto-off {auto} min" if auto else ""))
            await asyncio.sleep(0.6)   # дати процесу стартувати / впасти з «port busy»
        else:
            was = diag.server.task is not None
            await diag.server.stop()
            if was:
                diag.db.log("info", "iperf3 server off")
        return diag.server.snap()

    return router
