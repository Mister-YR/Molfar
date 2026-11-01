"""Перевірка здоров'я контейнера: веб-сервер відповідає (будь-яка HTTP-відповідь, навіть 401)."""
import os
import sys
import urllib.error
import urllib.request

host = os.environ.get("MOLFAR_HOST", "0.0.0.0")
if host in ("0.0.0.0", "::", ""):
    host = "127.0.0.1"
url = "http://%s:%s/" % (host if ":" not in host else "[%s]" % host, os.environ.get("MOLFAR_PORT", "8080"))
try:
    urllib.request.urlopen(url, timeout=5)
except urllib.error.HTTPError:
    pass  # 401 тощо — сервер живий
except Exception as e:
    print("unhealthy: %s" % e)
    sys.exit(1)
