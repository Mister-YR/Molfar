"""SQLite-сховище: налаштування, результати пінгу, speedtest, журнал подій."""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("MOLFAR_DB", "/var/lib/molfar/molfar.db")

# Значення за замовчуванням. Тип значення тут визначає, до якого типу
# приводиться те, що приходить з веб-інтерфейсу.
DEFAULTS = {
    "device_name": "Orange Pi",
    "language": "uk",                   # uk | en — інтерфейс, Telegram, журнал
    # Speedtest
    "speedtest_enabled": True,
    "speedtest_time": "03:00",          # локальний час запуску серії
    "speedtest_runs": 5,                # тестів у серії
    "speedtest_interval_min": 5,        # інтервал між тестами, хв
    "speedtest_engine": "auto",         # auto | ookla | python
    "speedtest_server_id": "",          # порожньо = автовибір
    "speedtest_server_name": "",        # підпис вибраного сервера для інтерфейсу
    "speedtest_fallback_auto": True,    # якщо вибраний сервер недоступний — повторити з автовибором
    # Пінг
    "ping_enabled": True,
    "ping_targets": ["1.1.1.1", "8.8.8.8", "10.10.105.1"],
    "ping_interval_sec": 60,
    "ping_count": 20,                   # пакетів на одну перевірку
    "ping_pause_during_speedtest": True,
    # Додаткові хости: [{"host", "name", "alert"}], до 37 (разом з основними — 40).
    # Пінгуються окремим циклом і на «Огляді» не показуються.
    "ping_extra": [],
    "ping_extra_interval_sec": 300,
    "ping_extra_count": 10,
    # Сповіщення
    "alert_enabled": False,
    "alert_loss_pct": 10.0,             # поріг втрат, %
    "alert_consecutive": 3,             # скільки перевірок поспіль
    "alert_cooldown_min": 30,           # мінімум між алертами
    "alert_speed_min_mbps": 0.0,        # 0 = не перевіряти
    "tg_series_report": False,          # надсилати підсумок кожної серії
    # Провайдер
    "isp_check_min": 10,                # як часто визначати провайдера, хв (0 = вимкнено)
    "alert_isp_change": True,           # сповіщення в Telegram при зміні провайдера
    # Інциденти
    "inc_inet_confirm": 2,              # скільки перевірок пінгу поспіль без відповіді = «немає інтернету»
    "alert_inc_internet": True,         # Telegram: інтернет відновлено (скільки не було)
    "alert_inc_molfar": True,           # Telegram: Мольфар знову онлайн (перезавантаження, живлення)
    "alert_inc_wifi": True,             # Telegram: масовий відвал Wi-Fi-клієнтів
    "alert_inc_router": True,           # Telegram: роутер недоступний / перезавантажився
    # Роутер (джерело Wi-Fi-клієнтів)
    "router_type": "none",              # none | tplink | asus
    "router_host": "",                  # 192.168.0.1 або https://192.168.50.1
    "router_user": "admin",
    "router_password": "",
    "router_poll_min": 2,               # як часто опитувати роутер, хв
    "wifi_drop_min": 2,                 # відвал = зникли ВСІ Wi-Fi-клієнти, а до цього їх було щонайменше стільки
    "tg_token": "",
    "tg_chat_id": "",
    # Мапа мережі
    "net_enabled": True,                # сканувати локальну мережу
    "net_scan_min": 5,                  # інтервал сканування, хв
    "net_offline_min": 15,              # без відповіді довше — «офлайн», хв
    "net_stale_days": 7,                # офлайн довше — прибрати з мапи (крім роутера і Мольфара)
    "net_log_days": 400,                # скільки зберігати журнал мапи, днів
    "net_iface": "",                    # порожньо — інтерфейс маршруту за замовчуванням
    "net_alert_new": False,             # Telegram: новий пристрій у мережі
    # Wi-Fi ефір (необовʼязкова функція: без Wi-Fi адаптера вкладка просто не показується)
    "wifi_air": True,                   # функція увімкнена (вкладка, плитка, сканування)
    "wifi_enabled": False,              # сканувати ефір за розкладом (кнопка «Сканувати» працює завжди)
    "wifi_scan_min": 30,                # інтервал сканування за розкладом, хв
    "wifi_iface": "",                   # порожньо — перший бездротовий інтерфейс (wlan0)
    "wifi_my_ssid": "",                 # свої мережі через кому — не рахуються завадами, підсвічуються
    "wifi_keep_days": 30,               # скільки зберігати історію сканувань, днів
    # Обслуговування
    "retention_days": 400,
    # Інтерфейс
    "overview_order": [],               # порядок плиток на «Огляді» (порожньо — як за замовчуванням)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state    (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ping_results (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    target TEXT NOT NULL,
    sent INTEGER, received INTEGER, loss REAL,
    rtt_min REAL, rtt_avg REAL, rtt_max REAL, jitter REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_ping_ts ON ping_results(ts, target);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY,
    started_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    trigger TEXT NOT NULL,              -- schedule | manual
    status TEXT NOT NULL,               -- running | done | stopped | error
    runs_planned INTEGER, runs_ok INTEGER DEFAULT 0,
    avg_download REAL, avg_upload REAL, avg_ping REAL, avg_jitter REAL
);
CREATE INDEX IF NOT EXISTS ix_series_ts ON series(started_ts);

CREATE TABLE IF NOT EXISTS speedtests (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    series_id INTEGER REFERENCES series(id),
    download REAL, upload REAL, ping REAL, jitter REAL, packet_loss REAL,
    server TEXT, engine TEXT, url TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_speed_ts ON speedtests(ts);

CREATE TABLE IF NOT EXISTS isp_history (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,                 -- first | isp | ip
    ip TEXT, asn INTEGER, isp TEXT, org TEXT, city TEXT, country TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS ix_isp_ts ON isp_history(ts);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                 -- internet | molfar | wifi | router
    started_ts INTEGER NOT NULL,
    ended_ts INTEGER,                   -- NULL = триває
    details TEXT                        -- JSON
);
CREATE INDEX IF NOT EXISTS ix_inc_ts ON incidents(started_ts);
CREATE INDEX IF NOT EXISTS ix_inc_end ON incidents(ended_ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    level TEXT NOT NULL,                -- info | warn | error
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


class DB:
    def __init__(self, path: str = DB_PATH):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        """Нові колонки в існуючих базах (оновлення без втрати даних)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(speedtests)")}
        for col in ("isp", "external_ip"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE speedtests ADD COLUMN {col} TEXT")

    # --- базові операції ---
    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table, row: dict):
        cols = ",".join(row)
        qs = ",".join("?" * len(row))
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))

    # --- налаштування ---
    def get_settings(self) -> dict:
        s = json.loads(json.dumps(DEFAULTS))  # глибока копія
        for r in self.query("SELECT key, value FROM settings"):
            if r["key"] in DEFAULTS:
                s[r["key"]] = json.loads(r["value"])
        return s

    def set_settings(self, upd: dict):
        with self.lock:
            for k, v in upd.items():
                self.conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v)),
                )
            self.conn.commit()

    # --- внутрішній стан (не показується в налаштуваннях) ---
    def get_state(self, key, default=None):
        r = self.one("SELECT value FROM state WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_state(self, key, value):
        self.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # --- журнал ---
    def log(self, level: str, message: str):
        self.execute("INSERT INTO events(ts, level, message) VALUES(?,?,?)",
                     (int(time.time()), level, message))

    def cleanup(self, retention_days: int):
        cutoff = int(time.time()) - retention_days * 86400
        with self.lock:
            self.conn.execute("DELETE FROM ping_results WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM speedtests WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM series WHERE started_ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM incidents WHERE ended_ts IS NOT NULL AND ended_ts < ?", (cutoff,))
            self.conn.commit()
