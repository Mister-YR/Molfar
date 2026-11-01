/* Лінійні іконки 24×24 (stroke = currentColor, class="f" — заливка).
   Щоб підставити свої (напр. з Noun Project), замініть вміст за ключем на <path …> з viewBox 0 0 24 24
   і не забудьте підпис автора, якщо цього вимагає ліцензія. */
window.NM_ICONS = {
  cloud: '<path d="M7 18.5h10.5a4 4 0 0 0 .4-7.98A6 6 0 0 0 6.2 9.6 4.5 4.5 0 0 0 7 18.5z"/>',
  router: '<rect x="2.5" y="12" width="19" height="7" rx="2"/><path d="M7 12 5.5 5M17 12l1.5-7"/><circle class="f" cx="6.5" cy="15.5" r="1"/><circle class="f" cx="9.5" cy="15.5" r="1"/><path d="M14 15.5h4.5"/>',
  switch: '<rect x="2" y="8" width="20" height="8.5" rx="1.6"/><path d="M5.5 12v1.8M8.5 12v1.8M11.5 12v1.8M14.5 12v1.8"/><circle class="f" cx="18.5" cy="12.8" r=".9"/>',
  ap: '<circle cx="12" cy="14" r="6.5"/><circle class="f" cx="12" cy="14" r="1.3"/><path d="M8.5 5.2a6 6 0 0 1 7 0M10.2 7.4a3 3 0 0 1 3.6 0"/>',
  sbc: '<rect x="3.5" y="6" width="17" height="12" rx="1.5"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M5.5 3.5V6M8 3.5V6M10.5 3.5V6M13 3.5V6M18 15h.01M6 15h.01"/>',
  server: '<rect x="4" y="3.5" width="16" height="7" rx="1.4"/><rect x="4" y="13.5" width="16" height="7" rx="1.4"/><circle class="f" cx="7.5" cy="7" r=".9"/><circle class="f" cx="7.5" cy="17" r=".9"/><path d="M11 7h5.5M11 17h5.5"/>',
  nas: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8.5 7.5h7M8.5 11h7"/><circle cx="12" cy="16.5" r="1.4"/>',
  pc: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M12 16v4M8.5 20h7"/>',
  laptop: '<rect x="5" y="5" width="14" height="10" rx="1.2"/><path d="M2.5 18.5h19L20 15H4z"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2.2"/><path d="M11 18.5h2"/>',
  tablet: '<rect x="4.5" y="3" width="15" height="18" rx="2"/><circle class="f" cx="12" cy="18" r=".8"/>',
  tv: '<rect x="2.5" y="4.5" width="19" height="12.5" rx="1.5"/><path d="M8 20.5 9.5 17M16 20.5 14.5 17"/>',
  camera: '<path d="M4 8h16v2.5a8 8 0 0 1-16 0z"/><circle cx="12" cy="11.5" r="2.6"/><path d="M6 5h12"/>',
  printer: '<path d="M7 9V3.5h10V9"/><rect x="3" y="9" width="18" height="7.5" rx="1.5"/><rect x="7" y="13.5" width="10" height="7"/>',
  speaker: '<rect x="6" y="2.5" width="12" height="19" rx="3"/><circle cx="12" cy="14" r="3.4"/><circle class="f" cx="12" cy="7" r="1"/>',
  iot: '<rect x="7" y="7" width="10" height="10" rx="1.6"/><path d="M10 7V4M14 7V4M10 20v-3M14 20v-3M7 10H4M7 14H4M20 10h-3M20 14h-3"/><circle class="f" cx="12" cy="12" r="1.3"/>',
  unknown: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1.1.9-1.1 1.6v.6"/><circle class="f" cx="12" cy="17" r=".9"/>',
  log: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>'
};
window.NM_KIND_ICON = {
  gateway: 'router', switch: 'switch', ap: 'ap', sbc: 'sbc', server: 'server', nas: 'nas', pc: 'pc',
  laptop: 'laptop', phone: 'phone', tablet: 'tablet', tv: 'tv', camera: 'camera', printer: 'printer',
  speaker: 'speaker', iot: 'iot', unknown: 'unknown', isp: 'cloud'
};
