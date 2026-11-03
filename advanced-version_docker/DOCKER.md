# Мольфар у Docker (ВМ / сервер x86_64 і ARM64)

Той самий сервіс, що й на Orange Pi, але в контейнері: для віртуальної машини, міні-ПК, NAS чи
ARM-сервера. Архітектура вибирається автоматично — образ збирається під процесор машини, на якій
запускається `docker compose` (`linux/amd64` або `linux/arm64`).

## Швидкий старт

Потрібні: Linux, Docker Engine 24+ з плагіном Compose v2.24+.

```bash
# скопіюйте папку molfar на ВМ, напр.: scp -r molfar user@<ip>:~
cd ~/molfar
cp .env.example .env          # пароль, порт, часовий пояс
nano .env
docker compose up -d --build
docker compose logs -f        # Ctrl+C — вийти з журналу
```

Веб-інтерфейс: `http://<IP ВМ>:8080`, логін `admin`, пароль — `MOLFAR_PASSWORD` з `.env`
(за замовчуванням `Molfar`).

Дані лежать поруч із `docker-compose.yml`:

| Папка         | Що там                                                        |
|---------------|---------------------------------------------------------------|
| `./data`      | `molfar.db` — історія, налаштування, мапа мережі              |
| `./wireguard` | профілі WireGuard, створені у веб-інтерфейсі (`*.conf`)       |

## Керування

```bash
docker compose ps                                  # стан (healthy / unhealthy)
docker compose restart molfar                      # перезапуск
docker compose down                                # зупинити (дані зберігаються)
docker compose up -d --build                       # оновлення: спершу замініть файли новою версією (data/ і wireguard/ не чіпайте)
docker compose exec molfar molfar-reset-password && docker compose restart molfar   # скинути пароль
docker compose exec molfar sqlite3 /var/lib/molfar/molfar.db   # база
docker compose exec molfar mtr 1.1.1.1             # діагностика: dig, mtr, traceroute, iperf3, tcpdump, nload…
```

## Вимоги до ВМ

- **Мережа ВМ — «міст» (bridged), не NAT.** Мольфар працює в мережі хоста (`network_mode: host`),
  тож бачить ту мережу, що й ВМ. Щоб мапа мережі знаходила пристрої вашої LAN, ВМ має бути в тій
  самій мережі (Proxmox: `vmbr0`; VirtualBox/VMware: «Мережевий міст»; Hyper-V: зовнішній комутатор).
- **WireGuard** — потрібен модуль ядра хоста (є в усіх ядрах 5.6+; перевірка: `sudo modprobe wireguard`).
  Для повного тунелю (`AllowedIPs = 0.0.0.0/0`) один раз виконайте на хості:
  ```bash
  echo 'net.ipv4.conf.all.src_valid_mark=1' | sudo tee /etc/sysctl.d/90-molfar.conf && sudo sysctl --system
  ```
- **Час** — контейнер бере годинник хоста, тож на ВМ має працювати NTP (`timedatectl`).
  Часовий пояс розкладу задається `TZ` у `.env`.
- **Docker Desktop (Windows/macOS) не підходить** — там `network_mode: host` не дає доступу до вашої LAN.

## Доступ з VPN до локальної мережі (пересилання IPv4)

На Orange Pi `install.sh` вмикає `net.ipv4.ip_forward=1`, щоб клієнти WireGuard діставались не лише
Мольфара, а й інших хостів LAN через нього. У контейнері `/proc/sys` лише для читання, тож це
налаштовується **на хості** (ВМ або NUC), один раз:

```bash
# 1. пересилання — постійно
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-zz-molfar-forward.conf && sudo sysctl --system

# 2. Docker (бекенд iptables) ставить політику FORWARD = DROP і цим блокує VPN → LAN. Вимкнути:
sudo nano /etc/docker/daemon.json        # додати:  { "ip-forward-no-drop": true }
sudo iptables -P FORWARD ACCEPT          # зняти вже поставлений DROP
sudo systemctl restart docker
```

Плюс, як і на Orange Pi: на роутері LAN — зворотний маршрут `<мережа VPN> → IP хоста Мольфара`,
а власну LAN Мольфара не додавайте в його `AllowedIPs`. Стан видно в журналі при старті:
`docker compose logs molfar | grep пересилання`. Якщо роутером Мольфар бути не повинен —
`MOLFAR_IP_FORWARD=0` у `.env` (тоді перевірка й підказки вимикаються).

Якщо в `daemon.json` уже є інші параметри, додайте `"ip-forward-no-drop": true` до наявного об'єкта,
а не другим об'єктом. Політика ACCEPT робить хост маршрутизатором між усіма його інтерфейсами —
для окремої ВМ під Мольфар це нормально; на спільному сервері краще дозволити лише потрібне:
`sudo iptables -I DOCKER-USER -i wg+ -j ACCEPT && sudo iptables -I DOCKER-USER -o wg+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT`
(ці правила не зберігаються після перезавантаження — додайте їх у `iptables-persistent` чи аналог).

## Чим відрізняється від встановлення на Orange Pi

| Функція               | У контейнері                                                                                       |
|-----------------------|----------------------------------------------------------------------------------------------------|
| Ping, speedtest, Telegram, історія, інциденти, провайдер | так само                                                       |
| Speedtest             | Ookla CLI під архітектуру образу; якщо недоступний — запасний `speedtest-cli`                     |
| Мапа мережі           | `arp-scan` працює; імена mDNS — лише якщо розкоментувати сокет D-Bus хоста в `docker-compose.yml` (потрібен `avahi-daemon` на хості) |
| WireGuard             | Керування з веб-інтерфейсу працює повністю (підняти/опустити, автозапуск, відкат через 60 с). Тунелі створюються в мережі хоста; рядок `DNS = …` у профілі ігнорується. Профілі «з автозапуском» піднімаються при старті контейнера, при `docker compose down` — опускаються |
| Wi-Fi ефір            | Працює, якщо адаптер бачить машина, де запущено Docker: на «залізі» (напр. NUC) — одразу, у ВМ — після передачі адаптера (PCIe / USB passthrough). Перевірка: `docker compose exec molfar iw dev`. DFS-канали визначаються за прапорцями адаптера |
| Температура CPU       | На ВМ зазвичай недоступна                                                                          |
| Діагностика           | Працює повністю: `traceroute`, `nslookup`, `telnet`, `iperf3` уже в образі. iperf3-сервер (перемикач у вкладці) слухає порт прямо на хості (мережа хоста); якщо на ВМ увімкнено фаєрвол — `sudo ufw allow 5201/tcp` |
| Пересилання IPv4      | Не вмикається з контейнера — налаштуйте на хості (розділ вище); при старті контейнер перевіряє й підказує |
| systemd, sudo         | Не потрібні: сервіс працює від root усередині контейнера з можливостями лише `NET_RAW` і `NET_ADMIN` |

Профілі WireGuard хоста (`/etc/wireguard` на самій ВМ) Мольфар у контейнері не бачить і не чіпає —
у нього власна папка `./wireguard`. Не давайте профілям однакових назв з інтерфейсами хоста (напр. `wg0`).

## Перенесення даних з Orange Pi

```bash
# на Orange Pi
sudo systemctl stop molfar
sudo tar czf molfar-data.tgz -C /var/lib/molfar molfar.db -C /etc wireguard
# на ВМ, у папці molfar (контейнер зупинено: docker compose down)
mkdir -p data && tar xzf molfar-data.tgz -C data molfar.db
tar xzf molfar-data.tgz wireguard        # профілі WG — за потреби
docker compose up -d
```

Автозапуск профілів WireGuard після перенесення увімкніть заново у вкладці WireGuard.

## Готовий образ для кількох архітектур

Якщо ВМ без доступу до інтернету або не хочеться збирати на кожній машині — зберіть образ один раз
на будь-якій машині з Docker (емуляція іншої архітектури через QEMU налаштовується автоматично):

```bash
# у ваш реєстр (Docker Hub, GHCR, Gitea…): один тег для amd64 і arm64
IMAGE=registry.example.com/molfar:latest ./docker/build-multiarch.sh
# на ВМ: у .env  MOLFAR_IMAGE=registry.example.com/molfar:latest
docker compose pull && docker compose up -d

# або без реєстру — у файли molfar-amd64.tar і molfar-arm64.tar
./docker/build-multiarch.sh --tar
# на ВМ (скопіюйте відповідний .tar):
docker load -i molfar-arm64.tar && docker compose up -d --no-build
```

Підтримуються `linux/amd64` і `linux/arm64`. 32-бітний ARM (`linux/arm/v7`) можна спробувати
(`PLATFORMS=linux/arm/v7`), але частина Python-залежностей там збирається з вихідних кодів і може не зібратися.

## Файли

```
Dockerfile               багатоетапна збірка: venv → Ookla speedtest під архітектуру → робочий образ (Debian trixie-slim)
docker-compose.yml       сервіс, мережа хоста, можливості, томи, healthcheck, ротація журналів
.env.example             налаштування (пароль, порт, TZ, …)
docker/entrypoint.sh     часовий пояс, перевірка пересилання IPv4, автозапуск/зупинка тунелів WireGuard, запуск сервісу
docker/svc-shim          заміна systemctl / systemd-run / journalctl для помічника molfar-wg
docker/sysctl            обгортка для wg-quick (у контейнері /proc/sys лише для читання)
docker/resolvconf        заглушка для рядка DNS у профілях WireGuard
docker/healthcheck.py    перевірка, що веб-сервер відповідає
docker/molfar-reset-password   скидання пароля веб-інтерфейсу
docker/build-multiarch.sh      збірка образу amd64 + arm64 (buildx)
```

Код сервісу (`molfar/`, `static/`, `deploy/molfar-wg`, `deploy/molfar-wifi`) не змінювався —
той самий, що встановлює `install.sh` на Orange Pi.
