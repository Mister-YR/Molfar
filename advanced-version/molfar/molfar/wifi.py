"""Wi-Fi ефір: сканування сусідніх мереж (iw scan + survey), модель спектра, оцінка каналів
і рекомендація оптимального каналу для роутера.

Звичайний Wi-Fi-чип не віддає «сирий» спектр, тому спектр моделюється з того, що чує адаптер:
кожна мережа — це смуга шириною 20/40/80/160 МГц з потужністю, що дорівнює її RSSI, плюс
бічні пелюстки за маскою 802.11 OFDM. Якщо драйвер підтримує survey, до оцінки додається
виміряна зайнятість каналу (busy time) — вона бачить і не-Wi-Fi завади.
"""
import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time

from . import netscan

log = logging.getLogger("molfar.wifi")

HELPER = os.environ.get("MOLFAR_WIFI_HELPER", "/usr/local/sbin/molfar-wifi")
USE_SUDO = os.environ.get("MOLFAR_WIFI_SUDO", "1") != "0" and os.geteuid() != 0
SYS_NET = os.environ.get("MOLFAR_SYS_NET", "/sys/class/net")
IW = os.environ.get("MOLFAR_IW") or shutil.which("iw") or ("/usr/sbin/iw" if os.path.exists("/usr/sbin/iw") else "iw")

NOISE_FLOOR = -95.0          # дБм — «тиша»
LEVEL_BAD = -50.0            # дБм сумарних завад — оцінка 0

CH24 = list(range(1, 14))
CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
       149, 153, 157, 161, 165]
DFS5 = set(range(52, 145))
BLOCKS80 = [[36, 40, 44, 48], [52, 56, 60, 64], [100, 104, 108, 112], [116, 120, 124, 128],
            [132, 136, 140, 144], [149, 153, 157, 161]]
BLOCKS40 = [[a, a + 4] for a in (36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157)]


class WifiError(Exception):
    def __init__(self, code, **kw):
        super().__init__(code)
        self.code, self.kw = code, kw


# ============================================================ частоти

def band_of(freq):
    if 2400 <= freq <= 2500:
        return "2.4"
    if 4900 <= freq < 5925:
        return "5"
    if 5925 <= freq <= 7125:
        return "6"
    return ""


def chan_of(freq):
    if freq == 2484:
        return 14
    if 2400 <= freq < 2484:
        return (freq - 2407) // 5
    if 5925 <= freq <= 7125:
        return (freq - 5950) // 5
    if 4900 <= freq < 5925:
        return (freq - 5000) // 5
    return 0


def freq_of(band, ch):
    if band == "2.4":
        return 2484 if ch == 14 else 2407 + 5 * ch
    if band == "6":
        return 5950 + 5 * ch
    return 5000 + 5 * ch


# ============================================================ розбір виводу iw

_BSS_RE = re.compile(r"^BSS ([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")


def _unescape_ssid(s):
    """iw екранує неASCII-байти як \\xNN — повертаємо UTF-8 (кирилиця в назві мережі)."""
    if "\\x" not in s:
        return s
    try:
        raw = re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), s.encode("latin-1", "replace"))
        return raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return s


def parse_scan(text):
    """Вивід `iw dev X scan` → список мереж (словники)."""
    out, cur, section = [], None, ""
    for raw in text.splitlines():
        m = _BSS_RE.match(raw)
        if m:
            cur = {"bssid": m.group(1).lower(), "ssid": "", "freq": 0, "signal": None, "age_ms": None,
                   "associated": "-- associated" in raw, "sec": "open", "std": [],
                   "ht_offset": "", "vht_width": None, "vht_seg1": 0, "vht_seg2": 0, "he": False,
                   "_rsn": False, "_wpa": False, "_privacy": False, "_auth": ""}
            out.append(cur)
            section = ""
            continue
        if cur is None:
            continue
        line = raw.strip()
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip("\t "))
        if indent <= 1 and ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            section = key
            if key == "freq":
                try:
                    cur["freq"] = int(float(val.split()[0]))
                except (ValueError, IndexError):
                    pass
            elif key == "signal":
                try:
                    cur["signal"] = float(val.split()[0])
                except (ValueError, IndexError):
                    pass
            elif key == "last seen" and "ago" in val:
                try:
                    cur["age_ms"] = int(val.split()[0])
                except (ValueError, IndexError):
                    pass
            elif key == "SSID":
                cur["ssid"] = _unescape_ssid(val)
            elif key == "capability":
                cur["_privacy"] = "Privacy" in val
            elif key == "RSN":
                cur["_rsn"] = True
                section = "RSN"
                if "Authentication suites" in val:
                    cur["_auth"] += val
            elif key == "WPA":
                cur["_wpa"] = True
            elif key == "HT capabilities" or key == "HT operation":
                if "HT" not in cur["std"]:
                    cur["std"].append("HT")
            elif key == "VHT capabilities" or key == "VHT operation":
                if "VHT" not in cur["std"]:
                    cur["std"].append("VHT")
            elif key.startswith("HE ") or key == "HE capabilities":
                cur["he"] = True
            elif key.startswith("EHT"):
                if "EHT" not in cur["std"]:
                    cur["std"].append("EHT")
            elif key == "DS Parameter set" and not cur["freq"]:
                pass
            continue
        # вкладені рядки « * ключ: значення»
        item = line.lstrip("* ").strip()
        k, _, v = item.partition(":")
        k, v = k.strip().lower(), v.strip()
        if section == "RSN" and k == "authentication suites":
            cur["_auth"] += " " + v
        elif section == "HT operation" and k == "secondary channel offset":
            cur["ht_offset"] = v.lower()
        elif section == "VHT operation":
            if k == "channel width":
                try:
                    cur["vht_width"] = int(v.split()[0])
                except (ValueError, IndexError):
                    pass
            elif k == "center freq segment 1":
                cur["vht_seg1"] = int(v) if v.isdigit() else 0
            elif k == "center freq segment 2":
                cur["vht_seg2"] = int(v) if v.isdigit() else 0
    res = []
    for b in out:
        if not b["freq"] or b["signal"] is None:
            continue
        _finish(b)
        res.append(b)
    return res


def _finish(b):
    f = b["freq"]
    b["band"] = band_of(f)
    b["channel"] = chan_of(f)
    if b["he"]:
        b["std"].append("HE")
    gen = "EHT" in b["std"] and "Wi-Fi 7" or b["he"] and "Wi-Fi 6" or "VHT" in b["std"] and "Wi-Fi 5" \
        or "HT" in b["std"] and "Wi-Fi 4" or "legacy"
    b["gen"] = gen
    # безпека
    auth = b["_auth"].upper()
    if b["_rsn"]:
        b["sec"] = "WPA3" if "SAE" in auth and "PSK" not in auth else "WPA2/3" if "SAE" in auth else \
            "WPA2-EAP" if "IEEE 802.1X" in auth or "802.1X" in auth else "WPA2"
    elif b["_wpa"]:
        b["sec"] = "WPA"
    elif b["_privacy"]:
        b["sec"] = "WEP"
    else:
        b["sec"] = "open"
    # ширина каналу й центр
    width, center = 20, f
    off = b["ht_offset"]
    if b["band"] in ("5", "6") and b["vht_width"] and b["vht_seg1"]:
        if b["vht_width"] == 1:
            seg1, seg2 = b["vht_seg1"], b["vht_seg2"]
            if seg2 and abs(seg2 - seg1) == 8:       # новий спосіб позначати 160 МГц
                width, center = 160, 5000 + 5 * seg2
            else:
                width, center = 80, 5000 + 5 * seg1
                if seg2:
                    width = 160                      # 80+80 — рахуємо як 160
        elif b["vht_width"] == 2:
            width, center = 160, 5000 + 5 * b["vht_seg1"]
    if width == 20 and off in ("above", "below"):
        width, center = 40, f + (10 if off == "above" else -10)
    b["width"], b["center"] = width, center
    for k in [k for k in b if k.startswith("_")] + ["ht_offset", "vht_width", "vht_seg1", "vht_seg2", "he"]:
        b.pop(k, None)
    b["vendor"] = netscan.short_vendor(netscan.clean_vendor(netscan.vendor_for(b["bssid"])))


def parse_survey(text):
    """Вивід `iw dev X survey dump` → {freq: {noise, busy_pct, in_use}}."""
    res, cur = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"frequency:\s*(\d+)(?:\.\d+)?\s*MHz(.*)", line)
        if m:
            cur = {"freq": int(m.group(1)), "in_use": "in use" in m.group(2), "noise": None,
                   "active": None, "busy": None}
            res[cur["freq"]] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"noise:\s*(-?\d+)", line)
        if m:
            cur["noise"] = int(m.group(1))
        m = re.match(r"channel active time:\s*(\d+)", line)
        if m:
            cur["active"] = int(m.group(1))
        m = re.match(r"channel busy time:\s*(\d+)", line)
        if m:
            cur["busy"] = int(m.group(1))
    for v in res.values():
        a, bz = v.pop("active"), v.pop("busy")
        v["busy_pct"] = round(min(100.0, 100.0 * bz / a), 1) if a and bz is not None else None
    return {f: v for f, v in res.items() if v["noise"] is not None or v["busy_pct"] is not None}


def parse_phy(text):
    """`iw phy phyN info` → {freq: {"ch", "disabled", "radar", "no_ir"}}."""
    res = {}
    for m in re.finditer(r"\*\s+(\d+)(?:\.\d+)?\s+MHz\s+\[(\d+)\]([^\n]*)", text):
        flags = m.group(3).lower()
        res[int(m.group(1))] = {"ch": int(m.group(2)), "disabled": "disabled" in flags,
                                "radar": "radar" in flags, "no_ir": "no ir" in flags or "passive" in flags}
    return res


def parse_link(text):
    if not text or "Not connected" in text:
        return {"connected": False}
    ssid = re.search(r"^\s*SSID:\s*(.*)$", text, re.M)
    freq = re.search(r"^\s*freq:\s*(\d+)", text, re.M)
    sig = re.search(r"^\s*signal:\s*(-?\d+)", text, re.M)
    bss = re.search(r"Connected to ([0-9a-fA-F:]{17})", text)
    f = int(freq.group(1)) if freq else 0
    return {"connected": True, "ssid": _unescape_ssid(ssid.group(1).strip()) if ssid else "",
            "bssid": bss.group(1).lower() if bss else "", "freq": f, "channel": chan_of(f) if f else None,
            "band": band_of(f) if f else "", "signal": int(sig.group(1)) if sig else None}


# ============================================================ модель спектра і оцінка

def _mw(dbm):
    return 10 ** (dbm / 10)


def _dbm(mw):
    return 10 * math.log10(max(mw, 1e-12))


def leak_db(gap):
    """Послаблення бічних пелюсток за маскою 802.11 OFDM на відстані gap МГц від краю смуги."""
    if gap <= 1:                                   # сусідня смуга впритул — вже за межами основної пелюстки
        return -20.0
    if gap <= 10:
        return -20.0 - (gap - 1) * 8 / 9           # −20 → −28 дБр
    if gap <= 20:
        return -28.0 - (gap - 10) * 1.2            # −28 → −40 дБр
    return -60.0


def interference_at(fc, bsss, half=10):
    """Сумарна потужність сусідів (мВт) у смузі [fc−half, fc+half] МГц. Повертає (мВт, к-ть на тому ж
    каналі, к-ть частково перекритих)."""
    total, same, partial = 0.0, 0, 0
    lo, hi = fc - half, fc + half
    for b in bsss:
        blo, bhi = b["center"] - b["width"] / 2, b["center"] + b["width"] / 2
        ov = max(0.0, min(hi, bhi) - max(lo, blo))
        p = _mw(b["signal"])
        if ov > 0:
            frac = ov / (2 * half)
            aligned = b["freq"] == fc or (blo <= lo and bhi >= hi and b["band"] != "2.4")
            if aligned:
                same += 1
                total += p * frac
            else:
                partial += 1
                total += p * frac * 3          # часткове перекриття гірше за спільний канал (немає CSMA)
        else:
            gap = max(lo - bhi, blo - hi)
            total += p * _mw(leak_db(gap))
    return total, same, partial


def score_from_level(level_dbm):
    return max(0.0, min(100.0, (LEVEL_BAD - level_dbm) / (LEVEL_BAD - NOISE_FLOOR) * 100))


def channel_scores(band, bsss, survey, phy):
    """Оцінка кожного 20-МГц каналу діапазону: 100 — чисто, 0 — дуже зашумлено."""
    chans = CH24 if band == "2.4" else CH5
    res = {}
    for ch in chans:
        f = freq_of(band, ch)
        pinfo = phy.get(f) if phy else None
        mw, same, partial = interference_at(f, bsss)
        level = _dbm(mw + _mw(NOISE_FLOOR))
        lscore = score_from_level(level)
        sv = survey.get(f) if survey else None
        busy = sv["busy_pct"] if sv and not sv["in_use"] else None
        score = lscore if busy is None else 0.6 * lscore + 0.4 * (100 - busy)
        res[ch] = {
            "ch": ch, "freq": f, "score": round(score), "level": round(level, 1),
            "same": same, "partial": partial, "busy": busy,
            "noise": sv["noise"] if sv else None,
            "dfs": band == "5" and (ch in DFS5 or bool(pinfo and pinfo["radar"])),
            "disabled": bool(pinfo and pinfo["disabled"]) if phy else False,
            "unknown": bool(phy) and pinfo is None,
        }
    return res


def _usable(c):
    return not c["disabled"] and not c["unknown"]


def recommend(band, scores, n_neighbors):
    """Рекомендація каналу (і ширини) для роутера."""
    if band == "2.4":
        main = [scores[c] for c in (1, 6, 11) if c in scores and _usable(scores[c])]
        if not main:
            return None
        best = max(main, key=lambda c: (c["score"], -c["same"] - c["partial"]))
        alt = None
        others = [c for c in scores.values() if _usable(c) and c["ch"] in (12, 13)]
        if others:
            o = max(others, key=lambda c: c["score"])
            if o["score"] >= best["score"] + 15:
                alt = {"ch": o["ch"], "score": o["score"]}
        width = 40 if best["score"] >= 85 and n_neighbors <= 3 else 20
        return {"ch": best["ch"], "score": best["score"], "width": width,
                "ranked": sorted(({"ch": c["ch"], "score": c["score"]} for c in main), key=lambda x: -x["score"]),
                "alt": alt}
    # 5 ГГц: найкращий блок для 80 МГц, основний канал — найчистіший у блоці
    def block_score(block):
        cs = [scores[c] for c in block]
        if not all(_usable(c) for c in cs):
            return None
        return round(0.5 * min(c["score"] for c in cs) + 0.5 * sum(c["score"] for c in cs) / len(cs))

    blocks = []
    for block in BLOCKS80:
        s = block_score(block)
        if s is None:
            continue
        prim = max((scores[c] for c in block), key=lambda c: c["score"])
        blocks.append({"chs": block, "score": s, "primary": prim["ch"], "dfs": any(scores[c]["dfs"] for c in block)})
    if not blocks:
        b40 = [{"chs": b, "score": block_score(b)} for b in BLOCKS40]
        b40 = [b for b in b40 if b["score"] is not None]
        if not b40:
            return None
        b = max(b40, key=lambda x: x["score"])
        prim = max((scores[c] for c in b["chs"]), key=lambda c: c["score"])
        return {"ch": prim["ch"], "score": b["score"], "width": 40, "block": b["chs"],
                "dfs": any(scores[c]["dfs"] for c in b["chs"]), "ranked": [], "alt": None}
    # невелика перевага каналам без DFS: роутер не чекатиме хвилину CAC і не стрибатиме від радарів
    best = max(blocks, key=lambda x: x["score"] - (5 if x["dfs"] else 0))
    alt = None
    if not best["dfs"]:
        dfs = [x for x in blocks if x["dfs"]]
        if dfs:
            d = max(dfs, key=lambda x: x["score"])
            if d["score"] >= best["score"] + 15:
                alt = {"ch": d["primary"], "score": d["score"], "block": d["chs"], "dfs": True}
    else:
        nd = [x for x in blocks if not x["dfs"]]
        if nd:
            d = max(nd, key=lambda x: x["score"])
            alt = {"ch": d["primary"], "score": d["score"], "block": d["chs"], "dfs": False}
    width = 80 if best["score"] >= 40 else 40
    return {"ch": best["primary"], "score": best["score"], "width": width, "block": best["chs"], "dfs": best["dfs"],
            "ranked": sorted(({"ch": x["primary"], "block": x["chs"], "score": x["score"], "dfs": x["dfs"]}
                              for x in blocks), key=lambda x: -x["score"]),
            "alt": alt}


def is_mine(b, mine):
    return bool(mine) and (b["ssid"] in mine or b["bssid"] in mine)


def analyze(bss, survey, phy, my_ssids, link=None):
    """Повна картина ефіру: мережі, оцінки каналів, рекомендації, «ваша мережа»."""
    mine = {s for s in my_ssids if s}
    if link and link.get("connected") and link.get("ssid") and not mine:
        mine = {link["ssid"]}
    for b in bss:
        b["mine"] = is_mine(b, mine)
    out = {"bands": {}, "mine": sorted(mine)}
    for band in ("2.4", "5"):
        nb = [b for b in bss if b["band"] == band]
        others = [b for b in nb if not b["mine"]]
        scores = channel_scores(band, others, survey, phy)
        rec = recommend(band, scores, len({b["ssid"] for b in others}))
        own = [b for b in nb if b["mine"]]
        current = None
        if own:
            o = max(own, key=lambda b: b["signal"])
            c = scores.get(o["channel"])
            current = {"ch": o["channel"], "width": o["width"], "ssid": o["ssid"], "bssid": o["bssid"],
                       "signal": o["signal"], "score": c["score"] if c else None}
            if band == "5" and o["width"] >= 80 and rec and rec.get("ranked"):
                blk = next((x for x in rec["ranked"] if o["channel"] in x["block"]), None)
                if blk:
                    current["score"] = blk["score"]
        supported = any(_usable(c) for c in scores.values()) if phy else True
        out["bands"][band] = {"networks": len(nb), "scores": list(scores.values()), "rec": rec,
                              "current": current, "supported": supported}
    out["survey"] = bool(survey)
    return out


# ============================================================ системні виклики

def wireless_ifaces():
    res = []
    try:
        names = sorted(os.listdir(SYS_NET))
    except OSError:
        return res
    for n in names:
        p = os.path.join(SYS_NET, n)
        if os.path.isdir(os.path.join(p, "wireless")) or os.path.exists(os.path.join(p, "phy80211")):
            phy = ""
            try:
                phy = os.path.basename(os.path.realpath(os.path.join(p, "phy80211")))
                with open(os.path.join(p, "phy80211", "name")) as f:
                    phy = f.read().strip() or phy
            except OSError:
                pass
            try:
                with open(os.path.join(p, "operstate")) as f:
                    oper = f.read().strip()
            except OSError:
                oper = "unknown"
            res.append({"name": n, "phy": phy, "oper": oper})
    return res


def _iw(*args, timeout=10):
    try:
        p = subprocess.run([IW, *args], capture_output=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C"})
        return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
    except (FileNotFoundError, PermissionError):
        return 127, "", "iw not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _helper(iface, timeout=90):
    cmd = (["sudo", "-n", HELPER] if USE_SUDO else [HELPER]) + ["scan", iface]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise WifiError("no_helper")
    except subprocess.TimeoutExpired:
        raise WifiError("timeout")
    try:
        data = json.loads(p.stdout.decode() or "{}")
    except ValueError:
        data = {}
    if not data.get("ok"):
        if "code" in data:
            raise WifiError(data["code"], err=data.get("err") or data.get("iface") or "")
        msg = p.stderr.decode(errors="replace").strip()
        if "password is required" in msg or "not allowed" in msg or "not in the sudoers" in msg:
            raise WifiError("no_sudo")
        raise WifiError("scan_failed", err=msg[:300] or f"rc={p.returncode}")
    return data


# ============================================================ сервіс

class Wifi:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings            # callable → dict
        self.lock = threading.Lock()
        self.scanning = False
        self.last_error = None              # {"code", "err", "ts"}
        self.last_attempt = None
        self._phy_cache = {}
        self.db.execute("""CREATE TABLE IF NOT EXISTS wifi_scans (
            id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, iface TEXT, cached INTEGER DEFAULT 0,
            n_bss INTEGER, scores TEXT, bss TEXT, survey TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_wifi_ts ON wifi_scans(ts)")

    # --- інтерфейс ---
    def pick_iface(self):
        want = self.settings()["wifi_iface"].strip()
        ifs = wireless_ifaces()
        if want:
            return next((i for i in ifs if i["name"] == want), {"name": want, "phy": "", "oper": "unknown"}), ifs
        return (ifs[0] if ifs else None), ifs

    def phy_info(self, phy):
        if not phy:
            return {}
        c = self._phy_cache.get(phy)
        if c and time.time() - c[0] < 3600:
            return c[1]
        rc, o, _ = _iw("phy", phy, "info")
        data = parse_phy(o) if rc == 0 else {}
        self._phy_cache[phy] = (time.time(), data)
        return data

    def link(self, iface):
        rc, o, _ = _iw("dev", iface, "link")
        return parse_link(o) if rc == 0 else {"connected": False}

    def regdomain(self):
        rc, o, _ = _iw("reg", "get")
        m = re.search(r"country (\w\w)", o) if rc == 0 else None
        return m.group(1) if m else ""

    # --- сканування ---
    def scan(self):
        """Блокуючий виклик (з потоку). Повертає id запису або кидає WifiError."""
        if not self.settings()["wifi_air"]:
            raise WifiError("off")
        if not self.lock.acquire(blocking=False):
            raise WifiError("busy")
        self.scanning = True
        self.last_attempt = int(time.time())
        try:
            iface, _ = self.pick_iface()
            if not iface:
                raise WifiError("no_wifi")
            try:
                data = _helper(iface["name"])
                scan_txt, survey_txt, cached = data.get("scan", ""), data.get("survey", ""), bool(data.get("cached"))
            except WifiError as e:
                if e.code not in ("no_helper", "no_sudo"):
                    raise
                # без помічника — лише кеш ядра (результати останнього сканування системи), якщо дозволено
                rc, o, err = _iw("dev", iface["name"], "scan", "dump", timeout=15)
                if rc != 0 or not o.strip():
                    raise
                scan_txt, cached = o, True
                rc, survey_txt, _ = _iw("dev", iface["name"], "survey", "dump")
                if rc != 0:
                    survey_txt = ""
            bss = parse_scan(scan_txt)
            survey = parse_survey(survey_txt)
            s = self.settings()
            phy = self.phy_info(iface["phy"])
            res = analyze([dict(b) for b in bss], survey, phy, _mine(s), self.link(iface["name"]))
            scores = {band: {str(c["ch"]): c["score"] for c in v["scores"] if not c["unknown"] and not c["disabled"]}
                      for band, v in res["bands"].items()}
            rid = self.db.insert("wifi_scans", {
                "ts": int(time.time()), "iface": iface["name"], "cached": int(cached), "n_bss": len(bss),
                "scores": json.dumps(scores, separators=(",", ":")),
                "bss": json.dumps(bss, separators=(",", ":"), ensure_ascii=False),
                "survey": json.dumps(survey, separators=(",", ":"))})
            self.last_error = None
            return rid
        except WifiError as e:
            self.last_error = {"code": e.code, "err": str(e.kw.get("err", ""))[:300], "ts": int(time.time())}
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("wifi scan")
            self.last_error = {"code": "scan_failed", "err": str(e)[:300], "ts": int(time.time())}
            raise WifiError("scan_failed", err=str(e)[:300])
        finally:
            self.scanning = False
            self.lock.release()

    # --- дані для UI ---
    def latest(self):
        return self.db.one("SELECT * FROM wifi_scans ORDER BY id DESC LIMIT 1")

    def snapshot(self):
        s = self.settings()
        iface, ifs = self.pick_iface()
        out = {
            "feature": s["wifi_air"], "available": s["wifi_air"] and bool(iface),
            "iface": iface["name"] if iface else None, "ifaces": ifs, "scanning": self.scanning,
            "helper": os.path.isfile(HELPER), "iw": bool(shutil.which(IW) or os.path.exists(IW)),
            "error": self.last_error, "enabled": s["wifi_enabled"], "every_min": s["wifi_scan_min"],
            "my_ssid": s["wifi_my_ssid"], "link": None, "reg": "", "scan": None,
        }
        if iface:
            out["link"] = self.link(iface["name"])
            out["reg"] = self.regdomain()
            out["oper"] = iface["oper"]
        row = self.latest()
        if row:
            bss = json.loads(row["bss"] or "[]")
            survey = {int(k): v for k, v in json.loads(row["survey"] or "{}").items()}
            phy = self.phy_info(iface["phy"]) if iface else {}
            res = analyze(bss, survey, phy, _mine(s), out["link"])
            bss.sort(key=lambda b: (-b["signal"]))
            out["scan"] = {"id": row["id"], "ts": row["ts"], "iface": row["iface"], "cached": bool(row["cached"]),
                           "networks": bss, **res}
        return out

    def history(self, since):
        rows = self.db.query("SELECT ts, scores FROM wifi_scans WHERE ts >= ? ORDER BY ts", (since,))
        out, acc = [], {"2.4": {}, "5": {}}
        for r in rows:
            sc = json.loads(r["scores"] or "{}")
            out.append({"ts": r["ts"], "s": sc})
            for band, chs in sc.items():
                for ch, v in chs.items():
                    a = acc.setdefault(band, {}).setdefault(ch, [0, 0, 101])
                    a[0] += v
                    a[1] += 1
                    a[2] = min(a[2], v)
        avg = {band: {ch: {"avg": round(a[0] / a[1]), "min": a[2], "n": a[1]} for ch, a in chs.items()}
               for band, chs in acc.items()}
        best = {}
        if avg.get("2.4"):
            cand = {ch: v for ch, v in avg["2.4"].items() if ch in ("1", "6", "11")}
            if cand:
                ch = max(cand, key=lambda c: (cand[c]["avg"] + cand[c]["min"]) / 2)
                best["2.4"] = {"ch": int(ch), **cand[ch]}
        if avg.get("5"):
            blocks = []
            for blk in BLOCKS80:
                vals = [avg["5"].get(str(c)) for c in blk]
                if all(vals):
                    sc = round(0.5 * min(v["avg"] for v in vals) + 0.5 * sum(v["avg"] for v in vals) / 4)
                    prim = max(blk, key=lambda c: avg["5"][str(c)]["avg"])
                    blocks.append({"ch": prim, "block": blk, "avg": sc, "dfs": prim in DFS5})
            if blocks:
                best["5"] = max(blocks, key=lambda x: x["avg"] - (5 if x["dfs"] else 0))
        return {"rows": out, "avg": avg, "best": best}

    def summary(self):
        """Коротко для плитки «Огляду» (без системних викликів — лише база)."""
        s = self.settings()
        row = self.db.one("SELECT ts, n_bss, scores FROM wifi_scans ORDER BY id DESC LIMIT 1")
        best = {}
        if row:
            sc = json.loads(row["scores"] or "{}")
            b24 = {c: v for c, v in sc.get("2.4", {}).items() if c in ("1", "6", "11")}
            if b24:
                c = max(b24, key=b24.get)
                best["2.4"] = {"ch": int(c), "score": b24[c]}
            b5 = sc.get("5", {})
            blocks = []
            for blk in BLOCKS80:
                v = [b5.get(str(c)) for c in blk]
                if all(x is not None for x in v):
                    prim = max(blk, key=lambda c: b5[str(c)])
                    blocks.append({"ch": prim, "score": round(0.5 * min(v) + 0.5 * sum(v) / 4), "dfs": prim in DFS5})
            if blocks:
                best["5"] = max(blocks, key=lambda x: x["score"] - (5 if x["dfs"] else 0))
        has = bool(wireless_ifaces())
        return {"feature": s["wifi_air"], "has_iface": has, "available": s["wifi_air"] and has,
                "last": row["ts"] if row else None,
                "networks": row["n_bss"] if row else 0, "best": best, "scanning": self.scanning,
                "enabled": s["wifi_enabled"], "error": (self.last_error or {}).get("code")}

    def cleanup(self):
        days = self.settings()["wifi_keep_days"]
        self.db.execute("DELETE FROM wifi_scans WHERE ts < ?", (int(time.time()) - days * 86400,))


def _mine(s):
    return [x.strip() for x in str(s.get("wifi_my_ssid") or "").split(",") if x.strip()]


async def scan_async(w):
    return await asyncio.to_thread(w.scan)
