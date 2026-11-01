"use strict";
/* Мольфар — вкладка «Мапа мережі». Використовує спільні tr(), LANG, LOCALE() з i18n.js і showTab() з app.js. */
(() => {
const ICONS = window.NM_ICONS, KIND_ICON = window.NM_KIND_ICON;
const KINDS = ['gateway','switch','ap','sbc','server','nas','pc','laptop','phone','tablet','tv','camera','printer','speaker','iot','unknown'];
const PARENT_KINDS = new Set(['gateway','switch','ap']);
const VLANS = ['lan','srv','iot','guest','none'];
const BANDS = ['','2.4','5','6'];
const COLW = 220, ROWH = 124, X0 = 300, ISPX = 70;

let D = null, skew = 0, byId = new Map(), sel = null, mode = null, formFor = null, confirmDel = false, formErr = null;
let editMap = false, view = { tx: 0, ty: 0, s: 1 }, fitted = false, drag = null, toastT = 0, active = false;
let logGroup = 'all', logItems = [], logDone = false, devEvents = {}, pollT = 0;

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tr = (k, p) => window.t('nm_' + k, p || {});
const has = k => Object.prototype.hasOwnProperty.call(I18N, 'nm_' + k);
const vis = (el, on) => el.classList.toggle('hidden', !on);
const isOpen = el => !el.classList.contains('hidden');
const nowMs = () => Date.now() + skew;

/* ------------------------------------------------------------ format */
function fmtDur(ms) {
  ms = Math.max(0, ms);
  const d = Math.floor(ms / 864e5), h = Math.floor(ms % 864e5 / 36e5), m = Math.floor(ms % 36e5 / 6e4);
  if (d) return `${d} ${tr('d_day')} ${h} ${tr('d_hour')}`;
  if (h) return `${h} ${tr('d_hour')} ${m} ${tr('d_min')}`;
  if (m) return `${m} ${tr('d_min')}`;
  return `${Math.max(1, Math.round(ms / 1000))} ${tr('d_sec')}`;
}
const durS = s => fmtDur((s || 0) * 1000);
function ago(sec) { const ms = nowMs() - sec * 1000; return ms < 60000 ? tr('just_now') : tr('ago', { d: fmtDur(ms) }); }
function fmtDate(sec) {
  return new Date(sec * 1000).toLocaleString(LOCALE(),
    { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
const trunc = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

/* ------------------------------------------------------------ api */
async function api(method, path, body) {
  let r;
  try {
    r = await fetch(path, {
      method, cache: 'no-store',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined
    });
  } catch (e) { throw { code: 'network' }; }
  let data = {};
  try { data = await r.json(); } catch (e) { /* порожня відповідь */ }
  if (r.status === 401) throw { code: 'auth' };
  if (!r.ok) {
    const d = data.detail;
    if (d && typeof d === 'object' && !Array.isArray(d) && d.code) throw { code: d.code, params: d.params || {} };
    if (typeof d === 'string') throw { code: 'message', message: d };
    if (r.status === 422) throw { code: 'validation' };
    throw { code: 'http' + r.status };
  }
  return data;
}
function errText(e, extra) {
  if (e && e.code === 'message') return e.message;
  if (e && e.code === 'validation') return tr('err_validation');
  return has('err_' + (e && e.code)) ? tr('err_' + e.code, Object.assign({}, e.params || {}, extra || {})) : tr('err_generic', { code: e && e.code || '?' });
}

async function load() {
  try {
    D = await api('GET', '/api/netmap');
  } catch (e) {
    if (e.code === 'auth') { banner(tr('err_auth')); return; }
    banner(tr('err_network')); schedule(15000); return;
  }
  banner('');
  skew = D.now * 1000 - Date.now();
  byId = new Map(D.devices.map(d => [d.id, d]));
  render();
  if (!fitted && D.devices.length && active) { fit(); fitted = true; }
  if (mode === 'device' && !formFor && !confirmDel) { if (byId.has(sel) || sel === 'isp') renderPanel(); else closePanel(); }
  if (mode === 'settings') renderSettings();
  schedule(D.scan.state === 'running' || (!D.scan.last && D.enabled) ? 3000 : 15000);
}
function schedule(ms) { clearTimeout(pollT); if (active) pollT = setTimeout(load, ms); }
document.addEventListener('visibilitychange', () => { if (!document.hidden && active) load(); });

/* ------------------------------------------------------------ tree layout */
const visible = () => D ? D.devices : [];
const kidsOf = id => visible().filter(n => n.parent_id === id && n.id !== id).sort((a, b) =>
  (PARENT_KINDS.has(b.kind) - PARENT_KINDS.has(a.kind)) || KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind) || a.name.localeCompare(b.name));
const isInfra = n => n.kind === 'isp' || PARENT_KINDS.has(n.kind) || n.role === 'gateway';
const half = n => isInfra(n) ? 28 : 24;
const canParent = n => n.id === D.root_id || n.role === 'gateway' || PARENT_KINDS.has(n.kind);
function subtreeIds(id) {
  const out = new Set(), st = [id];
  while (st.length) { const c = st.pop(); visible().forEach(n => { if (n.parent_id === c && !out.has(n.id)) { out.add(n.id); st.push(n.id); } }); }
  return out;
}
let ispNode = null;
function layout() {
  const root = byId.get(D.root_id);
  let row = 0;
  const placed = new Set();
  function place(n, depth) {
    placed.add(n.id);
    const kids = kidsOf(n.id).filter(k => !placed.has(k.id));
    n.ax = X0 + depth * COLW;
    if (!kids.length) { n.ay = 70 + row * ROWH; row++; }
    else { kids.forEach(k => place(k, depth + 1)); n.ay = (kids[0].y + kids[kids.length - 1].y) / 2; }
    n.x = n.pos ? n.pos[0] : n.ax; n.y = n.pos ? n.pos[1] : n.ay;
  }
  if (root) place(root, 0);
  visible().forEach(n => { if (!placed.has(n.id)) place(n, 1); });   // сироти — колонка під коренем
  ispNode = null;
  if (root && root.role === 'gateway') {
    const isp = D.isp || {};
    ispNode = { id: 'isp', kind: 'isp', status: 'online', name: isp.name || tr('isp_title'),
      ip: isp.ip || '', sub: isp.asn ? (String(isp.asn).toUpperCase().startsWith('AS') ? isp.asn : 'AS' + isp.asn) : '', x: ISPX, y: root.y };
  }
}

/* ------------------------------------------------------------ render */
function edgePath(a, b) {
  const sx = a.x + half(a), sy = a.y, ex = b.x - half(b), ey = b.y;
  if (ex - sx < 30) return `M${a.x},${sy + half(a)} V${(sy + ey) / 2} H${b.x} V${ey - half(b)}`;
  const mx = sx + Math.min(34, (ex - sx) / 2);
  return `M${sx},${sy} H${mx} V${ey} H${ex}`;
}
function subline(n) {
  if (n.kind === 'isp') return n.sub;
  if (n.link === 'wifi' && (n.ssid || n.band)) return [n.ssid, n.band ? tr('band_' + n.band) : ''].filter(Boolean).join(' · ');
  return n.vendor_short || n.vendor || (n.private_mac ? tr('private_mac') : tr('kind_' + n.kind));
}
function iconSvg(kind) { return ICONS[KIND_ICON[kind] || 'unknown'] || ICONS.unknown; }

function render() {
  if (!D) return;
  layout();
  const E = [], N = [];
  if (ispNode) E.push(`<path class="nm-edge wan" d="${edgePath(ispNode, byId.get(D.root_id))}"/>`);
  visible().forEach(n => {
    const p = byId.get(n.parent_id);
    if (!p || n.id === D.root_id) return;
    E.push(`<path class="nm-edge ${n.link === 'wifi' ? 'wifi' : ''} ${n.status === 'offline' ? 'dim' : ''}" d="${edgePath(p, n)}"/>`);
  });
  const all = ispNode ? [ispNode, ...visible()] : visible();
  all.forEach(n => {
    const h = half(n), sc = (isInfra(n) ? 30 : 26) / 24, isp = n.kind === 'isp';
    const cls = ['nm-node', 'st-' + n.status, isInfra(n) ? 'infra' : '', n.is_new && n.status === 'online' ? 'is-new' : '',
      sel === n.id ? 'sel' : '', drag && drag.target === n.id ? 'drop' : '', drag && drag.moved && drag.id === n.id && editMap ? 'dragging' : '',
      isp ? '' : 'movable'].join(' ');
    const vl = isp || n.vlan === 'none' ? '' : `<text class="vl v-${n.vlan}" y="${h + 60}">${esc(tr('vlan_' + n.vlan))}</text>`;
    const label = `${n.name}, ${isp ? tr('isp_title') : tr('st_' + n.status)}`;
    N.push(`<g class="${cls}" data-id="${esc(n.id)}" transform="translate(${n.x},${n.y})" tabindex="0" role="button" aria-label="${esc(label)}">
      <rect class="box" x="${-h}" y="${-h}" width="${2 * h}" height="${2 * h}" rx="${isInfra(n) ? 14 : 12}"/>
      <g class="ic" transform="translate(${-12 * sc},${-12 * sc}) scale(${sc})">${iconSvg(n.kind)}</g>
      ${isp ? '' : `<circle class="dot" cx="${h - 3}" cy="${-h + 3}" r="5"/>`}
      ${n.protected ? `<path class="prot" transform="translate(${-h - 3},${-h - 3}) scale(.5)" d="M12 2 4 5v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V5z"/>` : ''}
      <text class="nm" y="${h + 17}">${esc(trunc(n.name, 22))}</text>
      <text class="ip" y="${h + 32}">${esc(n.ip || '—')}</text>
      <text class="sb" y="${h + 46}">${esc(trunc(subline(n), 28))}</text>${vl}
    </g>`);
  });
  $('nm-edges').innerHTML = E.join('');
  $('nm-nodes').innerHTML = N.join('');
  renderHeader();
}
function renderHeader() {
  const c = D.counts;
  $('nm-chips').innerHTML = `<span class="badge ok">${tr('chip_online')} <b>${c.online}</b></span><span class="badge">${tr('chip_offline')} <b>${c.offline}</b></span>` +
    (c.new ? `<span class="badge warn">${tr('chip_new')} <b>${c.new}</b></span>` : '') + `<span class="badge">${tr('chip_hidden')} <b>${c.hidden}</b></span>`;
  const s = D.scan || {};
  $('nm-scanline').textContent = s.state === 'running' ? tr('scan_running') : s.error && (!s.last || s.last_error > s.last) ? tr('scan_failed', { err: s.error })
    : s.last ? tr('scan_last', { ago: ago(s.last), found: s.found }) : tr('scan_never');
  $('nm-scan').classList.toggle('spin', s.state === 'running');
  if (!D.enabled) banner(tr('disabled'));
  else if (!D.devices.length) banner(s.error ? tr('scan_failed', { err: s.error }) : tr('empty_map'));
}
function banner(msg) { const b = $('nm-banner'); b.textContent = msg; vis(b, !!msg); }
function renderLegend() {
  $('nm-legend').innerHTML = `<span><i class="ln"></i>${tr('legend_wired')}</span><span><i class="ln d"></i>${tr('legend_wifi')}</span>` +
    `<span><i class="dt" style="background:var(--ok)"></i>${tr('legend_online')}</span><span><i class="dt" style="background:var(--nm-off)"></i>${tr('legend_offline')}</span>` +
    `<span><i class="dt" style="background:var(--warn)"></i>${tr('legend_new')}</span>` +
    VLANS.filter(v => v !== 'none').map(v => `<span><i style="background:var(--v-${v})"></i>${tr('vlan_' + v)}</span>`).join('');
}

/* ------------------------------------------------------------ pan / zoom / drag */
const cv = $('nm-cv'), wrap = $('nm-wrap');
function applyView() { $('nm-world').setAttribute('transform', `translate(${view.tx},${view.ty}) scale(${view.s})`); }
/* fit(true) — уся мапа будь-якого розміру; fit() — не дрібніше 0.75, зверху, якщо не вміщається */
function fit(all_) {
  if (!D) return;
  const all = ispNode ? [ispNode, ...visible()] : visible();
  if (!all.length) return;
  const xs = all.map(n => n.x), ys = all.map(n => n.y);
  const x0 = Math.min(...xs) - 110, x1 = Math.max(...xs) + 110, y0 = Math.min(...ys) - 50, y1 = Math.max(...ys) + 100;
  const r = cv.getBoundingClientRect(), pw = (isOpen($('nm-panel')) && r.width > 760) ? 390 : 0;
  const w = r.width - pw - 20, h = r.height - 20;
  const full = Math.min(1.15, w / (x1 - x0), h / (y1 - y0));
  view.s = all_ === true ? Math.max(.25, full) : Math.max(.75, full);
  view.tx = Math.max(10, (w - (x1 - x0) * view.s) / 2 + 10) - x0 * view.s;
  view.ty = Math.max(10, (h - (y1 - y0) * view.s) / 2 + 10) - y0 * view.s;
  applyView();
}
function zoomAt(f, px, py) {
  const s2 = Math.max(.25, Math.min(2.5, view.s * f));
  view.tx = px - (px - view.tx) * (s2 / view.s); view.ty = py - (py - view.ty) * (s2 / view.s); view.s = s2; applyView();
}
/* колесо — прокрутка мапи; Ctrl/⌘ + колесо або щипок на тачпаді — масштаб */
cv.addEventListener('wheel', e => {
  e.preventDefault();
  if (e.ctrlKey || e.metaKey) {
    const r = cv.getBoundingClientRect();
    zoomAt(Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0025)), e.clientX - r.left, e.clientY - r.top);
  } else {
    const k = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? cv.clientHeight : 1;
    view.tx -= (e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX) * k;
    view.ty -= (e.shiftKey && !e.deltaX ? 0 : e.deltaY) * k;
    applyView();
  }
}, { passive: false });
$('nm-z-in').onclick = () => { const r = cv.getBoundingClientRect(); zoomAt(1.2, r.width / 2, r.height / 2); };
$('nm-z-out').onclick = () => { const r = cv.getBoundingClientRect(); zoomAt(1 / 1.2, r.width / 2, r.height / 2); };
$('nm-z-fit').onclick = () => fit(true);

const nodeById = id => id === 'isp' ? ispNode : byId.get(+id);
function dropOk(target, n) {
  if (!target || target.id === n.id || target.kind === 'isp' || !canParent(target)) return false;
  if (n.id === D.root_id) return false;
  return !subtreeIds(n.id).has(target.id);
}
cv.addEventListener('pointerdown', e => {
  if (e.button !== undefined && e.button !== 0) return;
  const g = e.target.closest('.nm-node');
  try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ok */ }
  drag = { id: g ? (g.dataset.id === 'isp' ? 'isp' : +g.dataset.id) : null, sx: e.clientX, sy: e.clientY, tx: view.tx, ty: view.ty, moved: false, target: null };
  if (g) { const n = nodeById(drag.id); drag.ox = n.x; drag.oy = n.y; }
});
cv.addEventListener('pointermove', e => {
  if (!drag) return;
  const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; if (!(drag.id && drag.id !== 'isp' && editMap)) cv.classList.add('panning'); }
  if (!drag.moved) return;
  if (drag.id && drag.id !== 'isp' && editMap) {
    const n = byId.get(drag.id);
    n.pos = [drag.ox + dx / view.s, drag.oy + dy / view.s];
    let best = null, bd = 56;
    visible().forEach(tg => { if (dropOk(tg, n)) { const d = Math.hypot(tg.x - n.pos[0], tg.y - n.pos[1]); if (d < bd) { bd = d; best = tg.id; } } });
    drag.target = best; render();
  } else { view.tx = drag.tx + dx; view.ty = drag.ty + dy; applyView(); }
});
async function endDrag() {
  if (!drag) return;
  const d = drag; drag = null; cv.classList.remove('panning');
  if (d.id && !d.moved) { select(d.id); return; }
  if (!(d.id && d.id !== 'isp' && d.moved && editMap)) return;
  const n = byId.get(d.id);
  if (d.target && d.target !== n.parent_id) {
    const old = n.parent_id, p = byId.get(d.target);
    n.parent_id = d.target; n.pos = null; render();
    try {
      await api('PATCH', `/api/netmap/devices/${n.id}`, { parent_id: d.target, pos: null });
      toast(tr('toast_moved', { a: n.name, b: p.name }), async () => {
        try { await api('PATCH', `/api/netmap/devices/${n.id}`, { parent_id: old, pos: null }); } catch (e) { toast(errText(e)); }
        devEvents[n.id] = null; load();
      });
    } catch (e) { toast(errText(e, { a: n.name })); }
  } else {
    const pos = d.target === n.parent_id ? null : n.pos;
    n.pos = pos; render();
    try { await api('PATCH', `/api/netmap/devices/${n.id}`, { pos }); } catch (e) { toast(errText(e)); }
  }
  devEvents[n.id] = null;
  load();
}
cv.addEventListener('pointerup', endDrag);
cv.addEventListener('pointercancel', () => { drag = null; cv.classList.remove('panning'); load(); });
cv.addEventListener('keydown', e => {
  const g = e.target.closest && e.target.closest('.nm-node');
  if (g && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); select(g.dataset.id === 'isp' ? 'isp' : +g.dataset.id); }
});

/* ------------------------------------------------------------ panel */
const panel = $('nm-panel');
function head(iconKind, title, meta) {
  return `<div class="hd"><div class="big"><svg viewBox="0 0 24 24">${iconKind === 'log' ? ICONS.log : iconKind === 'gear' ? ICONS.gear : iconSvg(iconKind)}</svg></div>
    <div style="min-width:0"><h2>${esc(title)}</h2><div class="muted small">${esc(meta || '')}</div></div>
    <button class="btn small x" type="button" data-a="close" aria-label="${esc(tr('btn_close'))}">✕</button></div>`;
}
function statusPill(n) {
  if (n.hidden) return `<span class="badge bad">${tr('st_hidden')}</span>`;
  if (n.status === 'static') return `<span class="badge">${tr('st_static')}</span>`;
  if (n.is_new && n.status === 'online') return `<span class="badge warn">${tr('st_new')}</span>`;
  return n.status === 'online' ? `<span class="badge ok">${tr('st_online')}</span>` : `<span class="badge">${tr('st_offline')}</span>`;
}
function select(id) {
  sel = id; mode = id === 'isp' ? 'isp' : 'device'; formFor = null; confirmDel = false; formErr = null;
  render(); renderPanel();
  if (mode === 'device') loadDevEvents(id);
}
function closePanel() { sel = null; mode = null; formFor = null; confirmDel = false; vis(panel, false); if (D) render(); }
async function loadDevEvents(id) {
  try {
    const r = await api('GET', `/api/netmap/events?device=${id}&limit=8`);
    devEvents[id] = r.events;
    if (mode === 'device' && sel === id && !formFor) renderPanel();
  } catch (e) { /* події не критичні */ }
}
function evText(e) {
  const p = e.params || {};
  switch (e.type) {
    case 'online': case 'restored': return tr('ev_' + e.type, { dur: durS(p.after) });
    case 'hidden': return tr('ev_hidden', { days: p.days });
    case 'ip_changed': return tr('ev_ip_changed', p);
    case 'moved': return tr('ev_moved', { from: p.from || '—', to: p.to });
    case 'edited': return tr('ev_edited', { fields: (p.fields || []).map(f => tr('fld_' + f)).join(', ') });
    case 'deleted': return tr(p.ignored ? 'ev_deleted_ignored' : 'ev_deleted');
    default: return tr('ev_' + e.type, p);
  }
}
function logRow(e, withName) {
  const live = e.device_id != null && byId.has(e.device_id);
  const name = withName ? (live ? `<button type="button" class="lnk" data-open="${e.device_id}">${esc(e.name)}</button>` : `<b>${esc(e.name || e.mac)}</b>`) + ' · ' : '';
  return `<div class="nm-le"><span class="mk ev-${esc(e.type)}"></span><div>${name}${esc(evText(e))}</div><span></span><span class="t">${esc(fmtDate(e.ts))}</span></div>`;
}

function renderPanel() {
  if (!mode) { vis(panel, false); return; }
  vis(panel, true);
  if (mode === 'log') return renderLog();
  if (mode === 'settings') return renderSettings();
  if (mode === 'isp') return renderIsp();
  const n = byId.get(sel);
  if (formFor) return renderForm(formFor === 'new' ? null : n);
  if (!n) { closePanel(); return; }
  const p = byId.get(n.parent_id), cfg = D.settings;
  const rows = [
    [tr('f_status'), statusPill(n) + (n.status === 'offline' ? ` <span class="muted small">${esc(fmtDur(nowMs() - n.last_seen * 1000))}</span>` : '')],
    [tr('f_ip'), `<span class="mono">${esc(n.ip || '—')}</span>`],
    [tr('f_mac'), `<span class="mono">${esc(n.mac || '—')}</span>${n.private_mac ? `<br><span class="note">${tr('private_mac')}</span>` : ''}`],
    [tr('f_kind'), esc(tr('kind_' + n.kind))],
    [tr('f_vendor'), esc(n.vendor || '—')],
  ];
  if (n.name_auto && n.name_auto !== n.name) rows.push([tr('f_autoname'), `<span class="mono">${esc(n.name_auto)}</span>`]);
  if (n.id !== D.root_id) rows.push([tr('f_conn'), p ? `${esc(tr('link_' + n.link))} → ${esc(p.name)}` : '—']);
  if (n.link === 'wifi' && (n.ssid || n.band)) rows.push([tr('f_wifi'), esc([n.ssid, n.band ? tr('band_' + n.band) : ''].filter(Boolean).join(' · '))]);
  if (n.vlan !== 'none') rows.push([tr('f_vlan'), `<span class="v-${n.vlan}">${esc(tr('vlan_' + n.vlan))}</span>`]);
  rows.push([tr('f_first'), esc(fmtDate(n.first_seen))]);
  rows.push([tr('f_last'), n.status === 'online' ? tr('now') : n.status === 'static' ? '—' : esc(fmtDate(n.last_seen))]);
  if (n.note) rows.push([tr('f_note'), esc(n.note)]);
  let info = '';
  if (n.protected) info = `<p class="note">${tr('note_protected')}</p>`;
  else if (n.status === 'static') info = `<p class="note">${tr('note_static')}</p>`;
  else if (n.keep) info = `<p class="note">${tr('note_keep')}</p>`;
  else if (n.status === 'offline' && n.hide_at) info = `<p class="warn-box">${tr('warn_offline', { left: fmtDur(n.hide_at * 1000 - nowMs()) })}</p>`;
  if (n.manual && n.status === 'offline' && n.last_seen === n.first_seen) info += `<p class="note">${tr('note_manual_wait')}</p>`;
  const ev = devEvents[n.id];
  const kids = kidsOf(n.id);
  panel.innerHTML = head(n.kind, n.name, tr('kind_' + n.kind)) + `<div class="bd">
    <dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl>
    ${info}
    <div class="actions">
      <button class="btn small primary" type="button" data-a="edit">${tr('btn_edit')}</button>
      ${n.pos ? `<button class="btn small" type="button" data-a="unpin">${tr('btn_unpin')}</button>` : ''}
      ${n.protected ? '' : confirmDel ? '' : `<button class="btn small danger" type="button" data-a="del">${tr('btn_delete')}</button>`}
    </div>
    ${confirmDel ? `<div class="warn-box" style="display:flex;flex-direction:column;gap:10px">
      <span>${tr('del_note', { kids: kids.length ? tr('del_kids', { p: p ? p.name : '' }) : '' })}</span>
      ${n.mac ? `<label class="check"><input type="checkbox" id="nm-del-ignore"><span>${tr('del_ignore')}</span></label>` : ''}
      <div class="actions"><button class="btn small danger solid" type="button" data-a="del-yes">${tr('btn_delete_yes')}</button><button class="btn small" type="button" data-a="del-no">${tr('btn_cancel')}</button></div></div>` : ''}
    <p class="sect">${tr('sect_events')}</p>
    <div class="nm-log">${!ev ? '' : ev.length ? ev.map(e => logRow(e, false)).join('') : `<div class="nm-empty">${tr('no_events')}</div>`}</div>
  </div>`;
}

function renderIsp() {
  const i = D.isp;
  const rows = i ? [[tr('f_isp'), esc(i.name || '—')], [tr('f_asn'), esc(i.asn || '—')], [tr('f_wanip'), `<span class="mono">${esc(i.ip || '—')}</span>`]] : [];
  if (i && i.city) rows.push([tr('f_city'), esc(i.city)]);
  panel.innerHTML = head('isp', ispNode ? ispNode.name : tr('isp_title'), tr('isp_title')) + `<div class="bd">
    ${rows.length ? `<dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl><p class="note">${tr('isp_src')}</p>` : `<p class="note">${tr('isp_none')}</p>`}</div>`;
}

/* ------------------------------------------------------------ form */
function opts(list, cur, label) { return list.map(v => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(label(v))}</option>`).join(''); }
function renderForm(n) {
  const isNew = !n;
  const d = n || { name: '', name_auto: '', kind: 'unknown', vlan: 'lan', ip: '', mac: '', vendor: '', link: 'wired', ssid: '', band: '', note: '', parent_id: D.root_id, keep: false };
  const isRoot = n && n.id === D.root_id;
  const banned = n ? subtreeIds(n.id) : new Set();
  const parents = visible().filter(x => canParent(x) && (!n || (x.id !== n.id && !banned.has(x.id))));
  const kinds = KINDS.filter(k => k !== 'gateway' || (n && n.role === 'gateway'));
  const e = formErr || {};
  panel.innerHTML = head(d.kind, isNew ? tr('new_device') : tr('editing'), isNew ? '' : n.name) + `<div class="bd"><form id="nm-frm" novalidate>
    ${e.msg ? `<p class="warn-box bad" role="alert">${esc(e.msg)}</p>` : ''}
    <label>${tr('f_name')}<input type="text" id="nmf-name" value="${esc(d.name_raw ?? d.name)}" maxlength="60" placeholder="${esc(d.name_auto || tr('ph_name'))}" class="${e.field === 'name' ? 'bad' : ''}"></label>
    <div class="row2">
      ${n && n.role === 'gateway' ? '' : `<label>${tr('f_kind')}<select id="nmf-kind">${opts(kinds, d.kind, k => tr('kind_' + k))}</select></label>`}
      <label>${tr('f_vlan')}<select id="nmf-vlan">${opts(VLANS, d.vlan, v => tr('vlan_' + v))}</select></label>
    </div>
    <div class="row2">
      <label>${tr('f_ip')}<input type="text" id="nmf-ip" class="mono ${e.field === 'ip' ? 'bad' : ''}" value="${esc(d.ip)}" placeholder="${tr('ph_ip')}" inputmode="decimal"></label>
      <label>${tr('f_mac')}<input type="text" id="nmf-mac" class="mono ${e.field === 'mac' ? 'bad' : ''}" value="${esc(d.mac)}" placeholder="${tr('ph_mac')}"></label>
    </div>
    <label>${tr('f_vendor')}<input type="text" id="nmf-vendor" value="${esc(d.vendor)}" maxlength="60" placeholder="${tr('ph_vendor')}"></label>
    ${isRoot ? '' : `<div class="row2">
      <label>${tr('f_parent')}<select id="nmf-parent">${opts(parents.map(x => x.id), d.parent_id, id => byId.get(id).name)}</select></label>
      <label>${tr('f_link')}<select id="nmf-link">${opts(['wired', 'wifi'], d.link, v => tr('link_' + v))}</select></label></div>
    <div class="row2">
      <label>${tr('f_ssid')}<input type="text" id="nmf-ssid" value="${esc(d.ssid)}" maxlength="60"></label>
      <label>${tr('f_band')}<select id="nmf-band">${opts(BANDS, d.band, b => tr('band_' + b))}</select></label></div>`}
    <label>${tr('f_note')}<textarea id="nmf-note" maxlength="200">${esc(d.note)}</textarea></label>
    ${n && n.protected ? '' : `<label class="check"><input type="checkbox" id="nmf-keep"${d.keep ? ' checked' : ''}><span>${tr('f_keep')}<small>${tr('f_keep_hint', { days: D.settings.stale_days })}</small></span></label>`}
    <div class="actions"><button class="btn small primary" type="submit">${tr('btn_save')}</button><button class="btn small" type="button" data-a="cancel">${tr('btn_cancel')}</button></div>
  </form></div>`;
  $('nm-frm').addEventListener('submit', ev => { ev.preventDefault(); saveForm(n); });
  panel.querySelectorAll('input').forEach(i => i.addEventListener('input', () => i.classList.remove('bad')));
  (e.field && $('nmf-' + e.field) || $('nmf-name')).focus();
}
async function saveForm(n) {
  const g = id => $(id) ? ($(id).type === 'checkbox' ? $(id).checked : $(id).value.trim()) : undefined;
  const vals = { name: g('nmf-name'), kind: g('nmf-kind'), vlan: g('nmf-vlan'), ip: g('nmf-ip'), mac: g('nmf-mac'), vendor: g('nmf-vendor'),
    link: g('nmf-link'), ssid: g('nmf-ssid'), band: g('nmf-band'), note: g('nmf-note'), keep: g('nmf-keep') };
  const pv = g('nmf-parent');
  const body = {};
  Object.entries(vals).forEach(([k, v]) => {
    if (v === undefined) return;
    if (!n) { body[k] = v; return; }
    const cur = k === 'name' ? n.name_raw : n[k];
    if (k === 'mac' ? v.toUpperCase() !== (cur || '').toUpperCase() : v !== cur) body[k] = v;
  });
  if (pv !== undefined && (!n || +pv !== n.parent_id)) body.parent_id = +pv;
  if (!n && !vals.name) { formErr = { msg: tr('err_name_required'), field: 'name' }; return renderForm(n); }
  if (n && !Object.keys(body).length) { formFor = null; formErr = null; return renderPanel(); }
  try {
    const r = n ? await api('PATCH', `/api/netmap/devices/${n.id}`, body) : await api('POST', '/api/netmap/devices', body);
    formFor = null; formErr = null; sel = r.id; mode = 'device'; devEvents[r.id] = null;
    toast(tr('toast_saved'));
    await load(); loadDevEvents(r.id);
  } catch (e) {
    const field = { bad_ip: 'ip', ip_exists: 'ip', bad_mac: 'mac', mac_exists: 'mac', name_required: 'name', name_too_long: 'name' }[e.code];
    formErr = { msg: errText(e, { a: n ? n.name : vals.name }), field };
    renderForm(n);
  }
}

/* ------------------------------------------------------------ log */
async function fetchLog(more) {
  const before = more && logItems.length ? `&before=${logItems[logItems.length - 1].ts}` : '';
  try {
    const r = await api('GET', `/api/netmap/events?type=${logGroup}&limit=100${before}`);
    logItems = more ? logItems.concat(r.events) : r.events;
    logDone = r.events.length < 100;
  } catch (e) { toast(errText(e)); }
  if (mode === 'log') renderLog();
}
function renderLog() {
  const c = D.counts, cfg = D.settings;
  const groups = ['all', 'offline', 'online', 'new', 'hidden', 'edits'];
  panel.innerHTML = head('log', tr('log_title'), tr('log_meta', { n: c.events, days: cfg.log_days })) + `<div class="bd">
    ${D.hidden.length ? `<p class="sect">${tr('sect_hidden')}</p><div class="nm-log">${D.hidden.map(h =>
      `<div class="nm-le"><span class="mk ev-hidden"></span><div><b>${esc(h.name)}</b> · <span class="mono">${esc(h.ip || h.mac)}</span><br><span class="note">${tr('hidden_row', { dur: fmtDur(nowMs() - h.last_seen * 1000) })}</span></div>
       <button class="btn small" type="button" data-a="restore" data-id="${h.id}">${tr('btn_restore')}</button></div>`).join('')}</div>` : ''}
    ${D.ignored.length ? `<p class="sect">${tr('sect_ignored')}</p><div class="nm-log">${D.ignored.map(h =>
      `<div class="nm-le"><span class="mk ev-deleted"></span><div><b>${esc(h.name)}</b> · <span class="mono">${esc(h.mac.toUpperCase())}</span></div>
       <button class="btn small" type="button" data-a="unignore" data-mac="${esc(h.mac)}" data-name="${esc(h.name)}">${tr('btn_unignore')}</button></div>`).join('')}</div>` : ''}
    <div class="nm-groups" role="tablist">${groups.map(g => `<button class="btn small" type="button" role="tab" aria-selected="${g === logGroup}" data-g="${g}" class="btn small ${g === logGroup ? 'on' : ''}">${tr('g_' + g)}</button>`).join('')}</div>
    <div class="nm-log">${logItems.length ? logItems.map(e => logRow(e, true)).join('') : `<div class="nm-empty">${tr('log_empty')}</div>`}</div>
    ${logDone ? '' : `<button class="btn small" type="button" data-a="more">${tr('btn_more')}</button>`}
    <p class="note">${tr('log_note', { days: cfg.log_days })}</p>
  </div>`;
}

/* ------------------------------------------------------------ стан сканування */
function renderSettings() {
  const s = D.scan || {}, cfg = D.settings;
  const rows = [
    [tr('set_scan'), s.last ? `${esc(fmtDate(s.last))} · ${esc(ago(s.last))}` : tr('set_never')],
    [tr('set_method'), s.method ? tr('method_' + (s.method === 'arp-scan' ? 'arp' : 'ping')) : '—'],
    [tr('set_iface'), `<span class="mono">${esc(s.iface || '—')}</span>`],
    [tr('set_subnet'), `<span class="mono">${esc(s.subnet || '—')}</span>`],
    [tr('set_found'), esc(s.found ?? '—')],
    [tr('set_duration'), s.duration != null ? esc(durS(s.duration)) : '—'],
  ];
  if (s.error) rows.push([tr('set_error'), `<span style="color:var(--bad)">${esc(s.error)}</span>`]);
  panel.innerHTML = head('gear', tr('set_title'), '') + `<div class="bd">
    ${D.enabled ? '' : `<p class="warn-box">${tr('disabled')}</p>`}
    <dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl>
    ${s.method === 'ping' ? `<p class="warn-box">${tr('set_fallback', { note: esc(s.note || '') })}</p>` : ''}
    <div class="actions"><button class="btn small primary" type="button" data-a="scan">${tr('scan_now')}</button></div>
    <p class="note">${tr('set_rules_note')}</p>
    <dl>
      <dt>${esc(window.t('s_net_every'))}</dt><dd>${Math.round(cfg.scan_interval / 60)}</dd>
      <dt>${esc(window.t('s_net_offline'))}</dt><dd>${Math.round(cfg.offline_after / 60)}</dd>
      <dt>${esc(window.t('s_net_stale'))}</dt><dd>${cfg.stale_days}</dd>
      <dt>${esc(window.t('s_net_log'))}</dt><dd>${cfg.log_days}</dd>
    </dl>
    <div class="actions"><button class="btn small" type="button" data-a="settings">${tr('set_open')}</button></div>
  </div>`;
}

/* ------------------------------------------------------------ panel actions */
panel.addEventListener('click', async e => {
  const op = e.target.closest('[data-open]');
  if (op) { select(+op.dataset.open); return; }
  const gb = e.target.closest('[data-g]');
  if (gb) { logGroup = gb.dataset.g; logItems = []; renderLog(); fetchLog(false); return; }
  const b = e.target.closest('[data-a]'); if (!b) return;
  const a = b.dataset.a, n = byId.get(sel);
  try {
    if (a === 'close') closePanel();
    else if (a === 'edit') { formFor = n.id; formErr = null; renderPanel(); }
    else if (a === 'cancel') { formErr = null; if (formFor === 'new') closePanel(); else { formFor = null; renderPanel(); } }
    else if (a === 'unpin') { await api('PATCH', `/api/netmap/devices/${n.id}`, { pos: null }); await load(); }
    else if (a === 'del') { confirmDel = true; renderPanel(); }
    else if (a === 'del-no') { confirmDel = false; renderPanel(); }
    else if (a === 'del-yes') {
      const ign = $('nm-del-ignore') && $('nm-del-ignore').checked;
      await api('DELETE', `/api/netmap/devices/${n.id}${ign ? '?ignore=1' : ''}`);
      confirmDel = false; closePanel(); toast(tr('toast_deleted', { a: n.name })); await load();
    }
    else if (a === 'restore') { await api('POST', `/api/netmap/devices/${b.dataset.id}/restore`); toast(tr('toast_restored')); await load(); renderLog(); fetchLog(false); }
    else if (a === 'unignore') { await api('DELETE', `/api/netmap/ignored/${encodeURIComponent(b.dataset.mac)}`); toast(tr('toast_unignored', { a: b.dataset.name })); await load(); renderLog(); fetchLog(false); }
    else if (a === 'more') fetchLog(true);
    else if (a === 'scan') scanNow();
    else if (a === 'settings') {
      window.showTab('settings');
      const fs = document.getElementById('settings-netmap');
      if (fs) setTimeout(() => fs.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    }
  } catch (err) {
    if (err instanceof Error) console.error('netmap:', err);
    if (err.code === 'not_found') { closePanel(); load(); }
    toast(errText(err));
  }
});

/* ------------------------------------------------------------ toolbar */
async function scanNow() {
  try {
    const r = await api('POST', '/api/netmap/scan');
    toast(tr(r.started ? 'toast_scan' : 'toast_scan_busy'));
    setTimeout(load, 800);
  } catch (e) { toast(errText(e)); }
}
$('nm-scan').onclick = scanNow;
$('nm-edit').onclick = () => {
  editMap = !editMap;
  $('nm-edit').classList.toggle('on', editMap); $('nm-edit').setAttribute('aria-pressed', String(editMap));
  wrap.classList.toggle('edit', editMap); vis($('nm-hint'), editMap);
};
$('nm-add').onclick = () => { if (!D) return; sel = null; mode = 'device'; formFor = 'new'; formErr = null; render(); renderPanel(); };
$('nm-auto').onclick = async () => { try { await api('POST', '/api/netmap/layout/reset'); toast(tr('toast_layout')); await load(); fit(); } catch (e) { toast(errText(e)); } };
$('nm-log').onclick = () => { if (mode === 'log') { closePanel(); return; } if (!D) return; sel = null; mode = 'log'; formFor = null; logItems = []; logDone = true; render(); renderLog(); vis(panel, true); fetchLog(false); };
$('nm-info').onclick = () => { if (mode === 'settings') { closePanel(); return; } if (!D) return; sel = null; mode = 'settings'; formFor = null; formErr = null; render(); vis(panel, true); renderSettings(); };
document.addEventListener('keydown', e => { if (active && e.key === 'Escape' && mode && !formFor) closePanel(); });

function toast(msg, undo) {
  const el = $('nm-toast'); clearTimeout(toastT);
  el.innerHTML = `<span>${esc(msg)}</span>${undo ? `<button class="btn small" type="button" id="nm-undo">${tr('toast_undo')}</button>` : ''}`;
  vis(el, true);
  if (undo) $('nm-undo').onclick = () => { vis(el, false); undo(); };
  toastT = setTimeout(() => vis(el, false), 5000);
}
function applyLabels() {
  $('nm-cv').setAttribute('aria-label', window.t('tab_netmap'));
  $('nm-z-in').setAttribute('aria-label', tr('zoom_in'));
  $('nm-z-out').setAttribute('aria-label', tr('zoom_out'));
  $('nm-z-fit').setAttribute('aria-label', tr('zoom_fit'));
  ['nm-scan', 'nm-edit', 'nm-add', 'nm-auto', 'nm-log', 'nm-info'].forEach(id => { const b = $(id); b.title = b.textContent.trim(); });
  renderLegend();
}

window.addEventListener('resize', () => { if (active && D && !drag) fit(); });

/* звʼязок з app.js: showTab() викликає show()/hide(), зміна мови — rerender() */
window.NetMap = {
  show() {
    active = true;
    applyLabels();
    if (D) { render(); if (!fitted && D.devices.length) { fit(); fitted = true; } }
    load();
  },
  hide() { active = false; clearTimeout(pollT); },
  rerender() {
    applyLabels();
    if (!D) return;
    render();
    if (mode && !formFor) renderPanel();
  },
};
applyLabels();
})();
