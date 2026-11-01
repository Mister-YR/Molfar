"""Опитування роутера: список клієнтів (кабель / Wi-Fi за діапазонами) і час завантаження.

Підтримуються:
  • tplink — TP-Link Archer (C64, C6, AX…) через бібліотеку tplinkrouterc6u;
  • asus   — ASUS / ExpertWiFi (EBR63, RT-…) через бібліотеку asusrouter.
Бібліотеки імпортуються лише тоді, коли вибрано відповідний тип роутера.

poll() повертає:
  {"clients": [{"mac", "name", "ip", "band", "guest"}],   band: wired | 2.4 | 5 | 6 | mlo | ?
   "boot_ts": int | None,      # коли роутер увімкнувся (ASUS)
   "wan_uptime": int | None,   # скільки секунд піднятий WAN (TP-Link)
   "model": str | None}
"""
import asyncio
import time
from urllib.parse import urlparse

ROUTER_TYPES = ("none", "tplink", "asus")
TIMEOUT = 25


class RouterError(Exception):
    pass


def _split_host(host: str):
    host = (host or "").strip()
    if not host:
        raise RouterError("router address is empty")
    if "://" not in host:
        host = "http://" + host
    u = urlparse(host)
    return u.scheme, u.hostname, u.port


# ---------------- TP-Link ----------------
def _tplink_band(conn_value: str):
    """'host_5g' → ('5', False); 'guest_2g' → ('2.4', True); 'wired' → ('wired', False)."""
    v = conn_value or ""
    if v == "wired":
        return "wired", False
    guest = v.startswith(("guest_", "iot_"))
    if v.endswith("_2g"):
        return "2.4", guest
    if v.endswith("_5g"):
        return "5", guest
    if v.endswith("_6g"):
        return "6", guest
    if v == "host_mlo":
        return "mlo", False
    return "?", guest


def _poll_tplink(host, user, password):
    try:
        from tplinkrouterc6u import TplinkRouterProvider
    except ImportError as e:
        raise RouterError("tplinkrouterc6u is not installed: re-run sudo ./install.sh") from e
    scheme, hostname, port = _split_host(host)
    url = f"{scheme}://{hostname}" + (f":{port}" if port else "")
    try:
        router = TplinkRouterProvider.get_client(url, password, user or "admin",
                                                 verify_ssl=False, timeout=TIMEOUT)
        router.authorize()
    except Exception as e:
        raise RouterError(f"login failed: {e}") from e
    try:
        st = router.get_status()
    except Exception as e:
        raise RouterError(f"status failed: {e}") from e
    finally:
        # TP-Link пускає лише одного адміністратора — обов'язково виходимо
        try:
            router.logout()
        except Exception:
            pass
    clients = []
    for d in st.devices or []:
        if getattr(d, "active", True) is False:
            continue
        band, guest = _tplink_band(getattr(d.type, "value", str(d.type)))
        clients.append({"mac": d.macaddr.upper().replace("-", ":"), "name": d.hostname or "",
                        "ip": d.ipaddr, "band": band, "guest": guest})
    return {"clients": clients, "boot_ts": None, "wan_uptime": st.wan_ipv4_uptime, "model": None}


# ---------------- ASUS ----------------
class _Asus:
    """Тримає одну сесію з роутером між опитуваннями (ASUS не викидає з веб-інтерфейсу)."""

    def __init__(self):
        self.key = None
        self.router = None
        self.session = None

    async def close(self):
        if self.router:
            try:
                await self.router.async_disconnect()
            except Exception:
                pass
        if self.session:
            await self.session.close()
        self.router = self.session = self.key = None

    async def poll(self, host, user, password):
        try:
            import aiohttp
            from asusrouter import AsusData, AsusRouter
            from asusrouter.modules.connection import ConnectionState, ConnectionType
        except ImportError as e:
            raise RouterError("asusrouter is not installed: re-run sudo ./install.sh") from e
        key = (host, user, password)
        if self.key != key:
            await self.close()
        if self.router is None:
            scheme, hostname, port = _split_host(host)
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT))
            self.router = AsusRouter(hostname=hostname, username=user or "admin", password=password,
                                     port=port, use_ssl=scheme == "https", session=self.session)
            self.key = key
            try:
                await asyncio.wait_for(self.router.async_connect(), TIMEOUT)
            except Exception as e:
                await self.close()
                raise RouterError(f"login failed: {e}") from e
        try:
            raw = await asyncio.wait_for(self.router.async_get_data(AsusData.CLIENTS, force=True), TIMEOUT)
            boot = await asyncio.wait_for(self.router.async_get_data(AsusData.BOOTTIME), TIMEOUT)
        except Exception as e:
            await self.close()  # наступного разу — нова сесія
            raise RouterError(f"query failed: {e}") from e
        bands = {ConnectionType.WIRED: "wired", ConnectionType.WLAN_2G: "2.4", ConnectionType.WLAN_5G: "5",
                 ConnectionType.WLAN_5G2: "5", ConnectionType.WLAN_6G: "6"}
        clients = []
        for mac, c in (raw or {}).items():
            if getattr(c, "state", None) != ConnectionState.CONNECTED:
                continue
            conn = c.connection
            desc = c.description
            clients.append({"mac": str(mac).upper(), "name": (desc.name if desc else "") or "",
                            "ip": getattr(conn, "ip_address", None) or "",
                            "band": bands.get(getattr(conn, "type", None), "?"),
                            "guest": bool(getattr(conn, "guest", False))})
        boot_ts = None
        dt = (boot or {}).get("datetime") if isinstance(boot, dict) else None
        if dt is not None:
            boot_ts = int(dt.timestamp())
        return {"clients": clients, "boot_ts": boot_ts, "wan_uptime": None, "model": None}


_asus = _Asus()


async def poll(s: dict) -> dict:
    kind = s["router_type"]
    if kind == "tplink":
        return await asyncio.to_thread(_poll_tplink, s["router_host"], s["router_user"], s["router_password"])
    if kind == "asus":
        return await _asus.poll(s["router_host"], s["router_user"], s["router_password"])
    raise RouterError("router is not configured")


async def close():
    await _asus.close()


def summarize(data: dict) -> dict:
    """Кількість клієнтів за типами — для інтерфейсу."""
    by = {}
    for c in data["clients"]:
        k = "guest" if c["guest"] and c["band"] != "wired" else c["band"]
        by[k] = by.get(k, 0) + 1
    wifi = sum(1 for c in data["clients"] if c["band"] != "wired")
    return {"total": len(data["clients"]), "wifi": wifi, "by": by, "ts": int(time.time())}
