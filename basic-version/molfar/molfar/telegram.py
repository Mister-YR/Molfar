"""Мінімальний клієнт Telegram Bot API (без зовнішніх залежностей)."""
import json
import urllib.error
import urllib.request


def send(token: str, chat_id: str, text: str, timeout: float = 10):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            desc = json.loads(e.read()).get("description", str(e))
        except Exception:
            desc = str(e)
        raise RuntimeError(f"Telegram: {desc}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram: {resp.get('description', 'error')}")
