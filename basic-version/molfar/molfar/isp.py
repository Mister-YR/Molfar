"""Визначення поточного інтернет-провайдера: зовнішній IP, ASN, назва, місто.

Опитуємо публічні сервіси по черзі, доки один не відповість. Ідентичність провайдера — номер
AS (ASN): назви в різних базах пишуться по-різному («Kyivstar», «Kyivstar PJSC»), а ASN однаковий.
"""
import json
import os
import re
import urllib.request

TIMEOUT = 8
UA = {"User-Agent": "molfar-isp/1.0", "Accept": "application/json"}
AS_RE = re.compile(r"^AS(\d+)\s*(.*)$", re.I)


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read(65536))


def _split_as(s):
    """'AS15895 Kyivstar PJSC' -> (15895, 'Kyivstar PJSC')"""
    m = AS_RE.match((s or "").strip())
    return (int(m.group(1)), m.group(2).strip()) if m else (None, (s or "").strip())


def _asn(v):
    if v in (None, ""):
        return None
    m = re.match(r"^(?:AS)?(\d+)$", str(v).strip(), re.I)
    return int(m.group(1)) if m else None


def p_ipinfo(d):
    asn, org = _split_as(d.get("org"))
    return dict(ip=d.get("ip"), asn=asn, isp=org, org=org, city=d.get("city"), country=d.get("country"))


def p_ipapi(d):
    if d.get("status") != "success":
        raise ValueError(d.get("message") or "ip-api: fail")
    asn, as_name = _split_as(d.get("as"))
    return dict(ip=d.get("query"), asn=asn, isp=d.get("isp") or as_name, org=d.get("org") or as_name,
                city=d.get("city"), country=d.get("countryCode") or d.get("country"))


def p_ifconfig(d):
    return dict(ip=d.get("ip"), asn=_asn(d.get("asn")), isp=d.get("asn_org"), org=d.get("asn_org"),
                city=d.get("city"), country=d.get("country_iso") or d.get("country"))


def p_ipwho(d):
    if d.get("success") is False:
        raise ValueError(d.get("message") or "ipwho: fail")
    c = d.get("connection") or {}
    return dict(ip=d.get("ip"), asn=_asn(c.get("asn")), isp=c.get("isp") or c.get("org"), org=c.get("org"),
                city=d.get("city"), country=d.get("country_code") or d.get("country"))


PROVIDERS = [
    ("ipinfo.io", os.environ.get("MOLFAR_ISP_URL_IPINFO", "https://ipinfo.io/json"), p_ipinfo),
    ("ip-api.com", os.environ.get("MOLFAR_ISP_URL_IPAPI",
                                  "http://ip-api.com/json/?fields=status,message,query,isp,org,as,city,country,countryCode"), p_ipapi),
    ("ifconfig.co", os.environ.get("MOLFAR_ISP_URL_IFCONFIG", "https://ifconfig.co/json"), p_ifconfig),
    ("ipwho.is", os.environ.get("MOLFAR_ISP_URL_IPWHO", "https://ipwho.is/"), p_ipwho),
]


def _clean(v, n=120):
    return str(v).strip()[:n] if v not in (None, "") else None


def lookup() -> dict:
    """Блокуючий виклик. Повертає dict(ip, asn, isp, org, city, country, source) або кидає RuntimeError."""
    errors = []
    for name, url, parse in PROVIDERS:
        try:
            r = parse(_get(url))
            if not r.get("ip") or not (r.get("asn") or r.get("isp")):
                raise ValueError("incomplete answer")
            out = {k: _clean(r.get(k)) for k in ("ip", "isp", "org", "city", "country")}
            out["asn"] = r.get("asn")
            out["source"] = name
            return out
        except Exception as e:
            errors.append(f"{name}: {str(e)[:80]}")
    raise RuntimeError("; ".join(errors) or "no providers")


def same_isp(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    if a.get("asn") and b.get("asn"):
        return a["asn"] == b["asn"]
    return (a.get("isp") or "").lower() == (b.get("isp") or "").lower()


def label(d: dict | None) -> str:
    if not d:
        return "—"
    return f"{d.get('isp') or '?'}" + (f" (AS{d['asn']})" if d.get("asn") else "")
