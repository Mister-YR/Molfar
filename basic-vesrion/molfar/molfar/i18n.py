"""Тексти бекенду (Telegram, журнал подій, помилки API) українською та англійською."""

LANGS = ("uk", "en")

M = {
    # --- службові ---
    "service_started": ("Сервіс запущено (speedtest: {engine})", "Service started (speedtest: {engine})"),
    "not_found": ("не знайдено", "not found"),
    # --- тривалість ---
    "dur_hm": ("{h} год {m} хв", "{h} h {m} min"),
    "dur_m": ("{m} хв", "{m} min"),
    "dur_s": ("{s} с", "{s} s"),
    # --- алерти ---
    "alert_title": ("🔴 <b>{name}</b>: високі втрати пакетів", "🔴 <b>{name}</b>: high packet loss"),
    "alert_line": ("• <code>{target}</code> — втрати {loss:.0f}%{extra}", "• <code>{target}</code> — loss {loss:.0f}%{extra}"),
    "alert_extra": (", RTT {rtt} мс, джитер {jitter} мс", ", RTT {rtt} ms, jitter {jitter} ms"),
    "alert_rule": ("Поріг {thr:g}%, {need} перевірки поспіль", "Threshold {thr:g}%, {need} checks in a row"),
    "recover_title": ("🟢 <b>{name}</b>: зв'язок відновлено", "🟢 <b>{name}</b>: connection restored"),
    "recover_line": ("• <code>{target}</code> — проблема тривала {dur}, макс. втрати {mx:.0f}%",
                     "• <code>{target}</code> — issue lasted {dur}, max loss {mx:.0f}%"),
    "tg_delayed": ("⏳ <i>затримане, від {time}</i>", "⏳ <i>delayed, from {time}</i>"),
    "tg_test": ("✅ <b>{name}</b>: тестове повідомлення Мольфара", "✅ <b>{name}</b>: Molfar test message"),
    # --- speedtest ---
    "st_single": ("одиночний тест", "single test"),
    "st_series": ("серія {n}×", "series of {n}"),
    "st_sched": ("за розкладом", "scheduled"),
    "st_manual": ("вручну", "manual"),
    "st_server": (", сервер {name}", ", server {name}"),
    "st_started": ("Speedtest: {kind} запущено ({how}{srv})", "Speedtest: {kind} started ({how}{srv})"),
    "st_error": ("Speedtest #{n}: {err}", "Speedtest #{n}: {err}"),
    "st_crash": ("Серія #{sid} впала: {err}", "Series #{sid} crashed: {err}"),
    "st_fallback": ("Speedtest #{n}: сервер {name} недоступний ({err}) — повтор з автовибором",
                    "Speedtest #{n}: server {name} unavailable ({err}) — retrying with auto-select"),
    "st_instead": ("[замість {name}]", "[instead of {name}]"),
    "st_summary": ("↓ {d} Мбіт/с, ↑ {u} Мбіт/с, пінг {p} мс", "↓ {d} Mbps, ↑ {u} Mbps, ping {p} ms"),
    "st_summary_jit": (", джитер {j} мс", ", jitter {j} ms"),
    "st_none_ok": ("немає успішних тестів", "no successful tests"),
    "st_status_done": ("завершено", "finished"),
    "st_status_stopped": ("зупинено", "stopped"),
    "st_status_error": ("помилка", "failed"),
    "st_finished": ("Speedtest серія #{sid} {status} ({ok} ок): {summary}",
                    "Speedtest series #{sid} {status} ({ok} ok): {summary}"),
    "tg_report": ("📊 <b>{name}</b>: speedtest ({n} тестів)\n{summary}", "📊 <b>{name}</b>: speedtest ({n} tests)\n{summary}"),
    "tg_slow": ("🐢 <b>{name}</b>: низька швидкість\n{summary}\nПоріг: {low:g} Мбіт/с",
                "🐢 <b>{name}</b>: low speed\n{summary}\nThreshold: {low:g} Mbps"),
    "pw_changed": ("Пароль веб-інтерфейсу змінено", "Web UI password changed"),
    # --- помилки API ---
    "e_range": ("range має бути day|week|month|year", "range must be day|week|month|year"),
    "e_value": ("Невірне значення для {k}", "Invalid value for {k}"),
    "e_limit": ("{k}: допустимо {a}…{b}", "{k}: allowed {a}…{b}"),
    "e_time": ("Час у форматі ГГ:ХХ", "Time must be HH:MM"),
    "e_engine": ("speedtest_engine: auto|ookla|python", "speedtest_engine: auto|ookla|python"),
    "e_server_id": ("ID сервера — лише цифри", "Server ID must be digits only"),
    "e_need_target": ("Потрібна хоча б одна адреса для пінгу", "At least one ping target is required"),
    "e_target": ("Невірна адреса: {x}", "Invalid address: {x}"),
    "e_extra_max": ("Забагато хостів: додаткових до {n} (разом з основними — {total})",
                    "Too many hosts: up to {n} extra ({total} including the main ones)"),
    "e_extra_dup": ("Хост {x} уже є в списку", "Host {x} is already in the list"),
    "e_extra_main": ("{x} уже є серед основних адрес пінгу", "{x} is already one of the main ping targets"),
    "e_order": ("Невірний порядок плиток", "Invalid tile order"),
    "e_lang": ("Мова: uk або en", "Language: uk or en"),
    "e_busy": ("Тест уже виконується", "A test is already running"),
    "e_search": ("Пошук недоступний: {err}", "Search unavailable: {err}"),
    "e_no_engine": ("speedtest не встановлено", "speedtest is not installed"),
    "e_wait_test": ("Зачекайте завершення тесту", "Wait for the running test to finish"),
    "e_list": ("Не вдалося отримати список: {err}", "Could not get server list: {err}"),
    "e_tg_creds": ("Вкажіть токен бота і chat_id", "Enter the bot token and chat_id"),
    "e_pw_current": ("Поточний пароль невірний", "Current password is incorrect"),
    "e_pw_len": ("Новий пароль: від 6 до 128 символів", "New password must be 6–128 characters"),
    "e_pw_same": ("Новий пароль збігається з поточним", "New password is the same as the current one"),
    # --- провайдер ---
    "ev_isp_first": ("Провайдер: {isp}, зовнішній IP {ip}", "ISP: {isp}, public IP {ip}"),
    "ev_isp_changed": ("Провайдер змінився: {old} → {new}, IP {ip}", "ISP changed: {old} → {new}, IP {ip}"),
    "ev_ip_changed": ("Зовнішній IP змінився: {old} → {new} ({isp})", "Public IP changed: {old} → {new} ({isp})"),
    "ev_isp_failed": ("Не вдалося визначити провайдера: {err}", "Could not detect ISP: {err}"),
    "tg_isp_changed": ("🔄 <b>{name}</b>: провайдер змінився\n{old} → <b>{new}</b>\nIP: <code>{ip}</code>",
                       "🔄 <b>{name}</b>: ISP changed\n{old} → <b>{new}</b>\nIP: <code>{ip}</code>"),
    "e_isp_failed": ("Не вдалося визначити провайдера: {err}", "Could not detect ISP: {err}"),
    # --- WireGuard ---
    "wg_unavailable": ("Керування WireGuard недоступне: оновіть Мольфар (sudo ./install.sh)",
                       "WireGuard management unavailable: update Molfar (sudo ./install.sh)"),
    "wg_helper_error": ("Помилка WireGuard-помічника: {err}", "WireGuard helper error: {err}"),
    "wg_usage": ("Невірний запит до WireGuard-помічника", "Invalid WireGuard helper request"),
    "wg_bad_name": ("Назва профілю: 1–15 символів, латиниця, цифри, _ = + . -",
                    "Profile name: 1–15 characters, Latin letters, digits, _ = + . -"),
    "wg_exists": ("Профіль з такою назвою вже існує", "A profile with this name already exists"),
    "wg_not_found": ("Профіль не знайдено", "Profile not found"),
    "wg_too_big": ("Конфігурація завелика (макс. 16 КБ)", "Configuration is too large (max 16 KB)"),
    "wg_bad_section": ("Рядок {line}: дозволені лише секції [Interface] і [Peer]",
                       "Line {line}: only [Interface] and [Peer] sections are allowed"),
    "wg_bad_line": ("Рядок {line}: очікується «Ключ = значення»", "Line {line}: expected “Key = value”"),
    "wg_no_section": ("Рядок {line}: параметр поза секцією [Interface]/[Peer]",
                      "Line {line}: parameter outside an [Interface]/[Peer] section"),
    "wg_forbidden_key": ("Рядок {line}: «{key}» заборонено з міркувань безпеки (виконує команди від root). Такі профілі редагуйте через SSH.",
                         "Line {line}: “{key}” is not allowed for security reasons (runs commands as root). Edit such profiles over SSH."),
    "wg_bad_key": ("Рядок {line}: невідомий параметр «{key}»", "Line {line}: unknown parameter “{key}”"),
    "wg_bad_value": ("Рядок {line}: невірне значення {key}", "Line {line}: invalid {key} value"),
    "wg_need_interface": ("Потрібна рівно одна секція [Interface]", "Exactly one [Interface] section is required"),
    "wg_need_privatekey": ("У [Interface] потрібен PrivateKey", "[Interface] needs a PrivateKey"),
    "wg_not_editable": ("Профіль містить PostUp/PreUp тощо — редагування через веб вимкнено",
                        "Profile contains PostUp/PreUp etc. — web editing is disabled"),
    "wg_start_failed": ("Не вдалося увімкнути тунель:\n{log}", "Failed to bring the tunnel up:\n{log}"),
    "wg_hint_fw": ("\n\nПідказка: профіль спрямовує весь трафік у тунель (AllowedIPs = 0.0.0.0/0 або ::/0), а для цього wg-quick потрібен nftables. Встановіть: sudo apt install nftables — або вкажіть в AllowedIPs лише потрібні мережі, напр. 10.10.0.0/16.",
                   "\n\nHint: the profile routes all traffic into the tunnel (AllowedIPs = 0.0.0.0/0 or ::/0), which needs nftables for wg-quick. Install it: sudo apt install nftables — or list only the networks you need in AllowedIPs, e.g. 10.10.0.0/16."),
    "wg_hint_resolv": ("\n\nПідказка: немає resolvconf для рядка «DNS =». Перезапустіть sudo ./install.sh або приберіть DNS з профілю.",
                       "\n\nHint: resolvconf is missing for the “DNS =” line. Re-run sudo ./install.sh or remove DNS from the profile."),
    "wg_hint_module": ("\n\nПідказка: ядро не підтримує WireGuard (модуль не завантажується). Оновіть систему: sudo apt full-upgrade.",
                       "\n\nHint: the kernel has no WireGuard support (module does not load). Update the system: sudo apt full-upgrade."),
    "wg_stop_failed": ("Не вдалося вимкнути тунель: {log}", "Failed to bring the tunnel down: {log}"),
    "wg_systemctl_failed": ("Помилка systemctl: {log}", "systemctl error: {log}"),
    "wg_bad_key_format": ("Невірний формат ключа", "Invalid key format"),
    "ev_wg_created": ("WireGuard: додано профіль {name}", "WireGuard: profile {name} added"),
    "ev_wg_saved": ("WireGuard: змінено профіль {name}", "WireGuard: profile {name} edited"),
    "ev_wg_deleted": ("WireGuard: видалено профіль {name}", "WireGuard: profile {name} deleted"),
    "ev_wg_up": ("WireGuard: тунель {name} увімкнено", "WireGuard: tunnel {name} up"),
    "ev_wg_down": ("WireGuard: тунель {name} вимкнено", "WireGuard: tunnel {name} down"),
    "ev_wg_enable": ("WireGuard: автозапуск {name} увімкнено", "WireGuard: autostart for {name} on"),
    "ev_wg_disable": ("WireGuard: автозапуск {name} вимкнено", "WireGuard: autostart for {name} off"),
    # --- мапа мережі ---
    "net_new_title": ("🆕 <b>{name}</b>: новий пристрій у мережі", "🆕 <b>{name}</b>: new device on the network"),
    "net_new_line": ("• <b>{dev}</b> — <code>{ip}</code>, MAC <code>{mac}</code>{vendor}",
                     "• <b>{dev}</b> — <code>{ip}</code>, MAC <code>{mac}</code>{vendor}"),
    "net_new_more": ("…і ще {n}", "…and {n} more"),
    "ev_net_new": ("Мапа мережі: новий пристрій {dev} ({ip}, {mac})", "Network map: new device {dev} ({ip}, {mac})"),
    "e_iface": ("Неправильна назва інтерфейсу (до 15 символів: літери, цифри, . _ - @)",
                "Invalid interface name (up to 15 characters: letters, digits, . _ - @)"),
    "e_net_not_found": ("Пристрій не знайдено", "Device not found"),
}


def tr(lang: str, msg_id: str, **kw) -> str:
    pair = M.get(msg_id)
    if pair is None:
        return msg_id
    text = pair[1] if lang == "en" else pair[0]
    return text.format(**kw) if kw else text


def fmt_dur(lang: str, sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return tr(lang, "dur_hm", h=h, m=m)
    if m:
        return tr(lang, "dur_m", m=m)
    return tr(lang, "dur_s", s=s)
