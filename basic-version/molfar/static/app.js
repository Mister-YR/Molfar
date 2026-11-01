"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });

let toastTimer;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3500);
}

const fmtTime = (ts, withDate = true) =>
  new Date(ts * 1000).toLocaleString(LOCALE(), withDate
    ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" });
function fmtAgo(sec) {
  if (sec == null) return t("never");
  if (sec < 60) return t("ago_s", { n: sec });
  if (sec < 3600) return t("ago_m", { n: Math.floor(sec / 60) });
  if (sec < 86400) return t("ago_h", { n: Math.floor(sec / 3600) });
  return t("ago_d", { n: Math.floor(sec / 86400) });
}
function fmtUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? t("up_d", { n: d }) : "") + t("up_hm", { h, m });
}
const fmtBytes = (b) => {
  if (b == null) return "—";
  const u = t("bytes"); let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
};
const lossClass = (loss) => (loss == null ? "" : loss >= 20 ? "bad" : loss > 0 ? "warn" : "ok");

// ---------------- мова ----------------
function setLangLocal(lang) {
  LANG = lang === "en" ? "en" : "uk";
  try { localStorage.setItem("molfar.lang", LANG); } catch (_) { /* приватний режим */ }
  applyI18n();
  $$("#lang-switch button").forEach((b) => b.classList.toggle("active", b.dataset.lang === LANG));
  document.title = deviceName ? `${t("brand")} · ${deviceName}` : t("brand");
}
function rerenderAll() {
  if (status) renderStatus(status);
  renderServerLabel();
  if (serverCache.length) renderServers(serverCache, serverSource);
  renderAuthInfo();
  if (currentTab === "history") loadHistory();
  if (currentTab === "events") loadEvents();
  if (currentTab === "wg") { renderWg(); wgSetEditorTitle(); }
  if (window.NetMap) NetMap.rerender();
  if (currentTab === "settings") renderExtra();
  setDashEdit(dashEditing);
}
$("#lang-switch").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-lang]");
  if (!b || b.dataset.lang === LANG) return;
  setLangLocal(b.dataset.lang);
  if (form.elements.language) form.elements.language.value = LANG;
  rerenderAll();
  try { await post("/api/settings", { language: LANG }); } catch (err) { toast(err.message, true); }
});

// ---------------- вкладки ----------------
let currentTab = "overview";
function showTab(name) {
  const b = $(`#tabs button[data-tab="${name}"]`);
  if (!b) return;
  currentTab = name;
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  if (name === "history") loadHistory();
  if (name === "settings") { loadSettings(); loadAuthInfo(); loadExtra(); }
  if (name === "events") loadEvents();
  if (name === "wg") loadWg();
  if (window.NetMap) { if (name === "netmap") NetMap.show(); else NetMap.hide(); }
  history.replaceState(null, "", "#" + name);
}
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) showTab(b.dataset.tab);
});
window.addEventListener("hashchange", () => {
  const h = location.hash.slice(1);
  if (h && h !== currentTab) showTab(h);
});

// ---------------- огляд ----------------
let status = null;
let deviceName = "";

function renderStatus(st) {
  status = st;
  const sys = st.system;
  const lp = st.ping.last;
  const worst = lp.length ? Math.max(...lp.map((p) => p.loss)) : null;
  $("#health-dot").className = "dot " + (worst == null ? "" : worst >= 20 ? "bad" : worst > 0 ? "warn" : "ok");
  $("#device-sub").textContent = `${deviceName ? deviceName + " · " : ""}${sys.hostname} · ${sys.ips.map((i) => i.addr.split("/")[0]).join(", ") || "—"}`;

  // пінг-плитки: слот 1–3 = основні адреси з налаштувань (порожня адреса — плитка схована)
  const slots = st.ping.targets || lp.map((p) => p.target);
  [0, 1, 2].forEach((i) => {
    const tile = $(`#dash [data-tile="ping${i + 1}"]`);
    const target = slots[i] || "";
    if (tile) renderPingTile(tile, target, lp.find((x) => x.target === target), st.alerts, target);
  });
  // додаткові хости з галкою «На Огляді» — власні плитки h:<хост>
  const onDash = (st.ping.extra || []).filter((h) => h.dash);
  syncHostTiles(onDash);
  onDash.forEach((h) => {
    const tile = $(`#dash [data-tile="${CSS.escape("h:" + h.host)}"]`);
    if (tile) renderPingTile(tile, h.host, h.last ? { ...h.last, target: h.host } : null, st.alerts, h.name ? `${h.name} · ${h.host}` : h.host);
  });
  if (currentTab === "settings") renderExtra();

  // speedtest
  const sr = st.series;
  const prog = $("#speed-progress");
  if (sr) {
    const pct = Math.round((sr.done / sr.runs) * 100);
    const phase = sr.phase === "testing"
      ? t("prog_testing", { c: sr.current, n: sr.runs, srv: sr.server ? " · " + sr.server : "" })
      : sr.phase === "waiting" ? t("prog_waiting", { d: sr.done, n: sr.runs, time: fmtTime(sr.next_at, false) }) : t("prog_starting");
    prog.innerHTML = `<div>${esc(phase)}</div><div class="bar"><i style="width:${Math.max(pct, 4)}%"></i></div>`;
    prog.classList.remove("hidden");
  } else prog.classList.add("hidden");
  $("#btn-single").disabled = !!sr;
  $("#btn-series").disabled = !!sr;
  $("#btn-stop").disabled = !sr;

  const ls = st.last_series, lt = st.last_test;
  const metric = (label, v, unit, d = 1) => `<div class="metric"><div class="label">${label}</div><div class="value">${n(v, d)} <small>${unit}</small></div></div>`;
  if (ls && ls.runs_ok) {
    $("#speed-metrics").innerHTML = metric(t("m_down"), ls.avg_download, t("mbps")) + metric(t("m_up"), ls.avg_upload, t("mbps")) +
      metric(t("ping"), ls.avg_ping, t("ms")) + metric(t("jitter"), ls.avg_jitter, t("ms"));
    $("#speed-meta").textContent = `${t("avg_of", { n: ls.runs_ok })} · ${fmtTime(ls.started_ts)} · ${t(ls.trigger === "schedule" ? "by_schedule" : "manual")}` +
      (lt && lt.server ? ` · ${lt.server}` : "") + (lt && lt.error ? ` · ${t("last_error", { e: lt.error })}` : "");
  } else {
    $("#speed-metrics").innerHTML = "";
    $("#speed-meta").textContent = lt && lt.error ? t("error_x", { e: lt.error }) : t("no_tests");
  }
  $("#speed-engine").textContent = st.engine ? t("engine_x", { e: st.engine }) : t("engine_none");
  const srv = st.speedtest_server || {};
  $("#speed-server").textContent = srv.id ? (srv.name || `ID ${srv.id}`) : t("autoselect");

  // автоматизація
  $("#tg-speedtest").checked = st.schedule.enabled;
  const tzDiff = st.tz && st.tz.offset !== -new Date().getTimezoneOffset() * 60;
  $("#sched-info").textContent = st.schedule.enabled
    ? t("sched_on", { time: st.schedule.time, tz: tzDiff ? ", " + st.tz.name : "", next: fmtTime(st.schedule.next_at) })
    : t("off");
  $("#tg-ping").checked = st.ping.enabled;
  $("#ping-info").textContent = !st.ping.enabled ? t("ping_stopped") : st.ping.paused ? t("ping_paused") :
    st.ping.next_at ? t("ping_next", { time: fmtTime(st.ping.next_at, false) }) : t("ping_running");
  const tg = st.telegram;
  $("#tg-alert").checked = tg.alerts_enabled;
  $("#alert-info").textContent = !tg.configured ? t("tg_none") :
    tg.last_error ? t("tg_err", { e: tg.last_error }) + (tg.queue ? t("tg_queue", { n: tg.queue }) : "") : t("tg_ok");

  // WireGuard
  const wg = st.wireguard;
  if (!wg.available) {
    $("#wg").innerHTML = `<div class="muted">${t("wg_unavail")}${wg.error ? ": " + esc(wg.error) : ""}</div>`;
  } else if (!wg.interfaces.length) {
    $("#wg").innerHTML = `<div class="muted">${t("wg_none")}</div>`;
  } else {
    $("#wg").innerHTML = wg.interfaces.map((i) => `<div><b>${esc(i.name)}</b> <span class="muted small">${i.listen_port ? t("wg_port", { p: esc(i.listen_port) }) : ""}</span></div>` +
      (i.peers.map((p) => {
        const age = p.handshake_age, cls = age == null ? "bad" : age < 180 ? "ok" : age < 600 ? "warn" : "bad";
        return `<div class="peer"><span class="badge ${cls}">${age == null ? t("wg_no_hs") : t("wg_hs", { ago: fmtAgo(age) })}</span>
          <div class="small">${esc(p.endpoint || "—")} · <span class="muted">${esc(p.allowed_ips)}</span></div>
          <div class="muted small">↓ ${fmtBytes(p.rx)} · ↑ ${fmtBytes(p.tx)} · ${esc(p.public_key)}</div></div>`;
      }).join("") || `<div class="muted small">${t("wg_no_peers")}</div>`)).join("");
  }

  renderIsp(st);
  renderLan(st.netmap);

  // пристрій
  const memUsed = sys.mem_total && sys.mem_available ? sys.mem_total - sys.mem_available : null;
  $("#sys").innerHTML = `
    <dt>${t("d_temp")}</dt><dd>${sys.cpu_temp != null ? sys.cpu_temp + " °C" : "—"}</dd>
    <dt>${t("d_load")}</dt><dd>${sys.load ? sys.load.map((x) => x.toFixed(2)).join(" / ") : "—"}</dd>
    <dt>${t("d_mem")}</dt><dd>${t("of", { a: fmtBytes(memUsed), b: fmtBytes(sys.mem_total) })}</dd>
    <dt>${t("d_disk")}</dt><dd>${sys.disk ? t("free_of", { a: fmtBytes(sys.disk.free), b: fmtBytes(sys.disk.total) }) : "—"}</dd>
    <dt>${t("d_uptime")}</dt><dd>${fmtUptime(sys.uptime)}</dd>
    <dt>${t("d_ips")}</dt><dd>${sys.ips.map((i) => `${esc(i.iface)}: ${esc(i.addr)}`).join("<br>") || "—"}</dd>`;
}

// ---------------- провайдер ----------------
const ispName = (c) => c ? `${c.isp || "?"}` : "—";
function vpnFullTunnel(st) {
  const wg = st.wireguard;
  return !!(wg && wg.available && (wg.interfaces || []).some((i) => i.peers.some((p) =>
    (p.allowed_ips || "").split(",").map((x) => x.trim()).some((x) => x === "0.0.0.0/0" || x === "::/0"))));
}
// картка «Локальна мережа» (мапа мережі)
function renderLan(nm) {
  const el = $("#lan");
  if (!el || !nm) return;
  const line = !nm.enabled ? t("lan_off") : nm.scanning ? t("lan_scanning")
    : nm.error ? t("lan_err", { err: esc(nm.error) }) : nm.last_scan ? t("lan_scanned", { ago: fmtAgo(Math.round(Date.now() / 1000 - nm.last_scan)) })
    : t("lan_wait");
  const v = (k) => nm[k] ?? 0;
  el.innerHTML = `<div class="lan-counts">
      <div><div class="value">${v("online")}</div><div class="label">${t("lan_online")}</div></div>
      <div><div class="value">${v("offline")}</div><div class="label">${t("lan_offline")}</div></div>
      <div><div class="value ${v("new") ? "warn" : ""}">${v("new")}</div><div class="label">${t("lan_new")}</div></div>
      <div><div class="value">${v("hidden")}</div><div class="label">${t("lan_hidden")}</div></div>
    </div><div class="muted small">${line}</div>`;
}

function renderIsp(st) {
  const i = st.isp || {}, c = i.current;
  const el = $("#isp");
  if (!c) {
    el.innerHTML = `<div class="muted">${t(i.enabled ? "isp_unknown" : "isp_disabled")}</div>` +
      (i.error ? `<div class="small warn-text">${esc(t("isp_error", { e: i.error }))}</div>` : "");
    return;
  }
  const loc = [c.city, c.country].filter(Boolean).join(", ");
  el.innerHTML = `
    <div class="isp-name">${esc(ispName(c))}</div>
    <dl class="kv">
      <dt>${t("isp_ip")}</dt><dd class="mono">${esc(c.ip || "—")}</dd>
      ${c.asn ? `<dt>${t("isp_asn")}</dt><dd class="mono">AS${esc(c.asn)}</dd>` : ""}
      ${c.org && c.org !== c.isp ? `<dt>${t("isp_org")}</dt><dd>${esc(c.org)}</dd>` : ""}
      ${loc ? `<dt>${t("isp_location")}</dt><dd>${esc(loc)}</dd>` : ""}
      ${c.since ? `<dt>${t("isp_since")}</dt><dd>${fmtTime(c.since)}</dd>` : ""}
      ${i.checked_at ? `<dt>${t("isp_checked")}</dt><dd>${fmtTime(i.checked_at, false)} · ${esc(c.source || "")}</dd>` : ""}
    </dl>` +
    (i.error ? `<div class="small warn-text">${esc(t("isp_error", { e: i.error }))}</div>` : "") +
    (vpnFullTunnel(st) ? `<div class="warn-box small">${t("isp_vpn_note")}</div>` : "");
}
$("#btn-isp-check").onclick = async (e) => {
  const b = e.currentTarget; b.disabled = true; b.textContent = t("isp_checking");
  try {
    const r = await post("/api/isp/check");
    toast(t("isp_done", { isp: `${ispName(r.current)}${r.current.asn ? " (AS" + r.current.asn + ")" : ""}` }));
  } catch (err) { toast(err.message, true); }
  finally { b.disabled = false; b.textContent = t("isp_check"); refreshStatus(); }
};

async function refreshStatus() {
  try {
    renderStatus(await api("/api/status"));
    if (currentTab === "wg" && wgProfiles) wgUpdateLive();
  } catch (e) {
    $("#health-dot").className = "dot bad";
    $("#device-sub").textContent = t("no_server");
  }
}

async function action(btn, fn, okMsg) {
  btn.disabled = true;
  try {
    await fn();
    if (okMsg) toast(okMsg);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    refreshStatus();
  }
}
$("#btn-single").onclick = (e) => action(e.currentTarget, () => post("/api/speedtest/start", { mode: "single" }), t("t_test_started"));
$("#btn-series").onclick = (e) => action(e.currentTarget, () => post("/api/speedtest/start", { mode: "series" }), t("t_series_started"));
$("#btn-stop").onclick = (e) => action(e.currentTarget, () => post("/api/speedtest/stop"), t("t_stopped"));
$("#btn-ping").onclick = (e) => {
  const b = e.currentTarget;
  b.textContent = t("pinging");
  action(b, async () => {
    const r = await post("/api/ping/run");
    toast(r.results.map((x) => `${x.target}: ${x.loss}% / ${x.rtt_avg ?? "—"} ${t("ms")}`).join(" · "));
  }).finally(() => (b.textContent = t("btn_ping")));
};
const quickToggle = (id, key) => ($(id).onchange = async (e) => {
  try { await post("/api/settings", { [key]: e.target.checked }); refreshStatus(); }
  catch (err) { toast(err.message, true); e.target.checked = !e.target.checked; }
});
quickToggle("#tg-speedtest", "speedtest_enabled");
quickToggle("#tg-ping", "ping_enabled");
quickToggle("#tg-alert", "alert_enabled");

// ---------------- історія ----------------
let range = "day";
const charts = {};
$("#range").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-r]");
  if (!b) return;
  range = b.dataset.r;
  $$("#range button").forEach((x) => x.classList.toggle("active", x === b));
  loadHistory();
});

function xTickFormat(v) {
  const d = new Date(v * 1000), L = LOCALE();
  if (range === "day") return d.toLocaleTimeString(L, { hour: "2-digit", minute: "2-digit" });
  if (range === "week") return d.toLocaleDateString(L, { weekday: "short", day: "2-digit" });
  if (range === "year") return d.toLocaleDateString(L, { month: "short" });
  return d.toLocaleDateString(L, { day: "2-digit", month: "2-digit" });
}

// «круглі» мітки часу: кожні 3 год / 1 день / 5 днів / 1 місяць за місцевим часом
function niceTicks(min, max) {
  const d = new Date(min * 1000), out = [];
  d.setMinutes(0, 0, 0);
  if (range === "day") { d.setHours(Math.ceil(d.getHours() / 3) * 3); }
  else { d.setHours(0); d.setDate(d.getDate() + 1); }
  if (range === "year") d.setDate(1), d.setMonth(d.getMonth() + 1);
  while (d.getTime() / 1000 <= max && out.length < 40) {
    if (range !== "month" || (d.getDate() - 1) % 5 === 0) out.push(d.getTime() / 1000);
    if (range === "day") d.setHours(d.getHours() + 3);
    else if (range === "year") d.setMonth(d.getMonth() + 1);
    else d.setDate(d.getDate() + 1);
  }
  return out;
}

// вставляє розриви в лінію там, де даних не було
function withGaps(points, bucket) {
  const out = [];
  for (let i = 0; i < points.length; i++) {
    if (i && points[i].x - points[i - 1].x > bucket * 2.5) out.push({ x: points[i - 1].x + bucket, y: null });
    out.push(points[i]);
  }
  return out;
}

function drawChart(id, datasets, { yMin = 0, yMax, since } = {}) {
  const grid = css("--border"), text = css("--muted");
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      elements: { point: { radius: 0, hitRadius: 6 }, line: { borderWidth: 2, tension: 0.2 } },
      scales: {
        x: { type: "linear", min: since, max: Math.floor(Date.now() / 1000), grid: { color: grid }, ticks: { color: text, autoSkip: false, callback: xTickFormat },
          afterBuildTicks: (axis) => { axis.ticks = niceTicks(axis.min, axis.max).map((value) => ({ value })); } },
        y: { min: yMin, suggestedMax: yMax, grid: { color: grid }, ticks: { color: text } },
      },
      plugins: {
        legend: { labels: { color: text, boxWidth: 12 } },
        tooltip: { callbacks: { title: (it) => it.length ? new Date(it[0].parsed.x * 1000).toLocaleString(LOCALE()) : "" } },
      },
    },
  });
}

async function loadHistory() {
  $("#csv-ping").href = `/api/export/ping.csv?range=${range}`;
  $("#csv-speed").href = `/api/export/speed.csv?range=${range}`;
  let ping, speed;
  try {
    [ping, speed] = await Promise.all([api(`/api/history/ping?range=${range}`), api(`/api/history/speed?range=${range}`)]);
  } catch (e) { toast(e.message, true); return; }
  const colors = [css("--c1"), css("--c2"), css("--c3"), "#10b981", "#f472b6", "#94a3b8"];
  // на графіках — три основні адреси і ті хости, які позначено в таблиці підсумку
  // основні адреси і хости «На Огляді» — завжди на графіку
  const main = [...((status && status.ping.targets) || []).filter(Boolean),
    ...((status && status.ping.extra) || []).filter((h) => h.dash).map((h) => h.host)];
  const targets = [...main.filter((x) => ping.series[x]),
    ...Object.keys(ping.series).filter((x) => !main.includes(x) && chartPick.has(x))];
  const ds = (key) => targets.map((x, i) => ({
    label: hostLabel(x), borderColor: colors[i % colors.length], backgroundColor: colors[i % colors.length],
    data: withGaps(ping.series[x].map((r) => ({ x: r.t, y: r[key] })), ping.bucket),
  }));
  drawChart("ch-loss", ds("loss"), { yMax: 5, since: ping.since });
  drawChart("ch-rtt", ds("rtt"), { since: ping.since });
  drawChart("ch-jitter", ds("jitter"), { since: ping.since });

  const ok = speed.tests.filter((x) => !x.error);
  const done = speed.series.filter((s) => s.runs_ok);
  const down = css("--c1"), up = css("--c2");
  drawChart("ch-speed", [
    { label: t("lg_down_series"), borderColor: down, backgroundColor: down, pointRadius: 3, data: done.map((s) => ({ x: s.started_ts, y: s.avg_download })) },
    { label: t("lg_up_series"), borderColor: up, backgroundColor: up, pointRadius: 3, data: done.map((s) => ({ x: s.started_ts, y: s.avg_upload })) },
    { label: t("lg_down_test"), showLine: false, pointRadius: 2, borderColor: down + "66", backgroundColor: down + "66", data: ok.map((x) => ({ x: x.ts, y: x.download })) },
    { label: t("lg_up_test"), showLine: false, pointRadius: 2, borderColor: up + "66", backgroundColor: up + "66", data: ok.map((x) => ({ x: x.ts, y: x.upload })) },
  ], { since: speed.since });

  // підсумок
  const sum = ping.summary, ms = t("ms");
  const spAvg = (k) => done.length ? done.reduce((a, s) => a + (s[k] || 0), 0) / done.length : null;
  const sorted = [...sum.filter((x) => main.includes(x.target)).sort((a, b) => main.indexOf(a.target) - main.indexOf(b.target)),
    ...sum.filter((x) => !main.includes(x.target))];
  const chartCell = (x) => main.includes(x) ? `<td title="${esc(t("chart_main"))}"><span class="chart-dot"></span></td>`
    : `<td><input type="checkbox" data-chart="${esc(x)}" aria-label="${esc(t("th_chart"))}: ${esc(hostLabel(x))}"${chartPick.has(x) ? " checked" : ""}></td>`;
  $("#summary-table").innerHTML = `<tr><th>${t("th_chart")}</th><th>${t("th_target")}</th><th class="num">${t("th_checks")}</th><th class="num">${t("loss")}</th><th class="num">${t("th_rtt_avg")}</th><th class="num">${t("th_rtt_mm")}</th><th class="num">${t("jitter")}</th><th class="num">${t("th_outages")}</th></tr>` +
    (sorted.map((s) => `<tr>${chartCell(s.target)}<td>${esc(hostLabel(s.target))}</td><td class="num">${s.checks}</td><td class="num">${n(s.loss, 2)}%</td><td class="num">${n(s.rtt)} ${ms}</td>
      <td class="num">${n(s.rtt_min, 0)} / ${n(s.rtt_max, 0)}</td><td class="num">${n(s.jitter)} ${ms}</td><td class="num">${s.outages}×</td></tr>`).join("") ||
      `<tr><td colspan="8" class="muted">${t("no_data")}</td></tr>`) +
    (done.length ? `<tr><td></td><td><b>Speedtest</b></td><td class="num">${t("n_series", { n: done.length })}</td><td colspan="5" class="num">${t("sum_speed", { d: n(spAvg("avg_download")), u: n(spAvg("avg_upload")), p: n(spAvg("avg_ping")) })}</td></tr>` : "");

  const seriesIsp = {};
  for (const x of speed.tests) if (x.isp && !seriesIsp[x.series_id]) seriesIsp[x.series_id] = x.isp;
  const stTxt = { done: "✓", stopped: t("st_stopped"), error: t("st_error") };
  $("#series-table").innerHTML = `<tr><th>${t("th_time")}</th><th>${t("th_trigger")}</th><th class="num">${t("th_tests")}</th><th class="num">↓ ${t("mbps")}</th><th class="num">↑ ${t("mbps")}</th><th class="num">${t("ping")}</th><th class="num">${t("jitter")}</th><th>${t("th_isp")}</th><th>${t("th_status")}</th></tr>` +
    (speed.series.slice().reverse().slice(0, 100).map((s) => `<tr><td>${fmtTime(s.started_ts)}</td><td>${t(s.trigger === "schedule" ? "tr_schedule" : "manual")}</td>
      <td class="num">${s.runs_ok}/${s.runs_planned}</td><td class="num">${n(s.avg_download)}</td><td class="num">${n(s.avg_upload)}</td>
      <td class="num">${n(s.avg_ping)}</td><td class="num">${n(s.avg_jitter)}</td><td>${esc(seriesIsp[s.id] || "—")}</td><td>${stTxt[s.status] || esc(s.status)}</td></tr>`).join("") ||
      `<tr><td colspan="9" class="muted">${t("no_series")}</td></tr>`);

  // зміни провайдера
  try {
    const ih = await api("/api/isp?limit=50");
    const kind = { first: "isp_kind_first", isp: "isp_kind_isp", ip: "isp_kind_ip" };
    $("#isp-table").innerHTML = `<tr><th>${t("th_time")}</th><th>${t("th_event")}</th><th>${t("th_isp")}</th><th>ASN</th><th>IP</th><th>${t("isp_location")}</th></tr>` +
      (ih.history.map((h) => `<tr><td>${fmtTime(h.ts)}</td><td><span class="badge ${h.kind === "isp" ? "warn" : ""}">${t(kind[h.kind] || h.kind)}</span></td>
        <td>${esc(h.isp || "—")}</td><td class="mono">${h.asn ? "AS" + esc(h.asn) : "—"}</td><td class="mono">${esc(h.ip || "—")}</td>
        <td>${esc([h.city, h.country].filter(Boolean).join(", ") || "—")}</td></tr>`).join("") ||
        `<tr><td colspan="6" class="muted">${t("no_isp_history")}</td></tr>`);
  } catch (_) { /* ignore */ }
}

// ---------------- налаштування ----------------
const form = $("#settings-form");
async function loadSettings() {
  const s = await api("/api/settings");
  for (const el of form.elements) {
    if (!el.name) continue;
    const [key, idx] = el.name.split(".");
    let v = s[key];
    if (idx !== undefined) v = (v || [])[+idx] ?? "";
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v ?? "";
  }
  $("#save-status").textContent = "";
  renderServerLabel();
}
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    const [key, idx] = el.name.split(".");
    const v = el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? 0 : Number(el.value)) : el.value.trim();
    if (idx !== undefined) { (data[key] = data[key] || [])[+idx] = v; } else data[key] = v;
  }
  try {
    await post("/api/settings", data);
    deviceName = data.device_name || "";
    const langChanged = data.language !== LANG;
    setLangLocal(data.language);
    if (langChanged) rerenderAll();
    $("#save-status").textContent = t("saved_mark");
    toast(t("saved"));
    loadSettings();
    refreshStatus();
  } catch (err) { toast(err.message, true); }
});
$("#btn-tg-test").onclick = (e) => action(e.currentTarget, () => post("/api/telegram/test", {
  tg_token: form.elements.tg_token.value.trim(), tg_chat_id: form.elements.tg_chat_id.value.trim(),
}), t("tg_sent"));

// ---------------- пароль ----------------
const pwForm = $("#password-form");
let authInfo = null;

function renderAuthInfo() {
  const a = authInfo;
  if (!a) return;
  const key = a.is_default ? "pw_info_default" : { env: "pw_info_env", db: "pw_info_db", none: "pw_info_none" }[a.source];
  $("#pw-info").textContent = t(key, { u: a.user });
  pwForm.elements.username.value = a.user;
  pwForm.elements.current.closest("label").classList.toggle("hidden", a.source === "none");
  $("#default-pw-warning").classList.toggle("hidden", !a.is_default);
}
async function loadAuthInfo() {
  try { authInfo = await api("/api/auth"); renderAuthInfo(); } catch (_) { /* ignore */ }
}
$("#pw-show").onchange = (e) => {
  ["current", "new", "repeat"].forEach((k) => (pwForm.elements[k].type = e.target.checked ? "text" : "password"));
};
pwForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = pwForm.elements;
  if (f.new.value.length < 6) return toast(t("pw_too_short"), true);
  if (f.new.value !== f.repeat.value) return toast(t("pw_mismatch"), true);
  const btn = pwForm.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await post("/api/auth/password", { current: f.current.value, new: f.new.value });
    pwForm.reset();
    toast(t("pw_done"));
    // старі облікові дані в браузері більше не діють — перезавантаження покаже вікно входу
    setTimeout(() => location.reload(), 1800);
  } catch (err) {
    toast(err.message, true);
  } finally { btn.disabled = false; }
});
$("#link-password").onclick = (e) => {
  e.preventDefault();
  showTab("settings");
  setTimeout(() => { pwForm.scrollIntoView({ behavior: "smooth", block: "center" }); pwForm.elements.current.focus(); }, 150);
};

// ---------------- вибір сервера speedtest ----------------
let serverCache = [];
let serverSource = "nearest";
const srvFmt = (x) => `${x.sponsor}${x.location ? " — " + x.location : ""}${x.country ? ", " + x.country : ""}`;

function renderServerLabel() {
  const id = form.elements.speedtest_server_id.value, name = form.elements.speedtest_server_name.value;
  $("#server-label").textContent = id ? `${name || t("server")} · ID ${id}` : t("srv_auto_label");
  $("#btn-server-auto").disabled = !id;
  $$("#server-list .server-row").forEach((r) => r.classList.toggle("selected", r.dataset.id === id));
}

async function setServer(id, name, msg) {
  try {
    await post("/api/settings", { speedtest_server_id: id, speedtest_server_name: id ? name : "" });
    form.elements.speedtest_server_id.value = id;
    form.elements.speedtest_server_name.value = id ? name : "";
    renderServerLabel();
    toast(msg);
    refreshStatus();
  } catch (e) { toast(e.message, true); }
}

function renderServers(list, source) {
  serverCache = list;
  serverSource = source;
  $("#server-status").textContent = list.length
    ? t(source === "search" ? "srv_found" : "srv_nearest", { n: list.length })
    : t("srv_nothing");
  $("#server-list").innerHTML = list.map((x, i) => `
    <div class="server-row" data-id="${esc(x.id)}">
      <div><div class="name">${esc(srvFmt(x))}</div>
        <div class="meta">ID ${esc(x.id)}${x.distance != null ? ` · ${x.distance} ${t("km")}` : ""}${x.host ? ` · ${esc(x.host)}` : ""}</div></div>
      <div class="btns">
        <button type="button" class="btn small" data-act="test" data-i="${i}" title="${esc(t("srv_test_title"))}">${t("srv_test")}</button>
        <button type="button" class="btn small primary" data-act="pick" data-i="${i}">${t("srv_pick")}</button>
      </div>
    </div>`).join("");
  renderServerLabel();
}

async function loadServers(search = "", refresh = false) {
  $("#server-status").textContent = t(search ? "srv_searching" : "srv_loading");
  $("#server-list").innerHTML = "";
  try {
    const q = new URLSearchParams({ search, refresh: refresh ? "true" : "false" });
    const r = await api(`/api/speedtest/servers?${q}`);
    renderServers(r.servers, r.source);
  } catch (e) { $("#server-status").textContent = "⚠ " + e.message; }
}

$("#btn-server-open").onclick = () => {
  const p = $("#server-panel");
  p.classList.toggle("hidden");
  if (!p.classList.contains("hidden") && !serverCache.length) loadServers();
};
$("#btn-server-auto").onclick = () => setServer("", "", t("srv_set_auto"));
$("#btn-server-near").onclick = () => loadServers("", true);
$("#btn-server-search").onclick = () => loadServers($("#server-search").value.trim());
$("#server-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); loadServers(e.target.value.trim()); }
});
$("#btn-server-manual").onclick = () => {
  const id = $("#server-manual").value.trim();
  if (!/^\d+$/.test(id)) return toast(t("srv_id_digits"), true);
  setServer(id, "", t("srv_id_saved", { id }));
};
$("#server-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const x = serverCache[+b.dataset.i];
  if (b.dataset.act === "pick") return setServer(x.id, srvFmt(x), t("srv_set", { x: x.sponsor }));
  b.disabled = true;
  try {
    await post("/api/speedtest/start", { mode: "single", server_id: x.id, server_name: srvFmt(x) });
    toast(t("srv_test_started", { x: x.sponsor }));
    refreshStatus();
  } catch (err) { toast(err.message, true); } finally { b.disabled = false; }
});
$("#link-server").onclick = (e) => {
  e.preventDefault();
  showTab("settings");
  setTimeout(() => { $("#server-panel").classList.remove("hidden"); if (!serverCache.length) loadServers(); $(".server-pick").scrollIntoView({ behavior: "smooth", block: "center" }); }, 150);
};

// ---------------- WireGuard-профілі ----------------
let wgProfiles = null;          // null — ще не завантажено
let wgRollbackSec = 60;
let wgEditing = null;           // null — новий профіль, рядок — назва профілю, що редагується
const wgEd = $("#wg-editor");
const FULL_TUNNEL_RE = /(^|[\s,=])(0\.0\.0\.0\/0|::\/0)(?=\s|,|$)/m;

async function loadWg() {
  try {
    const r = await api("/api/wg/profiles");
    wgRollbackSec = r.rollback_sec || 60;
    $("#wg-unavailable").classList.toggle("hidden", r.available);
    $("#btn-wg-add").disabled = !r.available;
    wgProfiles = r.profiles;
    renderWg();
  } catch (e) { toast(e.message, true); }
}

function wgLive(name) {
  const wg = status && status.wireguard;
  return wg && wg.available ? (wg.interfaces || []).find((i) => i.name === name) : null;
}

// ---- перевірка DNS: чи досяжний DNS-сервер профілю через тунель ----
function ip4ToInt(ip) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip);
  return m ? ((+m[1] << 24) >>> 0) + (+m[2] << 16) + (+m[3] << 8) + (+m[4]) : null;
}
function inCidr(ip, cidr) {
  if (cidr === "::/0") return ip.includes(":");
  if (ip.includes(":") || cidr.includes(":")) return false;  // інші IPv6-мережі не аналізуємо
  const [net, bits = "32"] = cidr.split("/"), a = ip4ToInt(ip), b = ip4ToInt(net), n = +bits;
  if (a == null || b == null) return false;
  const mask = n === 0 ? 0 : (~0 << (32 - n)) >>> 0;
  return ((a & mask) >>> 0) === ((b & mask) >>> 0);
}
// повертає { dns: [...], unreachable: [...] } для DNS-адрес (не доменів пошуку)
function wgDnsCheck(dns, allowed) {
  const ips = (dns || []).filter((x) => ip4ToInt(x) != null || x.includes(":"));
  return { dns: ips, unreachable: ips.filter((ip) => !(allowed || []).some((c) => inCidr(ip, c.trim()))) };
}
function wgParseText(text) {
  const grab = (k) => [...text.matchAll(new RegExp(`^\\s*${k}\\s*=\\s*(.+)$`, "gim"))]
    .flatMap((m) => m[1].split("#")[0].split(",")).map((x) => x.trim()).filter(Boolean);
  return { dns: grab("DNS"), allowed: grab("AllowedIPs"), address: grab("Address").join(", ") };
}
function wgDnsWarning(dns, allowed, address) {
  const c = wgDnsCheck(dns, allowed);
  if (!c.dns.length) return null;
  // підказка — шлюз мережі за тунелем (роутер), а не адреса з підмережі самого тунелю
  const own = (address || "").split(",").map((x) => x.trim().split("/")[0]).filter(Boolean);
  const nets = (allowed || []).map((x) => x.trim()).filter((x) => /^(10|192\.168|172)\./.test(x));
  const router = nets.find((n) => !own.some((ip) => inCidr(ip, n))) || nets[0];
  const hint = router ? router.replace(/\.\d+\/\d+$/, ".1") : "10.10.105.1";
  return c.unreachable.length
    ? { level: "bad", text: t("wg_dns_unreachable", { dns: c.unreachable.join(", "), hint }) }
    : { level: "warn", text: t("wg_dns_note", { dns: c.dns.join(", ") }) };
}

function wgLiveHtml(p) {
  const live = wgLive(p.name), peer = live && live.peers[0];
  if (!p.active || !peer) return { hs: "—", tr: "—" };
  const age = peer.handshake_age, cls = age == null ? "bad" : age < 180 ? "ok" : age < 600 ? "warn" : "bad";
  return {
    hs: `<span class="badge ${cls}">${age == null ? t("wg_no_hs") : fmtAgo(age)}</span>`,
    tr: `↓ ${fmtBytes(peer.rx)} · ↑ ${fmtBytes(peer.tx)}`,
  };
}

// оновлює лише handshake/трафік, не чіпаючи решту картки (виділення тексту, фокус)
function wgUpdateLive() {
  for (const p of wgProfiles || []) {
    const card = $(`.wg-card[data-name="${CSS.escape(p.name)}"]`);
    if (!card) continue;
    const h = wgLiveHtml(p);
    const hs = card.querySelector('[data-live="hs"]'), tr = card.querySelector('[data-live="tr"]');
    if (hs.innerHTML !== h.hs) hs.innerHTML = h.hs;
    if (tr.innerHTML !== h.tr) tr.innerHTML = h.tr;
  }
}

function renderWg() {
  if (!wgProfiles) return;
  if (!wgProfiles.length) { $("#wg-list").innerHTML = `<div class="card muted">${t("wg_no_profiles")}</div>`; return; }
  $("#wg-list").innerHTML = wgProfiles.map((p) => {
    const full = (p.allowed_ips || []).some((x) => x === "0.0.0.0/0" || x === "::/0");
    const lv = wgLiveHtml(p);
    const badges = [
      `<span class="badge ${p.active ? "ok" : ""}">${t(p.active ? "wg_active" : "wg_inactive")}</span>`,
      p.rollback ? `<span class="badge warn">${t("wg_rollback_badge")}</span>` : "",
      full ? `<span class="badge warn">${t("wg_fulltunnel_badge")}</span>` : "",
      wgDnsCheck(p.dns, p.allowed_ips).unreachable.length ? `<span class="badge bad">${t("wg_dns_badge")}</span>` : "",
      !p.editable ? `<span class="badge">${t("wg_readonly", { keys: esc(p.forbidden.join(", ")) })}</span>` : "",
    ].join(" ");
    return `<div class="card wg-card ${p.active ? "on" : ""}" data-name="${esc(p.name)}">
      <div class="card-head">
        <div class="wg-title"><b class="mono">${esc(p.name)}</b> ${badges}</div>
        <label class="switch-inline"><span class="muted small">${t("wg_autostart")}</span>
          <label class="switch"><input type="checkbox" data-act="autostart" ${p.enabled ? "checked" : ""}><span></span></label></label>
      </div>
      <dl class="kv">
        <dt>${t("wg_address")}</dt><dd class="mono">${esc(p.address || "—")}</dd>
        <dt>${t("wg_endpoint")}</dt><dd class="mono">${esc((p.endpoints || []).join(", ") || "—")}</dd>
        <dt>${t("wg_allowed")}</dt><dd class="mono">${esc((p.allowed_ips || []).join(", ") || "—")}</dd>
        ${(p.dns || []).length ? `<dt>${t("wg_dns")}</dt><dd class="mono">${esc(p.dns.join(", "))}</dd>` : ""}
        <dt>${t("wg_handshake")}</dt><dd data-live="hs">${lv.hs}</dd>
        <dt>${t("wg_traffic")}</dt><dd data-live="tr">${lv.tr}</dd>
        <dt>${t("wg_pubkey")}</dt><dd><div class="search-row"><input type="text" class="mono small-input" readonly value="${esc(p.public_key || "—")}">
          <button type="button" class="btn small" data-act="copy">${t("wg_copy")}</button></div></dd>
      </dl>
      ${(() => { const w = wgDnsWarning(p.dns, p.allowed_ips, p.address); return w && w.level === "bad" ? `<div class="warn-box bad small">${esc(w.text)}</div>` : ""; })()}
      <div class="actions">
        ${p.active ? `<button class="btn" data-act="down">${t("wg_down")}</button>` : `<button class="btn primary" data-act="up">${t("wg_up")}</button>`}
        <button class="btn" data-act="edit">${t(p.editable ? "wg_edit" : "wg_view")}</button>
        <button class="btn danger" data-act="delete">${t("wg_delete")}</button>
      </div>
    </div>`;
  }).join("");
}

// Після up/зміни активного тунелю: якщо браузер досі бачить Мольфар — зв'язок є, скасовуємо автовідкат
async function wgConfirm(name, rollback) {
  if (!rollback) return;
  toast(t("wg_confirming"));
  await new Promise((r) => setTimeout(r, 4000));
  try {
    const ctl = new AbortController(); setTimeout(() => ctl.abort(), 8000);
    const r = await fetch(`/api/wg/profiles/${encodeURIComponent(name)}/confirm`, { method: "POST", signal: ctl.signal });
    if (!r.ok) throw new Error(r.statusText);
    toast(t("wg_confirmed", { name }));
  } catch (_) {
    toast(t("wg_confirm_lost", { name, sec: rollback }), true);
  }
  loadWg();
}

async function wgAct(name, act, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await post(`/api/wg/profiles/${encodeURIComponent(name)}/${act}`);
    const msg = { up: "wg_is_up", down: "wg_is_down", enable: "wg_autostart_on", disable: "wg_autostart_off" }[act];
    toast(t(msg, { name }));
    await loadWg(); refreshStatus();
    if (act === "up") wgConfirm(name, r.rollback);
  } catch (e) {
    toast(e.message, true); loadWg();
  } finally { if (btn) btn.disabled = false; }
}

$("#wg-list").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || b.dataset.act === "autostart") return;
  const name = b.closest(".wg-card").dataset.name;
  const act = b.dataset.act;
  if (act === "copy") {
    const inp = b.previousElementSibling; inp.select();
    try { await navigator.clipboard.writeText(inp.value); } catch (_) { document.execCommand("copy"); }
    return toast(t("wg_copied"));
  }
  if (act === "edit") return wgOpenEditor(name);
  if (act === "delete") {
    if (!confirm(t("wg_confirm_delete", { name }))) return;
    try { await api(`/api/wg/profiles/${encodeURIComponent(name)}`, { method: "DELETE" }); toast(t("wg_deleted", { name })); }
    catch (err) { toast(err.message, true); }
    if (wgEditing === name) wgCloseEditor();
    return loadWg();
  }
  if (act === "down" && !confirm(t("wg_confirm_down", { name }))) return;
  wgAct(name, act, b);
});
$("#wg-list").addEventListener("change", (e) => {
  if (e.target.dataset.act !== "autostart") return;
  wgAct(e.target.closest(".wg-card").dataset.name, e.target.checked ? "enable" : "disable", e.target);
});

// ----- редактор -----
function wgSetMode(mode) {
  $$("#wg-mode button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("#wg-wizard").classList.toggle("hidden", mode !== "wizard");
  $("#wg-text").classList.toggle("hidden", mode !== "text");
}
function wgSetEditorTitle() {
  $("#wg-editor-title").textContent = wgEditing ? t("wg_edit_title", { name: wgEditing }) : t("wg_new_title");
}
function wgCheckText() {
  const v = wgEd.elements.config.value;
  $("#wg-fulltunnel").classList.toggle("hidden", !FULL_TUNNEL_RE.test(v));
  const pt = wgParseText(v), w = wgDnsWarning(pt.dns, pt.allowed, pt.address), box = $("#wg-dns-warn");
  box.classList.toggle("hidden", !w);
  if (w) { box.textContent = w.text; box.classList.toggle("bad", w.level === "bad"); }
  $("#wg-hidden-note").classList.toggle("hidden", !/<hidden>/.test(v));
}
async function wgOpenEditor(name = null) {
  wgEditing = name;
  wgEd.reset();
  wgEd.elements.name.disabled = !!name;
  $("#wg-mode").classList.toggle("hidden", !!name);
  $("#wg-file").closest("label").classList.toggle("hidden", !!name);
  $("#wg-w-pub").textContent = "";
  wgSetEditorTitle(); wgSetMode("text");
  const submit = wgEd.querySelector("button[type=submit]");
  submit.disabled = false; wgEd.elements.config.readOnly = false;
  if (name) {
    try {
      const r = await api(`/api/wg/profiles/${encodeURIComponent(name)}`);
      wgEd.elements.name.value = name;
      wgEd.elements.config.value = r.config;
      submit.disabled = !r.editable; wgEd.elements.config.readOnly = !r.editable;
    } catch (e) { return toast(e.message, true); }
  }
  wgCheckText();
  wgEd.classList.remove("hidden");
  wgEd.scrollIntoView({ behavior: "smooth", block: "start" });
}
function wgCloseEditor() { wgEd.classList.add("hidden"); wgEditing = null; }

$("#btn-wg-add").onclick = () => wgOpenEditor(null);
$("#btn-wg-reload").onclick = loadWg;
$("#btn-wg-cancel").onclick = wgCloseEditor;
$("#wg-mode").addEventListener("click", (e) => { const b = e.target.closest("button[data-mode]"); if (b) wgSetMode(b.dataset.mode); });
wgEd.elements.config.addEventListener("input", wgCheckText);

$("#wg-file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.size > 16384) return toast(t("wg_file_big"), true);
  const rd = new FileReader();
  rd.onload = () => {
    wgEd.elements.config.value = rd.result;
    if (!wgEd.elements.name.value) wgEd.elements.name.value = f.name.replace(/\.conf$/i, "").replace(/[^A-Za-z0-9_=+.-]/g, "").slice(0, 15);
    wgSetMode("text"); wgCheckText();
  };
  rd.readAsText(f);
});

async function wgShowPub(priv) {
  $("#wg-w-pub").textContent = "";
  if (!/^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$/.test(priv)) return;
  try {
    const r = await post("/api/wg/pubkey", { private_key: priv });
    $("#wg-w-pub").innerHTML = `${t("wg_w_pub")} <code class="mono">${esc(r.public_key)}</code>`;
  } catch (_) { /* ignore */ }
}
$("#btn-wg-keygen").onclick = async (e) => {
  try {
    const r = await post("/api/wg/keygen");
    wgEd.elements.w_privkey.value = r.private_key;
    wgShowPub(r.private_key);
  } catch (err) { toast(err.message, true); }
};
wgEd.elements.w_privkey.addEventListener("change", (e) => wgShowPub(e.target.value.trim()));

$("#btn-wg-build").onclick = () => {
  const f = wgEd.elements, v = (k) => f[k].value.trim();
  if (!v("w_address") || !v("w_privkey") || !v("w_peerpub") || !v("w_endpoint") || !v("w_allowed")) return toast(t("wg_w_need"), true);
  const lines = ["[Interface]", `PrivateKey = ${v("w_privkey")}`, `Address = ${v("w_address")}`];
  if (v("w_dns")) lines.push(`DNS = ${v("w_dns")}`);
  lines.push("", "[Peer]", `PublicKey = ${v("w_peerpub")}`);
  if (v("w_psk")) lines.push(`PresharedKey = ${v("w_psk")}`);
  lines.push(`Endpoint = ${v("w_endpoint")}`, `AllowedIPs = ${v("w_allowed")}`);
  if (v("w_keepalive") && v("w_keepalive") !== "0") lines.push(`PersistentKeepalive = ${v("w_keepalive")}`);
  f.config.value = lines.join("\n") + "\n";
  wgSetMode("text"); wgCheckText();
};

wgEd.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = wgEditing || wgEd.elements.name.value.trim();
  const config = wgEd.elements.config.value;
  const btn = wgEd.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const r = wgEditing
      ? await post(`/api/wg/profiles/${encodeURIComponent(name)}`, { config })
      : await post("/api/wg/profiles", { name, config });
    toast(t(wgEditing ? "wg_saved" : "wg_created", { name }));
    wgCloseEditor();
    await loadWg();
    if (r.restarted) wgConfirm(name, r.rollback);
  } catch (err) {
    toast(err.message, true);
  } finally { btn.disabled = false; }
});

// ---------------- журнал ----------------
async function loadEvents() {
  const ev = await api("/api/events?limit=200");
  $("#events").innerHTML = ev.map((x) => `<div class="event ${x.level}"><div class="t">${new Date(x.ts * 1000).toLocaleString(LOCALE())}</div><div class="m">${esc(x.message)}</div></div>`).join("") ||
    `<div class="muted">${t("no_events")}</div>`;
}
$("#btn-events-reload").onclick = loadEvents;

// ---------------- історія: вибір хостів для графіків ----------------
const chartPick = new Set((() => { try { return JSON.parse(localStorage.getItem("molfar.chartHosts") || "[]"); } catch (_) { return []; } })());
function hostLabel(host) {
  const x = status && status.ping.extra && status.ping.extra.find((h) => h.host === host);
  return x && x.name ? `${x.name} (${host})` : host;
}
$("#summary-table").addEventListener("change", (e) => {
  const cb = e.target.closest("input[data-chart]");
  if (!cb) return;
  if (cb.checked) chartPick.add(cb.dataset.chart); else chartPick.delete(cb.dataset.chart);
  try { localStorage.setItem("molfar.chartHosts", JSON.stringify([...chartPick])); } catch (_) { /* ignore */ }
  loadHistory();
});

// ---------------- додаткові хости для пінгу ----------------
const PX_MAX = 37;               // разом з трьома основними — 40
let pxList = null;
async function loadExtra() {
  try { pxList = (await api("/api/settings")).ping_extra || []; renderExtra(); } catch (_) { /* ignore */ }
}
function pxError(msg) { const el = $("#px-error"); el.textContent = msg || ""; el.classList.toggle("hidden", !msg); }
function renderExtra() {
  if (!pxList) return;
  const ex = (status && status.ping.extra) || [];
  $("#px-count").textContent = t("px_count", { n: pxList.length, max: PX_MAX });
  $("#px-add").disabled = pxList.length >= PX_MAX;
  const rows = pxList.map((h, i) => {
    const p = (ex.find((x) => x.host === h.host) || {}).last;
    const res = !p ? `<span class="muted">${t("px_wait")}</span>`
      : p.error ? `<span class="badge bad">${esc(p.error)}</span>`
      : `<span class="badge ${lossClass(p.loss)}">${n(p.loss, 0)}%</span> <span class="small">${n(p.rtt_avg)} ${t("ms")} · ${t("jitter").toLowerCase()} ${n(p.jitter)}</span>`;
    return `<tr data-i="${i}"><td>${esc(h.name || "—")}</td><td class="mono">${esc(h.host)}</td><td>${res}</td>
      <td class="muted small">${p ? fmtTime(p.ts, false) : "—"}</td>
      <td><label class="check"><input type="checkbox" data-px="alert"${h.alert ? " checked" : ""} aria-label="${esc(t("px_alert_col"))}: ${esc(h.host)}"></label></td>
      <td><label class="check"><input type="checkbox" data-px="dash"${h.dash ? " checked" : ""} aria-label="${esc(t("px_dash_col"))}: ${esc(h.host)}"></label></td>
      <td class="num"><button type="button" class="btn small danger" data-px="del">${t("px_remove")}</button></td></tr>`;
  });
  $("#px-table").innerHTML = `<tr><th>${t("px_name_col")}</th><th>${t("px_host")}</th><th>${t("px_last")}</th><th>${t("th_time")}</th><th>${t("px_alert_col")}</th><th>${t("px_dash_col")}</th><th></th></tr>` +
    (rows.join("") || `<tr><td colspan="7" class="muted">${t("px_empty")}</td></tr>`);
}
async function saveExtra(list, okMsg) {
  try {
    pxList = (await post("/api/settings", { ping_extra: list })).ping_extra;
    pxError(""); renderExtra();
    if (okMsg) toast(okMsg);
    setTimeout(refreshStatus, 4000);   // перший результат нового хоста — за кілька секунд
    return true;
  } catch (err) { pxError(err.message); return false; }
}
$("#px-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const host = $("#px-host").value.trim(), name = $("#px-name").value.trim();
  if (!host) { pxError(t("px_need_host")); $("#px-host").focus(); return; }
  if (pxList.length >= PX_MAX) { pxError(t("px_full", { max: PX_MAX })); return; }
  if (await saveExtra([...pxList, { host, name, alert: true }], t("px_added", { h: host }))) {
    $("#px-host").value = ""; $("#px-name").value = ""; $("#px-host").focus();
  }
});
["#px-host", "#px-name"].forEach((id) => $(id).addEventListener("input", () => pxError("")));
$("#px-table").addEventListener("click", (e) => {
  const b = e.target.closest('[data-px="del"]');
  if (!b) return;
  const i = +b.closest("tr").dataset.i, h = pxList[i];
  saveExtra(pxList.filter((_, k) => k !== i), t("px_removed", { h: h.host }));
});
$("#px-table").addEventListener("change", (e) => {
  const cb = e.target.closest('[data-px="alert"], [data-px="dash"]');
  if (!cb) return;
  const i = +cb.closest("tr").dataset.i, key = cb.dataset.px, h = pxList[i];
  saveExtra(pxList.map((x, k) => (k === i ? { ...x, [key]: cb.checked } : x)),
    key === "dash" ? t(cb.checked ? "px_dash_on" : "px_dash_off", { h: h.name || h.host }) : null);
});

// ---------------- Огляд: плитки пінгу ----------------
function renderPingTile(tile, target, p, alerts, title) {
  tile.classList.toggle("hidden", !target);
  ["ok", "warn", "bad"].forEach((c) => tile.classList.remove(c));
  if (!target) return;
  const body = tile.querySelector(".tile-body");
  if (!p) {
    body.innerHTML = `<div class="card-head"><span class="target">${esc(title)}</span></div><div class="muted">${t("no_ping_yet")}</div>`;
    return;
  }
  const cls = lossClass(p.loss);
  if (cls) tile.classList.add(cls);
  const al = alerts[target];
  const alertBadge = al && al.notified ? `<span class="badge bad">${t("badge_alert")}</span>`
    : al && al.bad ? `<span class="badge warn">${t("badge_bad", { n: al.bad })}</span>` : "";
  body.innerHTML = `<div class="card-head"><span class="target">${esc(title)}</span>${alertBadge}</div>
    <div class="big">${n(p.rtt_avg)} <small>${t("ms")}</small></div>
    <div class="row">
      <div><span class="muted small">${t("loss")}</span><b>${n(p.loss, 0)}%</b></div>
      <div><span class="muted small">${t("jitter")}</span><b>${n(p.jitter)} ${t("ms")}</b></div>
      <div><span class="muted small">${t("minmax")}</span><b>${n(p.rtt_min, 0)}/${n(p.rtt_max, 0)}</b></div>
    </div>
    <div class="muted small" style="margin-top:8px">${p.error ? "⚠ " + esc(p.error) : `${t("packets", { r: p.received, s: p.sent })} · ${fmtTime(p.ts, false)}`}</div>`;
}
const TILE_BAR = `<div class="tile-bar"><span class="tile-grip" aria-hidden="true">⠿</span><button type="button" class="btn small tile-mv" data-mv="-1">←</button><button type="button" class="btn small tile-mv" data-mv="1">→</button></div>`;
// створити плитки для нових хостів «На Огляді», прибрати плитки тих, з кого галку зняли
function syncHostTiles(hosts) {
  if (dd) return;  // не чіпати сітку посеред перетягування
  const want = new Set(hosts.map((h) => "h:" + h.host));
  let added = false;
  $$('#dash > .tile[data-tile^="h:"]').forEach((el) => { if (!want.has(el.dataset.tile)) el.remove(); });
  for (const id of want) {
    if ($(`#dash > [data-tile="${CSS.escape(id)}"]`)) continue;
    const el = document.createElement("div");
    el.className = "card ping-card tile";
    el.dataset.tile = id;
    el.innerHTML = TILE_BAR + '<div class="tile-body"></div>';
    // нова плитка — одразу після останньої плитки пінгу; збережене місце (якщо є) застосується нижче
    const pings = $$("#dash > .ping-card");
    (pings[pings.length - 1] || dash.firstElementChild).after(el);
    added = true;
  }
  if (added) {
    if (!dashEditing && dashSaved.some((id) => want.has(id))) applyDashOrder(dashSaved);
    setDashEdit(dashEditing);   // підписи стрілок для нових плиток
  }
}

// ---------------- Огляд: порядок плиток ----------------
const DASH_DEFAULT = ["ping1", "ping2", "ping3", "speed", "auto", "isp", "wg", "lan", "sys"];
const dash = $("#dash");
let dashEditing = false, dashBefore = null, dd = null, dashSaved = [];
const dashOrder = () => $$("#dash > .tile").map((el) => el.dataset.tile);
function applyDashOrder(order) {
  const present = dashOrder();
  const o = (order || []).filter((id) => present.includes(id));
  const rest = present.filter((id) => !o.includes(id));   // нові плитки лишаються в кінці, у поточному порядку
  [...o, ...rest].forEach((id) => {
    const el = $(`#dash > [data-tile="${CSS.escape(id)}"]`);
    if (el) dash.appendChild(el);
  });
}
// за замовчуванням: основні пінги, далі хости «На Огляді», далі решта
const dashDefault = () => {
  const hosts = dashOrder().filter((id) => id.startsWith("h:"));
  return [...DASH_DEFAULT.slice(0, 3), ...hosts, ...DASH_DEFAULT.slice(3)];
};
function setDashEdit(on) {
  dashEditing = on;
  dash.classList.toggle("editing", on);
  const b = $("#dash-edit");
  b.textContent = t(on ? "dash_done" : "dash_edit");
  b.setAttribute("aria-pressed", String(on));
  b.classList.toggle("primary", on);
  $("#dash-hint").classList.toggle("hidden", !on);
  $("#dash-reset").classList.toggle("hidden", !on);
  $$("#dash .tile-mv").forEach((x) => x.setAttribute("aria-label", t(x.dataset.mv === "1" ? "tile_later" : "tile_earlier")));
}
$("#dash-edit").onclick = async () => {
  if (!dashEditing) { dashBefore = dashOrder(); setDashEdit(true); return; }
  setDashEdit(false);
  const order = dashOrder();
  if (JSON.stringify(order) === JSON.stringify(dashBefore)) return;
  try { await post("/api/settings", { overview_order: order }); dashSaved = order; toast(t("dash_saved")); }
  catch (err) { toast(err.message, true); applyDashOrder(dashBefore); }
};
$("#dash-reset").onclick = () => applyDashOrder(dashDefault());
// стрілки: на одну видиму плитку раніше / пізніше
dash.addEventListener("click", (e) => {
  const b = e.target.closest(".tile-mv");
  if (!b || !dashEditing) return;
  const tile = b.closest(".tile");
  const vis = $$("#dash > .tile").filter((x) => !x.classList.contains("hidden"));
  const other = vis[vis.indexOf(tile) + Number(b.dataset.mv)];
  if (!other) return;
  if (b.dataset.mv === "1") other.after(tile); else other.before(tile);
  b.focus();
});
// перетягування за ⠿ — мишею і пальцем
dash.addEventListener("pointerdown", (e) => {
  const grip = e.target.closest(".tile-grip");
  if (!grip || !dashEditing || dd) return;
  e.preventDefault();
  const tile = grip.closest(".tile"), r = tile.getBoundingClientRect();
  const cs = getComputedStyle(tile), ph = document.createElement("div");
  ph.className = "tile-ph";
  ph.style.gridColumnStart = cs.gridColumnStart; ph.style.gridColumnEnd = cs.gridColumnEnd;   // та сама ширина
  ph.style.height = r.height + "px";
  tile.after(ph);
  Object.assign(tile.style, { position: "fixed", left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
  tile.classList.add("dragging");
  dd = { tile, ph, dx: e.clientX - r.left, dy: e.clientY - r.top };
  try { grip.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
});
dash.addEventListener("pointermove", (e) => {
  if (!dd) return;
  dd.tile.style.left = e.clientX - dd.dx + "px";
  dd.tile.style.top = e.clientY - dd.dy + "px";
  if (e.clientY < 70) window.scrollBy(0, -14); else if (e.clientY > window.innerHeight - 50) window.scrollBy(0, 14);
  const over = document.elementFromPoint(e.clientX, e.clientY);
  const tgt = over && over.closest("#dash > .tile");
  if (!tgt || tgt === dd.tile || tgt.classList.contains("hidden")) return;
  const r = tgt.getBoundingClientRect();
  const row = r.width < dash.clientWidth * 0.9;      // кілька плиток у рядку — порівнюємо по X, одна — по Y
  const after = row ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
  if (after) tgt.after(dd.ph); else tgt.before(dd.ph);
});
function dashDrop() {
  if (!dd) return;
  dd.ph.replaceWith(dd.tile);
  dd.tile.classList.remove("dragging");
  ["position", "left", "top", "width", "height"].forEach((k) => (dd.tile.style[k] = ""));
  dd = null;
}
dash.addEventListener("pointerup", dashDrop);
dash.addEventListener("pointercancel", dashDrop);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && dashEditing && !dd) { applyDashOrder(dashBefore); setDashEdit(false); } });

// ---------------- старт ----------------
(async function init() {
  let saved = null;
  try { saved = localStorage.getItem("molfar.lang"); } catch (_) { /* ignore */ }
  setLangLocal(saved || "uk");              // одразу, без «блимання» мови
  try {
    const s = await api("/api/settings");
    deviceName = s.device_name || "";
    dashSaved = s.overview_order || [];
    applyDashOrder(dashSaved);
    setLangLocal(s.language);                 // мова з пристрою — головна
  } catch (_) { /* ignore */ }
  await Promise.all([refreshStatus(), loadAuthInfo()]);
  renderServerLabel();
  setInterval(() => { if (!document.hidden) refreshStatus(); }, 5000);
  setInterval(() => { if (!document.hidden && currentTab === "history") loadHistory(); }, 60000);
  const h = location.hash.slice(1);
  if (h && h !== "overview") showTab(h);
})();
