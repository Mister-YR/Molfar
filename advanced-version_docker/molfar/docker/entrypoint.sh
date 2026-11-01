#!/bin/sh
# Точка входу контейнера Мольфара.
#  1. часовий пояс із TZ (розклад speedtest іде за ним);
#  2. WireGuard: прибирає «завислі» тунелі після аварійної зупинки й піднімає профілі з автозапуском;
#  3. запускає сервіс; на SIGTERM (docker stop) зупиняє його й опускає тунелі — інтерфейси
#     живуть у мережі хоста (network_mode: host), тож без цього вони лишилися б після зупинки.
set -eu

if [ "$#" -gt 0 ]; then exec "$@"; fi   # docker compose run molfar <команда>

# ---------- 1. Часовий пояс ----------
TZ="${TZ:-Europe/Kyiv}"
if [ -f "/usr/share/zoneinfo/$TZ" ]; then
  ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime
  echo "$TZ" > /etc/timezone
else
  echo "molfar: невідомий часовий пояс TZ=$TZ — лишаю UTC" >&2
  TZ=UTC; ln -sf /usr/share/zoneinfo/UTC /etc/localtime
fi
export TZ

# ---------- 2. Каталоги ----------
mkdir -p "$(dirname "${MOLFAR_DB:-/var/lib/molfar/molfar.db}")" /etc/wireguard /run/molfar-wg
chmod 700 /etc/wireguard

# ---------- 3. WireGuard ----------
profiles() {
  for f in /etc/wireguard/*.conf; do
    [ -f "$f" ] || continue
    n=$(basename "$f" .conf)
    case "$n" in *[!A-Za-z0-9_=+.-]*|"") continue ;; esac
    echo "$n"
  done
}

wg_start() {
  [ "${MOLFAR_WG_AUTOSTART:-1}" = 1 ] || { echo "molfar: автозапуск WireGuard вимкнено (MOLFAR_WG_AUTOSTART=0)"; return 0; }
  for n in $(profiles); do
    if systemctl is-active "wg-quick@$n.service" >/dev/null 2>&1; then
      # інтерфейс лишився від попереднього запуску (kill / збій) — перезапускаємо начисто
      systemctl stop "wg-quick@$n.service" >/dev/null 2>&1 || ip link del "$n" 2>/dev/null || true
    fi
    if systemctl is-enabled "wg-quick@$n.service" >/dev/null 2>&1; then
      if systemctl start "wg-quick@$n.service" >/dev/null 2>&1; then
        echo "molfar: WireGuard $n — піднято"
      else
        echo "molfar: WireGuard $n — НЕ піднявся (деталі — у вкладці WireGuard веб-інтерфейсу)" >&2
      fi
    fi
  done
}

wg_stop() {
  for n in $(profiles); do
    systemctl stop "molfar-wg-rollback-$n.timer" >/dev/null 2>&1 || true
    if systemctl is-active "wg-quick@$n.service" >/dev/null 2>&1; then
      systemctl stop "wg-quick@$n.service" >/dev/null 2>&1 && echo "molfar: WireGuard $n — опущено"
    fi
  done
}

# ---------- 3a. Пересилання IPv4 (доступ з VPN до LAN через Мольфар) ----------
# Аналог IP_FORWARD в install.sh. Мережа контейнера = мережа хоста, але /proc/sys тут лише для читання,
# тож увімкнути можна тільки на хості (або з privileged: true); тут — перевірка й підказка.
ip_forward_check() {
  [ "${MOLFAR_IP_FORWARD:-1}" = 0 ] && { echo "molfar: пересилання IPv4 не перевіряю (MOLFAR_IP_FORWARD=0)"; return 0; }
  if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)" != 1 ]; then
    if /usr/sbin/sysctl -q -w net.ipv4.ip_forward=1 2>/dev/null; then
      echo "molfar: пересилання IPv4 увімкнено (net.ipv4.ip_forward=1)"; fwd_set=1
    else
      echo "molfar: !! пересилання IPv4 вимкнене на хості — клієнти VPN не дістануться LAN через Мольфар." >&2
      echo "molfar:    на хості: echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-zz-molfar-forward.conf && sudo sysctl --system" >&2
      return 0
    fi
  fi
  # Docker (бекенд iptables) сам вмикає ip_forward і ставить політику FORWARD = DROP — тоді VPN→LAN блокується
  if nft list chain ip filter FORWARD 2>/dev/null | grep -q "policy drop"; then
    echo "molfar: !! політика FORWARD на хості = DROP (її ставить Docker) — трафік VPN→LAN блокується." >&2
    echo "molfar:    на хості: додайте \"ip-forward-no-drop\": true у /etc/docker/daemon.json," >&2
    echo "molfar:    sudo iptables -P FORWARD ACCEPT && sudo systemctl restart docker   (деталі — DOCKER.md)" >&2
  elif [ -z "${fwd_set:-}" ]; then
    echo "molfar: пересилання IPv4 — увімкнено"
  fi
}

ip_forward_check
wg_start

# ---------- 4. Сервіс ----------
cd /opt/molfar
/opt/molfar/venv/bin/python -m molfar &
PID=$!

term() {
  trap - TERM INT
  kill -TERM "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  wg_stop
  exit 0
}
trap term TERM INT

set +e
wait "$PID"
RC=$?
# сервіс завершився сам (помилка) — опускаємо тунелі й виходимо; Docker перезапустить контейнер
trap - TERM INT
wg_stop
exit "$RC"
