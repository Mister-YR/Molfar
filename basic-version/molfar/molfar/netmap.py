"""Мапа локальної мережі: злиття результатів сканування, правила, редагування топології.

Таблиці net_devices / net_events / net_ignored живуть у тій самій molfar.db (окреме зʼєднання, WAL).
Налаштування — у загальних налаштуваннях Мольфара (ключі net_*).
"""
import ipaddress
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import netscan as ns

log = logging.getLogger("molfar.netmap")

KINDS = ("gateway", "switch", "ap", "sbc", "server", "nas", "pc", "laptop", "phone", "tablet",
         "tv", "camera", "printer", "speaker", "iot", "unknown")
PARENT_KINDS = {"gateway", "switch", "ap"}
VLANS = ("lan", "srv", "iot", "guest", "none")
LINKS = ("wired", "wifi")
BANDS = ("", "2.4", "5", "6")
DAY = 86400
NEW_FOR = DAY

EDIT_FIELDS = ("name", "kind", "vendor", "ip", "mac", "vlan", "link", "ssid", "band", "note", "keep")


SCHEMA = """
CREATE TABLE IF NOT EXISTS net_devices(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  mac           TEXT UNIQUE,
  ip            TEXT NOT NULL DEFAULT '',
  name          TEXT NOT NULL DEFAULT '',
  name_auto     TEXT NOT NULL DEFAULT '',
  kind          TEXT NOT NULL DEFAULT 'unknown',
  kind_locked   INTEGER NOT NULL DEFAULT 0,
  vendor        TEXT NOT NULL DEFAULT '',
  vendor_locked INTEGER NOT NULL DEFAULT 0,
  private_mac   INTEGER NOT NULL DEFAULT 0,
  vlan          TEXT NOT NULL DEFAULT 'lan',
  link          TEXT NOT NULL DEFAULT 'wired',
  link_locked   INTEGER NOT NULL DEFAULT 0,
  parent_id     INTEGER,
  ssid          TEXT NOT NULL DEFAULT '',
  band          TEXT NOT NULL DEFAULT '',
  note          TEXT NOT NULL DEFAULT '',
  role          TEXT NOT NULL DEFAULT '',      -- 'gateway' | 'self' | ''
  manual        INTEGER NOT NULL DEFAULT 0,
  keep          INTEGER NOT NULL DEFAULT 0,    -- не прибирати після N днів офлайн
  status        TEXT NOT NULL DEFAULT 'online',-- online | offline | static
  hidden        INTEGER NOT NULL DEFAULT 0,
  pos_x         REAL,
  pos_y         REAL,
  first_seen    REAL NOT NULL,
  last_seen     REAL NOT NULL,
  status_since  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS net_events(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL,
  device_id INTEGER,
  mac       TEXT NOT NULL DEFAULT '',
  name      TEXT NOT NULL DEFAULT '',
  kind      TEXT NOT NULL DEFAULT '',
  type      TEXT NOT NULL,
  params    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS net_events_ts  ON net_events(ts);
CREATE INDEX IF NOT EXISTS net_events_dev ON net_events(device_id, ts);
CREATE TABLE IF NOT EXISTS net_ignored(
  mac  TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  ts   REAL NOT NULL
);
"""



class _DB:
    """Окреме зʼєднання з molfar.db з транзакціями (злиття сканування — одна транзакція)."""

    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.conn.in_transaction:
                yield
                return
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise


class NetMapError(Exception):
    def __init__(self, code, status=400, **params):
        super().__init__(code)
        self.code, self.status, self.params = code, status, params


def _valid_ip(ip):
    try:
        return ipaddress.ip_address(ip).version == 4
    except ValueError:
        return False


class NetMap:
    def __init__(self, db_path, get_settings):
        """get_settings() → загальні налаштування Мольфара (dict з ключами net_*)."""
        self.db = _DB(db_path)
        self._get_settings = get_settings
        self.lock = threading.RLock()
        self.scan_info = {"state": "idle"}
        self.request_scan = lambda: False   # Monitor підставляє «сканувати зараз»

    # ------------------------------------------------------------------ helpers
    def settings(self):
        s = self._get_settings()
        return {
            "enabled": bool(s.get("net_enabled", True)),
            "scan_interval": int(s.get("net_scan_min", 5)) * 60,
            "offline_after": int(s.get("net_offline_min", 15)) * 60,
            "stale_days": int(s.get("net_stale_days", 7)),
            "log_days": int(s.get("net_log_days", 400)),
            "iface": s.get("net_iface", "") or "",
        }

    def _rows(self, where="1", args=()):
        return self.db.q("SELECT * FROM net_devices WHERE %s ORDER BY id" % where, args)

    def _get(self, dev_id):
        return self.db.one("SELECT * FROM net_devices WHERE id=?", (dev_id,))

    @staticmethod
    def display(d):
        if d.get("name"):
            return d["name"]
        if d.get("name_auto"):
            return d["name_auto"]
        if d.get("vendor"):
            tail = (d.get("mac") or "").replace(":", "")[-6:].upper()
            return ("%s %s" % (ns.short_vendor(d["vendor"])[:24], tail)).strip()
        return d.get("mac") or d.get("ip") or "?"

    def _event(self, d, etype, ts, **params):
        self.db.x("INSERT INTO net_events(ts, device_id, mac, name, kind, type, params) VALUES(?,?,?,?,?,?,?)",
                  (ts, d["id"] if d else None, (d or {}).get("mac") or "", self.display(d) if d else "",
                   (d or {}).get("kind", ""), etype, json.dumps(params, ensure_ascii=False)))

    def _set(self, dev_id, **fields):
        if not fields:
            return
        cols = ", ".join("%s=?" % k for k in fields)
        self.db.x("UPDATE net_devices SET %s WHERE id=?" % cols, tuple(fields.values()) + (dev_id,))

    def _insert(self, now, **fields):
        base = {"first_seen": now, "last_seen": now, "status_since": now}
        base.update(fields)
        cols = ", ".join(base)
        return self.db.x("INSERT INTO net_devices(%s) VALUES(%s)" % (cols, ",".join("?" * len(base))),
                         tuple(base.values()))

    def _gateway(self):
        return self.db.one("SELECT * FROM net_devices WHERE role='gateway' ORDER BY id LIMIT 1")

    def _root_id(self):
        gw = self._gateway()
        if gw:
            return gw["id"]
        me = self.db.one("SELECT id FROM net_devices WHERE role='self' LIMIT 1")
        return me["id"] if me else None

    @staticmethod
    def _can_parent(d, root_id):
        return d["id"] == root_id or d["role"] == "gateway" or d["kind"] in PARENT_KINDS

    def _descendants(self, dev_id, rows=None):
        rows = rows if rows is not None else self._rows("hidden=0")
        kids = {}
        for r in rows:
            kids.setdefault(r["parent_id"], []).append(r["id"])
        out, stack = set(), [dev_id]
        while stack:
            for k in kids.get(stack.pop(), []):
                if k not in out:
                    out.add(k)
                    stack.append(k)
        return out

    def _reattach_children(self, d):
        """Клієнтів прибраного/видаленого пристрою переносимо на рівень вище."""
        target = d["parent_id"] or self._root_id()
        if target == d["id"]:
            target = None
        self.db.x("UPDATE net_devices SET parent_id=?, pos_x=NULL, pos_y=NULL WHERE parent_id=?", (target, d["id"]))

    def _fix_parents(self):
        rows = self._rows("hidden=0")
        by_id = {r["id"]: r for r in rows}
        root = self._root_id()
        for r in rows:
            if r["id"] == root:
                if r["parent_id"] is not None:
                    self._set(r["id"], parent_id=None)
                continue
            p = r["parent_id"]
            ok = p in by_id and p != r["id"] and self._can_parent(by_id[p], root)
            if ok:  # захист від циклу
                seen, cur = {r["id"]}, p
                while cur is not None and cur in by_id:
                    if cur in seen:
                        ok = False
                        break
                    seen.add(cur)
                    cur = by_id[cur]["parent_id"]
            if not ok and root is not None and p != root:
                self._set(r["id"], parent_id=root)

    # ------------------------------------------------------------------ scanning
    def apply_scan(self, res, now=None):
        """Злити результат сканування з мапою. → список нових пристроїв (для сповіщень)."""
        now = now or res.get("ts") or time.time()
        cfg = self.settings()
        new_devs = []
        with self.lock, self.db.transaction():
            ignored = {r["mac"] for r in self.db.q("SELECT mac FROM net_ignored")}
            hosts = [h for h in res["hosts"] if h["mac"] not in ignored]

            # 1. сам Мольфар
            me = res["self"]
            row = self.db.one("SELECT * FROM net_devices WHERE role='self' LIMIT 1")
            if not row and me.get("mac"):
                row = self.db.one("SELECT * FROM net_devices WHERE mac=?", (me["mac"],))
            fields = dict(ip=me["ip"], name_auto=me.get("hostname", "")[:60], status="online", hidden=0,
                          last_seen=now, role="self")
            if row:
                if me.get("mac") and row["mac"] != me["mac"]:
                    fields["mac"] = me["mac"]
                self._set(row["id"], **fields)
            else:
                new_id = self._insert(now, mac=me.get("mac") or None, name="Мольфар", kind="sbc",
                                      vendor=ns.vendor_for(me.get("mac", "")) or "Orange Pi", link="wired",
                                      **fields)
                self._event(self._get(new_id), "new", now, ip=me["ip"])

            # 2. роутер (шлюз за замовчуванням)
            gw_ip = res.get("gateway_ip") or ""
            gw_host = next((h for h in hosts if h["ip"] == gw_ip), None)
            gw = self._gateway()
            if gw_host:
                other = self.db.one("SELECT * FROM net_devices WHERE mac=?", (gw_host["mac"],))
                if gw and gw["mac"] != gw_host["mac"]:
                    if other:
                        self._set(gw["id"], role="")
                        self._set(other["id"], role="gateway", kind="gateway", hidden=0, keep=1)
                    else:
                        self._set(gw["id"], mac=gw_host["mac"])
                elif not gw:
                    if other:
                        self._set(other["id"], role="gateway", kind="gateway", hidden=0, keep=1, parent_id=None)
                    else:
                        self._insert(now, mac=gw_host["mac"], ip=gw_ip, kind="gateway", role="gateway", keep=1,
                                     vendor=gw_host.get("vendor") or ns.vendor_for(gw_host["mac"]),
                                     name_auto=gw_host.get("hostname", ""), link="wired")
            elif not gw and gw_ip:
                self._insert(now, mac=None, ip=gw_ip, kind="gateway", role="gateway", keep=1, link="wired",
                             status="offline")
            root = self._root_id()

            # 3. ручні пристрої лише з IP: «усиновлюємо» MAC, якщо його ще немає на мапі
            known = {r["mac"] for r in self._rows("mac IS NOT NULL")}
            by_ip = {h["ip"]: h for h in hosts}
            for r in self._rows("manual=1 AND mac IS NULL AND ip<>''"):
                h = by_ip.get(r["ip"])
                if h and h["mac"] not in known:
                    self._set(r["id"], mac=h["mac"])
                    known.add(h["mac"])

            # 4. знайдені хости
            seen = set()
            rows_by_mac = {r["mac"]: r for r in self._rows("mac IS NOT NULL")}
            for h in hosts:
                r = rows_by_mac.get(h["mac"])
                private = ns.is_private_mac(h["mac"])
                vendor = h.get("vendor") or ns.vendor_for(h["mac"])
                hostname = h.get("hostname", "")
                if not r:
                    kind = ns.guess_kind(vendor, hostname, private)
                    new_id = self._insert(now, mac=h["mac"], ip=h["ip"], name_auto=hostname, vendor=vendor,
                                          private_mac=int(private), kind=kind, link=ns.guess_link(kind),
                                          parent_id=root, status="online")
                    nd = self._get(new_id)
                    self._event(nd, "new", now, ip=h["ip"])
                    new_devs.append({"id": new_id, "name": self.display(nd), "ip": h["ip"],
                                     "mac": h["mac"].upper(), "vendor": vendor})
                    seen.add(new_id)
                    continue
                seen.add(r["id"])
                upd = {"last_seen": now}
                if h["ip"] != r["ip"]:
                    if r["ip"]:
                        self._event(r, "ip_changed", now, old=r["ip"], new=h["ip"])
                    upd["ip"] = h["ip"]
                if hostname and hostname != r["name_auto"]:
                    upd["name_auto"] = hostname
                if vendor and not r["vendor_locked"] and vendor != r["vendor"]:
                    upd["vendor"] = vendor
                if not r["kind_locked"] and r["kind"] == "unknown" and r["role"] == "":
                    k = ns.guess_kind(vendor, hostname, private)
                    if k != "unknown":
                        upd["kind"] = k
                        if not r["link_locked"]:
                            upd["link"] = ns.guess_link(k)
                if r["hidden"]:
                    upd.update(hidden=0, status="online", status_since=now)
                    self._event(r, "restored", now, after=round(now - r["last_seen"]))
                elif r["status"] != "online":
                    upd.update(status="online", status_since=now)
                    self._event(r, "online", now, after=round(now - r["last_seen"]))
                self._set(r["id"], **upd)
            # ручні без MAC, але з IP, що відповів
            for r in self._rows("manual=1 AND mac IS NULL AND ip<>''"):
                if r["ip"] in by_ip:
                    seen.add(r["id"])
                    upd = {"last_seen": now}
                    if r["status"] != "online":
                        upd.update(status="online", status_since=now, hidden=0)
                        self._event(r, "online", now, after=round(now - r["last_seen"]))
                    self._set(r["id"], **upd)

            # 5. ті, кого не знайшли → офлайн після offline_after
            for r in self._rows("status='online' AND role<>'self'"):
                if r["id"] in seen:
                    continue
                if now - r["last_seen"] >= cfg["offline_after"]:
                    self._set(r["id"], status="offline", status_since=now)
                    self._event(r, "offline", now, last_seen=r["last_seen"])

            self._fix_parents()
        self.tick(now)
        return new_devs

    def tick(self, now=None):
        """Прибрати з мапи тих, хто офлайн ≥ stale_days; почистити журнал старший за log_days."""
        now = now or time.time()
        cfg = self.settings()
        with self.lock, self.db.transaction():
            limit = now - cfg["stale_days"] * DAY
            for r in self._rows("hidden=0 AND status='offline' AND role='' AND keep=0 AND last_seen<=?", (limit,)):
                self._set(r["id"], hidden=1, pos_x=None, pos_y=None)
                self._reattach_children(r)
                self._event(r, "hidden", now, days=int((now - r["last_seen"]) // DAY))
            self.db.x("DELETE FROM net_events WHERE ts < ?", (now - cfg["log_days"] * DAY,))
            self._fix_parents()

    def run_scan(self):
        """Одне сканування (блокує — викликати через asyncio.to_thread). → нові пристрої."""
        self.scan_info.update(state="running", started=time.time())
        try:
            res = ns.scan(self.settings())
            new_devs = self.apply_scan(res)
            self.scan_info = {"state": "idle", "last": res["ts"], "duration": res["duration"],
                              "method": res["method"], "note": res["note"], "iface": res["iface"],
                              "subnet": res["subnet"], "found": len(res["hosts"]) + 1, "error": ""}
            return new_devs
        except Exception as e:  # noqa: BLE001
            log.exception("network scan failed")
            self.scan_info.update(state="idle", error=str(e)[:200], last_error=time.time())
            return []

    # ------------------------------------------------------------------ API views
    def _ser(self, d, now, stale):
        protected = d["role"] in ("gateway", "self")
        return {
            "id": d["id"], "mac": (d["mac"] or "").upper(), "ip": d["ip"], "name": self.display(d),
            "name_raw": d["name"], "name_auto": d["name_auto"], "kind": d["kind"], "vendor": d["vendor"],
            "vendor_short": ns.short_vendor(d["vendor"]),
            "private_mac": bool(d["private_mac"]), "vlan": d["vlan"], "link": d["link"], "parent_id": d["parent_id"],
            "ssid": d["ssid"], "band": d["band"], "note": d["note"], "role": d["role"], "manual": bool(d["manual"]),
            "keep": bool(d["keep"]), "protected": protected, "status": d["status"], "hidden": bool(d["hidden"]),
            "pos": [d["pos_x"], d["pos_y"]] if d["pos_x"] is not None else None,
            "first_seen": d["first_seen"], "last_seen": d["last_seen"], "status_since": d["status_since"],
            "is_new": (not protected) and now - d["first_seen"] < NEW_FOR and not d["manual"],
            "hide_at": (d["last_seen"] + stale) if (d["status"] == "offline" and not protected and not d["keep"]) else None,
        }

    def counts(self):
        """Дешевий підсумок для «Огляду» (викликається кожні 5 с)."""
        r = self.db.one(
            "SELECT SUM(hidden=0 AND status='online') AS online, SUM(hidden=0 AND status='offline') AS offline, "
            "SUM(hidden=1) AS hidden, SUM(hidden=0 AND role='' AND manual=0 AND first_seen > ?) AS new, "
            "SUM(hidden=0) AS total FROM net_devices", (time.time() - NEW_FOR,))
        return {k: int(v or 0) for k, v in r.items()}

    def snapshot_device(self, row):
        return self._ser(row, time.time(), self.settings()["stale_days"] * DAY)

    def snapshot(self):
        now = time.time()
        cfg = self.settings()
        stale = cfg["stale_days"] * DAY
        rows = self._rows()
        devs = [self._ser(r, now, stale) for r in rows]
        visible = [d for d in devs if not d["hidden"]]
        return {
            "now": now,
            "root_id": self._root_id(),
            "devices": visible,
            "hidden": [d for d in devs if d["hidden"]],
            "ignored": self.db.q("SELECT mac, name, ts FROM net_ignored ORDER BY ts DESC"),
            "settings": cfg,
            "scan": dict(self.scan_info),
            "counts": {
                "online": sum(1 for d in visible if d["status"] == "online"),
                "offline": sum(1 for d in visible if d["status"] == "offline"),
                "new": sum(1 for d in visible if d["is_new"]),
                "hidden": len(devs) - len(visible),
                "events": self.db.one("SELECT COUNT(*) AS n FROM net_events")["n"],
                "oldest_event": self.db.one("SELECT MIN(ts) AS t FROM net_events")["t"],
            },
        }

    def events(self, limit=200, before=None, group="all", device_id=None):
        groups = {
            "offline": ("offline",),
            "online": ("online", "restored"),
            "hidden": ("hidden", "deleted"),
            "new": ("new",),
            "edits": ("added", "edited", "moved", "restored_manual", "ip_changed", "unignored"),
        }
        where, args = ["1"], []
        if device_id is not None:
            where.append("device_id=?")
            args.append(int(device_id))
        if group in groups:
            where.append("type IN (%s)" % ",".join("?" * len(groups[group])))
            args.extend(groups[group])
        if before:
            where.append("ts < ?")
            args.append(float(before))
        limit = max(1, min(int(limit), 1000))
        rows = self.db.q("SELECT * FROM net_events WHERE %s ORDER BY ts DESC, id DESC LIMIT %d"
                         % (" AND ".join(where), limit), tuple(args))
        for r in rows:
            r["params"] = json.loads(r["params"] or "{}")
            r["mac"] = (r["mac"] or "").upper()
        return rows

    # ------------------------------------------------------------------ editing
    def _validate(self, data, current=None):
        out = {}
        for key in EDIT_FIELDS:
            if key not in data:
                continue
            val = data[key]
            if key == "keep":
                out[key] = 1 if val else 0
                continue
            val = "" if val is None else str(val).strip()
            if key == "name":
                if len(val) > 60:
                    raise NetMapError("name_too_long")
            elif key == "kind":
                if val not in KINDS:
                    raise NetMapError("bad_kind")
            elif key == "vlan":
                if val not in VLANS:
                    raise NetMapError("bad_vlan")
            elif key == "link":
                if val not in LINKS:
                    raise NetMapError("bad_link")
            elif key == "band":
                if val not in BANDS:
                    raise NetMapError("bad_band")
            elif key == "ip":
                if val and not _valid_ip(val):
                    raise NetMapError("bad_ip")
            elif key == "mac":
                if val:
                    m = ns.norm_mac(val)
                    if not m:
                        raise NetMapError("bad_mac")
                    other = self.db.one("SELECT * FROM net_devices WHERE mac=?", (m,))
                    if other and (not current or other["id"] != current["id"]):
                        raise NetMapError("mac_exists", 409, name=self.display(other), id=other["id"])
                    val = m
                else:
                    val = None
            else:
                val = val[:200 if key == "note" else 60]
            out[key] = val
        return out

    def _check_parent(self, dev, parent_id):
        root = self._root_id()
        parent = self._get(int(parent_id)) if parent_id is not None else None
        if not parent or parent["hidden"] or parent["id"] == dev["id"]:
            raise NetMapError("bad_parent")
        if not self._can_parent(parent, root):
            raise NetMapError("bad_parent")
        if parent["id"] in self._descendants(dev["id"]):
            raise NetMapError("bad_parent")
        return parent

    def create_device(self, data):
        now = time.time()
        with self.lock, self.db.transaction():
            fields = self._validate(data)
            if not fields.get("name"):
                raise NetMapError("name_required")
            ip = fields.get("ip") or ""
            if ip and not fields.get("mac"):
                other = self.db.one("SELECT * FROM net_devices WHERE ip=? AND hidden=0", (ip,))
                if other:
                    raise NetMapError("ip_exists", 409, name=self.display(other), id=other["id"], ip=ip)
            kind = fields.get("kind", "unknown")
            if kind == "gateway":
                raise NetMapError("bad_kind")
            fields.setdefault("link", ns.guess_link(kind))
            static = not fields.get("mac") and not ip
            dev_id = self._insert(now, manual=1, status="static" if static else "offline",
                                  kind_locked=1, vendor_locked=1, link_locked=1, **fields)
            dev = self._get(dev_id)
            parent_id = data.get("parent_id", self._root_id())
            if parent_id is not None:
                self._check_parent(dev, parent_id)
                self._set(dev_id, parent_id=int(parent_id))
            self._event(self._get(dev_id), "added", now)
        if not static:
            self.request_scan()
        return self._get(dev_id)

    def update_device(self, dev_id, data):
        now = time.time()
        with self.lock, self.db.transaction():
            dev = self._get(dev_id)
            if not dev:
                raise NetMapError("not_found", 404)
            fields = self._validate(data, dev)
            if dev["role"] == "gateway":
                fields.pop("kind", None)
            if fields.get("kind") == "gateway":
                raise NetMapError("bad_kind")
            if fields.get("ip") and fields["ip"] != dev["ip"]:
                other = self.db.one("SELECT * FROM net_devices WHERE ip=? AND hidden=0 AND id<>?", (fields["ip"], dev_id))
                if other:
                    raise NetMapError("ip_exists", 409, name=self.display(other), id=other["id"], ip=fields["ip"])
            changed = [k for k, v in fields.items() if dev[k] != v]
            upd = {k: fields[k] for k in changed}
            if "kind" in upd:
                upd["kind_locked"] = 1
            if "vendor" in upd:
                upd["vendor_locked"] = 1
            if "link" in upd:
                upd["link_locked"] = 1
            if "mac" in upd or "ip" in upd:
                if dev["status"] == "static" and (upd.get("mac") or upd.get("ip")):
                    upd.update(status="offline", status_since=now)
                if not (upd.get("mac", dev["mac"]) or upd.get("ip", dev["ip"])):
                    upd.update(status="static", status_since=now)
            if "pos" in data:
                pos = data["pos"]
                if pos is None:
                    upd.update(pos_x=None, pos_y=None)
                else:
                    try:
                        x, y = float(pos[0]), float(pos[1])
                    except (TypeError, ValueError, IndexError):
                        raise NetMapError("bad_pos")
                    if not (-1e5 < x < 1e5 and -1e5 < y < 1e5):
                        raise NetMapError("bad_pos")
                    upd.update(pos_x=x, pos_y=y)
            moved = None
            if "parent_id" in data and data["parent_id"] != dev["parent_id"] and dev["role"] != "gateway":
                parent = self._check_parent(dev, data["parent_id"])
                old = self._get(dev["parent_id"]) if dev["parent_id"] else None
                upd["parent_id"] = parent["id"]
                moved = (self.display(old) if old else "", self.display(parent))
                # перетягнули без явного способу: на точку доступу — Wi-Fi, на комутатор — кабель
                if "link" not in data:
                    want = "wifi" if parent["kind"] == "ap" else "wired" if parent["kind"] == "switch" else None
                    if want and want != dev["link"]:
                        upd.update(link=want, link_locked=1)
            self._set(dev_id, **upd)
            new = self._get(dev_id)
            if moved:
                self._event(new, "moved", now, **{"from": moved[0], "to": moved[1]})
            if changed:
                self._event(new, "edited", now, fields=changed)
            # пристрій перестав бути комутатором/точкою — його клієнтів переносимо вище
            if "kind" in upd and new["kind"] not in PARENT_KINDS and new["role"] != "gateway":
                self._reattach_children(new)
            self._fix_parents()
        return self._get(dev_id)

    def delete_device(self, dev_id, ignore=False):
        now = time.time()
        with self.lock, self.db.transaction():
            dev = self._get(dev_id)
            if not dev:
                raise NetMapError("not_found", 404)
            if dev["role"] in ("gateway", "self"):
                raise NetMapError("protected", 403)
            self._reattach_children(dev)
            ignored = bool(ignore and dev["mac"])
            if ignored:
                self.db.x("INSERT OR REPLACE INTO net_ignored(mac, name, ts) VALUES(?,?,?)",
                          (dev["mac"], self.display(dev), now))
            self._event(dev, "deleted", now, ignored=ignored)
            self.db.x("DELETE FROM net_devices WHERE id=?", (dev_id,))
            self._fix_parents()

    def restore_device(self, dev_id):
        now = time.time()
        with self.lock, self.db.transaction():
            dev = self._get(dev_id)
            if not dev:
                raise NetMapError("not_found", 404)
            self._set(dev_id, hidden=0, keep=1, parent_id=self._root_id(), pos_x=None, pos_y=None)
            self._event(dev, "restored_manual", now)
            self._fix_parents()
        return self._get(dev_id)

    def unignore(self, mac):
        m = ns.norm_mac(mac)
        with self.lock, self.db.transaction():
            row = self.db.one("SELECT * FROM net_ignored WHERE mac=?", (m,))
            if not row:
                raise NetMapError("not_found", 404)
            self.db.x("DELETE FROM net_ignored WHERE mac=?", (m,))
            self.db.x("INSERT INTO net_events(ts, device_id, mac, name, kind, type, params) VALUES(?,?,?,?,?,?,?)",
                      (time.time(), None, m, row["name"], "", "unignored", "{}"))
        self.request_scan()

    def reset_layout(self):
        with self.lock:
            self.db.x("UPDATE net_devices SET pos_x=NULL, pos_y=NULL")
