"use strict";
/* Вкладка «Wi-Fi ефір»: спектр сусідніх мереж, оцінка каналів, водоспад за час, рекомендація каналу. */
(() => {
const q = (s, el = document) => el.querySelector(s);
const SVGNS = "http://www.w3.org/2000/svg";
const Y_MIN = -95, Y_MAX = -25;                  // дБм на осі спектра
const CH24 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13];
const CH5 = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165];
const CH5_LABEL = new Set([36, 52, 100, 116, 132, 149, 165]);
// відрізки частот (МГц) на осі X; між відрізками — розрив (там немає каналів Wi-Fi)
const SEGS = { "2.4": [[2392, 2484]], "5": [[5165, 5335], [5485, 5725], [5730, 5840]] };
const DFS5 = new Set([52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144]);
const WEATHER5 = new Set([120, 124, 128]);      // метеорадари: у ЄС перевірка до 10 хв
const freqOf = (band, ch) => band === "2.4" ? 2407 + 5 * ch : 5000 + 5 * ch;
const chans = (band) => band === "2.4" ? CH24 : CH5;

let D = null, H = null, band = "2.4", range = "day", sel = null, hover = null, active = false;
let pollT = null, histFor = null;
try { band = localStorage.getItem("molfar.wifi.band") === "5" ? "5" : "2.4"; } catch (_) { /* приватний режим */ }

const level = (s) => s == null ? "" : s >= 70 ? "ok" : s >= 40 ? "warn" : "bad";
const levelText = (s) => t("wf_lvl_" + (level(s) || "na"));
const ssidOf = (b) => b.ssid ? esc(b.ssid) : `<i class="muted">${t("wf_hidden")}</i>`;
const ssidPlain = (b) => b.ssid || t("wf_hidden");
const blockTxt = (blk) => blk ? `${blk[0]}–${blk[blk.length - 1]}` : "";

/* ------------------------------------------------------------ вісь X */
function xmap(band, x0, x1) {
  const segs = SEGS[band], gap = band === "5" ? 14 : 0;
  const total = segs.reduce((a, [lo, hi]) => a + hi - lo, 0);
  const k = (x1 - x0 - gap * (segs.length - 1)) / total;
  const starts = [];
  let x = x0;
  for (const [lo, hi] of segs) { starts.push(x); x += (hi - lo) * k + gap; }
  const fx = (f) => {
    for (let i = 0; i < segs.length; i++) {
      const [lo, hi] = segs[i];
      if (f >= lo - 0.01 && f <= hi + 0.01) return starts[i] + (Math.min(hi, Math.max(lo, f)) - lo) * k;
    }
    return null;
  };
  fx.k = k; fx.segs = segs.map(([lo, hi], i) => [starts[i], starts[i] + (hi - lo) * k]);
  return fx;
}
const cellHalf = (band) => band === "2.4" ? 2.5 : 10;

function el(name, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

/* ------------------------------------------------------------ DFS */
// DFS за прапорцями адаптера (radar detection); якщо їх немає — за стандартом (52–144)
function isDfs(ch) {
  const c = D && D.scan ? D.scan.bands["5"].scores.find((x) => x.ch === ch) : null;
  return c ? !!c.dfs : DFS5.has(ch);
}
// суцільні групи каналів 5 ГГц з однаковою ознакою DFS (розрив 64→100 і 144→149 теж ділить групу)
function dfsGroups() {
  const out = [];
  for (const ch of CH5) {
    const d = isDfs(ch), last = out[out.length - 1];
    if (last && last.dfs === d && ch - last.b === 4) last.b = ch;
    else out.push({ a: ch, b: ch, dfs: d });
  }
  return out;
}
// канали 5 ГГц, які займає мережа (з урахуванням ширини 40/80/160 МГц)
function spanChans(n) {
  const lo = n.center - n.width / 2, hi = n.center + n.width / 2;
  return CH5.filter((ch) => { const f = freqOf("5", ch); return f > lo && f < hi; });
}
const dfsBadge = (dfs) => dfs ? `<span class="badge dfs">DFS</span>` : `<span class="badge nodfs">${t("wf_nodfs")}</span>`;
// рядок «DFS / без DFS» під підписами каналів: дужка над групою + підпис
function drawDfsRowSvg(parent, fx, y) {
  const g = el("g", { class: "wf-dfsrow" }, parent);
  for (const gr of dfsGroups()) {
    const x0 = fx(freqOf("5", gr.a) - 10), x1 = fx(freqOf("5", gr.b) + 10);
    if (x0 == null || x1 == null) continue;
    const a = x0 + 2, b = x1 - 2, cls = gr.dfs ? "dfs" : "nodfs";
    el("path", { d: `M${a},${y - 4} V${y} H${b} V${y - 4}`, class: "wf-dfsline " + cls }, g);
    const full = gr.dfs ? (WEATHER5.has(gr.a) || gr.b - gr.a >= 40 ? t("wf_dfs_row") : "DFS") : t("wf_nodfs");
    const txt = (b - a) >= full.length * 6.2 + 6 ? full : (gr.dfs ? "DFS" : (b - a) >= 30 ? t("wf_nodfs_short") : "");
    if (txt) el("text", { x: (a + b) / 2, y: y + 12, "text-anchor": "middle", class: "wf-dfstxt " + cls }, g).textContent = txt;
  }
}
function drawDfsRowCanvas(g, fx, y) {
  g.save();
  g.font = "11px system-ui, sans-serif"; g.textAlign = "center"; g.lineWidth = 1.5;
  for (const gr of dfsGroups()) {
    const x0 = fx(freqOf("5", gr.a) - 10), x1 = fx(freqOf("5", gr.b) + 10);
    if (x0 == null || x1 == null) continue;
    const a = x0 + 2, b = x1 - 2;
    g.strokeStyle = gr.dfs ? css("--warn") : css("--muted");
    g.setLineDash(gr.dfs ? [4, 3] : []);
    g.beginPath(); g.moveTo(a, y - 4); g.lineTo(a, y); g.lineTo(b, y); g.lineTo(b, y - 4); g.stroke();
    const full = gr.dfs ? "DFS" : t("wf_nodfs");
    const txt = (b - a) >= full.length * 6.2 + 6 ? full : (gr.dfs ? "DFS" : (b - a) >= 30 ? t("wf_nodfs_short") : "");
    g.fillStyle = gr.dfs ? css("--text") : css("--muted");
    if (txt) g.fillText(txt, (a + b) / 2, y + 12);
  }
  g.restore();
}

/* ------------------------------------------------------------ завантаження */
async function load() {
  clearTimeout(pollT);
  try {
    D = await api("/api/wifi");
  } catch (err) {
    q("#wf-notice").innerHTML = `<div class="warn-box bad">${esc(err.message)}</div>`;
    return;
  }
  render();
  if (!D.available) return;
  if (histFor !== range + ":" + (D.scan ? D.scan.id : 0)) loadHistory();
  if (active && D.scanning) pollT = setTimeout(load, 2500);
}
async function loadHistory() {
  histFor = range + ":" + (D && D.scan ? D.scan.id : 0);
  try { H = await api("/api/wifi/history?range=" + range); } catch (_) { H = null; }
  renderWaterfall(); renderPeriod();
}

async function scan() {
  const b = q("#wf-scan");
  b.disabled = true;
  b.textContent = t("wf_scanning");
  if (D) { D.scanning = true; renderLine(); }
  try {
    D = await post("/api/wifi/scan");
    toast(t("wf_scan_done", { n: D.scan ? D.scan.networks.length : 0 }));
  } catch (err) {
    toast(err.message, true);
    try { D = await api("/api/wifi"); } catch (_) { /* лишаємо старі дані */ }
  }
  b.disabled = false;
  b.textContent = t("wf_scan");
  render();
  loadHistory();
}

/* ------------------------------------------------------------ рендер */
function render() {
  if (!D) return;
  $$("#wf-band button").forEach((x) => x.classList.toggle("active", x.dataset.band === band));
  $$("#wf-range button").forEach((x) => x.classList.toggle("active", x.dataset.range === range));
  const sb = q("#wf-scan");
  sb.disabled = !!D.scanning || !D.iface;
  sb.textContent = t(D.scanning ? "wf_scanning" : "wf_scan");
  if (!D.available) {        // відкрили #wifi напряму, а функція вимкнена чи адаптера немає
    q("#wf-body").classList.add("hidden");
    q("#wf-scan").disabled = true;
    q("#wf-line").textContent = "";
    q("#wf-notice").innerHTML = `<div class="warn-box">${t(D.feature ? "wf_no_iface" : "wf_off")}</div>`;
    return;
  }
  q("#wf-body").classList.toggle("hidden", !D.scan);   // спершу показати — інакше ширина графіків 0
  renderLine(); renderNotice(); renderRec(); renderSpectrum(); renderWaterfall(); renderPeriod(); renderTable();
}

function renderLine() {
  const s = D.scan, parts = [];
  if (D.scanning) parts.push(t("wf_line_scanning"));
  else if (s) parts.push(t("wf_line_scanned", { ago: fmtAgo(Math.round(Date.now() / 1000 - s.ts)) }));
  if (D.iface) parts.push(`<span class="mono">${esc(D.iface)}</span>`);
  if (s) parts.push(t("wf_line_nets", { n: s.networks.length }));
  if (D.reg) parts.push(t("wf_line_reg", { r: esc(D.reg) }));
  if (D.link && D.link.connected) parts.push(t("wf_line_link", { ssid: esc(D.link.ssid), ch: D.link.channel ?? "?" }));
  parts.push(D.enabled ? t("wf_line_auto", { m: D.every_min }) : t("wf_line_manual"));
  q("#wf-line").innerHTML = parts.join(" · ");
}

function renderNotice() {
  const box = q("#wf-notice"), out = [];
  if (!D.iface) out.push(`<div class="warn-box">${t("wf_no_iface")}</div>`);
  else if (!D.helper) out.push(`<div class="warn-box">${t("wf_no_helper")}</div>`);
  if (D.error && (!D.scan || D.error.ts > D.scan.ts))
    out.push(`<div class="warn-box bad">${esc(t("wf_err_" + D.error.code) !== "wf_err_" + D.error.code ? t("wf_err_" + D.error.code) : t("wf_err_generic"))}${D.error.err ? ` <span class="mono small">${esc(D.error.err)}</span>` : ""}</div>`);
  if (D.link && D.link.connected && !D.scan) out.push(`<div class="warn-box">${t("wf_on_wifi")}</div>`);
  if (D.scan && D.scan.cached) out.push(`<div class="warn-box">${t("wf_cached")}</div>`);
  if (!D.scan && D.iface && !D.scanning) out.push(`<div class="card wf-empty"><p>${t("wf_empty")}</p></div>`);
  box.innerHTML = out.join("");
}

/* --- рекомендація --- */
function recBand(b) {
  const B = D.scan.bands[b], r = B.rec, name = t(b === "2.4" ? "band_2_4" : "band_5");
  if (!B.supported) return `<div class="wf-rec-band"><div class="wf-rec-h">${name}</div><div class="muted">${t("wf_band_unsupported")}</div></div>`;
  if (!r) return `<div class="wf-rec-band"><div class="wf-rec-h">${name}</div><div class="muted">${t("no_data")}</div></div>`;
  const what = b === "5" && r.block ? t("wf_rec_block", { b: blockTxt(r.block), w: r.width }) : t("wf_rec_width", { w: r.width });
  const lines = [];
  const cur = B.current;
  if (cur) {
    const same = b === "2.4" ? cur.ch === r.ch : (r.block || []).includes(cur.ch);
    const gain = cur.score != null ? r.score - cur.score : null;
    if (same || (gain != null && gain < 8))
      lines.push(`<div class="wf-cur ok">✓ ${t("wf_cur_ok", { ssid: esc(cur.ssid || t("wf_hidden")), ch: cur.ch })}</div>`);
    else
      lines.push(`<div class="wf-cur warn">${t("wf_cur_move", { ssid: esc(cur.ssid || t("wf_hidden")), ch: cur.ch, s: cur.score ?? "—", to: r.ch, d: gain != null ? "+" + gain : "" })}</div>`);
  } else lines.push(`<div class="muted small">${t("wf_cur_unknown")}</div>`);
  if (r.dfs) lines.push(`<div class="muted small">${t("wf_dfs_note")}</div>`);
  if (r.alt && b === "2.4") lines.push(`<div class="muted small">${t("wf_alt_1213", { ch: r.alt.ch, s: r.alt.score })}</div>`);
  if (r.alt && b === "5") lines.push(`<div class="muted small">${t(r.alt.dfs ? "wf_alt_dfs" : "wf_alt_nodfs", { b: blockTxt(r.alt.block), ch: r.alt.ch, s: r.alt.score })}</div>`);
  const ranked = (r.ranked || []).slice(0, b === "2.4" ? 3 : 6).map((x) => {
    const lbl = b === "2.4" ? x.ch : blockTxt(x.block);
    return `<div class="wf-rank ${x.ch === r.ch ? "best" : ""}"><span class="wf-rank-l">${lbl}${b === "5" ? " " + dfsBadge(x.dfs) : ""}</span>
      <span class="wf-rank-bar"><i class="${level(x.score)}" style="width:${Math.max(3, x.score)}%"></i></span><span class="wf-rank-v">${x.score}</span></div>`;
  }).join("");
  return `<div class="wf-rec-band">
    <div class="wf-rec-h">${name} <span class="muted small">· ${t("wf_nets_n", { n: B.networks })}</span></div>
    <div class="wf-rec-main"><span class="wf-rec-ch">${t("wf_channel")} ${r.ch}</span>
      <span class="badge ${level(r.score)}">${r.score} · ${levelText(r.score)}</span>${b === "5" ? dfsBadge(!!r.dfs) : ""}</div>
    <div class="muted small">${what}</div>
    ${lines.join("")}
    ${ranked ? `<div class="wf-ranks"><div class="muted small">${t(b === "2.4" ? "wf_ranked_24" : "wf_ranked_5")}</div>${ranked}</div>` : ""}
  </div>`;
}
function renderRec() {
  const box = q("#wf-rec");
  if (!D.scan) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="card-head"><h2>${t("wf_rec_title")}</h2>
      <span class="muted small">${D.scan.survey ? t("wf_survey_yes") : t("wf_survey_no")}</span></div>
    <div class="wf-rec-grid">${recBand("2.4")}${recBand("5")}</div>
    <div class="muted small wf-how">${t("wf_how")}</div>`;
}

/* --- спектр --- */
function networks(b) { return D.scan ? D.scan.networks.filter((n) => n.band === b) : []; }
function scoresOf(b) { return D.scan ? D.scan.bands[b].scores : []; }

function renderSpectrum() {
  const host = q("#wf-spec");
  if (!D.scan) { host.innerHTML = ""; return; }
  const W = Math.max(300, host.clientWidth), small = W < 560;
  const PL = 46, PR = 8, PT = 18, plotH = small ? 170 : 220, STRIP = 24, LBL = 18, DFSR = band === "5" ? 22 : 0;
  const Hh = PT + plotH + 6 + STRIP + LBL + DFSR;
  const fx = xmap(band, PL, W - PR);
  const fy = (dbm) => PT + (Y_MAX - Math.max(Y_MIN, Math.min(Y_MAX, dbm))) / (Y_MAX - Y_MIN) * plotH;
  const svg = el("svg", { width: W, height: Hh, viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": t("wf_spec_aria") });
  // сітка
  const grid = el("g", { class: "wf-grid" }, svg);
  for (let v = -90; v <= -30; v += 10) {
    const y = fy(v);
    el("line", { x1: PL, x2: W - PR, y1: y, y2: y }, grid);
    el("text", { x: PL - 6, y: y + 4, "text-anchor": "end" }, grid).textContent = v;
  }
  el("text", { x: PL - 6, y: PT - 6, "text-anchor": "end", class: "wf-unit" }, grid).textContent = t("wf_dbm");
  if (band === "5") {   // DFS-канали заштриховані у полі спектра
    const defs = el("defs", {}, svg);
    const pat = el("pattern", { id: "wf-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
    el("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "wf-hatchline" }, pat);
    for (const gr of dfsGroups().filter((x) => x.dfs)) {
      const x0 = fx(freqOf("5", gr.a) - 10), x1 = fx(freqOf("5", gr.b) + 10);
      if (x0 != null && x1 != null) el("rect", { x: x0, y: PT, width: x1 - x0, height: plotH, fill: "url(#wf-hatch)", class: "wf-dfszone" }, grid);
    }
  }
  fx.segs.forEach(([a, b]) => el("line", { x1: a, x2: b, y1: fy(Y_MIN), y2: fy(Y_MIN), class: "wf-base" }, grid));
  // рекомендований канал і поточний канал роутера — вертикальні мітки
  const B = D.scan.bands[band], r = B.rec;
  if (r) {
    const lo = r.block ? freqOf(band, r.block[0]) - 10 : freqOf(band, r.ch) - 10;
    const hi = r.block ? freqOf(band, r.block[r.block.length - 1]) + 10 : freqOf(band, r.ch) + 10;
    const x0 = fx(lo), x1 = fx(hi);
    if (x0 != null && x1 != null) {
      el("rect", { x: x0, y: PT, width: Math.max(1, x1 - x0), height: plotH, class: "wf-recband" }, svg);
      el("text", { x: (x0 + x1) / 2, y: PT - 6, "text-anchor": "middle", class: "wf-rectext" }, svg).textContent = "★ " + r.ch;
    }
  }
  // горби мереж: слабші знизу, сильніші й «мої» зверху
  const nets = networks(band).slice().sort((a, b) => (a.mine - b.mine) || (a.signal - b.signal));
  const humps = el("g", {}, svg), hits = [];
  for (const n of nets) {
    const lo = n.center - n.width / 2, hi = n.center + n.width / 2;
    const x0 = fx(lo), x1 = fx(hi);
    if (x0 == null || x1 == null) continue;
    const ramp = Math.min((x1 - x0) * 0.18, 2.5 * fx.k + 2);
    const yb = fy(Y_MIN), yt = fy(n.signal);
    const d = `M${x0},${yb} C${x0 + ramp * 0.5},${yb} ${x0 + ramp * 0.4},${yt} ${x0 + ramp},${yt} L${x1 - ramp},${yt} C${x1 - ramp * 0.4},${yt} ${x1 - ramp * 0.5},${yb} ${x1},${yb}`;
    const cls = "wf-hump" + (n.mine ? " mine" : "") + (sel === n.bssid || hover === n.bssid ? " sel" : "");
    el("path", { d, class: cls, "data-b": n.bssid }, humps);
    hits.push({ n, x0, x1, yt });
  }
  // підписи: свої, вибрана й найсильніші, без накладань
  const lab = el("g", { class: "wf-labels" }, svg), boxes = [];
  const want = networks(band).slice().sort((a, b) => (b.mine - a.mine) || ((b.bssid === sel) - (a.bssid === sel)) || (b.signal - a.signal));
  let shown = 0;
  for (const n of want) {
    if (shown >= (small ? 5 : 9) && !n.mine && n.bssid !== sel) continue;
    const h = hits.find((x) => x.n === n);
    if (!h) continue;
    const txt = ssidPlain(n).slice(0, 18);
    const w = txt.length * 6.3 + 4, cx = Math.min(W - PR - w / 2, Math.max(PL + w / 2, (h.x0 + h.x1) / 2));
    let y = h.yt - 5;
    const box = (yy) => ({ a: cx - w / 2, b: cx + w / 2, t: yy - 11, u: yy + 2 });
    let bx = box(y), tries = 0;
    while (boxes.some((o) => o.a < bx.b && bx.a < o.b && o.t < bx.u && bx.t < o.u) && tries < 4) { y -= 13; bx = box(y); tries++; }
    if (tries >= 4 || y < 10) continue;
    boxes.push(bx);
    el("text", { x: cx, y, "text-anchor": "middle", class: n.mine ? "mine" : n.bssid === sel ? "sel" : "" }, lab).textContent = txt;
    shown++;
  }
  // смуга оцінок каналів
  const sy = PT + plotH + 6, strip = el("g", { class: "wf-strip" }, svg), cells = [];
  const curCh = B.current ? B.current.ch : null;
  for (const c of scoresOf(band)) {
    const x0 = fx(c.freq - cellHalf(band)), x1 = fx(c.freq + cellHalf(band));
    if (x0 == null || x1 == null) continue;
    const off = c.unknown || c.disabled;
    const rr = el("rect", { x: x0 + 1, y: sy, width: Math.max(1, x1 - x0 - 2), height: STRIP, rx: 4,
      class: "wf-cell " + (off ? "off" : level(c.score)) + (r && c.ch === r.ch ? " best" : "") }, strip);
    rr.dataset.ch = c.ch;
    if (x1 - x0 >= 18 && !off) el("text", { x: (x0 + x1) / 2, y: sy + STRIP / 2 + 4, "text-anchor": "middle", class: "wf-cellv" }, strip).textContent = c.score;
    const showLbl = band === "2.4" || x1 - x0 >= 22 || CH5_LABEL.has(c.ch);
    if (showLbl) el("text", { x: (x0 + x1) / 2, y: sy + STRIP + 13, "text-anchor": "middle", class: "wf-chl" + (c.ch === curCh ? " cur" : "") }, strip).textContent = c.ch;
    if (c.ch === curCh) el("path", { d: `M${(x0 + x1) / 2 - 4},${sy - 1} l4,-5 l4,5 z`, class: "wf-curmark" }, strip);
    cells.push({ c, x0, x1 });
  }
  if (band === "5") drawDfsRowSvg(svg, fx, sy + STRIP + LBL + 6);
  host.replaceChildren(svg, tipEl("wf-spec-tip"));
  // наведення
  const tip = q("#wf-spec-tip");
  svg.onpointermove = (e) => {
    const rc = svg.getBoundingClientRect(), x = e.clientX - rc.left, y = e.clientY - rc.top;
    let html = null, hb = null;
    if (y >= sy && y <= sy + STRIP + LBL + DFSR) {
      const cc = cells.find((k) => x >= k.x0 && x <= k.x1);
      if (cc) html = cellTip(cc.c);
    } else if (y >= PT && y <= PT + plotH) {
      const under = hits.filter((h) => x >= h.x0 && x <= h.x1 && y >= h.yt - 4).sort((a, b) => b.n.signal - a.n.signal);
      if (under.length) { hb = under[0].n.bssid; html = netTip(under[0].n, under.length - 1); }
    }
    if (hb !== hover) { hover = hb; highlightRow(); repaintHumps(); }
    showTip(tip, html, x, y, W);
  };
  svg.onpointerleave = () => { if (hover) { hover = null; highlightRow(); repaintHumps(); } showTip(tip, null); };
  svg.onclick = () => { sel = hover && sel !== hover ? hover : null; renderSpectrum(); highlightRow(); };
  renderSpecLegend();
}
function repaintHumps() {
  // легке оновлення підсвітки без повного перерендеру
  document.querySelectorAll("#wf-spec .wf-hump").forEach((p) => p.classList.toggle("sel", p.dataset.b === hover || p.dataset.b === sel));
}
function tipEl(id) { const d = document.createElement("div"); d.className = "wf-tip hidden"; d.id = id; return d; }
function showTip(tip, html, x, y, W) {
  if (!html) { tip.classList.add("hidden"); return; }
  tip.innerHTML = html;
  tip.classList.remove("hidden");
  const tw = tip.offsetWidth;
  tip.style.left = Math.max(4, Math.min(W - tw - 4, x + 12)) + "px";
  tip.style.top = Math.max(0, y - tip.offsetHeight - 10) + "px";
}
function netTip(n, more) {
  return `<b>${ssidOf(n)}</b>${n.mine ? ` <span class="badge ok">${t("wf_mine")}</span>` : ""}
    <div class="kv small"><dt>${t("wf_channel")}</dt><dd>${n.channel} · ${n.width} ${t("wf_mhz")}</dd>
    <dt>${t("wf_signal")}</dt><dd>${n.signal} ${t("wf_dbm")}</dd>
    ${n.band === "5" ? `<dt>DFS</dt><dd>${spanChans(n).some(isDfs) ? t("wf_yes") : t("wf_nodfs")}</dd>` : ""}
    <dt>${t("wf_sec")}</dt><dd>${esc(n.sec)} · ${esc(n.gen)}</dd>
    <dt>BSSID</dt><dd class="mono">${esc(n.bssid)}</dd>${n.vendor ? `<dt>${t("wf_vendor")}</dt><dd>${esc(n.vendor)}</dd>` : ""}</div>
    ${more > 0 ? `<div class="muted small">${t("wf_more_here", { n: more })}</div>` : ""}`;
}
function cellTip(c) {
  if (c.disabled || c.unknown) return `<b>${t("wf_channel")} ${c.ch}</b><div class="muted small">${t(c.disabled ? "wf_ch_disabled" : "wf_ch_unknown")}</div>`;
  return `<b>${t("wf_channel")} ${c.ch}</b> <span class="badge ${level(c.score)}">${c.score} · ${levelText(c.score)}</span>
    <div class="kv small"><dt>${t("wf_same")}</dt><dd>${c.same}</dd><dt>${t("wf_partial")}</dt><dd>${c.partial}</dd>
    <dt>${t("wf_level")}</dt><dd>${c.level} ${t("wf_dbm")}</dd>
    ${c.busy != null ? `<dt>${t("wf_busy")}</dt><dd>${c.busy}%</dd>` : ""}${c.noise != null ? `<dt>${t("wf_noise")}</dt><dd>${c.noise} ${t("wf_dbm")}</dd>` : ""}
    ${D.scan && c.freq > 5000 ? `<dt>DFS</dt><dd>${c.dfs ? (WEATHER5.has(c.ch) ? t("wf_dfs_weather") : t("wf_dfs_short")) : t("wf_nodfs_long")}</dd>` : ""}</div>`;
}
function renderSpecLegend() {
  q("#wf-spec-legend").innerHTML = `
    <span><i class="sw mine"></i>${t("wf_lg_mine")}</span><span><i class="sw other"></i>${t("wf_lg_other")}</span>
    <span><i class="sw recb"></i>${t("wf_lg_rec")}</span>
    <span><i class="sq ok"></i>${t("wf_lvl_ok")}</span><span><i class="sq warn"></i>${t("wf_lvl_warn")}</span><span><i class="sq bad"></i>${t("wf_lvl_bad")}</span>
    ${band === "5" ? `<span><i class="sq hatch"></i>${t("wf_lg_dfs")}</span>` : ""}`;
  q("#wf-dfs-note").innerHTML = band === "5" ? t("wf_dfs_explain") : "";
  q("#wf-dfs-note").classList.toggle("hidden", band !== "5");
}

/* --- водоспад --- */
const SPAN = { day: 86400, week: 7 * 86400, month: 30 * 86400 };
function hex2rgb(h) {
  h = h.replace("#", "");
  if (h.length === 3) h = h.split("").map((x) => x + x).join("");
  const v = parseInt(h, 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
function renderWaterfall() {
  const host = q("#wf-fall");
  if (!host || !D || !D.scan) return;
  const rows = (H && H.rows || []).filter((r) => r.s && r.s[band] && Object.keys(r.s[band]).length);
  const W = Math.max(300, host.clientWidth), PL = 46, PR = 8, Hh = W < 560 ? 150 : 190, PB = band === "5" ? 40 : 18;
  const dpr = window.devicePixelRatio || 1;
  const cv = document.createElement("canvas");
  cv.width = W * dpr; cv.height = (Hh + PB) * dpr; cv.style.width = W + "px"; cv.style.height = Hh + PB + "px";
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  const fx = xmap(band, PL, W - PR);
  const now = Date.now() / 1000, span = SPAN[range];
  const surf = hex2rgb(css("--panel-2") || "#1c232d"), hot = hex2rgb(css("--bad") || "#ef4444");
  const mix = (k) => `rgb(${surf.map((v, i) => Math.round(v + (hot[i] - v) * k)).join(",")})`;
  g.fillStyle = css("--panel-2");
  fx.segs.forEach(([a, b]) => g.fillRect(a, 0, b - a, Hh));
  const ty = (ts) => (now - ts) / span * Hh;
  // крок між скануваннями — для висоти рядка
  const gaps = rows.slice(1).map((r, i) => r.ts - rows[i].ts).sort((a, b) => a - b);
  const step = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 1800;
  const cells = [];
  rows.forEach((r, i) => {
    const next = rows[i + 1] ? rows[i + 1].ts : now;
    const dur = Math.min(next - r.ts, step * 2);
    const y1 = ty(r.ts), y0 = Math.max(0, ty(r.ts + dur));
    const h = Math.max(1.5, y1 - y0);
    for (const [ch, sc] of Object.entries(r.s[band])) {
      const f = freqOf(band, +ch), x0 = fx(f - cellHalf(band)), x1 = fx(f + cellHalf(band));
      if (x0 == null || x1 == null) continue;
      g.fillStyle = mix(Math.pow((100 - sc) / 100, 1.6));   // більше відтінків у середині шкали
      g.fillRect(x0 + 0.5, y1 - h, Math.max(1, x1 - x0 - 1), h);
      cells.push({ x0, x1, y0: y1 - h, y1, ts: r.ts, ch: +ch, sc });
    }
  });
  // вісь часу
  g.fillStyle = css("--muted"); g.font = "11px system-ui, sans-serif"; g.textAlign = "right";
  const ticks = range === "day" ? [0, 6, 12, 18, 24].map((h) => h * 3600) : range === "week" ? [0, 2, 4, 6].map((d) => d * 86400) : [0, 10, 20, 30].map((d) => d * 86400);
  g.strokeStyle = css("--border"); g.lineWidth = 1;
  ticks.forEach((s) => {
    const y = Math.min(Hh - 1, s / span * Hh + 0.5);
    g.beginPath(); g.moveTo(PL, y); g.lineTo(W - PR, y); g.globalAlpha = 0.5; g.stroke(); g.globalAlpha = 1;
    const lbl = s === 0 ? t("wf_now") : range === "day" ? `−${s / 3600} ${t("wf_h")}` : `−${s / 86400} ${t("wf_d")}`;
    g.fillText(lbl, PL - 4, Math.max(10, Math.min(Hh - 2, y + 4)));
  });
  // підписи каналів
  g.textAlign = "center";
  for (const ch of chans(band)) {
    const f = freqOf(band, ch), x0 = fx(f - cellHalf(band)), x1 = fx(f + cellHalf(band));
    if (x0 == null) continue;
    if (band === "2.4" || x1 - x0 >= 22 || CH5_LABEL.has(ch)) g.fillText(ch, (x0 + x1) / 2, Hh + 13);
  }
  if (band === "5") drawDfsRowCanvas(g, fx, Hh + 24);
  if (!rows.length) {
    g.fillStyle = css("--muted"); g.textAlign = "center"; g.font = "13px system-ui, sans-serif";
    const msg = t("wf_fall_empty"), cx = (PL + W - PR) / 2;
    if (g.measureText(msg).width > W - PL - PR - 16 && msg.includes(" — ")) {   // вузький екран — у два рядки
      const [l1, l2] = msg.split(" — ");
      g.fillText(l1, cx, Hh / 2 - 8); g.fillText(l2, cx, Hh / 2 + 10);
    } else g.fillText(msg, cx, Hh / 2);
  }
  host.replaceChildren(cv, tipEl("wf-fall-tip"));
  const tip = q("#wf-fall-tip");
  cv.onpointermove = (e) => {
    const rc = cv.getBoundingClientRect(), x = e.clientX - rc.left, y = e.clientY - rc.top;
    const c = cells.find((k) => x >= k.x0 && x <= k.x1 && y >= k.y0 && y <= k.y1);
    showTip(tip, c ? `<b>${t("wf_channel")} ${c.ch}</b> <span class="badge ${level(c.sc)}">${c.sc} · ${levelText(c.sc)}</span><div class="muted small">${esc(fmtTime(c.ts))}</div>` : null, x, y, W);
  };
  cv.onpointerleave = () => showTip(tip, null);
  q("#wf-fall-n").textContent = rows.length ? t("wf_fall_n", { n: rows.length }) : "";
}
function renderPeriod() {
  const box = q("#wf-period");
  if (!box) return;
  const b = H && H.best && H.best[band], n = H ? H.rows.length : 0;
  if (!b || n < 2) { box.innerHTML = D && D.enabled ? "" : `<span class="muted small">${t("wf_period_hint")}</span>`; return; }
  const what = band === "2.4" ? `${t("wf_channel")} <b>${b.ch}</b>` : `<b>${blockTxt(b.block)}</b> (${t("wf_primary")} ${b.ch})${b.dfs ? ' <span class="badge">DFS</span>' : ""}`;
  box.innerHTML = `${t("wf_period_best", { r: t("wf_range_" + range) })}: ${what} · ${t("wf_avg")} <span class="badge ${level(b.avg)}">${b.avg}</span>${b.min != null ? ` · ${t("wf_min")} ${b.min}` : ""}`;
}

/* --- таблиця мереж --- */
function bars(sig) {
  const n = sig >= -55 ? 4 : sig >= -67 ? 3 : sig >= -75 ? 2 : 1;
  return `<span class="wf-bars" aria-hidden="true">${[1, 2, 3, 4].map((i) => `<i class="${i <= n ? "on" : ""}" style="height:${3 + i * 3}px"></i>`).join("")}</span>`;
}
function renderTable() {
  const tb = q("#wf-table");
  const nets = networks(band);
  q("#wf-net-count").textContent = nets.length;
  if (!nets.length) { tb.innerHTML = `<tr><td class="muted">${t("wf_no_nets")}</td></tr>`; return; }
  tb.innerHTML = `<thead><tr><th>SSID</th><th class="num">${t("wf_channel")}</th><th class="num">${t("wf_width")}</th><th class="num">${t("wf_signal")}</th>
    <th>${t("wf_sec")}</th><th>${t("wf_std")}</th><th>${t("wf_vendor")}</th><th>BSSID</th><th></th></tr></thead><tbody>` +
    nets.map((n) => `<tr data-b="${esc(n.bssid)}" class="${n.mine ? "mine" : ""}${n.bssid === sel ? " sel" : ""}">
      <td>${ssidOf(n)}${n.associated ? ` <span class="badge">${t("wf_linked")}</span>` : ""}</td>
      <td class="num">${n.band === "5" && spanChans(n).some(isDfs) ? '<span class="badge dfs">DFS</span> ' : ""}${n.channel}</td><td class="num">${n.width}</td>
      <td class="num">${bars(n.signal)} ${n.signal}</td>
      <td>${esc(n.sec)}</td><td>${esc(n.gen)}</td><td>${esc(n.vendor || "—")}</td><td class="mono small">${esc(n.bssid)}</td>
      <td>${n.ssid ? `<button type="button" class="btn small ${n.mine ? "primary" : ""}" data-mine="${esc(n.ssid)}">${t(n.mine ? "wf_mine" : "wf_set_mine")}</button>` : ""}</td></tr>`).join("") + "</tbody>";
}
function highlightRow() {
  $$("#wf-table tr[data-b]").forEach((r) => r.classList.toggle("sel", r.dataset.b === hover || r.dataset.b === sel));
}

async function toggleMine(ssid) {
  const cur = (D.my_ssid || "").split(",").map((x) => x.trim()).filter(Boolean);
  const next = cur.includes(ssid) ? cur.filter((x) => x !== ssid) : [...cur, ssid];
  try {
    await post("/api/settings", { wifi_my_ssid: next.join(", ") });
    toast(t(cur.includes(ssid) ? "wf_mine_off" : "wf_mine_on", { s: ssid }));
    await load();
  } catch (err) { toast(err.message, true); }
}

/* ------------------------------------------------------------ події */
q("#wf-scan").onclick = scan;
q("#wf-band").onclick = (e) => {
  const b = e.target.closest("button[data-band]");
  if (!b || b.dataset.band === band) return;
  band = b.dataset.band; sel = null; hover = null;
  try { localStorage.setItem("molfar.wifi.band", band); } catch (_) { /* ignore */ }
  render();
};
q("#wf-range").onclick = (e) => {
  const b = e.target.closest("button[data-range]");
  if (!b || b.dataset.range === range) return;
  range = b.dataset.range;
  $$("#wf-range button").forEach((x) => x.classList.toggle("active", x.dataset.range === range));
  loadHistory();
};
q("#wf-table").addEventListener("click", (e) => {
  const m = e.target.closest("button[data-mine]");
  if (m) { toggleMine(m.dataset.mine); return; }
  const r = e.target.closest("tr[data-b]");
  if (r) { sel = sel === r.dataset.b ? null : r.dataset.b; renderSpectrum(); highlightRow(); }
});
q("#wf-table").addEventListener("pointerover", (e) => {
  const r = e.target.closest("tr[data-b]");
  const b = r ? r.dataset.b : null;
  if (b !== hover) { hover = b; repaintHumps(); }
});
q("#wf-table").addEventListener("pointerleave", () => { hover = null; repaintHumps(); });
let rT;
window.addEventListener("resize", () => { if (!active) return; clearTimeout(rT); rT = setTimeout(() => { renderSpectrum(); renderWaterfall(); }, 120); });
matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => { if (active) renderWaterfall(); });

window.WifiAir = {
  show() { active = true; load(); },
  hide() { active = false; clearTimeout(pollT); },
  rerender() { if (D) { render(); } },
};
})();
