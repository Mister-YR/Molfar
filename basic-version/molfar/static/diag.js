"use strict";
/* Вкладка «Діагностика»: ping, traceroute, nslookup, telnet, iperf3 (клієнт + сервер на Мольфарі). */
(() => {
const q = (s, el = document) => el.querySelector(s);
const TOOLS = ["ping", "traceroute", "nslookup", "telnet", "iperf3"];
const NS_TYPES = ["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "SRV", "CAA"];
const AUTO_OFF = [0, 15, 60, 240, 1440];
const DEF = {
  ping: { host: "1.1.1.1", count: 10, size: 56, family: "" },
  traceroute: { host: "1.1.1.1", mode: "udp", max_hops: 30, port: 443, resolve: false, family: "" },
  nslookup: { host: "google.com", type: "A", server: "" },
  telnet: { host: "", port: 443, wait: 5 },
  iperf3: { host: "", port: 5201, duration: 10, streams: 1, direction: "down", protocol: "tcp", bandwidth: 100, family: "" },
};

let tool = "ping", info = null, job = null, pollT = null, infoT = null, active = false, chart = null, shown = null;
const params = {};
try {
  tool = TOOLS.includes(localStorage.getItem("molfar.diag.tool")) ? localStorage.getItem("molfar.diag.tool") : "ping";
  Object.assign(params, JSON.parse(localStorage.getItem("molfar.diag.params") || "{}"));
} catch (_) { /* приватний режим */ }
for (const k of TOOLS) params[k] = { ...DEF[k], ...(params[k] || {}) };
const saveLocal = () => { try { localStorage.setItem("molfar.diag.tool", tool); localStorage.setItem("molfar.diag.params", JSON.stringify(params)); } catch (_) { /* ignore */ } };

/* ------------------------------------------------------------ помилки API: "code" або "code:{json}" */
function errText(e) {
  const m = String(e && e.message || e || "");
  const i = m.indexOf(":{");
  const code = i > 0 ? m.slice(0, i) : m;
  let kw = {};
  if (i > 0) { try { kw = JSON.parse(m.slice(i + 1)); } catch (_) { /* ignore */ } }
  const key = "dg_e_" + code;
  return I18N[key] ? t(key, kw) : m;
}
const fmtMbps = (v) => v == null ? "—" : v >= 1000 ? (v / 1000).toFixed(2) + " " + t("dg_gbps") : v.toFixed(v >= 100 ? 0 : 1) + " " + t("mbps");
const num = (v, d = 1) => v == null ? "—" : Number(v).toFixed(d);

/* ------------------------------------------------------------ форма інструмента */
function field(name, label, html, cls = "") { return `<label class="${cls}"><span>${label}</span>${html}</label>`; }
function inp(name, type = "text", extra = "") {
  const v = params[tool][name];
  return `<input type="${type}" name="${name}" value="${esc(v == null ? "" : String(v))}" ${extra}>`;
}
function sel(name, opts) {
  const v = String(params[tool][name] ?? "");
  return `<select name="${name}">${opts.map(([val, txt]) => `<option value="${esc(val)}"${String(val) === v ? " selected" : ""}>${esc(txt)}</option>`).join("")}</select>`;
}
const famSel = () => field("family", t("dg_family"), sel("family", [["", t("auto")], ["4", "IPv4"], ["6", "IPv6"]]));
const hostField = (ph) => field("host", t(tool === "iperf3" ? "dg_iperf_server" : "dg_host"),
  inp("host", "text", `placeholder="${esc(ph)}" autocomplete="off" spellcheck="false" required maxlength="253"`), "dg-host");

function renderForm() {
  const p = params[tool];
  q("#dg-form-title").textContent = t("dg_t_" + tool);
  q("#dg-form-hint").innerHTML = t("dg_h_" + tool);
  let f = "";
  if (tool === "ping") {
    f = hostField("1.1.1.1 / google.com") + `<div class="row3">${field("count", t("dg_count"), inp("count", "number", 'min="1" max="100"'))}
      ${field("size", t("dg_size"), inp("size", "number", 'min="16" max="1472"'))}${famSel()}</div>`;
  } else if (tool === "traceroute") {
    f = hostField("1.1.1.1 / google.com") + `<div class="row3">
      ${field("mode", t("dg_mode"), sel("mode", [["udp", "UDP"], ["icmp", "ICMP"], ["tcp", "TCP"]]))}
      ${field("max_hops", t("dg_max_hops"), inp("max_hops", "number", 'min="1" max="64"'))}
      ${p.mode === "tcp" ? field("port", t("dg_port"), inp("port", "number", 'min="1" max="65535"')) : famSel()}</div>
      <label class="check"><input type="checkbox" name="resolve"${p.resolve ? " checked" : ""}> <span>${t("dg_resolve")}</span></label>`;
  } else if (tool === "nslookup") {
    f = hostField("google.com / 8.8.8.8") + `<div class="row2">
      ${field("type", t("dg_type"), sel("type", NS_TYPES.map((x) => [x, x])))}
      ${field("server", t("dg_dns_server"), inp("server", "text", `placeholder="${esc(t("dg_dns_server_ph"))}" autocomplete="off" spellcheck="false"`))}</div>`;
  } else if (tool === "telnet") {
    f = hostField("192.168.1.1 / mail.example.com") + `<div class="row2">
      ${field("port", t("dg_port"), inp("port", "number", 'min="1" max="65535" required'))}
      ${field("wait", t("dg_wait"), inp("wait", "number", 'min="2" max="30"'))}</div>
      <div class="dg-chips" id="dg-ports">${[[22, "SSH"], [23, "Telnet"], [25, "SMTP"], [53, "DNS"], [80, "HTTP"], [443, "HTTPS"], [3389, "RDP"], [8080, "HTTP-alt"]]
        .map(([n, l]) => `<button type="button" class="chip" data-port="${n}">${l} <span class="muted">${n}</span></button>`).join("")}</div>`;
  } else if (tool === "iperf3") {
    f = hostField("192.168.1.10") + `<div class="row3">
      ${field("port", t("dg_port"), inp("port", "number", 'min="1" max="65535"'))}
      ${field("duration", t("dg_duration"), inp("duration", "number", 'min="3" max="60"'))}
      ${field("streams", t("dg_streams"), inp("streams", "number", 'min="1" max="16"'))}</div>
      ${field("direction", t("dg_direction"), sel("direction", [["down", t("dg_dir_down")], ["up", t("dg_dir_up")], ["bidir", t("dg_dir_bidir")]]))}
      <div class="row2">
      ${field("protocol", t("dg_protocol"), sel("protocol", [["tcp", "TCP"], ["udp", "UDP"]]))}
      ${p.protocol === "udp" ? field("bandwidth", t("dg_bandwidth"), inp("bandwidth", "number", 'min="1" max="10000"')) : famSel()}</div>`;
  }
  q("#dg-fields").innerHTML = f;
  renderQuick();
  $$("#dg-tools button").forEach((b) => b.classList.toggle("active", b.dataset.tool === tool));
  const missing = info && info.tools && !info.tools[tool] && tool !== "telnet";
  q("#dg-missing").classList.toggle("hidden", !missing);
  if (missing) q("#dg-missing").innerHTML = t("dg_missing", { tool });
  q("#dg-run").disabled = !!missing || isRunning();
}

function renderQuick() {
  const gw = info && info.gateway;
  let list;
  if (tool === "iperf3") list = [];
  else if (tool === "nslookup") list = ["google.com", "cloudflare.com", "ukr.net", gw].filter(Boolean);
  else if (tool === "telnet") list = [gw, "smtp.gmail.com", "google.com"].filter(Boolean);
  else list = [gw, "1.1.1.1", "8.8.8.8", "google.com"].filter(Boolean);
  q("#dg-quick").innerHTML = list.length
    ? `<span class="muted small">${t("dg_quick")}</span>` + list.map((h) => `<button type="button" class="chip" data-host="${esc(h)}">${h === gw ? t("dg_gateway") + " · " : ""}${esc(h)}</button>`).join("")
    : (tool === "iperf3" ? `<span class="muted small">${t("dg_iperf_hint_client")}</span>` : "");
}

function readForm() {
  const p = params[tool];
  for (const el of q("#dg-fields").querySelectorAll("input, select")) {
    if (el.type === "checkbox") p[el.name] = el.checked;
    else if (el.type === "number") p[el.name] = el.value === "" ? "" : Number(el.value);
    else p[el.name] = el.value.trim();
  }
  saveLocal();
  return p;
}

/* ------------------------------------------------------------ запуск і опитування */
const isRunning = () => job && job.status === "running";

async function run(e) {
  e && e.preventDefault();
  if (isRunning()) return;
  const p = readForm();
  try {
    job = await post("/api/diag/run", { tool, ...p });
    job.allLines = job.lines.slice();
    shown = null;
    renderJob(true);
    poll();
  } catch (err) { toast(errText(err), true); }
}

async function stop() {
  if (!isRunning()) return;
  try { await post(`/api/diag/jobs/${job.id}/stop`); } catch (err) { toast(errText(err), true); }
}

async function poll() {
  clearTimeout(pollT);
  if (!job) return;
  try {
    const j = await api(`/api/diag/jobs/${job.id}?since=${job.allLines.length}`);
    job = { ...j, allLines: job.allLines.concat(j.lines) };
    renderJob(false, j.lines);
  } catch (err) {
    job.status = "error"; job.error = errText(err); renderJob(true);
  }
  if (isRunning()) pollT = setTimeout(poll, job.tool === "iperf3" ? 1000 : 600);
  else loadInfo();
}

/* ------------------------------------------------------------ вивід */
function statusBadge(st, err) {
  const cls = { running: "", done: "ok", stopped: "warn", error: "bad" }[st] || "";
  return `<span class="badge ${cls}">${st === "running" ? '<span class="dg-spin"></span>' : ""}${esc(t("dg_st_" + st))}${err ? " · " + esc(errText(err)) : ""}</span>`;
}

function renderJob(full, newLines) {
  const j = job;
  const out = q("#dg-out");
  out.classList.remove("hidden");
  q("#dg-out-title").textContent = `${t("dg_t_" + j.tool)} · ${j.target}`;
  q("#dg-out-status").innerHTML = statusBadge(j.status, j.error) + ` <span class="muted small">${num(j.elapsed, 1)} ${t("dg_sec")}</span>`;
  q("#dg-cmd").textContent = "$ " + (j.cmd || "").replace(/^\/\S*\//, "");
  const con = q("#dg-console");
  const stick = con.scrollTop + con.clientHeight >= con.scrollHeight - 30;
  if (full || !newLines) con.textContent = (j.allLines || j.lines || []).join("\n");
  else if (newLines.length) con.textContent += (con.textContent ? "\n" : "") + newLines.join("\n");
  if (stick) con.scrollTop = con.scrollHeight;
  // прогрес для iperf3 (його JSON приходить лише наприкінці)
  const prog = q("#dg-progress");
  const showProg = j.status === "running" && j.tool === "iperf3";
  prog.classList.toggle("hidden", !showProg);
  if (showProg) {
    const pct = Math.min(100, 100 * j.elapsed / (j.params.duration + 1));
    prog.firstElementChild.style.width = pct.toFixed(0) + "%";
  }
  q("#dg-run").disabled = j.status === "running";
  q("#dg-stop").classList.toggle("hidden", j.status !== "running");
  if (j.status !== "running" && shown !== j.id) { shown = j.id; renderResult(j.tool, j.result, j.params); }
  if (j.status === "running") q("#dg-result").innerHTML = "";
}

function tile(label, value, cls = "", sub = "") {
  return `<div class="dg-tile ${cls}"><div class="muted small">${label}</div><div class="dg-big">${value}</div>${sub ? `<div class="muted small">${sub}</div>` : ""}</div>`;
}
const lossCls = (l) => l == null ? "" : l === 0 ? "ok" : l < 5 ? "warn" : "bad";
const rttCls = (v) => v == null ? "" : v < 30 ? "ok" : v < 100 ? "warn" : "bad";

function sparkBars(vals) {
  if (!vals || vals.length < 2) return "";
  const mx = Math.max(...vals) || 1, w = 100 / vals.length;
  return `<svg class="dg-spark" viewBox="0 0 100 30" preserveAspectRatio="none">${vals.map((v, i) =>
    `<rect x="${(i * w + w * 0.15).toFixed(2)}" y="${(30 - 28 * v / mx).toFixed(2)}" width="${(w * 0.7).toFixed(2)}" height="${(28 * v / mx).toFixed(2)}"><title>${v} ${t("ms")}</title></rect>`).join("")}</svg>`;
}

function renderResult(tl, r, p) {
  const el = q("#dg-result");
  if (chart) { chart.destroy(); chart = null; }
  if (!r) { el.innerHTML = ""; return; }
  let h = "";
  if (tl === "ping") {
    h = `<div class="dg-tiles">${tile(t("dg_loss"), num(r.loss, 1) + "%", lossCls(r.loss), `${r.received ?? 0}/${r.sent ?? 0} ${t("dg_packets")}`)}
      ${tile(t("dg_avg"), num(r.avg, 1) + ` <small>${t("ms")}</small>`, rttCls(r.avg), `${t("dg_minmax")}: ${num(r.min, 1)} / ${num(r.max, 1)}`)}
      ${tile(t("dg_jitter"), num(r.jitter, 2) + ` <small>${t("ms")}</small>`, r.jitter == null ? "" : r.jitter < 5 ? "ok" : r.jitter < 20 ? "warn" : "bad")}</div>
      ${sparkBars(r.rtts)}`;
  } else if (tl === "traceroute") {
    const hops = r.hops || [];
    const mx = Math.max(1, ...hops.map((x) => x.avg || 0));
    h = `<div class="dg-line">${r.reached ? `<span class="badge ok">${t("dg_tr_reached", { n: hops.length })}</span>`
      : `<span class="badge warn">${t("dg_tr_not_reached")}</span>`}</div>
      <div class="table-wrap"><table class="dg-hops"><thead><tr><th>#</th><th>${t("dg_tr_addr")}</th><th class="num">${t("dg_avg")}</th><th class="num">${t("dg_loss")}</th><th></th></tr></thead><tbody>` +
      hops.map((x) => {
        const loss = x.sent ? Math.round(100 * x.lost / x.sent) : null;
        const addr = x.addrs.length ? x.addrs.map((a, i) => `<span class="mono">${esc(a)}</span>${x.names[i] ? ` <span class="muted small">${esc(x.names[i])}</span>` : ""}`).join("<br>")
          : `<span class="muted">${t("dg_tr_noreply")}</span>`;
        return `<tr><td class="num">${x.hop}</td><td>${addr}${x.flags.length ? ` <span class="badge bad">${esc(x.flags.join(" "))}</span>` : ""}</td>
          <td class="num">${x.avg == null ? "—" : num(x.avg, 1) + " " + t("ms")}</td><td class="num ${lossCls(loss)}">${loss == null ? "—" : loss + "%"}</td>
          <td class="dg-barcell"><div class="dg-bar ${rttCls(x.avg)}" style="width:${x.avg ? Math.max(2, 100 * x.avg / mx).toFixed(0) : 0}%"></div></td></tr>`;
      }).join("") + "</tbody></table></div>";
  } else if (tl === "nslookup") {
    h = `<div class="dg-tiles">${tile(t("dg_dns_answers"), (r.answers || []).length, r.error ? "bad" : (r.answers || []).length ? "ok" : "warn")}
      ${tile(t("dg_dns_time"), (r.ms ?? "—") + ` <small>${t("ms")}</small>`, r.ms == null ? "" : r.ms < 100 ? "ok" : r.ms < 500 ? "warn" : "bad")}
      ${tile(t("dg_dns_server_used"), `<span class="mono dg-mid">${esc(r.server || "—")}</span>`)}</div>
      ${r.error ? `<div class="notice">${esc(r.error)}</div>` : ""}
      ${(r.answers || []).length ? `<ul class="dg-answers">${r.answers.map((a) => `<li class="mono">${esc(a)}</li>`).join("")}</ul>` : ""}`;
  } else if (tl === "telnet") {
    const st = r.state;
    const cls = { open: "ok", refused: "bad", timeout: "warn", unreachable: "bad", dns: "bad" }[st] || "warn";
    h = `<div class="dg-telnet ${cls}"><div class="dg-big">${esc(t("dg_tn_" + st))}</div>
      <div class="muted">${esc(p.host)}:${p.port} — ${esc(t("dg_tn_" + st + "_d"))}</div></div>
      ${r.banner && r.banner.length ? `<div class="muted small">${t("dg_banner")}</div><pre class="dg-banner">${esc(r.banner.join("\n"))}</pre>` : ""}`;
  } else if (tl === "iperf3") {
    if (r.error && r.sent_mbps == null && r.mbps == null) {
      h = `<div class="notice">${esc(t("dg_iperf_failed"))}: ${esc(r.error)}</div>`;
    } else {
      const tiles = [];
      const dirLabel = (rev) => rev ? t("dg_dir_down") : t("dg_dir_up");
      if (r.udp) {
        tiles.push(tile(dirLabel(r.reverse) + " · UDP", fmtMbps(r.mbps), "", `${t("dg_jitter")} ${num(r.jitter_ms, 2)} ${t("ms")}`));
        tiles.push(tile(t("dg_loss"), num(r.lost_pct, 2) + "%", lossCls(r.lost_pct), r.packets ? `${r.lost ?? 0}/${r.packets} ${t("dg_packets")}` : ""));
        if (r.mbps_rev != null) tiles.push(tile(t("dg_dir_down") + " · UDP", fmtMbps(r.mbps_rev), "", `${t("dg_loss")} ${num(r.lost_pct_rev, 2)}%`));
      } else {
        tiles.push(tile(r.recv_mbps_rev != null ? t("dg_dir_up") : dirLabel(r.reverse), fmtMbps(r.recv_mbps), "",
          `${t("dg_sender")} ${fmtMbps(r.sent_mbps)} · ${t("dg_retr")} ${r.retransmits ?? "—"}`));
        if (r.recv_mbps_rev != null) tiles.push(tile(t("dg_dir_down"), fmtMbps(r.recv_mbps_rev), "", `${t("dg_retr")} ${r.retransmits_rev ?? "—"}`));
      }
      const cpu = (v) => v == null ? null : Math.min(100, v);   // iperf3 рахує суму ядер — може бути >100
      if (r.cpu_local != null) tiles.push(tile(t("dg_cpu"), `${num(cpu(r.cpu_local), 0)}% <small>/ ${num(cpu(r.cpu_remote), 0)}%</small>`, r.cpu_local > 90 ? "warn" : "", t("dg_cpu_d")));
      h = `<div class="dg-tiles">${tiles.join("")}</div><div class="dg-chart"><canvas id="dg-chart"></canvas></div>
        ${r.cpu_local > 90 ? `<div class="muted small">${t("dg_cpu_warn")}</div>` : ""}`;
    }
  }
  el.innerHTML = h;
  if (tl === "iperf3" && r.intervals && r.intervals.length && window.Chart) drawChart(r);
}

function drawChart(r) {
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const ds = [{ label: r.intervals_rev.length ? t("dg_dir_up") : (r.reverse ? t("dg_dir_down") : t("dg_dir_up")),
    data: r.intervals.map((x) => x[1]), borderColor: css("--c1"), backgroundColor: css("--c1") + "33", fill: true, tension: 0.25, pointRadius: 0 }];
  if (r.intervals_rev.length) ds.push({ label: t("dg_dir_down"), data: r.intervals_rev.map((x) => x[1]), borderColor: css("--c2"), backgroundColor: css("--c2") + "22", fill: true, tension: 0.25, pointRadius: 0 });
  chart = new Chart(q("#dg-chart"), {
    type: "line",
    data: { labels: r.intervals.map((x) => x[0] + " " + t("dg_sec")), datasets: ds },
    options: { animation: false, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: css("--muted"), boxWidth: 12 } } },
      scales: { x: { ticks: { color: css("--muted"), maxTicksLimit: 12 }, grid: { color: css("--border") } },
        y: { beginAtZero: true, title: { display: true, text: t("mbps"), color: css("--muted") }, ticks: { color: css("--muted") }, grid: { color: css("--border") } } } },
  });
}

/* ------------------------------------------------------------ iperf3-сервер */
function renderServer() {
  const s = info.server, el = q("#dg-server");
  const have = info.tools.iperf3;
  const ips = (info.addresses || []).filter((a) => !a.iface.startsWith("docker") && !a.iface.startsWith("br-")).map((a) => a.addr.split("/")[0]);
  const ip = ips[0] || "IP";
  const state = !have ? `<span class="badge bad">${t("dg_srv_noiperf")}</span>`
    : s.running ? `<span class="badge ok">${t("dg_srv_on", { port: s.port })}</span>`
    : s.enabled ? `<span class="badge warn">${t("dg_srv_starting")}</span>` : `<span class="badge">${t("dg_srv_off")}</span>`;
  const offTxt = s.off_at ? t("dg_srv_off_at", { time: fmtTime(s.off_at, false) }) : "";
  el.innerHTML = `<div class="card-head"><h2>${t("dg_srv_title")}</h2><div>${state}</div></div>
    <div class="muted small">${t("dg_srv_note")}</div>
    <form id="dg-srv-form" class="dg-srv-row">
      <label class="check"><span class="switch"><input type="checkbox" name="enabled"${s.enabled ? " checked" : ""}${have ? "" : " disabled"}><span></span></span> <span>${t("dg_srv_enable")}</span></label>
      <label><span>${t("dg_port")}</span><input type="number" name="port" min="1024" max="65535" value="${s.port}"></label>
      <label><span>${t("dg_srv_autooff")}</span><select name="auto_off_min">${AUTO_OFF.map((m) => `<option value="${m}"${m === s.auto_off_min ? " selected" : ""}>${m ? t("dg_srv_after", { m: m >= 60 ? (m / 60) + " " + t("dg_hours") : m + " " + t("dg_min") }) : t("dg_srv_never")}</option>`).join("")}</select></label>
      <button class="btn small" type="submit"${have ? "" : " disabled"}>${t("apply")}</button>
    </form>
    ${s.error ? `<div class="notice">${esc(errText(new Error(s.error)))}</div>` : ""}
    ${offTxt ? `<div class="muted small">${offTxt}</div>` : ""}
    <div class="dg-howto">
      <div class="muted small">${t("dg_srv_howto")}</div>
      <pre class="dg-cmdline">iperf3 -c ${esc(ip)} -p ${s.port}          <span class="muted"># ${t("dg_dir_up_from")}</span>
iperf3 -c ${esc(ip)} -p ${s.port} -R       <span class="muted"># ${t("dg_dir_down_from")}</span></pre>
      ${ips.length > 1 ? `<div class="muted small">${t("dg_srv_addrs")}: ${ips.map((x) => `<span class="mono">${esc(x)}</span>`).join(", ")}</div>` : ""}
    </div>
    ${s.clients && s.clients.length ? `<div class="table-wrap"><table><thead><tr><th>${t("dg_srv_client")}</th><th>${t("dg_time")}</th><th class="num">${t("dg_srv_rate")}</th></tr></thead><tbody>
      ${s.clients.map((c) => `<tr><td class="mono">${esc(c.ip)}</td><td>${fmtTime(c.ts)}</td><td class="num">${c.rate == null ? "—" : fmtMbps(c.rate)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${s.log && s.log.length ? `<details class="dg-log"><summary class="muted small">${t("dg_srv_log")}</summary><pre class="dg-console small">${esc(s.log.join("\n"))}</pre></details>` : ""}`;
  q("#dg-srv-form").onsubmit = saveServer;
  q("#dg-srv-form [name=enabled]").onchange = saveServer;
}

async function saveServer(e) {
  e && e.preventDefault && e.preventDefault();
  const f = q("#dg-srv-form");
  const body = { enabled: f.enabled.checked, port: Number(f.port.value), auto_off_min: Number(f.auto_off_min.value) };
  try {
    info.server = await post("/api/diag/iperf/server", body);
    toast(t(body.enabled ? "dg_srv_toast_on" : "dg_srv_toast_off", { port: body.port }));
  } catch (err) { toast(errText(err), true); }
  renderServer();
}

/* ------------------------------------------------------------ історія */
function histSummary(h) {
  const s = h.summary || {};
  if (s.error && h.status !== "done") return `<span class="bad">${esc(errText(new Error(s.error)))}</span>`;
  switch (h.tool) {
    case "ping": return s.sent ? `${t("dg_loss")} ${num(s.loss, 1)}% · ${num(s.avg, 1)} ${t("ms")} · ${t("dg_jitter").toLowerCase()} ${num(s.jitter, 2)}` : "—";
    case "traceroute": return `${s.hops ?? 0} ${t("dg_hops")}${s.reached ? "" : " · " + t("dg_tr_not_reached")}${s.last_avg != null ? " · " + num(s.last_avg, 1) + " " + t("ms") : ""}`;
    case "nslookup": return s.error ? `<span class="bad">${esc(s.error)}</span>` : `${s.answers} · <span class="mono">${esc(s.first || "")}</span>`;
    case "telnet": return esc(t("dg_tn_" + (s.state || "error")));
    case "iperf3": return s.udp ? `${fmtMbps(s.mbps)} · ${t("dg_loss")} ${num(s.lost_pct, 2)}%`
      : `${fmtMbps(s.recv_mbps)}${s.recv_mbps_rev != null ? " / " + fmtMbps(s.recv_mbps_rev) : ""}`;
    default: return "";
  }
}
function renderHistory() {
  const rows = info.history || [];
  q("#dg-history").innerHTML = rows.length ? `<thead><tr><th>${t("dg_time")}</th><th>${t("dg_tool")}</th><th>${t("dg_target")}</th><th>${t("dg_result")}</th><th>${t("dg_status")}</th></tr></thead><tbody>` +
    rows.map((h) => `<tr data-id="${h.id}"><td>${fmtTime(h.ts)}</td><td>${esc(t("dg_t_" + h.tool))}</td><td class="mono">${esc(h.target)}</td>
      <td>${histSummary(h)}</td><td>${statusBadge(h.status)}</td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="muted">${t("dg_hist_empty")}</td></tr></tbody>`;
  q("#dg-hist-clear").classList.toggle("hidden", !rows.length);
}

async function openHistory(id) {
  try {
    const r = await api(`/api/diag/history/${id}`);
    job = { id: "h" + id, tool: r.tool, target: r.target, params: r.params, status: r.status, error: r.summary.error,
      elapsed: r.duration, cmd: "", allLines: (r.output || "").split("\n"), lines: [], result: null };
    shown = null;
    renderJob(true);
    q("#dg-result").innerHTML = `<div class="muted small">${t("dg_hist_from", { time: fmtTime(r.ts) })}</div>`;
    q("#dg-out").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { toast(errText(err), true); }
}

/* ------------------------------------------------------------ дані */
async function loadInfo() {
  try {
    info = await api("/api/diag");
    renderServer();
    renderHistory();
    if (!q("#dg-fields").innerHTML) renderForm();
    else { renderQuick(); q("#dg-run").disabled = isRunning() || (!info.tools[tool] && tool !== "telnet"); }
    const running = (info.jobs || []).find((j) => j.status === "running");
    if (running && !isRunning()) { job = { ...running, allLines: [] }; job.allLines = []; poll(); }
  } catch (err) { toast(errText(err), true); }
  clearTimeout(infoT);
  if (active) infoT = setTimeout(() => { if (!document.hidden && !isRunning()) loadInfo(); else if (active) infoT = setTimeout(loadInfo, 10000); }, 10000);
}

/* ------------------------------------------------------------ події */
q("#dg-tools").onclick = (e) => {
  const b = e.target.closest("button[data-tool]");
  if (!b || b.dataset.tool === tool) return;
  readForm();
  tool = b.dataset.tool; saveLocal(); renderForm();
};
q("#dg-form").onsubmit = run;
q("#dg-stop").onclick = stop;
q("#dg-fields").addEventListener("change", (e) => {
  if (["mode", "protocol"].includes(e.target.name)) { readForm(); renderForm(); }
});
q("#dg-form").addEventListener("click", (e) => {
  const h = e.target.closest("button[data-host]");
  if (h) { q("#dg-fields [name=host]").value = h.dataset.host; readForm(); return; }
  const p = e.target.closest("button[data-port]");
  if (p) { q("#dg-fields [name=port]").value = p.dataset.port; readForm(); }
});
q("#dg-copy").onclick = async () => {
  const txt = q("#dg-cmd").textContent + "\n" + q("#dg-console").textContent;
  try { await navigator.clipboard.writeText(txt); toast(t("dg_copied")); } catch (_) {
    const r = document.createRange(); r.selectNodeContents(q("#dg-console"));
    const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }
};
q("#dg-history").addEventListener("click", (e) => { const r = e.target.closest("tr[data-id]"); if (r) openHistory(r.dataset.id); });
q("#dg-hist-clear").onclick = async () => {
  try { await api("/api/diag/history", { method: "DELETE" }); await loadInfo(); } catch (err) { toast(errText(err), true); }
};

window.Diag = {
  show() { active = true; if (!q("#dg-fields").innerHTML) renderForm(); loadInfo(); },
  hide() { active = false; clearTimeout(infoT); },
  rerender() {
    if (!q("#dg-fields").innerHTML) return;
    readForm(); renderForm();
    if (info) { renderServer(); renderHistory(); }
    if (job) { shown = null; renderJob(true); }
  },
};
})();
