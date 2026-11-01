"""HTTP Basic Auth з паролем, який можна змінити з веб-інтерфейсу.

Порядок перевірки:
  1. якщо в БД є хеш (state.auth_hash) — пароль перевіряється по ньому;
  2. інакше — пароль з /etc/molfar.env (MOLFAR_PASSWORD, за замовчуванням "Molfar");
  3. якщо й там порожньо — авторизація вимкнена.
Скидання на пароль з env: sudo molfar-reset-password
"""
import base64
import hashlib
import hmac
import os
import secrets

ITERATIONS = 120_000
DEFAULT_PASSWORD = "Molfar"


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_hash(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


class Auth:
    def __init__(self, db):
        self.db = db
        self.user = os.environ.get("MOLFAR_USER", "admin")
        self.env_pass = os.environ.get("MOLFAR_PASSWORD", "")
        self.hash = db.get_state("auth_hash")
        self._ok_cache: set[str] = set()  # sha256 заголовків, що вже пройшли перевірку

    @property
    def source(self) -> str:
        return "db" if self.hash else ("env" if self.env_pass else "none")

    @property
    def enabled(self) -> bool:
        return self.source != "none"

    @property
    def is_default(self) -> bool:
        return self.source == "env" and self.env_pass == DEFAULT_PASSWORD

    def verify(self, user: str, pw: str) -> bool:
        # порівнюємо байти: compare_digest не приймає не-ASCII рядки (кирилицю в паролі)
        if not secrets.compare_digest(user.encode(), self.user.encode()):
            return False
        if self.hash:
            return verify_hash(pw, self.hash)
        return secrets.compare_digest(pw.encode(), self.env_pass.encode())

    def check_header(self, header: str) -> bool:
        if not self.enabled:
            return True
        if not header.startswith("Basic "):
            return False
        key = hashlib.sha256(header.encode()).hexdigest()
        if key in self._ok_cache:
            return True
        try:
            u, _, p = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            return False
        try:
            ok = self.verify(u, p)
        except Exception:
            ok = False
        if ok:  # PBKDF2 повільний — рахуємо раз, далі з кешу
            if len(self._ok_cache) > 50:
                self._ok_cache.clear()
            self._ok_cache.add(key)
            return True
        return False

    def set_password(self, new: str):
        self.hash = hash_password(new)
        self.db.set_state("auth_hash", self.hash)
        self._ok_cache.clear()
