"""Стан пристрою: температура, навантаження, пам'ять, IP-адреси, WireGuard."""
import asyncio
import os
import shutil
import socket
import subprocess
import time

from .probes import run_cmd

_wg_cache = {"ts": 0, "data": None}
_ip_cache = {"ts": 0, "data": []}


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def cpu_temp():
    temps = []
    base = "/sys/class/thermal"
    if os.path.isdir(base):
        for z in os.listdir(base):
            v = _read(f"{base}/{z}/temp")
            if v and v.lstrip("-").isdigit():
                temps.append(int(v) / 1000)
    return round(max(temps), 1) if temps else None


def ip_addresses():
    if time.time() - _ip_cache["ts"] < 60:
        return _ip_cache["data"]
    ips = []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1] != "lo":
                ips.append({"iface": parts[1], "addr": parts[3]})
    except Exception:
        pass
    _ip_cache.update(ts=time.time(), data=ips)
    return ips


def system_info(data_dir: str):
    mem = {}
    for line in (_read("/proc/meminfo", "") or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            mem[k] = int(v.split()[0]) * 1024
    try:
        du = shutil.disk_usage(data_dir)
        disk = {"total": du.total, "free": du.free}
    except OSError:
        disk = None
    up = _read("/proc/uptime", "0 0").split()[0]
    return {
        "hostname": socket.gethostname(),
        "uptime": int(float(up)),
        "load": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "cpu_temp": cpu_temp(),
        "mem_total": mem.get("MemTotal"),
        "mem_available": mem.get("MemAvailable"),
        "disk": disk,
        "ips": ip_addresses(),
    }


async def wireguard_status():
    """Парсить `wg show all dump`. Потребує root або правила sudoers (див. install.sh)."""
    if time.time() - _wg_cache["ts"] < 15:
        return _wg_cache["data"]
    data = {"available": False, "interfaces": [], "error": None}
    wg = shutil.which("wg") or "/usr/bin/wg"
    cmds = [[wg, "show", "all", "dump"]] if os.geteuid() == 0 else \
        [["sudo", "-n", wg, "show", "all", "dump"], [wg, "show", "all", "dump"]]
    out = None
    for cmd in cmds:
        try:
            rc, o, e = await run_cmd(cmd, 5)
            if rc == 0:
                out = o
                break
            data["error"] = (e.strip() or f"rc={rc}")[:200]
        except (FileNotFoundError, asyncio.TimeoutError) as ex:
            data["error"] = str(ex) or "wg unavailable"
    if out is not None:
        data["available"] = True
        data["error"] = None
        ifaces = {}
        now = time.time()
        for line in out.splitlines():
            f = line.split("\t")
            if len(f) == 5:
                ifaces[f[0]] = {"name": f[0], "listen_port": f[3], "peers": []}
            elif len(f) == 9:
                hs = int(f[5]) if f[5].isdigit() else 0
                ifaces.setdefault(f[0], {"name": f[0], "listen_port": None, "peers": []})
                ifaces[f[0]]["peers"].append({
                    "public_key": f[1][:10] + "…",
                    "endpoint": None if f[3] == "(none)" else f[3],
                    "allowed_ips": f[4],
                    "handshake_age": int(now - hs) if hs else None,
                    "rx": int(f[6]), "tx": int(f[7]),
                })
        data["interfaces"] = list(ifaces.values())
    _wg_cache.update(ts=time.time(), data=data)
    return data
