"""Інциденти: зникнення інтернету, простій і перезавантаження Мольфара, масовий відвал Wi-Fi,
недоступність і перезавантаження роутера.

Кожен інцидент — рядок у таблиці incidents (kind, started_ts, ended_ts, details JSON).
ended_ts = NULL — інцидент триває.

Як визначається простій Мольфара
--------------------------------
Сервіс кожні 30 с записує «я живий» (heartbeat) разом з boot_id ядра. При штатній зупинці
(systemctl stop / reboot) додатково записує clean_stop. Після старту:
  • boot_id змінився → плата перезавантажувалась; clean_stop є → штатне перезавантаження,
    немає → зникло живлення або плата зависла;
  • boot_id той самий → перезапускався лише сервіс (оновлення) або він упав.
Простій = від останнього heartbeat (або clean_stop) до старту сервісу.

Orange Pi Zero 3 не має батарейки годинника: одразу після ввімкнення системний час неправильний,
доки не спрацює NTP. Тому після перезавантаження простій рахується лише після синхронізації часу,
а момент старту відновлюється через монотонний годинник.
"""
import asyncio
import ctypes
import html
import ipaddress
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

from . import probes, routers
from .i18n import fmt_dur, tr

log = logging.getLogger("molfar.incidents")

KINDS = ("internet", "molfar", "wifi", "router")
HEARTBEAT_SEC = 30
SYNC_WAIT_MAX = 6 * 3600          # скільки чекати NTP після перезавантаження
WIFI_MAX_OPEN_SEC = 12 * 3600     # відвал без повернення закривається через 12 год
ROUTER_FAILS_TO_OPEN = 2          # невдалих опитувань поспіль → «роутер недоступний»
TOOL_ERR_RE = re.compile(r"not installed|not permitted|permission denied|socket:|^timeout$", re.I)


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return None


def uptime_sec():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except OSError:
        return None


class _Timex(ctypes.Structure):
    _fields_ = [("modes", ctypes.c_uint), ("_pad", ctypes.c_char * 252)]


def clock_synced() -> bool:
    """Чи синхронізований системний час (systemd-timesyncd, chrony, ntpd)."""
    if os.path.exists("/run/systemd/timesync/synchronized"):
        return True
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        tx = _Timex()
        state = libc.adjtimex(ctypes.byref(tx))
        return state not in (-1, 5)  # 5 = TIME_ERROR: годинник не синхронізований
    except Exception:
        return True  # не можемо перевірити — вважаємо, що час правильний


def is_public(target: str) -> bool:
    try:
        return ipaddress.ip_address(target).is_global
    except ValueError:
        return not target.endswith((".local", ".lan", ".home", ".internal"))  # ім'я хоста


async def default_gateway():
    try:
        rc, out, _ = await probes.run_cmd(["ip", "-4", "route", "show", "default"], 5)
    except Exception:
        return None
    m = re.search(r"default via (\S+)", out or "")
    return m.group(1) if m else None


def _hm(ts):
    return f"{datetime.fromtimestamp(ts):%d.%m %H:%M}"


def _is_router_down(r):
    return r["kind"] == "router" and (r.get("details") or {}).get("unreachable")


def summarize(rows, since, now):
    """Підсумок за вікно [since, now]. Тривалості обрізані вікном.

    internet — разів, скільки часу не було, найдовше, середнє, причини (isp/lan/nolink), доступність %;
    molfar   — скільки разів перезапускався (усього) з розбивкою за причинами і скільки був недоступний;
    wifi     — разів, скільки тривало, найбільший відвал;
    router   — лише «роутер недоступний» (count/total); перезавантаження й перепідключення WAN окремо.
    """
    def blank():
        return {"count": 0, "total_sec": 0, "longest_sec": 0, "avg_sec": 0, "open": False}
    out = {k: blank() for k in KINDS}
    out["internet"]["causes"] = {"isp": 0, "lan": 0, "nolink": 0, "unknown": 0}
    out["molfar"]["reasons"] = {"power": 0, "reboot": 0, "restart": 0, "crash": 0}
    out["molfar"].update(reboots=0, restarts=0)
    out["wifi"].update(max_lost=0, max_before=0, lost_total=0)
    out["router"].update(reboots=0, wan_resets=0)
    for r in rows:
        k = r["kind"]
        if k not in out:
            continue
        d = r.get("details") or {}
        if k == "router":
            if d.get("reboot"):
                out["router"]["reboots"] += 1
            if d.get("wan_reset"):
                out["router"]["wan_resets"] += 1
            if not d.get("unreachable"):
                continue  # перезавантаження без недоступності — не «роутер недоступний»
        end = r["ended_ts"] if r["ended_ts"] is not None else now
        dur = max(0, min(end, now) - max(r["started_ts"], since))
        o = out[k]
        o["count"] += 1
        o["total_sec"] += dur
        o["longest_sec"] = max(o["longest_sec"], end - r["started_ts"])
        o["open"] = o["open"] or r["ended_ts"] is None
        if k == "internet":
            c = d.get("cause")
            o["causes"][c if c in o["causes"] else "unknown"] += 1
        elif k == "molfar":
            reason = d.get("reason") if d.get("reason") in o["reasons"] else "crash"
            o["reasons"][reason] += 1
            o["reboots" if d.get("reboot") else "restarts"] += 1
        elif k == "wifi":
            o["lost_total"] += d.get("lost") or 0
            if (d.get("lost") or 0) > o["max_lost"]:
                o["max_lost"], o["max_before"] = d.get("lost") or 0, d.get("before") or 0
    for o in out.values():
        o["avg_sec"] = round(o["total_sec"] / o["count"]) if o["count"] else 0
    # доступність інтернету — від часу, коли Мольфар працював і міг це бачити
    observed = max(1, (now - since) - out["molfar"]["total_sec"])
    out["internet"]["availability_pct"] = round(max(0.0, 100.0 * (1 - out["internet"]["total_sec"] / observed)), 2)
    out["window_sec"] = now - since
    return out


def by_period(rows, since, now, unit="day"):
    """Розбивка за календарними днями (unit=day) або місяцями (unit=month) за місцевим часом.
    Інцидент рахується в періоді, де почався; час — обрізаний межами періоду."""
    def floor(ts):
        d = datetime.fromtimestamp(ts)
        d = d.replace(hour=0, minute=0, second=0, microsecond=0)
        return d.replace(day=1) if unit == "month" else d

    def nxt(d):
        if unit == "month":
            return d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1)
        return (d + timedelta(days=1, hours=2)).replace(hour=0)  # стійко до переходу на літній час

    out = []
    d = floor(since)
    while d.timestamp() < now:
        a, b = max(int(d.timestamp()), since), min(int(nxt(d).timestamp()), now)
        # інциденти, що хоч частково припадають на період [a, b)
        inside = [r for r in rows if r["started_ts"] < b and (now if r["ended_ts"] is None else r["ended_ts"]) >= a]
        sm = summarize([r for r in inside if a <= r["started_ts"] < b], a, b)
        # час — включно з інцидентами, що почались раніше й тривали в цьому періоді
        tm = summarize(inside, a, b)
        out.append({"start": int(d.timestamp()), "end": b,
                    "internet": {"count": sm["internet"]["count"], "total_sec": tm["internet"]["total_sec"]},
                    "molfar": {"count": sm["molfar"]["count"], "power": sm["molfar"]["reasons"]["power"],
                               "reboots": sm["molfar"]["reboots"], "total_sec": tm["molfar"]["total_sec"]},
                    "wifi": {"count": sm["wifi"]["count"], "total_sec": tm["wifi"]["total_sec"], "max_lost": sm["wifi"]["max_lost"]},
                    "router": {"count": sm["router"]["count"], "total_sec": tm["router"]["total_sec"],
                               "reboots": sm["router"]["reboots"] + sm["router"]["wan_resets"]}})
        d = nxt(d)
    return out


class Incidents:
    def __init__(self, monitor):
        self.m = monitor
        self.db = monitor.db
        self.clock_ok = True
        self.start_mono = time.monotonic()
        self.boot_id = boot_id()
        self.open_mono = {}           # id відкритого інциденту → monotonic() його початку
        # інтернет
        self.inet_bad = 0
        self.inet_first_ts = None
        self.inet_first_mono = None
        self.inet_open = None         # id
        # роутер
        self.router_state = {"last_ok": None, "error": None, "summary": None, "fails": 0}
        self.router_open = None       # id «роутер недоступний»
        self.wifi_prev = None         # {mac: band} з попереднього опитування
        self.wifi_open = None         # id
        self.router_boot_ts = None
        self.router_wan_uptime = None
        self.router_wakeup = asyncio.Event()

    # ---------- базові операції ----------
    def _open(self, kind, started_ts, details, mono=None):
        iid = self.db.insert("incidents", {"kind": kind, "started_ts": int(started_ts),
                                           "details": json.dumps(details, ensure_ascii=False)})
        if mono is not None and not self.clock_ok:
            self.open_mono[iid] = mono
        return iid

    def _get(self, iid):
        r = self.db.one("SELECT * FROM incidents WHERE id=?", (iid,))
        if r:
            r["details"] = json.loads(r["details"] or "{}")
        return r

    def _close(self, iid, ended_ts, extra=None):
        r = self._get(iid)
        if not r:
            return None
        d = {**r["details"], **(extra or {})}
        ended_ts = max(int(ended_ts), r["started_ts"])
        self.db.execute("UPDATE incidents SET ended_ts=?, details=? WHERE id=?",
                        (ended_ts, json.dumps(d, ensure_ascii=False), iid))
        self.open_mono.pop(iid, None)
        r.update(ended_ts=ended_ts, details=d)
        return r

    def _update_details(self, iid, extra):
        r = self._get(iid)
        if r:
            self.db.execute("UPDATE incidents SET details=? WHERE id=?",
                            (json.dumps({**r["details"], **extra}, ensure_ascii=False), iid))

    def _say(self, level, msg_id, alert_key, s=None, **kw):
        """Запис у журнал + Telegram (якщо увімкнено загальні сповіщення і цей тип)."""
        s = s or self.m.settings
        name = html.escape(s["device_name"])
        text = tr(s["language"], msg_id, name=name, **kw)
        plain = text
        for tag in ("<b>", "</b>", "<code>", "</code>", "<i>", "</i>"):
            plain = plain.replace(tag, "")
        self.db.log(level, plain)
        if alert_key and s["alert_enabled"] and s.get(alert_key, True):
            self.m.notify(text)

    def now(self):
        return int(time.time())

    # ---------- старт / зупинка ----------
    async def on_start(self):
        """Викликається при старті сервісу (до запуску циклів)."""
        self._drop_partial_wifi()
        hb = self.db.get_state("heartbeat")
        stop = self.db.get_state("clean_stop")
        self.db.set_state("clean_stop", None)
        # інциденти, що лишились відкритими: що було під час простою, невідомо — закриваємо
        # на момент останнього heartbeat; якщо проблема ще є, відкриється новий інцидент
        cut = (stop or hb or {}).get("ts")
        for r in self.db.query("SELECT id, started_ts FROM incidents WHERE ended_ts IS NULL"):
            self._close(r["id"], max(cut or r["started_ts"], r["started_ts"]), {"cut": True})

        if not hb:
            self.clock_ok = True
            return  # перший запуск — простою ще немає
        rebooted = bool(self.boot_id and hb.get("boot_id") and hb["boot_id"] != self.boot_id)
        clean = bool(stop and stop.get("boot_id") == hb.get("boot_id") and stop.get("ts", 0) >= hb["ts"] - 5)
        down_from = stop["ts"] if clean else hb["ts"]
        reason = ("reboot" if clean else "power") if rebooted else ("restart" if clean else "crash")
        self.clock_ok = not rebooted or clock_synced()
        if self.clock_ok:
            self._record_downtime(down_from, reason, rebooted, synced_late=False)
        else:
            self.m._spawn(self._wait_sync_then_record(down_from, reason, rebooted))

    def _drop_partial_wifi(self):
        """Записи старих версій, де «відвалом» вважалось часткове відключення клієнтів, — прибираємо:
        тепер Wi-Fi-інцидент лише коли зникли всі клієнти."""
        n = 0
        for r in self.db.query("SELECT id, details FROM incidents WHERE kind='wifi'"):
            d = json.loads(r["details"] or "{}")
            if not d.get("all") and (d.get("lost") or 0) < (d.get("before") or 0):
                self.db.execute("DELETE FROM incidents WHERE id=?", (r["id"],))
                n += 1
        if n:
            log.info("removed %d partial Wi-Fi incidents from older versions", n)

    async def _wait_sync_then_record(self, down_from, reason, rebooted):
        t0 = time.monotonic()
        while not clock_synced() and time.monotonic() - t0 < SYNC_WAIT_MAX:
            await asyncio.sleep(5)
        synced = clock_synced()
        self._on_clock_synced()
        self._record_downtime(down_from, reason, rebooted, synced_late=True, unsynced=not synced)

    def _on_clock_synced(self):
        """Час «стрибнув» після NTP: перераховуємо початок відкритих інцидентів."""
        self.clock_ok = True
        now, mono = time.time(), time.monotonic()
        for iid, m0 in list(self.open_mono.items()):
            self.db.execute("UPDATE incidents SET started_ts=? WHERE id=?", (int(now - (mono - m0)), iid))
        self.open_mono.clear()
        if self.inet_first_mono is not None:
            self.inet_first_ts = int(now - (mono - self.inet_first_mono))

    def _record_downtime(self, down_from, reason, rebooted, synced_late, unsynced=False):
        started_real = time.time() - (time.monotonic() - self.start_mono)  # момент старту сервісу
        up = uptime_sec()
        details = {"reason": reason, "reboot": rebooted}
        if rebooted and up is not None:
            details["boot_ts"] = int(time.time() - up)
        if unsynced:
            details["unsynced"] = True
        if started_real < down_from:  # годинник так і не синхронізувався — тривалість невідома
            started_real = down_from
            details["unsynced"] = True
        iid = self.db.insert("incidents", {"kind": "molfar", "started_ts": int(down_from),
                                           "ended_ts": int(started_real),
                                           "details": json.dumps(details, ensure_ascii=False)})
        s = self.m.settings
        lang = s["language"]
        dur = fmt_dur(lang, started_real - down_from) if not details.get("unsynced") else tr(lang, "inc_dur_unknown")
        self._say("info" if reason == "restart" else "warn", "tg_molfar_back",
                  None if reason == "restart" else "alert_inc_molfar", s,
                  dur=dur, start=_hm(down_from), reason=tr(lang, f"inc_reason_{reason}"))
        self.write_heartbeat()
        return iid

    def write_heartbeat(self):
        if self.clock_ok:
            self.db.set_state("heartbeat", {"ts": self.now(), "boot_id": self.boot_id})

    def on_shutdown(self):
        self.write_heartbeat()
        if self.clock_ok:
            self.db.set_state("clean_stop", {"ts": self.now(), "boot_id": self.boot_id})

    async def heartbeat_loop(self):
        while True:
            try:
                if not self.clock_ok and clock_synced():
                    self._on_clock_synced()
                self.write_heartbeat()
            except Exception:
                log.exception("heartbeat")
            await asyncio.sleep(HEARTBEAT_SEC)

    # ---------- інтернет ----------
    async def on_ping(self, results, s, ts):
        # збій самого ping (не встановлений, немає прав) — це не обрив інтернету, раунд пропускаємо;
        # «Network is unreachable» (немає лінку / маршруту) — справжній обрив
        results = [r for r in results if not (r.get("error") and TOOL_ERR_RE.search(r["error"]))]
        public = [r for r in results if is_public(r["target"])] or results
        if not public:
            return
        down = all(r["received"] == 0 for r in public)
        if down:
            self.inet_bad += 1
            if self.inet_bad == 1:
                self.inet_first_ts, self.inet_first_mono = ts, time.monotonic()
            if self.inet_open is None and self.inet_bad >= s["inc_inet_confirm"]:
                gw = await default_gateway()
                gw_ok = None
                if gw:
                    g = await probes.ping_target(gw, 4)
                    gw_ok = g["received"] > 0
                cause = "nolink" if gw is None else ("isp" if gw_ok else "lan")
                self.inet_open = self._open("internet", self.inet_first_ts,
                                            {"cause": cause, "gw": gw, "targets": [r["target"] for r in public]},
                                            mono=self.inet_first_mono)
                self.db.log("warn", tr(s["language"], "ev_inet_down", targets=", ".join(r["target"] for r in public),
                                       cause=tr(s["language"], f"inc_cause_{cause}", gw=gw or "?") if cause else ""))
        else:
            if self.inet_open is not None:
                r = self._close(self.inet_open, ts)
                self.inet_open = None
                if r:
                    lang = s["language"]
                    cause = r["details"].get("cause")
                    self._say("info", "tg_inet_back", "alert_inc_internet", s,
                              dur=fmt_dur(lang, r["ended_ts"] - r["started_ts"]), start=_hm(r["started_ts"]),
                              cause=("\n" + tr(lang, f"inc_cause_{cause}", gw=html.escape(r['details'].get('gw') or '?'))) if cause else "")
            self.inet_bad = 0
            self.inet_first_ts = self.inet_first_mono = None

    # ---------- роутер і Wi-Fi ----------
    async def router_loop(self):
        await asyncio.sleep(20)
        while True:
            s = self.m.settings
            every = max(1, s["router_poll_min"]) * 60
            if s["router_type"] != "none" and s["router_host"]:
                try:
                    await self.poll_router(s)
                except routers.RouterError as e:
                    log.warning("router poll: %s", e)  # помилка вже в router_state і журналі інцидентів
                except Exception:
                    log.exception("router poll")
            else:
                self.router_state.update(error=None, summary=None)
            try:
                await asyncio.wait_for(self.router_wakeup.wait(), every)
            except asyncio.TimeoutError:
                pass
            self.router_wakeup.clear()

    async def poll_router(self, s, record=True):
        ts = self.now()
        try:
            data = await routers.poll(s)
        except Exception as e:
            err = str(e)[:200]
            self.router_state.update(error=err, fails=self.router_state["fails"] + 1)
            if record:
                self._router_failed(s, ts, err)
            raise
        self.router_state.update(error=None, fails=0, last_ok=ts, summary=routers.summarize(data))
        if record:
            self._router_ok(s, ts, data)
            self._wifi_check(s, ts, data)
        return data

    def _router_failed(self, s, ts, err):
        if self.router_open is None and self.router_state["fails"] >= ROUTER_FAILS_TO_OPEN:
            # роутер мовчить — початок рахуємо від останнього вдалого опитування
            last = self.router_state["last_ok"]
            start = ts if last is None else min(ts, last + max(1, s["router_poll_min"]) * 60)
            self.router_open = self._open("router", start, {"unreachable": True, "error": err},
                                          mono=time.monotonic())
            self.db.log("warn", tr(s["language"], "ev_router_down", err=err))

    def _router_ok(self, s, ts, data):
        lang = s["language"]
        boot, wan = data.get("boot_ts"), data.get("wan_uptime")
        if self.router_open is not None:
            r = self._get(self.router_open)
            rebooted = bool(r and boot and boot >= r["started_ts"] - 120)
            wan_reset = bool(r and wan is not None and wan < ts - r["started_ts"] + 120)
            r = self._close(self.router_open, ts, {"reboot": rebooted, "wan_reset": wan_reset and not rebooted,
                                                    **({"boot_ts": boot} if boot else {})})
            self.router_open = None
            if r:
                note = tr(lang, "inc_router_rebooted") if rebooted else tr(lang, "inc_router_wan") if wan_reset else ""
                self._say("info", "tg_router_back", "alert_inc_router", s,
                          dur=fmt_dur(lang, r["ended_ts"] - r["started_ts"]), start=_hm(r["started_ts"]), note=note)
        else:
            # перезавантаження без жодного пропущеного опитування
            if boot and self.router_boot_ts and boot - self.router_boot_ts > 120:
                self._point_router(s, boot, ts, {"reboot": True, "boot_ts": boot}, "tg_router_reboot")
            elif wan is not None and self.router_wan_uptime is not None and wan + 60 < self.router_wan_uptime:
                self._point_router(s, ts - wan, ts, {"wan_reset": True}, "tg_router_wan")
        if boot:
            self.router_boot_ts = boot
        if wan is not None:
            self.router_wan_uptime = wan

    def _point_router(self, s, started, ended, details, msg_id):
        self.db.insert("incidents", {"kind": "router", "started_ts": int(started), "ended_ts": int(ended),
                                     "details": json.dumps(details)})
        self._say("warn", msg_id, "alert_inc_router", s, time=_hm(started))

    def _wifi_check(self, s, ts, data):
        """Відвал Wi-Fi = між двома опитуваннями зникли ВСІ Wi-Fi-клієнти (а до цього їх було
        щонайменше wifi_drop_min). Часткові відключення (телефон пішов з дому) — не інцидент.
        Закінчується, щойно хоч один Wi-Fi-клієнт підключився знову."""
        if not data["clients"]:
            return  # роутер не віддав жодного клієнта, навіть кабельних (і самого Pi) — збій опитування
        cur = {c["mac"]: c["band"] for c in data["clients"] if c["band"] != "wired"}
        prev = self.wifi_prev
        self.wifi_prev = cur
        lang = s["language"]
        if self.wifi_open is not None:
            r = self._get(self.wifi_open)
            if not r:
                self.wifi_open = None
                return
            if cur:
                lost = set(r["details"].get("lost_macs", []))
                back = len(lost & cur.keys())
                self._close(self.wifi_open, ts, {"recovered": back, "after_recovery": len(cur)})
                self.wifi_open = None
                self._say("info", "tg_wifi_back", "alert_inc_wifi", s, back=len(cur),
                          before=r["details"].get("before", 0), dur=fmt_dur(lang, ts - r["started_ts"]))
            elif ts - r["started_ts"] > WIFI_MAX_OPEN_SEC:
                self._close(self.wifi_open, ts, {"recovered": 0, "gave_up": True})
                self.wifi_open = None
            return
        if not prev or cur or len(prev) < s["wifi_drop_min"]:
            return
        bands = {}
        for b in sorted(set(prev.values())):
            n = sum(1 for v in prev.values() if v == b)
            bands[b] = {"before": n, "after": 0, "lost": n}
        details = {"before": len(prev), "after": 0, "lost": len(prev), "all": True, "bands": bands,
                   "lost_macs": list(prev)[:200], "router": s["router_type"]}
        self.wifi_open = self._open("wifi", ts, details, mono=time.monotonic())
        band_lines = "".join(tr(lang, "tg_wifi_band", band=tr(lang, f"band_{b}"), b=v["before"], a=0)
                             for b, v in bands.items())
        self._say("warn", "tg_wifi_drop", "alert_inc_wifi", s, before=len(prev), bands=band_lines)

    # ---------- для API ----------
    def list(self, since, now=None, limit=500):
        now = now or self.now()
        rows = self.db.query("SELECT * FROM incidents WHERE (ended_ts IS NULL OR ended_ts >= ?) AND started_ts <= ? "
                             "ORDER BY started_ts DESC LIMIT ?", (since, now, limit))
        for r in rows:
            d = json.loads(r["details"] or "{}")
            d.pop("lost_macs", None)
            r["details"] = d
        return rows

    def status(self):
        now = self.now()
        since = now - 86400
        rows = self.list(since, now, 200)
        s = self.m.settings
        return {
            "since": since, "now": now,
            "summary": summarize(rows, since, now),
            "items": rows,
            "clock_ok": self.clock_ok,
            "router": {"type": s["router_type"], "configured": s["router_type"] != "none" and bool(s["router_host"]),
                       **self.router_state},
        }
