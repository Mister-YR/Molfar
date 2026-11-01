# syntax=docker/dockerfile:1
# Мольфар — образ для x86_64 (linux/amd64) і ARM64 (linux/arm64).
# Звичайна збірка на самій ВМ:  docker compose up -d --build   (архітектура визначається автоматично)
# Мультиархітектурний образ:     docker/build-multiarch.sh       (див. DOCKER.md)

ARG DEBIAN_TAG=trixie-slim

# ---------- 1. Python-залежності (venv) ----------
FROM debian:${DEBIAN_TAG} AS pydeps
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-dev build-essential libffi-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/molfar/venv \
 && /opt/molfar/venv/bin/pip install --upgrade pip \
 && /opt/molfar/venv/bin/pip install -r /tmp/requirements.txt

# ---------- 2. Ookla Speedtest CLI під архітектуру цілі ----------
FROM debian:${DEBIAN_TAG} AS ookla
ARG TARGETARCH
ARG TARGETVARIANT
ARG OOKLA_VER=1.2.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN set -eu; mkdir -p /out; \
    case "${TARGETARCH}${TARGETVARIANT}" in \
      amd64) A=x86_64 ;; arm64*) A=aarch64 ;; armv7|arm) A=armhf ;; 386) A=i386 ;; *) A= ;; \
    esac; \
    if [ -n "$A" ] && curl -fsSL "https://install.speedtest.net/app/cli/ookla-speedtest-${OOKLA_VER}-linux-${A}.tgz" | tar xz -C /tmp; then \
      install -m 755 /tmp/speedtest /out/speedtest; echo "Ookla speedtest: $A"; \
    else \
      echo "!! Ookla speedtest недоступний для ${TARGETARCH}${TARGETVARIANT} — буде запасний speedtest-cli (Python)"; \
    fi

# ---------- 3. Робочий образ ----------
FROM debian:${DEBIAN_TAG}
ARG EXTRA_TOOLS=1
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/var/lib/molfar \
    TZ=Europe/Kyiv \
    MOLFAR_HOST=0.0.0.0 MOLFAR_PORT=8080 \
    MOLFAR_DB=/var/lib/molfar/molfar.db \
    MOLFAR_WG_SUDO=0 MOLFAR_WIFI_SUDO=0

# Обов'язкові пакети (як CORE_PKGS в install.sh) + iw для Wi-Fi ефіру + procps (sysctl) + tzdata
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 iputils-ping iproute2 wireguard-tools nftables arp-scan iw procps tzdata ca-certificates \
 && if [ "$EXTRA_TOOLS" != "0" ]; then \
      apt-get install -y --no-install-recommends \
        dnsutils traceroute mtr-tiny net-tools ethtool iperf3 tcpdump iftop nload sqlite3 avahi-utils curl \
      || echo "!! частину додаткових утиліт не встановлено"; \
    fi \
 && rm -rf /var/lib/apt/lists/*

COPY --from=pydeps /opt/molfar/venv /opt/molfar/venv
COPY --from=ookla /out/ /usr/local/bin/

# Код сервісу
COPY requirements.txt /opt/molfar/
COPY molfar /opt/molfar/molfar
COPY static /opt/molfar/static

# Привілейовані помічники (у контейнері сервіс працює від root з обмеженими capabilities, sudo не потрібен)
COPY deploy/molfar-wg deploy/molfar-wifi /usr/local/sbin/
# Заміна systemd для molfar-wg, обгортки sysctl/resolvconf для wg-quick
COPY docker/svc-shim /usr/local/lib/molfar/svc-shim
COPY docker/sysctl docker/resolvconf /usr/local/sbin/
COPY docker/molfar-reset-password /usr/local/bin/
COPY docker/entrypoint.sh /usr/local/bin/molfar-entrypoint
COPY docker/healthcheck.py /usr/local/lib/molfar/healthcheck.py
RUN set -eu; \
    chmod 755 /usr/local/sbin/molfar-wg /usr/local/sbin/molfar-wifi /usr/local/sbin/sysctl /usr/local/sbin/resolvconf \
              /usr/local/lib/molfar/svc-shim /usr/local/bin/molfar-reset-password /usr/local/bin/molfar-entrypoint; \
    for b in systemctl systemd-run journalctl; do ln -sf /usr/local/lib/molfar/svc-shim /usr/bin/$b; done; \
    mkdir -p /var/lib/molfar /etc/wireguard /run/molfar-wg; chmod 700 /etc/wireguard; \
    /opt/molfar/venv/bin/python -c "import fastapi, uvicorn, speedtest, tplinkrouterc6u, asusrouter"

WORKDIR /opt/molfar
VOLUME ["/var/lib/molfar", "/etc/wireguard"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
  CMD ["/opt/molfar/venv/bin/python", "-I", "/usr/local/lib/molfar/healthcheck.py"]
ENTRYPOINT ["/usr/local/bin/molfar-entrypoint"]
