"""Виміри: ping (втрати, RTT, джитер) та speedtest (Ookla CLI або speedtest-cli)."""
import asyncio
import json
import re
import urllib.parse
import urllib.request
import shutil
import statistics
import sys

RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms")
SUMMARY_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:\-]{0,252}$")


def valid_target(t: str) -> bool:
    return bool(TARGET_RE.match(t or ""))


async def run_cmd(args, timeout):
    """Запускає процес без shell. При таймауті або скасуванні процес вбивається."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        raise
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def jitter_of(rtts):
    """Джитер = середня абсолютна різниця між сусідніми RTT (як у RFC 3550 / speedtest)."""
    if len(rtts) < 2:
        return None
    return round(statistics.fmean(abs(b - a) for a, b in zip(rtts, rtts[1:])), 2)


async def ping_target(target: str, count: int) -> dict:
    res = dict(target=target, sent=count, received=0, loss=100.0,
               rtt_min=None, rtt_avg=None, rtt_max=None, jitter=None, error=None)
    args = ["ping", "-n", "-c", str(count), "-i", "0.2", "-W", "2", "--", target]
    try:
        rc, out, err = await run_cmd(args, timeout=count * 0.2 + 20)
    except asyncio.TimeoutError:
        res["error"] = "timeout"
        return res
    except FileNotFoundError:
        res["error"] = "ping is not installed"
        return res

    rtts = [float(x) for x in RTT_RE.findall(out)]
    m = SUMMARY_RE.search(out)
    if m:
        res["sent"] = int(m.group(1))
        res["received"] = int(m.group(2))
    else:
        res["received"] = len(rtts)
        if not rtts:
            res["error"] = (err.strip() or out.strip() or f"ping rc={rc}")[:200]
    if res["sent"]:
        res["loss"] = round(100.0 * (res["sent"] - res["received"]) / res["sent"], 2)
    if rtts:
        res["rtt_min"] = round(min(rtts), 2)
        res["rtt_avg"] = round(statistics.fmean(rtts), 2)
        res["rtt_max"] = round(max(rtts), 2)
        res["jitter"] = jitter_of(rtts)
    return res


# ---------------- speedtest ----------------

async def detect_engine() -> str | None:
    if shutil.which("speedtest"):
        try:
            _, out, err = await run_cmd(["speedtest", "--version"], 15)
            if "Ookla" in out + err:
                return "ookla"
        except Exception:
            pass
    try:
        import speedtest  # noqa: F401  (пакет speedtest-cli)
        return "python"
    except ImportError:
        return None


def _error_text(out, err):
    for line in (err + "\n" + out).splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                j = json.loads(line)
                msg = j.get("message") or j.get("error")
                if msg:
                    return str(msg)[:300]
            except ValueError:
                pass
    return (err.strip() or out.strip() or "unknown error")[:300]


async def run_speedtest(engine: str, server_id: str = "") -> dict:
    """Повертає dict з download/upload (Мбіт/с), ping/jitter (мс). Кидає RuntimeError."""
    if engine == "ookla":
        args = ["speedtest", "--accept-license", "--accept-gdpr", "-f", "json", "-p", "no"]
        if server_id:
            args += ["-s", server_id]
        rc, out, err = await run_cmd(args, 240)
        data = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if j.get("type") == "result" or "download" in j:
                    data = j
        if rc != 0 or not data:
            raise RuntimeError(_error_text(out, err))
        srv = data.get("server", {})
        return dict(
            download=round(data["download"]["bandwidth"] * 8 / 1e6, 2),
            upload=round(data["upload"]["bandwidth"] * 8 / 1e6, 2),
            ping=round(data["ping"]["latency"], 2),
            jitter=round(data["ping"].get("jitter", 0), 2),
            packet_loss=data.get("packetLoss"),
            server=f'{srv.get("name", "")} ({srv.get("location", "")})'.strip(),
            engine="ookla",
            url=(data.get("result") or {}).get("url"),
            isp=data.get("isp"),
            external_ip=(data.get("interface") or {}).get("externalIp"),
        )

    if engine == "python":
        args = [sys.executable, "-m", "speedtest", "--json", "--secure"]
        if server_id:
            args += ["--server", server_id]
        rc, out, err = await run_cmd(args, 240)
        try:
            data = json.loads(out)
        except ValueError:
            raise RuntimeError(_error_text(out, err))
        srv = data.get("server", {})
        return dict(
            download=round(data["download"] / 1e6, 2),
            upload=round(data["upload"] / 1e6, 2),
            ping=round(data["ping"], 2),
            jitter=None,
            packet_loss=None,
            server=f'{srv.get("sponsor", "")} ({srv.get("name", "")})',
            engine="python",
            url=data.get("share"),
            isp=(data.get("client") or {}).get("isp"),
            external_ip=(data.get("client") or {}).get("ip"),
        )

    raise RuntimeError("speedtest is not installed (neither Ookla CLI nor speedtest-cli)")


# ---------------- список серверів ----------------

SEARCH_URL = "https://www.speedtest.net/api/js/servers"
LIST_RE = re.compile(r"^\s*(\d+)\)\s+(.+?)\s+\((.+?)\)\s+\[([\d.]+)\s*km\]")


def _server(id_, sponsor, location="", country="", host="", distance=None):
    return {"id": str(id_), "sponsor": str(sponsor or "").strip(), "location": str(location or "").strip(),
            "country": str(country or "").strip(), "host": str(host or "").strip(),
            "distance": round(float(distance), 1) if distance not in (None, "") else None}


async def list_servers_cli(engine: str) -> list[dict]:
    """Найближчі сервери за даними самого рушія (Ookla: ~10 штук)."""
    if engine == "ookla":
        rc, out, err = await run_cmd(
            ["speedtest", "--accept-license", "--accept-gdpr", "-L", "-f", "json"], 60)
        data = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if "servers" in j:
                    data = j
        if rc != 0 or data is None:
            raise RuntimeError(_error_text(out, err))
        return [_server(x.get("id"), x.get("name"), x.get("location"), x.get("country"),
                        f'{x.get("host", "")}:{x.get("port", "")}'.strip(":"))
                for x in data["servers"] if x.get("id")]
    if engine == "python":
        rc, out, err = await run_cmd([sys.executable, "-m", "speedtest", "--list", "--secure"], 60)
        res = []
        for line in out.splitlines():
            m = LIST_RE.match(line)
            if m:
                loc = m.group(3).rsplit(",", 1)
                res.append(_server(m.group(1), m.group(2), loc[0], loc[1] if len(loc) > 1 else "",
                                   distance=m.group(4)))
        if not res:
            raise RuntimeError(_error_text(out, err))
        return res[:30]
    raise RuntimeError("speedtest is not installed")


def search_servers(query: str, limit: int = 30) -> list[dict]:
    """Пошук серверів за містом / провайдером через публічний API speedtest.net (блокуючий виклик)."""
    qs = urllib.parse.urlencode({"engine": "js", "https_functional": "true",
                                 "search": query, "limit": limit})
    req = urllib.request.Request(f"{SEARCH_URL}?{qs}", headers={"User-Agent": "Mozilla/5.0 molfar"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if not isinstance(data, list):
        raise RuntimeError("unexpected response from speedtest.net")
    return [_server(x.get("id"), x.get("sponsor"), x.get("name"), x.get("country"),
                    x.get("host"), x.get("distance"))
            for x in data if isinstance(x, dict) and str(x.get("id", "")).isdigit()]
