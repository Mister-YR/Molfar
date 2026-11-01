[Українська](README.md) | **English**

# Molfar

**Molfar** is a small monitoring tool for your home or office network. You install it on a single-board
computer (for example, an Orange Pi Zero 3) or in a virtual machine, and it keeps an eye on your internet
around the clock: whether it's up, how fast it is and how stable it is. Everything is shown in a web
interface you can open from your phone or computer, and Molfar notifies you about problems in Telegram on its own.

## Why

Your ISP promises 100 Mbps, but videos keep buffering? The internet "drops out sometimes", but nobody knows
exactly when? Molfar answers these questions with numbers:

- **Speed** — every night, or on a schedule, it runs a series of speed tests and calculates the average download and upload speed.
- **Ping and jitter** — it constantly pings `1.1.1.1`, `8.8.8.8` and your own addresses: latency, packet loss, stability.
- **History** — charts for the day, week, month and year, plus CSV export (handy for showing to your ISP).
- **Notifications** — in Telegram, when the connection goes down and when it comes back.
- **ISP** — who is currently providing your internet and whether your external IP address has changed.
- **Network map** — which devices are on your network and when new ones appear.
- **WireGuard** — manage VPN tunnels so you can reach Molfar (and your home network) from anywhere.

Molfar is written in Python, stores its data in a single SQLite file, and the interface is available in Ukrainian and English.

## What's in the repository

| Folder | What it is | Who it's for |
|---|---|---|
| [`basic-vesrion/`](basic-version/) | **Basic version**: speed test, ping, history, Telegram, ISP, network map, WireGuard. The lightest one, with minimal dependencies. | Those who just want to keep track of their internet quality. |
| [`advanced-version/`](advanced-version/) | **Advanced version** — everything from the basic one, plus **Incidents** (outage log: internet loss, reboots, router unavailability), **Wi-Fi airspace** (neighbouring networks, channel congestion, advice on which channel to pick, DFS markers) and polling of **TP-Link / ASUS** routers (not tested yet). | Those who want to see more than "is the internet up or not" and tune their Wi-Fi. |
| [`advanced-version_docker/`](advanced-version_docker/) | **Advanced version in Docker**: the same code, plus a `Dockerfile` and `docker-compose.yml` for running on a virtual machine or server, **x86_64 or ARM64**. | Those who want to run Molfar on a VM, NUC, NAS or server rather than an Orange Pi. |

Each folder contains:

- `molfar/` — the project itself, ready to install:
  - `molfar/molfar/` — the backend (Python, FastAPI): checks, scheduling, database, notifications;
  - `molfar/static/` — the web interface (HTML, CSS, JavaScript, Chart.js charts);
  - `molfar/deploy/` — system files: systemd service, WireGuard and Wi-Fi helpers, a sample VPN profile;
  - `molfar/tests/` — automated tests;
  - `molfar/install.sh` — one-command installation.
- `README.md` / `README.en.md` — full description of the version (in Ukrainian / English).
- `molfar.zip` — the same version as a single archive, so it's easy to copy to the device (the Docker folder has no archive).

## Getting started

**On an Orange Pi or another single-board computer running Debian / Ubuntu / Armbian:**

```bash
cd advanced-version/molfar      # or basic-vesrion/molfar
sudo ./install.sh
```

**On a virtual machine or server with Docker:**

```bash
cd advanced-version_docker/molfar
cp .env.example .env            # password, port, time zone
docker compose up -d --build
```

Then open `http://<device-IP>:8080` in your browser (login `admin`, password `Molfar` — change it
in the settings). For details, see the README of the corresponding version and
[`DOCKER.md`](advanced-version_docker/molfar/DOCKER.md) for Docker.
