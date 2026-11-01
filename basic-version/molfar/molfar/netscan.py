"""Пошук пристроїв у локальній мережі для мапи: arp-scan (CAP_NET_RAW), запасний пінг + ARP;
виробник за MAC (OUI) і здогадка про тип пристрою."""
import concurrent.futures as cf
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time

# ============================================================ виробник і тип

OUI_FILES = (
    "/usr/share/arp-scan/ieee-oui.txt",
    "/usr/share/arp-scan/mac-vendor.txt",
    "/usr/share/ieee-data/oui.txt",
)
_OUI_RE = re.compile(r"^([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})\s+(?:\(hex\)\s+)?(.+?)\s*$")
_oui = None
_oui_lock = threading.Lock()

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def norm_mac(mac):
    """'a4-c3-f0-85-2b-77' / 'A4C3F0852B77' → 'a4:c3:f0:85:2b:77' або '' якщо неправильна."""
    if not mac:
        return ""
    hexs = re.sub(r"[^0-9a-fA-F]", "", str(mac)).lower()
    if len(hexs) != 12:
        return ""
    out = ":".join(hexs[i:i + 2] for i in range(0, 12, 2))
    if out in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
        return ""
    return out


def is_private_mac(mac):
    """Біт «locally administered» — випадкова MAC телефона/ноутбука."""
    try:
        return bool(int(mac[0:2], 16) & 0x02)
    except (ValueError, TypeError):
        return False


def _load_oui():
    global _oui
    with _oui_lock:
        if _oui is not None:
            return _oui
        table = {}
        for path in OUI_FILES:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("#"):
                            continue
                        m = _OUI_RE.match(line)
                        if m:
                            key = (m.group(1) + m.group(2) + m.group(3)).lower()
                            table.setdefault(key, m.group(4).strip())
            except OSError:
                continue
        _oui = table
        return _oui


def vendor_for(mac):
    if not mac or is_private_mac(mac):
        return ""
    return _load_oui().get(mac.replace(":", "")[:6], "")


_SUFFIX_RE = re.compile(
    r"[\s,.]+(co\.?,?\s*ltd\.?|company|inc\.?|incorporated|corporation|corporate|corp\.?|llc|gmbh|ag|s\.?a\.?|"
    r"ltd\.?|limited|pty|plc|bv|technologies|technology|tech|electronics|electronic|industries|industrial|"
    r"digital|communications?|networks?|systems?|international|holdings?|group|smart|computer|software|trading|co)\b.*$", re.I)
_PREFIX_RE = re.compile(r"^(hangzhou|shenzhen|beijing|shanghai|guangzhou|zhejiang|xiamen|fujian|nanjing|chengdu)\s+", re.I)


def short_vendor(v):
    """'Hangzhou Hikvision Digital Technology Co.,Ltd.' → 'Hikvision'."""
    v = (v or "").strip()
    s = _PREFIX_RE.sub("", v)
    prev = None
    while prev != s:
        prev, s = s, _SUFFIX_RE.sub("", s).strip(" ,.")
    return s or v


def clean_vendor(v):
    v = (v or "").strip()
    if not v or v.lower().startswith("(unknown"):
        return ""
    v = re.sub(r"\s*\(DUP: \d+\)\s*$", "", v)
    return v[:80]


# --- тип пристрою ---------------------------------------------------------
# порядок важливий: перше збігання перемагає
HOST_RULES = (
    (("iphone", "android", "galaxy-s", "galaxy-a", "pixel", "redmi", "oneplus", "poco", "phone", "honor"), "phone"),
    (("ipad", "tablet", "galaxy-tab", "-tab"), "tablet"),
    (("macbook", "laptop", "notebook", "thinkpad", "ideapad", "zenbook", "vivobook", "latitude"), "laptop"),
    (("desktop-", "-pc", "pc-", "workstation", "imac", "optiplex"), "pc"),
    (("bravia", "webos", "lgwebos", "roku", "chromecast", "androidtv", "firetv", "fire-tv", "appletv", "apple-tv", "smarttv", "-tv", "tv-"), "tv"),
    (("printer", "laserjet", "deskjet", "officejet", "mfc-", "epson", "canon", "brother", "xerox", "kyocera"), "printer"),
    (("camera", "ipcam", "-cam", "cam-", "nvr", "dvr", "hikvision", "reolink"), "camera"),
    (("diskstation", "synology", "qnap", "truenas", "-nas", "nas-", "unraid"), "nas"),
    (("espressif", "esp32", "esp8266", "esp-", "tasmota", "shelly", "sonoff", "tuya", "zigbee", "wled", "plug", "bulb", "lamp"), "iot"),
    (("nest", "echo-", "alexa", "homepod", "sonos", "google-home", "speaker"), "speaker"),
    (("raspberrypi", "orangepi", "rpi-", "armbian"), "sbc"),
    (("server", "proxmox", "pve", "esxi", "hypervisor"), "server"),
)
VENDOR_RULES = (
    (("espressif", "tuya", "shelly", "itead", "sonoff", "lumi united", "signify", "philips lighting", "lifx", "broadlink"), "iot"),
    (("hikvision", "dahua", "reolink", "ezviz", "uniview", "axis communications", "hanwha"), "camera"),
    (("synology", "qnap", "asustor", "buffalo"), "nas"),
    (("seiko epson", "brother industries", "canon", "xerox", "kyocera", "lexmark", "ricoh", "pantum"), "printer"),
    (("roku", "hisense", "tcl ", "vizio", "lg electronics"), "tv"),
    (("sonos", "amazon technologies"), "speaker"),
    (("raspberry pi", "xunlong", "shenzhen xunlong", "friendlyelec", "hardkernel", "radxa"), "sbc"),
    (("ubiquiti", "tp-link", "asustek", "mikrotik", "routerboard", "netgear", "zyxel", "d-link", "tenda", "keenetic",
      "cisco", "aruba", "ruckus", "cudy", "mercusys", "edimax", "grandstream", "openmesh"), "ap"),
    (("apple",), "phone"),
    (("dell", "lenovo", "micro-star", "asrock", "giga-byte", "gigabyte", "realtek", "elitegroup"), "pc"),
    (("intel corporate", "liteon", "lite-on", "azurewave", "hon hai", "murata", "chicony", "compal"), "laptop"),
    (("xiaomi", "huawei", "oppo", "vivo mobile", "samsung", "motorola", "zte", "honor"), "phone"),
)

WIFI_KINDS = {"phone", "tablet", "laptop", "speaker", "iot"}


def guess_kind(vendor, hostname, private):
    h = (hostname or "").lower()
    v = (vendor or "").lower()
    if h:
        for keys, kind in HOST_RULES:
            if any(k in h for k in keys):
                return kind
    if v:
        for keys, kind in VENDOR_RULES:
            if any(k in v for k in keys):
                return kind
    if private:
        return "phone"
    return "unknown"


def guess_link(kind):
    return "wifi" if kind in WIFI_KINDS else "wired"


# ============================================================ сканування
log = logging.getLogger("molfar.netmap")

MAX_HOSTS = 4096          # більші підмережі обрізаються до /22 навколо Pi
PING_SWEEP_MAX = 1024
SKIP_IFACE_PREFIX = ("wg", "tun", "tap", "ppp", "docker", "veth", "br-", "lo")

_name_cache = {}          # ip -> (name, ts)
NAME_TTL = 3600


class ScanError(Exception):
    pass


def _run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def default_route(iface_override=""):
    """→ (iface, gateway_ip) з маршруту за замовчуванням з найменшою метрикою."""
    try:
        out = _run(["ip", "-4", "route", "show", "default"], 5).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise ScanError("ip route: %s" % e)
    best = None
    for line in out.splitlines():
        tok = line.split()
        info = {}
        for i, t in enumerate(tok[:-1]):
            if t in ("via", "dev", "metric"):
                info[t] = tok[i + 1]
        dev = info.get("dev", "")
        if not dev:
            continue
        if iface_override and dev != iface_override:
            continue
        if not iface_override and dev.startswith(SKIP_IFACE_PREFIX):
            continue
        metric = int(info.get("metric", "0") or 0)
        if best is None or metric < best[2]:
            best = (dev, info.get("via", ""), metric)
    if best:
        return best[0], best[1]
    if iface_override:
        return iface_override, ""
    raise ScanError("no default route")


def iface_addr(iface):
    """→ (ip, ipaddress.IPv4Interface, mac) інтерфейсу."""
    try:
        out = _run(["ip", "-4", "-o", "addr", "show", "dev", iface], 5).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise ScanError("ip addr: %s" % e)
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out)
    if not m:
        raise ScanError("no IPv4 address on %s" % iface)
    ifc = ipaddress.ip_interface(m.group(1))
    mac = ""
    try:
        with open("/sys/class/net/%s/address" % iface) as fh:
            mac = norm_mac(fh.read().strip())
    except OSError:
        pass
    return str(ifc.ip), ifc, mac


def scan_network(ifc):
    net = ifc.network
    if net.num_addresses > MAX_HOSTS:
        net = ipaddress.ip_interface("%s/22" % ifc.ip).network
    return net


def arp_scan(iface, net):
    binary = shutil.which("arp-scan") or ("/usr/sbin/arp-scan" if os.path.exists("/usr/sbin/arp-scan") else "")
    if not binary:
        raise ScanError("arp-scan not installed")
    cmd = [binary, "--interface=%s" % iface, "--plain", "--retry=2", "--ignoredups", str(net)]
    try:
        p = _run(cmd, 180)
    except subprocess.TimeoutExpired:
        raise ScanError("arp-scan timeout")
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip().splitlines()
        raise ScanError("arp-scan: %s" % (msg[-1] if msg else "exit %d" % p.returncode))
    hosts = {}
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        ip, mac = parts[0].strip(), norm_mac(parts[1])
        if not mac:
            continue
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        if mac not in hosts:
            hosts[mac] = {"ip": ip, "mac": mac, "vendor": clean_vendor(parts[2] if len(parts) > 2 else "")}
    return list(hosts.values())


def _ping(ip):
    try:
        return _run(["ping", "-c", "1", "-W", "1", "-n", "-q", ip], 4).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def neigh_table(iface):
    try:
        out = _run(["ip", "-4", "neigh", "show", "dev", iface], 5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    res = {}
    for line in out.splitlines():
        tok = line.split()
        if len(tok) < 3 or "lladdr" not in tok:
            continue
        state = tok[-1]
        mac = norm_mac(tok[tok.index("lladdr") + 1])
        if mac:
            res[tok[0]] = (mac, state)
    return res


def ping_sweep(iface, net, self_ip):
    hosts = [str(h) for h in net.hosts()][:PING_SWEEP_MAX]
    hosts = [h for h in hosts if h != self_ip]
    with cf.ThreadPoolExecutor(max_workers=64) as pool:
        alive = {ip for ip, ok in zip(hosts, pool.map(_ping, hosts)) if ok}
    out = {}
    for ip, (mac, state) in neigh_table(iface).items():
        if ip in alive or state in ("REACHABLE", "DELAY", "PROBE"):
            out.setdefault(mac, {"ip": ip, "mac": mac, "vendor": ""})
    return list(out.values())


def _short_name(name, ip):
    if not name or name == ip:
        return ""
    name = name.strip().rstrip(".")
    first = name.split(".")[0]
    if not first or first.replace("-", "").isdigit():
        return ""
    return first[:60]


def _rdns(ip):
    try:
        return _short_name(socket.gethostbyaddr(ip)[0], ip)
    except (OSError, UnicodeError):
        return ""


def _mdns(ip):
    binary = shutil.which("avahi-resolve-address")
    if not binary:
        return ""
    try:
        p = _run([binary, ip], 3)
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = p.stdout.strip().split()
    return _short_name(parts[1], ip) if len(parts) >= 2 else ""


def _lookup(ip):
    return _rdns(ip) or _mdns(ip)


def resolve_names(ips):
    now = time.time()
    out, todo = {}, []
    for ip in ips:
        hit = _name_cache.get(ip)
        if hit and now - hit[1] < NAME_TTL:
            out[ip] = hit[0]
        else:
            todo.append(ip)
    if todo:
        pool = cf.ThreadPoolExecutor(max_workers=16)
        futs = {pool.submit(_lookup, ip): ip for ip in todo}
        done, _ = cf.wait(futs, timeout=8)
        for f in done:
            ip = futs[f]
            try:
                name = f.result()
            except Exception:  # noqa: BLE001
                name = ""
            out[ip] = name
            _name_cache[ip] = (name, now)
        pool.shutdown(wait=False)
    return out


def scan(settings):
    """Одне сканування → dict з hosts, self, gateway_ip, method …"""
    t0 = time.time()
    iface, gw_ip = default_route(settings.get("iface") or "")
    self_ip, ifc, self_mac = iface_addr(iface)
    net = scan_network(ifc)
    method, note = "arp-scan", ""
    try:
        hosts = arp_scan(iface, net)
    except ScanError as e:
        log.warning("%s; fallback to ping sweep", e)
        note = str(e)
        method = "ping"
        hosts = ping_sweep(iface, net, self_ip)
    hosts = [h for h in hosts if h["mac"] != self_mac and h["ip"] != self_ip]
    names = resolve_names([h["ip"] for h in hosts] + [self_ip])
    for h in hosts:
        h["hostname"] = names.get(h["ip"], "")
    return {
        "ts": time.time(),
        "duration": round(time.time() - t0, 1),
        "method": method,
        "note": note,
        "iface": iface,
        "subnet": str(net),
        "gateway_ip": gw_ip,
        "self": {"ip": self_ip, "mac": self_mac, "hostname": names.get(self_ip, "") or socket.gethostname()},
        "hosts": hosts,
    }
