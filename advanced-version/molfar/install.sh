#!/usr/bin/env bash
# Встановлення Мольфара (molfar) на Orange Pi Zero 3 (Armbian / Debian / Ubuntu, arm64).
# Запуск:  sudo ./install.sh          (повторний запуск = оновлення, дані зберігаються)
#
# Змінні (необов'язкові):
#   TIMEZONE=Europe/Kyiv     часовий пояс (розклад speedtest іде за ним); TIMEZONE=keep — не змінювати
#   MOLFAR_PASSWORD=...      пароль веб-інтерфейсу при першому встановленні (за замовчуванням Molfar)
#   PORT=8080                порт веб-інтерфейсу
#   EXTRA_TOOLS=0            не ставити додаткові мережеві утиліти
#   WIFI_SCAN=auto           Wi-Fi ефір (необов'язково): auto — лише якщо є Wi-Fi адаптер, 1 — завжди, 0 — не ставити
#   IP_FORWARD=0             не вмикати пересилання IPv4 (за замовчуванням вмикається — доступ з VPN до LAN через Мольфар)
set -euo pipefail

APP_DIR=/opt/molfar
DATA_DIR=/var/lib/molfar
ENV_FILE=/etc/molfar.env
SVC_USER=molfar
PORT="${PORT:-8080}"
TIMEZONE="${TIMEZONE:-Europe/Kyiv}"
WIFI_SCAN="${WIFI_SCAN:-auto}"
EXTRA_TOOLS="${EXTRA_TOOLS:-1}"
IP_FORWARD="${IP_FORWARD:-1}"
OOKLA_VER=1.2.0
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

[ "$(id -u)" -eq 0 ] || { echo "Запустіть через sudo"; exit 1; }

# Обов'язкові пакети — без них Мольфар не працює
CORE_PKGS=(
  python3 python3-venv python3-pip   # сервіс
  iputils-ping                       # ping (втрати, RTT, джитер)
  iproute2                           # ip, ss
  wireguard-tools                    # wg, wg-quick
  nftables                           # nft — wg-quick потребує його для AllowedIPs = 0.0.0.0/0 або ::/0
  sudo curl ca-certificates          # sudo-правила, завантаження Ookla
  unzip                              # розпакування оновлень molfar.zip
  arp-scan                           # мапа мережі: пошук пристроїв у локальній мережі
)
# Мережеві утиліти для діагностики (ставляться «за можливості», помилка не зупиняє встановлення)
EXTRA_PKGS=(
  dnsutils        # dig, nslookup
  traceroute      # traceroute
  mtr-tiny        # mtr — пінг + трасування в реальному часі
  net-tools       # netstat, ifconfig, route
  ethtool         # швидкість/дуплекс Ethernet-порту
  iperf3          # тест швидкості в локальній мережі / через тунель
  tcpdump         # перехоплення пакетів
  iftop nload     # трафік по з'єднаннях / по інтерфейсу наживо
  sqlite3         # перегляд бази Мольфара
  fake-hwclock    # зберігає час між перезавантаженнями (у Zero 3 немає батарейки RTC)
  avahi-utils     # мапа мережі: імена пристроїв з mDNS (iPhone, принтери, NAS)
)

has_pkg() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }

# ---------- 1. Годинник ----------
# У Zero 3 немає RTC: після ввімкнення час може відставати на години/дні, і тоді apt відмовляє
# («Not live until …»). Тому час виправляємо ДО apt-get update.
http_date() {  # час з HTTP-заголовка Date — працює навіть коли NTP (UDP 123) заблоковано
  local url d
  for url in http://deb.debian.org/ http://www.google.com/ http://cloudflare.com/; do
    if command -v curl >/dev/null; then
      d=$(curl -sI -m 8 "$url" 2>/dev/null | tr -d '\r' | sed -n 's/^[Dd]ate: //p' | head -1)
    else
      d=$(python3 - "$url" 2>/dev/null <<'PY'
import sys, urllib.request
try:
    print(urllib.request.urlopen(urllib.request.Request(sys.argv[1], method="HEAD"), timeout=8).headers.get("Date", ""))
except Exception as e:
    print(getattr(e, "headers", {}).get("Date", "") if hasattr(e, "headers") else "")
PY
)
    fi
    [ -n "$d" ] && { echo "$d"; return 0; }
  done
  return 1
}

fix_clock() {
  echo "==> Годинник"
  if [ "$TIMEZONE" != "keep" ]; then
    if timedatectl set-timezone "$TIMEZONE" 2>/dev/null; then echo "    часовий пояс: $TIMEZONE"
    else echo "    !! невідомий часовий пояс «$TIMEZONE» — залишено $(timedatectl show -p Timezone --value 2>/dev/null)"; fi
  fi
  timedatectl set-ntp true 2>/dev/null || true
  local i
  for i in $(seq 1 15); do
    [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] && { echo "    синхронізовано через NTP: $(date '+%F %T %Z')"; return 0; }
    sleep 1
  done
  local d now web
  if d=$(http_date) && web=$(date -d "$d" +%s 2>/dev/null); then
    now=$(date +%s)
    if [ $(( now > web ? now - web : web - now )) -gt 60 ]; then
      date -s "@$web" >/dev/null && echo "    NTP не відповів — час встановлено за HTTP: $(date '+%F %T %Z')"
    else
      echo "    час правильний ($(date '+%F %T %Z')), NTP ще синхронізується"
    fi
  else
    echo "    !! не вдалося перевірити час. Якщо apt видасть «Not live until», виставте його вручну:"
    echo "       sudo date -s \"$(date '+%Y-%m-%d %H:%M')\"   ← замініть на поточний"
  fi
}

setup_timesync() {
  # systemd-timesyncd — лише якщо в системі немає іншого NTP-клієнта (chrony/ntp/ntpsec конфліктують з ним)
  local other=""
  for p in chrony ntp ntpsec openntpd; do has_pkg "$p" && other=$p; done
  if [ -n "$other" ]; then
    echo "    NTP-клієнт: $other (вже встановлено)"
  else
    has_pkg systemd-timesyncd || apt-get install -y -qq systemd-timesyncd >/dev/null
    install -d /etc/systemd/timesyncd.conf.d
    cat >/etc/systemd/timesyncd.conf.d/molfar.conf <<'EOF'
# Мольфар: запасні NTP-сервери (основні можна задати в /etc/systemd/timesyncd.conf, напр. адресу роутера)
[Time]
FallbackNTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org time.cloudflare.com time.google.com
EOF
    systemctl enable --now systemd-timesyncd >/dev/null 2>&1 || true
    systemctl restart systemd-timesyncd >/dev/null 2>&1 || true
    timedatectl set-ntp true 2>/dev/null || true
    echo "    NTP-клієнт: systemd-timesyncd"
  fi
}

fix_clock

# ---------- 2. Пакети ----------
echo "==> Оновлення списку пакетів"
if ! apt-get update -qq 2>/tmp/molfar-apt.err; then
  cat /tmp/molfar-apt.err >&2
  echo "!! apt-get update завершився з помилкою (див. вище) — пробую встановити з наявних списків"
fi
if grep -q "Not live until" /tmp/molfar-apt.err 2>/dev/null; then
  echo "!! apt скаржиться на час (Not live until) — перевірте: timedatectl; виправте час і запустіть скрипт ще раз"
fi
rm -f /tmp/molfar-apt.err

echo "==> Обов'язкові пакети: ${CORE_PKGS[*]}"
apt-get install -y -qq --no-install-recommends "${CORE_PKGS[@]}" >/dev/null
setup_timesync

if [ "$EXTRA_TOOLS" != "0" ]; then
  echo "==> Мережеві утиліти: ${EXTRA_PKGS[*]}"
  if ! apt-get install -y -qq --no-install-recommends "${EXTRA_PKGS[@]}" >/dev/null 2>&1; then
    for p in "${EXTRA_PKGS[@]}"; do   # по одному — щоб відсутній пакет не зупинив решту
      apt-get install -y -qq --no-install-recommends "$p" >/dev/null 2>&1 || echo "    !! не вдалося встановити $p"
    done
  fi
  # iperf3 не має запускатись як сервіс сам по собі
  systemctl disable --now iperf3 >/dev/null 2>&1 || true
fi

# ---------- Wi-Fi ефір (необов'язково — ніколи не зупиняє встановлення) ----------
WIFI_IF=$(for d in /sys/class/net/*; do { [ -d "$d/wireless" ] || [ -e "$d/phy80211" ]; } && basename "$d"; done 2>/dev/null | head -1 || true)
case "$WIFI_SCAN" in
  0|no|off)  WIFI_ON=0 ;;
  1|yes|on)  WIFI_ON=1 ;;
  *)         if [ -n "$WIFI_IF" ]; then WIFI_ON=1; else WIFI_ON=0; fi ;;
esac
if [ -n "$WIFI_IF" ]; then WIFI_WHY="вимкнено: WIFI_SCAN=$WIFI_SCAN"; else WIFI_WHY="немає Wi-Fi адаптера"; fi
if [ "$WIFI_ON" = 1 ]; then
  echo "==> Wi-Fi ефір: iw (адаптер: ${WIFI_IF:-не знайдено})"
  if command -v iw >/dev/null || [ -x /usr/sbin/iw ] || [ -x /sbin/iw ]; then
    echo "    iw: вже встановлено"
  elif ! apt-get install -y -qq --no-install-recommends iw >/dev/null 2>&1; then
    echo "    !! не вдалося встановити iw — Wi-Fi ефір не працюватиме, решта Мольфара — так"
  fi
else
  echo "==> Wi-Fi ефір: пропущено ($WIFI_WHY)"
fi

# ---------- 3. WireGuard ----------
echo "==> WireGuard"
# wg-quick викликає `resolvconf`, якщо в профілі є «DNS = …»
if command -v resolvconf >/dev/null; then
  echo "    resolvconf: $(command -v resolvconf)"
elif systemctl is-active --quiet systemd-resolved && command -v resolvectl >/dev/null; then
  # systemd-resolved уже керує DNS — НЕ ставимо openresolv (він перехопив би /etc/resolv.conf),
  # а вмикаємо вбудовану сумісність: resolvectl, викликаний як «resolvconf»
  ln -sf "$(command -v resolvectl)" /usr/local/sbin/resolvconf
  echo "    resolvconf → resolvectl (systemd-resolved)"
elif apt-get install -y -qq --no-install-recommends openresolv >/dev/null 2>&1; then
  echo "    resolvconf: openresolv"
else
  echo "    !! resolvconf недоступний — профілі з рядком «DNS = …» не запустяться (приберіть DNS або встановіть openresolv)"
fi
if [ -d /sys/module/wireguard ] || modprobe wireguard 2>/dev/null; then
  echo "    модуль ядра: є"
else
  echo "    !! модуль ядра wireguard не завантажується — тунелі не запрацюють (оновіть ядро: sudo apt full-upgrade)"
fi

echo "==> Користувач $SVC_USER"
id "$SVC_USER" &>/dev/null || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 750 "$DATA_DIR"

# Міграція зі старої назви (netmon), якщо вона вже стояла на пристрої
if [ -f /etc/systemd/system/netmon.service ]; then
  echo "==> Знайдено стару версію (netmon) — переношу дані й налаштування"
  systemctl disable --now netmon >/dev/null 2>&1 || true
  for ext in "" -wal -shm; do
    if [ -f "/var/lib/netmon/netmon.db$ext" ] && [ ! -f "$DATA_DIR/molfar.db$ext" ]; then
      cp -a "/var/lib/netmon/netmon.db$ext" "$DATA_DIR/molfar.db$ext"
    fi
  done
  chown -R "$SVC_USER":"$SVC_USER" "$DATA_DIR"
  if [ -f /etc/netmon.env ] && [ ! -f "$ENV_FILE" ]; then
    sed -e 's/NETMON_/MOLFAR_/g' -e 's#/var/lib/netmon/netmon.db#/var/lib/molfar/molfar.db#' /etc/netmon.env >"$ENV_FILE"
  fi
  rm -f /etc/systemd/system/netmon.service /etc/sudoers.d/netmon
  echo "    старі файли в /opt/netmon та /var/lib/netmon можна видалити вручну"
fi

echo "==> Код у $APP_DIR"
install -d "$APP_DIR"
rm -rf "$APP_DIR/molfar" "$APP_DIR/static"
cp -r "$SRC_DIR/molfar" "$SRC_DIR/static" "$SRC_DIR/requirements.txt" "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -prune -exec rm -rf {} +

echo "==> Python venv"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Ookla Speedtest CLI"
if speedtest --version 2>/dev/null | grep -q Ookla; then
  echo "    вже встановлено"
else
  case "$(uname -m)" in
    aarch64|arm64) ARCH=aarch64 ;;
    armv7l|armhf)  ARCH=armhf ;;
    x86_64)        ARCH=x86_64 ;;
    *) ARCH="" ;;
  esac
  if [ -n "$ARCH" ]; then
    TMP=$(mktemp -d)
    if curl -fsSL "https://install.speedtest.net/app/cli/ookla-speedtest-${OOKLA_VER}-linux-${ARCH}.tgz" | tar xz -C "$TMP"; then
      install -m 755 "$TMP/speedtest" /usr/local/bin/speedtest
      echo "    встановлено /usr/local/bin/speedtest"
    else
      echo "    !! не вдалося завантажити — буде використано speedtest-cli (Python)"
    fi
    rm -rf "$TMP"
  fi
fi

echo "==> Конфіг $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  PASS="${MOLFAR_PASSWORD:-Molfar}"   # пароль за замовчуванням; інший: sudo MOLFAR_PASSWORD=... ./install.sh
  cat >"$ENV_FILE" <<EOF
# Доступ до веб-інтерфейсу (порожній пароль = без авторизації)
MOLFAR_USER=admin
MOLFAR_PASSWORD=$PASS
MOLFAR_HOST=0.0.0.0
MOLFAR_PORT=$PORT
MOLFAR_DB=$DATA_DIR/molfar.db
EOF
  NEW_PASS=$PASS
fi
chown root:"$SVC_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

WG_BIN=$(command -v wg || echo /usr/bin/wg)
echo "==> WireGuard-помічник /usr/local/sbin/molfar-wg"
install -o root -g root -m 755 "$SRC_DIR/deploy/molfar-wg" /usr/local/sbin/molfar-wg
install -d -o root -g root -m 700 /etc/wireguard
if [ "$WIFI_ON" = 1 ]; then
  echo "==> Wi-Fi-помічник /usr/local/sbin/molfar-wifi"
  install -o root -g root -m 755 "$SRC_DIR/deploy/molfar-wifi" /usr/local/sbin/molfar-wifi
  WIFI_SUDO="# Wi-Fi ефір: лише сканування (iw scan / survey dump), без підключень і змін налаштувань
$SVC_USER ALL=(root) NOPASSWD: /usr/local/sbin/molfar-wifi"
else
  rm -f /usr/local/sbin/molfar-wifi   # вимкнено або немає адаптера — помічник не потрібен
  WIFI_SUDO=""
fi
cat >/etc/sudoers.d/molfar <<EOF2
# Мольфар: читання статусу та керування профілями WireGuard (лише через перевірений помічник)
$SVC_USER ALL=(root) NOPASSWD: $WG_BIN show all dump
$SVC_USER ALL=(root) NOPASSWD: /usr/local/sbin/molfar-wg
$WIFI_SUDO
EOF2
chmod 440 /etc/sudoers.d/molfar
visudo -cf /etc/sudoers.d/molfar >/dev/null

echo "==> Команда скидання пароля: molfar-reset-password"
cat >/usr/local/sbin/molfar-reset-password <<'EOF2'
#!/bin/sh
# Скидає пароль, змінений у веб-інтерфейсі, на MOLFAR_PASSWORD з /etc/molfar.env (за замовчуванням Molfar)
set -e
cd /opt/molfar
sudo -u molfar MOLFAR_DB=/var/lib/molfar/molfar.db /opt/molfar/venv/bin/python -m molfar reset-password
systemctl restart molfar
EOF2
chmod 755 /usr/local/sbin/molfar-reset-password

# ---------- Пересилання IPv4 ----------
# Потрібне, щоб клієнти WireGuard діставались хостів локальної мережі через Мольфар
# (плюс зворотний маршрут на роутері LAN: <мережа VPN> → IP Мольфара).
# Зберігається двічі: sysctl.d — при кожному завантаженні системи, drop-in сервісу — при кожному запуску Мольфара.
SYSCTL_FILE=/etc/sysctl.d/99-zz-molfar-forward.conf    # «zz» — застосовується після інших файлів sysctl.d
FWD_DROPIN=/etc/systemd/system/molfar.service.d/ip-forward.conf
if [ "$IP_FORWARD" != "0" ]; then
  echo "==> Пересилання IPv4 (net.ipv4.ip_forward=1)"
  printf '# Мольфар: доступ з VPN до локальної мережі\nnet.ipv4.ip_forward=1\n' > "$SYSCTL_FILE"
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  install -d /etc/systemd/system/molfar.service.d
  printf '[Service]\n# «+» — команда від root, хоча сервіс працює від molfar\nExecStartPre=+/usr/sbin/sysctl -w net.ipv4.ip_forward=1\n' > "$FWD_DROPIN"
else
  echo "==> Пересилання IPv4: не змінюю (IP_FORWARD=0)"
  rm -f "$SYSCTL_FILE" "$FWD_DROPIN"
fi

echo "==> systemd"
install -m 644 "$SRC_DIR/deploy/molfar.service" /etc/systemd/system/molfar.service
systemctl daemon-reload
systemctl enable molfar >/dev/null 2>&1
systemctl restart molfar

sleep 2

# ---------- Підсумок ----------
ok()   { printf '    \033[32m✓\033[0m %-22s %s\n' "$1" "$2"; }
bad()  { printf '    \033[31m✗\033[0m %-22s %s\n' "$1" "$2"; }
chk()  { if command -v "$2" >/dev/null 2>&1; then ok "$1" "$(command -v "$2")"; else bad "$1" "$3"; fi; }
echo
echo "==> Перевірка"
if systemctl is-active --quiet molfar; then ok "сервіс molfar" "працює"; else bad "сервіс molfar" "НЕ ЗАПУСТИВСЯ — journalctl -u molfar -e"; fi
if [ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null)" = "1" ]; then ok "пересилання IPv4" "увімкнено (зберігається після перезавантаження)"
elif [ "$IP_FORWARD" = "0" ]; then ok "пересилання IPv4" "вимкнено (IP_FORWARD=0)"
else bad "пересилання IPv4" "не ввімкнулося — sysctl net.ipv4.ip_forward"; fi
[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] && ok "час (NTP)" "$(date '+%F %T %Z')" || bad "час (NTP)" "ще не синхронізовано: $(date '+%F %T %Z')"
ok "часовий пояс" "$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone)"
chk "ping" ping "пакет iputils-ping"
if command -v arp-scan >/dev/null || [ -x /usr/sbin/arp-scan ]; then ok "arp-scan (мапа мережі)" "$(command -v arp-scan || echo /usr/sbin/arp-scan)"
else bad "arp-scan (мапа мережі)" "пакет arp-scan — без нього мапа шукає пристрої лише пінгом"; fi
grep -q '^AmbientCapabilities=CAP_NET_RAW' /etc/systemd/system/molfar.service && ok "мапа: CAP_NET_RAW" "arp-scan без root" || bad "мапа: CAP_NET_RAW" "немає в molfar.service"
chk "wg / wg-quick" wg-quick "пакет wireguard-tools"
if command -v nft >/dev/null || command -v iptables-restore >/dev/null; then ok "nft / iptables" "$(command -v nft || command -v iptables-restore)"
else bad "nft / iptables" "пакет nftables — потрібен для повного тунелю (0.0.0.0/0)"; fi
chk "resolvconf (DNS у WG)" resolvconf "пакет openresolv"
if [ -d /sys/module/wireguard ]; then ok "модуль wireguard" "завантажено"; else bad "модуль wireguard" "немає"; fi
if speedtest --version 2>/dev/null | grep -q Ookla; then ok "speedtest (Ookla)" "$(command -v speedtest)"
else bad "speedtest (Ookla)" "буде запасний speedtest-cli"; fi
sudo -u "$SVC_USER" sudo -n /usr/local/sbin/molfar-wg list >/dev/null 2>&1 && ok "керування WireGuard" "molfar-wg через sudo" || bad "керування WireGuard" "sudo -n molfar-wg не працює"
skip() { printf '    \033[90m–\033[0m %-22s %s\n' "$1" "$2"; }
if [ "$WIFI_ON" != 1 ]; then
  skip "Wi-Fi ефір" "не встановлено ($WIFI_WHY) — необовʼязково"
elif [ -n "$WIFI_IF" ]; then
  if command -v rfkill >/dev/null && rfkill list wifi 2>/dev/null | grep -q "Soft blocked: yes"; then
    bad "Wi-Fi ефір" "$WIFI_IF заблоковано rfkill — sudo rfkill unblock wifi"
  elif sudo -u "$SVC_USER" sudo -n /usr/local/sbin/molfar-wifi scan "$WIFI_IF" >/dev/null 2>&1; then
    ok "Wi-Fi ефір" "$WIFI_IF, сканування працює"
  else
    bad "Wi-Fi ефір" "$WIFI_IF є, але сканування не вдалося (див. вкладку «Wi-Fi ефір»)"
  fi
else
  skip "Wi-Fi ефір" "помічник встановлено, але Wi-Fi адаптера зараз немає"
fi
if [ "$EXTRA_TOOLS" != "0" ]; then
  for t in dig traceroute mtr netstat ethtool iperf3 tcpdump iftop nload sqlite3; do chk "$t" "$t" "не встановлено"; done
fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "Веб-інтерфейс:  http://$IP:$(grep ^MOLFAR_PORT "$ENV_FILE" | cut -d= -f2)"
if [ -n "${NEW_PASS:-}" ]; then
  echo "Логін: admin   Пароль: $NEW_PASS   (змініть у «Налаштування → Пароль веб-інтерфейсу»)"
fi
