"""HTTP API + статичний веб-інтерфейс."""
import asyncio
import csv
import hashlib
import html
import io
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import probes, sysinfo, telegram, wgmgr
from .netmap import NetMapError
from .auth import Auth
from .db import DB, DB_PATH, DEFAULTS
from .i18n import LANGS, tr
from .monitor import Monitor
from . import netscan
from .diag import Diag, make_router as diag_router

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

db = DB(DB_PATH)
monitor = Monitor(db)
auth = Auth(db)
diag = Diag(db)


def _t(msg_id, **kw):
    return tr(db.get_settings()["language"], msg_id, **kw)


def err(code, msg_id, **kw):
    return HTTPException(code, _t(msg_id, **kw))


@asynccontextmanager
async def lifespan(_app):
    await monitor.start()
    await diag.start()
    yield
    await diag.shutdown()
    await monitor.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(diag_router(diag, gateway=lambda: netscan.default_route()[1] or None,
                               addresses=sysinfo.ip_addresses))


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not auth.check_header(request.headers.get("authorization", "")):
        return Response("Unauthorized", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="molfar", charset="UTF-8"'})
    return await call_next(request)


# діапазон -> (тривалість, розмір кошика для агрегації), секунди
RANGES = {
    "day": (86400, 300),
    "week": (7 * 86400, 3600),
    "month": (30 * 86400, 4 * 3600),
    "year": (365 * 86400, 86400),
}


def _range(name):
    if name not in RANGES:
        raise err(400, "e_range")
    span, bucket = RANGES[name]
    return int(time.time()) - span, bucket


def _tz_offset():
    return int(datetime.now().astimezone().utcoffset().total_seconds())


def _tz_name():
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return datetime.now().astimezone().tzname()


# ---------------- статус ----------------
@app.get("/api/status")
async def api_status():
    st = monitor.status()
    st["system"] = sysinfo.system_info(os.path.dirname(DB_PATH) or ".")
    st["wireguard"] = await sysinfo.wireguard_status()
    st["last_series"] = db.one("SELECT * FROM series WHERE status!='running' AND runs_ok > 0 ORDER BY id DESC LIMIT 1")
    st["last_test"] = db.one("SELECT * FROM speedtests ORDER BY id DESC LIMIT 1")
    st["server_time"] = int(time.time())
    st["tz"] = {"name": _tz_name(), "offset": _tz_offset()}
    return st


# ---------------- налаштування ----------------
SECRET_KEYS = {"tg_token"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
LIMITS = {
    "speedtest_runs": (1, 20), "speedtest_interval_min": (1, 120),
    "ping_interval_sec": (10, 3600), "ping_count": (3, 100),
    "alert_loss_pct": (1, 100), "alert_consecutive": (1, 100),
    "alert_cooldown_min": (0, 1440), "alert_speed_min_mbps": (0, 10000),
    "retention_days": (7, 1825), "isp_check_min": (0, 1440),
    "net_scan_min": (1, 60), "net_offline_min": (2, 1440), "net_stale_days": (1, 365), "net_log_days": (30, 1825),
    "ping_extra_interval_sec": (30, 3600), "ping_extra_count": (3, 50),
}
IFACE_RE = re.compile(r"^[A-Za-z0-9._@-]{0,15}$")
MAX_PING_HOSTS = 40                       # основні 3 + додаткові
MAX_EXTRA = MAX_PING_HOSTS - 3
TILE_IDS = ("ping1", "ping2", "ping3", "speed", "auto", "isp", "wg", "lan", "sys")


def _clean_extra(v):
    """[{"host","name","alert"}] — перевірка списку додаткових хостів."""
    if not isinstance(v, list):
        raise err(400, "e_value", k="ping_extra")
    if len(v) > MAX_EXTRA:
        raise err(400, "e_extra_max", n=MAX_EXTRA, total=MAX_PING_HOSTS)
    out, seen = [], set()
    for x in v:
        if isinstance(x, str):
            x = {"host": x}
        if not isinstance(x, dict):
            raise err(400, "e_value", k="ping_extra")
        host = str(x.get("host") or "").strip()
        if not probes.valid_target(host):
            raise err(400, "e_target", x=host or "—")
        if host.lower() in seen:
            raise err(400, "e_extra_dup", x=host)
        seen.add(host.lower())
        out.append({"host": host, "name": str(x.get("name") or "").strip()[:40], "alert": bool(x.get("alert", True)),
                    "dash": bool(x.get("dash", False))})
    return out


def _clean_order(v):
    if not isinstance(v, list) or len(v) > 60:
        raise err(400, "e_order")
    out = []
    for x in v:
        x = str(x)
        # фіксовані плитки або плитка додаткового хоста «h:<хост>»
        if x not in TILE_IDS and not (x.startswith("h:") and probes.valid_target(x[2:])):
            raise err(400, "e_order")
        if x not in out:
            out.append(x)
    return out


def _mask(v: str):
    return ("••••" + v[-4:]) if v else ""


@app.get("/api/settings")
async def get_settings():
    s = db.get_settings()
    for k in SECRET_KEYS:
        s[k] = _mask(s[k])
    return s


@app.post("/api/settings")
async def save_settings(data: dict = Body(...)):
    out = {}
    for k, v in data.items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, int):
                v = int(v)
            elif isinstance(d, float):
                v = float(v)
            elif k == "ping_extra":
                v = _clean_extra(v)
            elif k == "overview_order":
                v = _clean_order(v)
            elif isinstance(d, list):
                v = [str(x).strip() for x in v][:3]
            else:
                v = str(v).strip()
        except (TypeError, ValueError):
            raise err(400, "e_value", k=k)
        if k in LIMITS and not (LIMITS[k][0] <= v <= LIMITS[k][1]):
            raise err(400, "e_limit", k=k, a=LIMITS[k][0], b=LIMITS[k][1])
        if k in SECRET_KEYS and v.startswith("••••"):
            continue  # маска — значення не змінювали
        out[k] = v
    if "speedtest_time" in out and not TIME_RE.match(out["speedtest_time"]):
        raise err(400, "e_time")
    if "speedtest_engine" in out and out["speedtest_engine"] not in ("auto", "ookla", "python"):
        raise err(400, "e_engine")
    if "language" in out and out["language"] not in LANGS:
        raise err(400, "e_lang")
    if "speedtest_server_id" in out and out["speedtest_server_id"] and not out["speedtest_server_id"].isdigit():
        raise err(400, "e_server_id")
    if "speedtest_server_id" in out and not out["speedtest_server_id"]:
        out["speedtest_server_name"] = ""
    if "speedtest_server_name" in out:
        out["speedtest_server_name"] = out["speedtest_server_name"][:120]
    if "ping_targets" in out:
        t = out["ping_targets"]
        if not any(t):
            raise err(400, "e_need_target")
        for x in t:
            if x and not probes.valid_target(x):
                raise err(400, "e_target", x=x)
        out["ping_targets"] = (t + ["", "", ""])[:3]
    if "ping_targets" in out or "ping_extra" in out:
        cur = db.get_settings()
        main = {x.lower() for x in out.get("ping_targets", cur["ping_targets"]) if x}
        for h in out.get("ping_extra", cur["ping_extra"]):
            if h["host"].lower() in main:
                raise err(400, "e_extra_main", x=h["host"])
    if any(k in out for k in ("ping_extra", "ping_extra_interval_sec", "ping_extra_count")):
        monitor.ping_extra_wakeup.set()   # новий список — пінгувати одразу
    if "net_iface" in out and not IFACE_RE.match(out["net_iface"]):
        raise err(400, "e_iface")
    if "isp_check_min" in out:
        monitor.isp_wakeup.set()  # новий інтервал — застосувати одразу
    prev = db.get_settings()
    net_changed = any(k.startswith("net_") and out[k] != prev[k] for k in out)
    if "speedtest_time" in out and out["speedtest_time"] != db.get_settings()["speedtest_time"]:
        db.set_state("last_schedule_date", None)  # новий час — дозволити запуск сьогодні
    db.set_settings(out)
    monitor.ping_wakeup.set()  # застосувати нові параметри пінгу одразу
    if net_changed:
        await asyncio.to_thread(monitor.netmap.tick)   # нові «прибирати після» / «зберігати журнал»
        monitor.netmap_scan_now()
    return await get_settings()


# ---------------- керування ----------------
@app.post("/api/speedtest/start")
async def speedtest_start(data: dict = Body(default={})):
    mode = data.get("mode", "single")
    server = None
    sid_in = str(data.get("server_id") or "").strip()
    if sid_in:
        if not sid_in.isdigit():
            raise err(400, "e_server_id")
        server = {"id": sid_in, "name": str(data.get("server_name") or "")[:120]}
    sid = monitor.start_series("manual", runs=1 if mode == "single" else None, server=server)
    if sid is None:
        raise err(409, "e_busy")
    return {"series_id": sid}


_servers_cache = {"ts": 0, "engine": None, "data": []}


@app.get("/api/speedtest/servers")
async def speedtest_servers(search: str = "", refresh: bool = False):
    """Без search — найближчі сервери від рушія (кеш 1 год); з search — пошук на speedtest.net."""
    search = search.strip()[:60]
    if search:
        try:
            return {"source": "search", "servers": await asyncio.to_thread(probes.search_servers, search)}
        except Exception as e:
            raise err(502, "e_search", err=e)
    s = db.get_settings()
    engine = monitor.engine if s["speedtest_engine"] == "auto" else s["speedtest_engine"]
    if not engine:
        raise err(503, "e_no_engine")
    c = _servers_cache
    if refresh or c["engine"] != engine or time.time() - c["ts"] > 3600 or not c["data"]:
        if monitor.speedtest_running:
            raise err(409, "e_wait_test")
        try:
            c.update(data=await probes.list_servers_cli(engine), ts=time.time(), engine=engine)
        except Exception as e:
            raise err(502, "e_list", err=e)
    return {"source": "nearest", "servers": c["data"]}


@app.post("/api/speedtest/stop")
async def speedtest_stop():
    stopped = await monitor.stop_series()
    return {"stopped": stopped}


@app.post("/api/ping/run")
async def ping_run():
    return {"results": await monitor.ping_round(manual=True)}


@app.post("/api/telegram/test")
async def telegram_test(data: dict = Body(default={})):
    s = db.get_settings()
    token = data.get("tg_token") or s["tg_token"]
    if token.startswith("••••"):
        token = s["tg_token"]
    chat = data.get("tg_chat_id") or s["tg_chat_id"]
    if not token or not chat:
        raise err(400, "e_tg_creds")
    try:
        await asyncio.to_thread(telegram.send, token, chat,
                                tr(s["language"], "tg_test", name=html.escape(s["device_name"])))
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


# ---------------- пароль ----------------
@app.get("/api/auth")
async def auth_info():
    return {"user": auth.user, "source": auth.source, "is_default": auth.is_default}


@app.post("/api/auth/password")
async def change_password(data: dict = Body(...)):
    current = str(data.get("current") or "")
    new = str(data.get("new") or "")
    if auth.enabled and not await asyncio.to_thread(auth.verify, auth.user, current):
        raise err(403, "e_pw_current")
    if not (6 <= len(new) <= 128):
        raise err(400, "e_pw_len")
    if new == current:
        raise err(400, "e_pw_same")
    await asyncio.to_thread(auth.set_password, new)
    db.log("info", _t("pw_changed"))
    return {"ok": True, "user": auth.user}


# ---------------- провайдер ----------------
@app.get("/api/isp")
async def isp_info(limit: int = 50):
    hist = db.query("SELECT * FROM isp_history ORDER BY id DESC LIMIT ?", (min(limit, 500),))
    st = monitor.status()["isp"]
    return {**st, "history": hist}


@app.post("/api/isp/check")
async def isp_check():
    try:
        cur = await monitor.check_isp()
    except Exception as e:
        raise err(502, "e_isp_failed", err=str(e)[:300])
    return {"current": cur}


# ---------------- WireGuard-профілі ----------------
WG_STATUS = {"bad_name": 400, "too_big": 400, "bad_section": 400, "bad_line": 400, "no_section": 400,
             "forbidden_key": 400, "bad_key": 400, "bad_value": 400, "need_interface": 400,
             "need_privatekey": 400, "not_editable": 409, "exists": 409, "not_found": 404,
             "unavailable": 503}
WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$")


WG_HINTS = [  # (фрагмент журналу wg-quick, підказка)
    ("tables-restore: command not found", "wg_hint_fw"),
    ("nft: command not found", "wg_hint_fw"),
    ("resolvconf: command not found", "wg_hint_resolv"),
    ("Unknown device type", "wg_hint_module"),
    ("Protocol not supported", "wg_hint_module"),
]


def wg_err(e: wgmgr.WgError):
    kw = {k: e.kw.get(k, "") for k in ("key", "line", "log", "err")}
    exc = err(WG_STATUS.get(e.code, 500), f"wg_{e.code}", **kw)
    log = str(e.kw.get("log", ""))
    for needle, hint in WG_HINTS:
        if needle in log:
            exc.detail += _t(hint)
            break
    return exc


async def _wg(coro, event=None, name=None):
    try:
        res = await coro
    except wgmgr.WgError as e:
        raise wg_err(e)
    sysinfo._wg_cache["ts"] = 0  # оновити картку WireGuard на «Огляді»
    if event:
        db.log("info", _t(event, name=name))
    return res


@app.get("/api/wg/profiles")
async def wg_profiles():
    if not wgmgr.available():
        return {"available": False, "profiles": [], "rollback_sec": wgmgr.ROLLBACK_SEC}
    return {"available": True, "profiles": await _wg(wgmgr.list_profiles()), "rollback_sec": wgmgr.ROLLBACK_SEC}


@app.get("/api/wg/profiles/{name}")
async def wg_profile(name: str):
    return await _wg(wgmgr.get_profile(name))


@app.post("/api/wg/profiles")
async def wg_create(data: dict = Body(...)):
    name = str(data.get("name") or "").strip()
    return await _wg(wgmgr.create(name, str(data.get("config") or "")), "ev_wg_created", name)


@app.post("/api/wg/profiles/{name}")
async def wg_save(name: str, data: dict = Body(...)):
    return await _wg(wgmgr.save(name, str(data.get("config") or "")), "ev_wg_saved", name)


@app.delete("/api/wg/profiles/{name}")
async def wg_delete(name: str):
    return await _wg(wgmgr.delete(name), "ev_wg_deleted", name)


@app.post("/api/wg/profiles/{name}/{act}")
async def wg_action(name: str, act: str):
    if act not in ("up", "down", "enable", "disable", "confirm"):
        raise HTTPException(404)
    event = None if act == "confirm" else f"ev_wg_{act}"
    return await _wg(wgmgr.action(name, act), event, name)


@app.post("/api/wg/keygen")
async def wg_keygen():
    return await _wg(wgmgr.keygen())


@app.post("/api/wg/pubkey")
async def wg_pubkey(data: dict = Body(...)):
    priv = str(data.get("private_key") or "").strip()
    if not WG_KEY_RE.match(priv):
        raise err(400, "wg_bad_key_format")
    return {"public_key": await _wg(wgmgr.pubkey(priv))}


# ---------------- мапа мережі ----------------
def _nm(call, *a, **kw):
    try:
        return call(*a, **kw)
    except NetMapError as e:
        raise HTTPException(e.status, {"code": e.code, "params": e.params})


@app.get("/api/netmap")
async def netmap_get():
    snap = await asyncio.to_thread(monitor.netmap.snapshot)
    cur = monitor.isp_current or {}
    snap["isp"] = {"name": cur.get("isp") or cur.get("org") or "", "asn": cur.get("asn") or "",
                   "ip": cur.get("ip") or "", "city": cur.get("city") or ""} if cur else None
    snap["enabled"] = db.get_settings()["net_enabled"]
    return snap


@app.get("/api/netmap/events")
async def netmap_events(limit: int = 200, before: float | None = None, type: str = "all", device: int | None = None):
    return {"events": await asyncio.to_thread(monitor.netmap.events, limit, before, type, device)}


@app.post("/api/netmap/scan")
async def netmap_scan():
    return {"ok": True, "started": monitor.netmap_scan_now()}


@app.post("/api/netmap/devices")
async def netmap_create(data: dict = Body(...)):
    nm = monitor.netmap
    return nm.snapshot_device(_nm(nm.create_device, data))


@app.patch("/api/netmap/devices/{dev_id}")
async def netmap_update(dev_id: int, data: dict = Body(...)):
    nm = monitor.netmap
    return nm.snapshot_device(_nm(nm.update_device, dev_id, data))


@app.delete("/api/netmap/devices/{dev_id}")
async def netmap_delete(dev_id: int, ignore: bool = False):
    _nm(monitor.netmap.delete_device, dev_id, ignore=ignore)
    return {"ok": True}


@app.post("/api/netmap/devices/{dev_id}/restore")
async def netmap_restore(dev_id: int):
    nm = monitor.netmap
    return nm.snapshot_device(_nm(nm.restore_device, dev_id))


@app.delete("/api/netmap/ignored/{mac}")
async def netmap_unignore(mac: str):
    _nm(monitor.netmap.unignore, mac)
    return {"ok": True}


@app.post("/api/netmap/layout/reset")
async def netmap_layout_reset():
    monitor.netmap.reset_layout()
    return {"ok": True}


# ---------------- історія ----------------
@app.get("/api/history/ping")
async def history_ping(range: str = "day"):
    since, bucket = _range(range)
    off = _tz_offset()
    rows = await asyncio.to_thread(db.query, """
        SELECT ((ts + :off) / :b) * :b - :off AS t, target,
               ROUND(AVG(loss), 2) AS loss, MAX(loss) AS max_loss,
               ROUND(AVG(rtt_avg), 2) AS rtt, ROUND(MAX(rtt_max), 2) AS rtt_max,
               ROUND(AVG(jitter), 2) AS jitter
        FROM ping_results WHERE ts >= :since
        GROUP BY t, target ORDER BY t""", {"off": off, "b": bucket, "since": since})
    summary = await asyncio.to_thread(db.query, """
        SELECT target, COUNT(*) AS checks,
               ROUND(100.0 * (SUM(sent) - SUM(received)) / MAX(SUM(sent), 1), 2) AS loss,
               ROUND(AVG(rtt_avg), 2) AS rtt, ROUND(MIN(rtt_min), 2) AS rtt_min,
               ROUND(MAX(rtt_max), 2) AS rtt_max, ROUND(AVG(jitter), 2) AS jitter,
               SUM(CASE WHEN loss >= 100 THEN 1 ELSE 0 END) AS outages
        FROM ping_results WHERE ts >= ? GROUP BY target""", (since,))
    series = {}
    for r in rows:
        series.setdefault(r.pop("target"), []).append(r)
    return {"bucket": bucket, "since": since, "series": series, "summary": summary}


@app.get("/api/history/speed")
async def history_speed(range: str = "day"):
    since, bucket = _range(range)
    tests = db.query("SELECT id, ts, series_id, download, upload, ping, jitter, server, isp, external_ip, error "
                     "FROM speedtests WHERE ts >= ? ORDER BY ts", (since,))
    series = db.query("SELECT * FROM series WHERE started_ts >= ? AND status != 'running' "
                      "ORDER BY started_ts", (since,))
    return {"bucket": bucket, "since": since, "tests": tests, "series": series}


@app.get("/api/series")
async def list_series(limit: int = 30):
    return db.query("SELECT * FROM series ORDER BY id DESC LIMIT ?", (min(limit, 500),))


@app.get("/api/series/{sid}")
async def series_detail(sid: int):
    s = db.one("SELECT * FROM series WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404)
    s["tests"] = db.query("SELECT * FROM speedtests WHERE series_id=? ORDER BY ts", (sid,))
    return s


@app.get("/api/events")
async def events(limit: int = 100):
    return db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 1000),))


@app.get("/api/export/{kind}.csv")
async def export_csv(kind: str, range: str = "month"):
    since, _ = _range(range)
    if kind == "ping":
        rows = await asyncio.to_thread(db.query, "SELECT * FROM ping_results WHERE ts >= ? ORDER BY ts", (since,))
    elif kind == "speed":
        rows = db.query("SELECT * FROM speedtests WHERE ts >= ? ORDER BY ts", (since,))
    else:
        raise HTTPException(404)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=["time", *rows[0].keys()])
        w.writeheader()
        for r in rows:
            w.writerow({"time": datetime.fromtimestamp(r["ts"]).isoformat(sep=" "), **r})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="molfar-{kind}-{range}.csv"'})


# ---------------- веб-інтерфейс ----------------
_index_cache = {"key": None, "html": None}


def _asset_version() -> str:
    """Хеш вмісту статичних файлів: змінюється з кожним оновленням Мольфара."""
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(STATIC_DIR)):
        for fn in sorted(files):
            if fn.endswith((".js", ".css")):
                with open(os.path.join(root, fn), "rb") as f:
                    h.update(fn.encode() + f.read())
    return h.hexdigest()[:10]


@app.get("/")
async def index():
    path = os.path.join(STATIC_DIR, "index.html")
    key = tuple(os.stat(os.path.join(r, f)).st_mtime_ns for r, _, fs in sorted(os.walk(STATIC_DIR)) for f in sorted(fs))
    if _index_cache["key"] != key:
        ver = _asset_version()
        html = open(path, encoding="utf-8").read()
        # /static/app.js -> /static/app.js?v=<хеш>: після оновлення браузер не візьме старий файл з кешу
        html = re.sub(r'(/static/[\w./-]+\.(?:js|css))"', rf'\1?v={ver}"', html)
        _index_cache.update(key=key, html=html)
    return Response(_index_cache["html"], media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        # файли з ?v=… незмінні — кешуємо надовго; без версії — завжди перепитуємо сервер (304, якщо не змінився)
        resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if request.url.query.startswith("v=")
                                         else "no-cache")
    elif request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
