"""Ядро: цикл пінгу, планувальник speedtest-серій, алерти в Telegram."""
import asyncio
import html
import logging
import statistics
import time
from datetime import datetime, timedelta

from . import isp as ispmod
from . import probes, routers, telegram
from .netmap import NetMap
from .wifi import Wifi, WifiError
from .db import DB
from .incidents import Incidents
from .i18n import fmt_dur, tr

log = logging.getLogger("molfar")
ISP_CONFIRM_SEC = 20


def _plain(msg: str) -> str:
    for tag in ("<b>", "</b>", "<code>", "</code>", "<i>", "</i>"):
        msg = msg.replace(tag, "")
    return msg


class Monitor:
    def __init__(self, db: DB):
        self.db = db
        self.engine: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self.ping_lock = asyncio.Lock()
        self.tg_lock = asyncio.Lock()
        self.ping_wakeup = asyncio.Event()
        self.ping_extra_wakeup = asyncio.Event()
        self.next_extra_at: int | None = None
        self.last_ping: dict[str, dict] = {}
        self.next_ping_at: int | None = None
        self.alert_state: dict[str, dict] = {}
        self.tg_queue: list[tuple[float, str]] = []
        self.tg_last_error: str | None = None
        self.series_task: asyncio.Task | None = None
        self.series_info: dict | None = None
        self.speedtest_running = False
        self.isp_current = None        # останній визначений провайдер
        self.isp_checked_at = None
        self.isp_error = None
        self.isp_lock = asyncio.Lock()
        self.isp_wakeup = asyncio.Event()
        self.netmap = NetMap(db.path, db.get_settings)
        self.netmap_wakeup = asyncio.Event()
        self.wifi = Wifi(db, db.get_settings)
        self.wifi_wakeup = asyncio.Event()
        self.wifi_next_at: int | None = None
        self.netmap.request_scan = self.netmap_scan_now
        self.inc = Incidents(self)

    @property
    def settings(self):
        return self.db.get_settings()

    def t(self, msg_id, s=None, **kw):
        return tr((s or self.settings)["language"], msg_id, **kw)

    # ---------- життєвий цикл ----------
    async def start(self):
        # серії, що «зависли» після перезавантаження
        self.db.execute("UPDATE series SET status='stopped', finished_ts=? WHERE status='running'",
                        (int(time.time()),))
        self.engine = await probes.detect_engine()
        log.info("speedtest engine: %s", self.engine)
        self.db.log("info", self.t("service_started", engine=self.engine or self.t("not_found")))
        self.isp_current = self.db.get_state("isp_current")
        self.isp_checked_at = (self.isp_current or {}).get("checked_at")
        try:
            await self.inc.on_start()
        except Exception:
            log.exception("incidents start")
        for coro in (self._ping_loop(), self._ping_extra_loop(), self._schedule_loop(), self._maintenance_loop(),
                     self._isp_loop(), self._netmap_loop(), self._wifi_loop(),
                     self.inc.heartbeat_loop(), self.inc.router_loop()):
            self._tasks.append(asyncio.create_task(coro))

    async def shutdown(self):
        if self.series_task and not self.series_task.done():
            self.series_task.cancel()
            try:
                await self.series_task
            except BaseException:
                pass
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        try:
            self.inc.on_shutdown()
            await routers.close()
        except Exception:
            log.exception("incidents shutdown")

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # ---------- пінг ----------
    async def _ping_loop(self):
        while True:
            s = self.settings
            try:
                paused = self.speedtest_running and s["ping_pause_during_speedtest"]
                if s["ping_enabled"] and not paused:
                    await self.ping_round(s)
            except Exception:
                log.exception("ping round failed")
            self.next_ping_at = int(time.time()) + s["ping_interval_sec"]
            try:
                await asyncio.wait_for(self.ping_wakeup.wait(), s["ping_interval_sec"])
            except asyncio.TimeoutError:
                pass
            self.ping_wakeup.clear()

    async def ping_round(self, s=None, manual=False):
        """Основні адреси + додаткові хости, позначені «На Огляді» — з основним інтервалом."""
        s = s or self.settings
        main = [t for t in s["ping_targets"] if t]
        dash = [h["host"] for h in s["ping_extra"] if h.get("dash") and h["host"] not in main]
        targets = main + dash
        no_alert = {h["host"] for h in s["ping_extra"] if not h.get("alert", True)}
        async with self.ping_lock:
            results = await asyncio.gather(*(probes.ping_target(t, s["ping_count"]) for t in targets))
            ts = int(time.time())
            for r in results:
                self.db.insert("ping_results", {"ts": ts, **r})
                self.last_ping[r["target"]] = {**r, "ts": ts}
            self._prune_targets(s)
            if not manual:
                self._check_loss_alerts([r for r in results if r["target"] not in no_alert], s, ts)
        if not manual:
            try:
                # «немає інтернету» — лише за основними адресами, без додаткових хостів
                await self.inc.on_ping([r for r in results if r["target"] in main], s, ts)
            except Exception:
                log.exception("incidents ping")
        return results

    def _prune_targets(self, s):
        """Прибрати з «останніх» і зі стану алертів адреси, яких більше немає в налаштуваннях."""
        keep = {t for t in s["ping_targets"] if t} | {h["host"] for h in s["ping_extra"]}
        no_alert = {h["host"] for h in s["ping_extra"] if not h.get("alert", True)}
        for t in list(self.last_ping):
            if t not in keep:
                self.last_ping.pop(t, None)
        for t in list(self.alert_state):
            if t not in keep or t in no_alert:
                self.alert_state.pop(t, None)

    # ---------- додаткові хости (не на «Огляді») ----------
    async def _ping_extra_loop(self):
        await asyncio.sleep(10)  # основний пінг — першим
        while True:
            s = self.settings
            try:
                paused = self.speedtest_running and s["ping_pause_during_speedtest"]
                if s["ping_enabled"] and any(not h.get("dash") for h in s["ping_extra"]) and not paused:
                    await self.ping_extra_round(s)
            except Exception:
                log.exception("extra ping round failed")
            self.next_extra_at = int(time.time()) + s["ping_extra_interval_sec"]
            try:
                await asyncio.wait_for(self.ping_extra_wakeup.wait(), s["ping_extra_interval_sec"])
            except asyncio.TimeoutError:
                pass
            self.ping_extra_wakeup.clear()

    async def ping_extra_round(self, s=None):
        s = s or self.settings
        hosts = [h for h in s["ping_extra"] if not h.get("dash")]   # ті, що на «Огляді», — в основному циклі
        sem = asyncio.Semaphore(10)  # не більше 10 ping одночасно — Zero 3 вистачає з запасом

        async def one(h):
            async with sem:
                return await probes.ping_target(h["host"], s["ping_extra_count"])

        results = await asyncio.gather(*(one(h) for h in hosts))
        ts = int(time.time())
        for r in results:
            self.db.insert("ping_results", {"ts": ts, **r})
            self.last_ping[r["target"]] = {**r, "ts": ts}
        self._prune_targets(s)
        alerting = {h["host"] for h in hosts if h.get("alert", True)}
        self._check_loss_alerts([r for r in results if r["target"] in alerting], s, ts)
        return results

    def _check_loss_alerts(self, results, s, ts):
        thr = s["alert_loss_pct"]
        need = s["alert_consecutive"]
        cooldown = s["alert_cooldown_min"] * 60
        new_bad, recovered = [], []
        for r in results:
            st = self.alert_state.setdefault(
                r["target"], {"bad": 0, "since": None, "max_loss": 0, "notified": False, "last_sent": 0})
            if r["loss"] >= thr:
                st["bad"] += 1
                if st["bad"] == 1:
                    st["since"] = ts
                    st["max_loss"] = 0
                st["max_loss"] = max(st["max_loss"], r["loss"])
                if st["bad"] >= need and not st["notified"] and ts - st["last_sent"] >= cooldown:
                    st["notified"] = True
                    st["last_sent"] = ts
                    new_bad.append(r)
            else:
                if st["notified"]:
                    recovered.append((r, st["since"], st["max_loss"]))
                st.update(bad=0, since=None, max_loss=0, notified=False)

        name = html.escape(s["device_name"])
        lang = s["language"]
        names = {h["host"]: h["name"] for h in s["ping_extra"] if h.get("name")}
        label = lambda t: f"{names[t]} ({t})" if t in names else t  # noqa: E731
        if new_bad:
            lines = [tr(lang, "alert_title", name=name)]
            for r in new_bad:
                extra = tr(lang, "alert_extra", rtt=r["rtt_avg"], jitter=r["jitter"]) if r["rtt_avg"] is not None else ""
                lines.append(tr(lang, "alert_line", target=html.escape(label(r["target"])), loss=r["loss"], extra=extra))
            lines.append(tr(lang, "alert_rule", thr=thr, need=need))
            msg = "\n".join(lines)
            self.db.log("warn", _plain(msg))
            if s["alert_enabled"]:
                self.notify(msg)
        if recovered:
            lines = [tr(lang, "recover_title", name=name)]
            for r, since, mx in recovered:
                lines.append(tr(lang, "recover_line", target=html.escape(label(r["target"])),
                                dur=fmt_dur(lang, ts - since), mx=mx))
            msg = "\n".join(lines)
            self.db.log("info", _plain(msg))
            if s["alert_enabled"]:
                self.notify(msg)

    # ---------- Telegram ----------
    def notify(self, text: str):
        s = self.settings
        if not (s["tg_token"] and s["tg_chat_id"]):
            return
        self.tg_queue.append((time.time(), text))
        del self.tg_queue[:-30]  # не більше 30 повідомлень у черзі
        self._spawn(self.flush_tg())

    async def flush_tg(self):
        async with self.tg_lock:
            s = self.settings
            while self.tg_queue:
                created, text = self.tg_queue[0]
                if time.time() - created > 120:
                    text = self.t("tg_delayed", s, time=f"{datetime.fromtimestamp(created):%d.%m %H:%M}") + "\n" + text
                try:
                    await asyncio.to_thread(telegram.send, s["tg_token"], s["tg_chat_id"], text)
                    self.tg_last_error = None
                except Exception as e:  # немає інтернету — спробуємо пізніше
                    self.tg_last_error = str(e)
                    log.warning("telegram send failed: %s", e)
                    break
                self.tg_queue.pop(0)

    # ---------- speedtest ----------
    @property
    def series_running(self):
        return self.series_task is not None and not self.series_task.done()

    def start_series(self, trigger: str, runs: int | None = None, server: dict | None = None):
        if self.series_running:
            return None
        s = self.settings
        if server is not None:  # разовий тест на конкретному сервері, налаштування не змінюються
            s["speedtest_server_id"] = server["id"]
            s["speedtest_server_name"] = server.get("name", "")
        runs = runs or s["speedtest_runs"]
        interval = s["speedtest_interval_min"]
        sid = self.db.insert("series", {"started_ts": int(time.time()), "trigger": trigger,
                                        "status": "running", "runs_planned": runs})
        self.series_info = {"id": sid, "trigger": trigger, "runs": runs, "current": 0,
                            "done": 0, "phase": "starting", "next_at": None,
                            "interval_min": interval, "started_ts": int(time.time()),
                            "server": s["speedtest_server_name"] or s["speedtest_server_id"] or None}
        self.series_task = asyncio.create_task(self._series(sid, runs, interval, s))
        kind = self.t("st_single", s) if runs == 1 else self.t("st_series", s, n=runs)
        srv = self.t("st_server", s, name=s["speedtest_server_name"] or s["speedtest_server_id"]) if s["speedtest_server_id"] else ""
        how = self.t("st_sched" if trigger == "schedule" else "st_manual", s)
        self.db.log("info", self.t("st_started", s, kind=kind, how=how, srv=srv))
        return sid

    async def stop_series(self):
        if not self.series_running:
            return False
        self.series_task.cancel()
        try:
            await self.series_task
        except BaseException:
            pass
        return True

    async def _series(self, sid, runs, interval, s):
        info = self.series_info
        status = "done"
        try:
            for i in range(runs):
                info.update(current=i + 1, phase="testing", next_at=None)
                if self.engine is None:
                    self.engine = await probes.detect_engine()
                engine = self.engine if s["speedtest_engine"] == "auto" else s["speedtest_engine"]
                self.speedtest_running = True
                row = {"ts": int(time.time()), "series_id": sid, "engine": engine}
                try:
                    row.update(await self._run_one(engine, s, i + 1))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    row["error"] = str(e)[:300]
                    self.db.log("error", self.t("st_error", s, n=i + 1, err=e))
                finally:
                    self.speedtest_running = False
                    self.ping_wakeup.set()  # одразу відновити пінг
                self.db.insert("speedtests", row)
                info["done"] = i + 1
                if i < runs - 1:
                    info.update(phase="waiting", next_at=int(time.time()) + interval * 60)
                    await asyncio.sleep(interval * 60)
        except asyncio.CancelledError:
            status = "stopped"
        except Exception as e:
            status = "error"
            log.exception("series failed")
            self.db.log("error", self.t("st_crash", s, sid=sid, err=e))
        finally:
            self.speedtest_running = False
            self._finish_series(sid, status, s)
            self.series_info = None

    async def _run_one(self, engine, s, n):
        server_id = s["speedtest_server_id"]
        try:
            return await probes.run_speedtest(engine, server_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not (server_id and s["speedtest_fallback_auto"]):
                raise
            name = s["speedtest_server_name"] or server_id
            self.db.log("warn", self.t("st_fallback", s, n=n, name=name, err=str(e)[:120]))
            res = await probes.run_speedtest(engine, "")
            res["server"] = f"{res.get('server', '')} " + self.t("st_instead", s, name=name)
            return res

    def _finish_series(self, sid, status, s):
        rows = self.db.query("SELECT * FROM speedtests WHERE series_id=? AND error IS NULL", (sid,))

        def avg(k):
            vals = [r[k] for r in rows if r[k] is not None]
            return round(statistics.fmean(vals), 2) if vals else None

        res = {"avg_download": avg("download"), "avg_upload": avg("upload"),
               "avg_ping": avg("ping"), "avg_jitter": avg("jitter")}
        if not rows and status == "done":
            status = "error"
        self.db.execute(
            "UPDATE series SET status=?, finished_ts=?, runs_ok=?, avg_download=?, avg_upload=?, "
            "avg_ping=?, avg_jitter=? WHERE id=?",
            (status, int(time.time()), len(rows), res["avg_download"], res["avg_upload"],
             res["avg_ping"], res["avg_jitter"], sid))

        s = {**s, "language": self.settings["language"]}  # мова могла змінитися під час серії
        status_txt = self.t(f"st_status_{status}", s)
        if rows:
            summary = self.t("st_summary", s, d=res["avg_download"], u=res["avg_upload"], p=res["avg_ping"])
            if res["avg_jitter"] is not None:
                summary += self.t("st_summary_jit", s, j=res["avg_jitter"])
        else:
            summary = self.t("st_none_ok", s)
        self.db.log("info" if status == "done" else "warn",
                    self.t("st_finished", s, sid=sid, status=status_txt, ok=len(rows), summary=summary))

        name = html.escape(s["device_name"])
        if status != "stopped" and s["tg_series_report"]:
            self.notify(self.t("tg_report", s, name=name, n=len(rows), summary=summary))
        low = s["alert_speed_min_mbps"]
        if (s["alert_enabled"] and low > 0 and res["avg_download"] is not None
                and res["avg_download"] < low):
            self.notify(self.t("tg_slow", s, name=name, summary=summary, low=low))

    def next_scheduled(self):
        s = self.settings
        if not s["speedtest_enabled"]:
            return None
        hh, mm = map(int, s["speedtest_time"].split(":"))
        now = datetime.now()
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= now or self.db.get_state("last_schedule_date") == now.date().isoformat():
            t += timedelta(days=1)
        return int(t.timestamp())

    async def _schedule_loop(self):
        while True:
            try:
                s = self.settings
                if s["speedtest_enabled"]:
                    hh, mm = map(int, s["speedtest_time"].split(":"))
                    now = datetime.now()
                    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    today = now.date().isoformat()
                    # 15-хвилинне вікно — на випадок перезапуску саме в момент старту
                    if (target <= now < target + timedelta(minutes=15)
                            and self.db.get_state("last_schedule_date") != today
                            and not self.series_running):
                        self.db.set_state("last_schedule_date", today)
                        self.start_series("schedule")
            except Exception:
                log.exception("schedule loop")
            await asyncio.sleep(20)

    # ---------- провайдер ----------
    async def _isp_loop(self):
        await asyncio.sleep(15)  # дати мережі піднятися після старту
        while True:
            every = self.settings["isp_check_min"]
            if every > 0:
                try:
                    await self.check_isp()
                except Exception:
                    pass  # помилку вже збережено в isp_error
            try:
                await asyncio.wait_for(self.isp_wakeup.wait(), max(every, 1) * 60)
            except asyncio.TimeoutError:
                pass
            self.isp_wakeup.clear()

    async def check_isp(self):
        """Визначає провайдера. Зміну провайдера підтверджує повторною перевіркою через 20 с."""
        async with self.isp_lock:
            try:
                cur = await asyncio.to_thread(ispmod.lookup)
                prev = self.isp_current
                if prev and not ispmod.same_isp(prev, cur):
                    # можлива зміна: перевіряємо ще раз — якщо це був короткий збій або інша база
                    # повернула інший ASN, друга відповідь покаже старого провайдера і зміни не буде
                    await asyncio.sleep(ISP_CONFIRM_SEC)
                    cur = await asyncio.to_thread(ispmod.lookup)
            except Exception as e:
                self.isp_error = str(e)[:300]
                self.isp_checked_at = int(time.time())
                if self.isp_current is None:
                    self.db.log("warn", self.t("ev_isp_failed", err=self.isp_error))
                raise
            self.isp_error = None
            self.isp_checked_at = int(time.time())
            self._record_isp(cur)
            return cur

    def _record_isp(self, cur):
        prev = self.isp_current
        now = int(time.time())
        kind = None
        if prev is None:
            kind = "first"
            self.db.log("info", self.t("ev_isp_first", isp=ispmod.label(cur), ip=cur["ip"]))
        elif not ispmod.same_isp(prev, cur):
            kind = "isp"
            self.db.log("warn", self.t("ev_isp_changed", old=ispmod.label(prev), new=ispmod.label(cur), ip=cur["ip"]))
            s = self.settings
            if s["alert_enabled"] and s["alert_isp_change"]:
                self.notify(self.t("tg_isp_changed", s, name=html.escape(s["device_name"]),
                                   old=html.escape(ispmod.label(prev)), new=html.escape(ispmod.label(cur)),
                                   ip=html.escape(cur["ip"] or "?")))
        elif prev.get("ip") != cur.get("ip"):
            kind = "ip"
            self.db.log("info", self.t("ev_ip_changed", old=prev.get("ip"), new=cur["ip"], isp=ispmod.label(cur)))
        if kind:
            self.db.insert("isp_history", {"ts": now, "kind": kind, **{k: cur.get(k) for k in
                           ("ip", "asn", "isp", "org", "city", "country", "source")}})
            cur = {**cur, "since": now}
        else:
            cur = {**cur, "since": prev.get("since", now)}
        cur["checked_at"] = self.isp_checked_at
        self.isp_current = cur
        self.db.set_state("isp_current", cur)

    # ---------- мапа мережі ----------
    def netmap_scan_now(self) -> bool:
        if self.netmap.scan_info.get("state") == "running":
            return False
        self.netmap_wakeup.set()
        return True

    async def _netmap_loop(self):
        await asyncio.sleep(5)  # дати сервісу піднятися
        while True:
            s = self.settings
            try:
                if s["net_enabled"]:
                    new_devs = await asyncio.to_thread(self.netmap.run_scan)
                    if new_devs:
                        self._report_new_devices(new_devs, s)
                else:
                    await asyncio.to_thread(self.netmap.tick)
            except Exception:
                log.exception("network map")
            try:
                await asyncio.wait_for(self.netmap_wakeup.wait(), s["net_scan_min"] * 60)
            except asyncio.TimeoutError:
                pass
            self.netmap_wakeup.clear()

    def _report_new_devices(self, devs, s):
        first_run = self.db.get_state("netmap_seeded") is None
        if first_run:  # перше сканування — це не «нові» пристрої, а вся мережа
            self.db.set_state("netmap_seeded", int(time.time()))
            return
        for d in devs:
            self.db.log("info", self.t("ev_net_new", s, dev=d["name"], ip=d["ip"], mac=d["mac"]))
        if not s["net_alert_new"]:
            return
        lines = [self.t("net_new_line", s, dev=html.escape(d["name"]), ip=d["ip"], mac=d["mac"],
                        vendor=(" · " + html.escape(d["vendor"])) if d["vendor"] else "") for d in devs[:10]]
        if len(devs) > 10:
            lines.append(self.t("net_new_more", s, n=len(devs) - 10))
        self.notify(self.t("net_new_title", s, name=html.escape(s["device_name"])) + "\n" + "\n".join(lines))

    def _wifi_summary(self):
        try:
            return {**self.wifi.summary(), "next_at": self.wifi_next_at}
        except Exception:  # noqa: BLE001
            return None

    def netmap_summary(self, s):
        try:
            c = self.netmap.counts()
        except Exception:  # noqa: BLE001 — картка на «Огляді» не має ламати статус
            c = {}
        info = self.netmap.scan_info
        return {"enabled": s["net_enabled"], **c, "last_scan": info.get("last"),
                "scanning": info.get("state") == "running", "error": info.get("error") or ""}

    # ---------- Wi-Fi ефір ----------
    async def wifi_scan(self):
        """Ручне сканування (кнопка). Кидає WifiError."""
        return await asyncio.to_thread(self.wifi.scan)

    async def _wifi_loop(self):
        await asyncio.sleep(30)
        while True:
            s = self.settings
            every = s["wifi_scan_min"] * 60
            if s["wifi_air"] and s["wifi_enabled"] and self.wifi.pick_iface()[0]:
                if self.speedtest_running:     # не заважати speedtest, якщо Мольфар сам на Wi-Fi
                    every = 120
                else:
                    try:
                        await asyncio.to_thread(self.wifi.scan)
                    except WifiError as e:
                        if e.code != "busy":
                            log.info("wifi scan: %s %s", e.code, e.kw.get("err", ""))
                    except Exception:
                        log.exception("wifi scan")
                self.wifi_next_at = int(time.time()) + every
            else:
                self.wifi_next_at = None
            try:
                await asyncio.wait_for(self.wifi_wakeup.wait(), every)
            except asyncio.TimeoutError:
                pass
            self.wifi_wakeup.clear()

    # ---------- обслуговування ----------
    async def _maintenance_loop(self):
        n = 0
        while True:
            try:
                if self.tg_queue:
                    await self.flush_tg()
                if n % 360 == 0:  # раз на 6 годин
                    await asyncio.to_thread(self.db.cleanup, self.settings["retention_days"])
                    await asyncio.to_thread(self.wifi.cleanup)
            except Exception:
                log.exception("maintenance")
            n += 1
            await asyncio.sleep(60)

    # ---------- стан для UI ----------
    def status(self):
        s = self.settings
        return {
            "engine": self.engine,
            "series": self.series_info,
            "speedtest_running": self.speedtest_running,
            "ping": {
                "enabled": s["ping_enabled"],
                "paused": self.speedtest_running and s["ping_pause_during_speedtest"],
                "next_at": self.next_ping_at,
                "last": [self.last_ping[t] for t in s["ping_targets"] if t in self.last_ping],
                "targets": s["ping_targets"],
                "extra": [{**h, "last": self.last_ping.get(h["host"])} for h in s["ping_extra"]],
                "extra_next_at": self.next_extra_at,
            },
            "alerts": {t: {"bad": st["bad"], "notified": st["notified"], "since": st["since"]}
                       for t, st in self.alert_state.items()},
            "telegram": {"configured": bool(s["tg_token"] and s["tg_chat_id"]),
                         "alerts_enabled": s["alert_enabled"],
                         "queue": len(self.tg_queue), "last_error": self.tg_last_error},
            "speedtest_server": {"id": s["speedtest_server_id"], "name": s["speedtest_server_name"]},
            "isp": {"current": self.isp_current, "checked_at": self.isp_checked_at,
                    "error": self.isp_error, "enabled": s["isp_check_min"] > 0},
            "netmap": self.netmap_summary(s),
            "wifi": self._wifi_summary(),
            "incidents": self.inc.status(),
            "schedule": {"enabled": s["speedtest_enabled"], "time": s["speedtest_time"],
                         "next_at": self.next_scheduled()},
        }
