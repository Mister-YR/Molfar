# Molfar — network monitor for Orange Pi Zero 3

*Українська версія: [README.md](README.md)*

A lightweight service (Python + SQLite, ~40 MB RAM) that:

- **Speedtest** — every day at a chosen time runs a series (by default 5 tests, 5 minutes apart) and stores the average. Buttons: “Single test”, “Series”, “Stop”.
- **Speedtest server selection** — auto-select the nearest server, or pick a specific one from the list / search. If the chosen server is unavailable, the test can automatically retry on an auto-selected one (the result is tagged “[instead of …]”).
- **Ping** — continuously (by default every 60 s, 20 packets) pings `1.1.1.1`, `8.8.8.8` and a third target of your choice; computes loss, RTT (min/avg/max) and **jitter**.
- **History** — charts and summaries for a day / week / month / year, CSV export.
- **Telegram** — alerts on high packet loss (N checks in a row above the threshold), a recovery message with the outage duration, optionally a summary of every series and a low-speed alert. If the internet is down, messages are queued and delivered once the connection is back.
- **ISP** — detects the current internet provider (name, AS number, public IP, city) every 10 min and after each speedtest. History of ISP and IP changes, ISP in the series table, Telegram alert on change (handy with a backup uplink).
- **WireGuard** — a tab to manage profiles: add (paste / upload a `.conf`, or use the wizard with key generation), edit, bring tunnels up/down, autostart, delete. Status (handshake, traffic) is also on the Overview.
- **Two languages** — Ukrainian and English (UK/EN switch in the header or *Settings → General*). The language applies to the web UI, Telegram alerts and the event log.
- Password-protected web UI (password can be changed in Settings), mobile-friendly, light and dark themes.

## Installation

On the Orange Pi (Armbian / Debian / Ubuntu):

```bash
# copy molfar.zip to the device, e.g.: scp molfar.zip user@<ip>:~
unzip molfar.zip && cd molfar
sudo ./install.sh
```

The script prepares the system itself:

1. **Clock** — sets the `Europe/Kyiv` timezone and enables NTP; if NTP does not answer, takes the time from an HTTP `Date` header (the Zero 3 has no RTC battery, and apt refuses to work with a wrong clock).
2. **Required packages** — `python3`, `python3-venv`, `python3-pip`, `iputils-ping`, `iproute2`, `wireguard-tools`, `nftables` (needed by wg-quick for profiles with `AllowedIPs = 0.0.0.0/0`), `sudo`, `curl`, `ca-certificates`, `unzip`, plus the `systemd-timesyncd` NTP client (unless chrony/ntp is present).
3. **Network tools** — `dig`/`nslookup`, `traceroute`, `mtr`, `netstat`/`ifconfig`, `ethtool`, `iperf3`, `tcpdump`, `iftop`, `nload`, `sqlite3`, `fake-hwclock`. Best effort: a missing package does not stop the install.
4. **WireGuard** — checks the kernel module; for `DNS =` in profiles sets up `resolvconf` (via `systemd-resolved` if it is running, otherwise installs `openresolv`).
5. Ookla Speedtest CLI, the `molfar` user, the systemd service, sudo rules — and a ✓/✗ check table at the end.

Variables: `TIMEZONE=Europe/Warsaw` (or `keep`), `PORT=8081`, `EXTRA_TOOLS=0` (skip tools), `MOLFAR_PASSWORD=…`, e.g. `sudo TIMEZONE=keep EXTRA_TOOLS=0 ./install.sh`.

**Default web login: `admin` / `Molfar`.** While the default password is in use, the UI shows a warning. Change it in **Settings → Web UI password**, or set it at install time: `sudo MOLFAR_PASSWORD=MyPassword ./install.sh`. Re-running `install.sh` updates the code; data is kept.

**Forgot the password?** `sudo molfar-reset-password` restores the password from `/etc/molfar.env` (default `Molfar`).

| What | Where |
|---|---|
| Code | `/opt/molfar` |
| Database | `/var/lib/molfar/molfar.db` |
| Username, default password, port | `/etc/molfar.env` (after editing: `sudo systemctl restart molfar`) |
| Password changed in the web UI | stored in the database as a PBKDF2 hash (never in plain text) |
| Logs | `journalctl -u molfar -f` |

## Remote access over WireGuard

The service listens on `0.0.0.0:8080`, so it is reachable from any network your WireGuard routes (10.10.105.x, 10.10.106.x …).

- **WireGuard on the router, Pi in its LAN** — nothing extra needed: connect to the tunnel and open `http://<Pi LAN IP>:8080`. It is best to give the Pi a static IP (DHCP reservation on the router).
- **Pi is a WireGuard peer itself** — see `deploy/wg0.conf.example` (with `PersistentKeepalive = 25` so the Pi stays reachable behind NAT).

### WireGuard profiles in the web UI

The **WireGuard** tab manages tunnels Molfar itself uses to connect to your networks (`/etc/wireguard/<name>.conf` + `wg-quick@<name>`):

- **Add** — paste a `.conf` from your router, upload the file, or fill in the **wizard** (“Generate” creates a key pair; give the public key to the server admin).
- **Edit** — the private key is shown as `<hidden>` and kept unless you replace it. A `<name>.conf.bak` backup is written first. An active tunnel is restarted.
- **Bring up / down / Autostart / Delete** — buttons on the profile card.
- **Lock-out protection.** After bringing a tunnel up or editing an active one, the browser confirms within seconds that Molfar is still reachable. Without confirmation within 60 s (e.g. `AllowedIPs = 0.0.0.0/0` captured all traffic) the tunnel is brought down automatically.
- **Security.** The web service has no root: all changes go through the helper `/usr/local/sbin/molfar-wg`, the only command it may run via sudo. The helper validates every parameter and **rejects `PreUp/PostUp/PreDown/PostDown/SaveConfig`**, which run arbitrary commands as root. Hand-made profiles with such lines are shown read-only; edit them over SSH.

On-device check after updating:
```bash
sudo -u molfar sudo -n /usr/local/sbin/molfar-wg list    # {"ok": true, "profiles": [...]}
sudo wg show                                              # after “Bring up” in the web UI
systemctl status wg-quick@<name> --no-pager
```

Using another site's gateway as the third ping target (e.g. `10.10.106.1`) also shows the quality of the tunnel itself.

The web UI uses HTTP Basic Auth without HTTPS. That is fine inside the WireGuard tunnel; do not expose port 8080 to the internet.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) → you get a token.
2. Send your bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` — the `chat.id` in the response is your Chat ID (for a group, add the bot to the group; the id will be negative).
3. Web UI → **Settings → Telegram alerts** → paste the token and Chat ID → “Send test message” → enable alerts.

Alert logic: if loss ≥ threshold (default 10%) for **N checks in a row** (default 3), you get 🔴; when loss drops below the threshold — 🟢 with the outage duration. “Alert cooldown” prevents spam on a flapping link.

## ISP detection

- The public IP and ISP come from public services in turn: `ipinfo.io` → `ip-api.com` → `ifconfig.co` → `ipwho.is` (if one is down, the next is asked). One request every `isp_check_min` minutes (default 10; `0` — off), ~150 requests a day.
- The ISP is identified by its **autonomous system number (ASN)**, not its name: databases write “Kyivstar” vs “Kyivstar PJSC”, but the ASN is the same — so switching source never causes a false “change”.
- A possible ISP change is **confirmed by a second check 20 s later** — a short glitch does not trigger an alert.
- An IP-only change (dynamic address) is recorded in history and the log but not sent to Telegram.
- The Ookla speedtest also reports the ISP and public IP — they are stored with every test (ISP column in the series table).
- With an active WireGuard tunnel routing `AllowedIPs = 0.0.0.0/0`, the public IP is the VPN server's — the UI warns about this.

## How the metrics are calculated

- **Loss** = (sent − received) / sent for one check.
- **Jitter (ping)** = mean absolute difference between consecutive RTTs (as in RFC 3550 and Ookla).
- **Speedtest series** — average of successful tests only; failed tests are stored but excluded from the average.
- Ping is paused during a speedtest (configurable) so a saturated link does not trigger false loss alerts.
- History is aggregated: day — 5-minute buckets, week — 1 h, month — 4 h, year — 1 day. Raw data is kept for 400 days (configurable).

## API

Everything the web UI does is available over HTTP (Basic Auth):

| Method | Path | |
|---|---|---|
| GET | `/api/status` | current state, latest results, WireGuard, system |
| GET/POST | `/api/settings` | read/save settings (JSON) |
| POST | `/api/speedtest/start` | `{"mode":"single"}` or `{"mode":"series"}`; optional `"server_id"` — one-off on another server |
| POST | `/api/speedtest/stop` | stop the test/series |
| GET | `/api/speedtest/servers` | nearest servers (`?refresh=true` to refresh), `?search=Kyiv` to search |
| POST | `/api/ping/run` | run a ping check now |
| GET | `/api/history/ping?range=day\|week\|month\|year` | aggregated ping history |
| GET | `/api/history/speed?range=…` | tests and series for the period |
| GET | `/api/export/ping.csv?range=…`, `/api/export/speed.csv?range=…` | CSV |
| GET | `/api/events` | event log |
| POST | `/api/telegram/test` | test message |
| GET | `/api/isp` | current ISP + change history |
| POST | `/api/isp/check` | detect ISP now |
| GET | `/api/auth` | user and password source |
| POST | `/api/auth/password` | `{"current":"…","new":"…"}` — change password |
| GET/POST | `/api/wg/profiles` | list profiles / create `{"name","config"}` |
| GET/POST/DELETE | `/api/wg/profiles/{name}` | config (key hidden) / save `{"config"}` / delete |
| POST | `/api/wg/profiles/{name}/up\|down\|enable\|disable\|confirm` | tunnel control |
| POST | `/api/wg/keygen`, `/api/wg/pubkey` | key pair / public key from private |

## Tips

- Connect the Pi to the router **with a cable** — the Zero 3's Wi-Fi heavily understates speedtest results.
- The Ookla CLI on the Zero 3 handles several hundred Mbps; the Python engine (`speedtest-cli`, fallback) hits the CPU limit sooner — use Ookla for links >300 Mbps.
- Pick the speedtest server in **Settings → Speedtest → Choose…**: nearest servers, search by city or provider, or a manual ID. The “Test” button next to a server runs a one-off test on it without changing settings.
- SD card: writes are small (~1 row per target per check), but for long-term use prefer a quality card (A1/A2) or a longer ping interval.
- If WireGuard shows “Status unavailable”, check `sudo -u molfar sudo -n wg show all dump`.
- Technical error texts coming from `ping`, Ookla or the OS are shown as-is (in English) in both languages.

## Structure

```
molfar/
├── install.sh              install / update
├── requirements.txt
├── deploy/
│   ├── molfar.service      systemd unit
│   ├── molfar-wg           root helper for WireGuard profiles (via sudo)
│   └── wg0.conf.example    WireGuard client example
├── molfar/
│   ├── app.py              HTTP API
│   ├── auth.py             Basic Auth, password change (PBKDF2)
│   ├── i18n.py             backend texts uk/en
│   ├── isp.py              ISP detection (ASN, IP)
│   ├── monitor.py          ping loop, scheduler, alerts
│   ├── probes.py           ping / speedtest
│   ├── db.py               SQLite, default settings
│   ├── sysinfo.py          temperature, IPs, WireGuard
│   ├── wgmgr.py            molfar-wg calls
│   └── telegram.py
└── static/                 web UI (Chart.js bundled, no internet needed for the UI)
    └── i18n.js             UI dictionary uk/en
```
