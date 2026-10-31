/* Shared filter panel: scope selection, time slider, subscription exclusions, URL state. */
const ADT = (() => {
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const HOUR = 3600 * 1000;

  let config, scopes, now, maxHours, opts;
  let controller = null;
  let debounceTimer = null;
  const state = {
    scope: 'tenant', mg: '', sub: '', rg: '',
    startH: 0, endH: 0, hideTf: true, exclude: new Set(),
    top: 10, portalOnly: false,
  };

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  async function api(path, init) {
    const resp = await fetch(path, Object.assign({ headers: { Accept: 'application/json', 'X-CSRF-Token': csrf } }, init));
    if (resp.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search); throw new Error('Signed out'); }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.error || resp.statusText);
    return body;
  }

  const hourToDate = (h) => new Date(now.getTime() - (maxHours - h) * HOUR);
  const dateToHour = (d) => Math.round(maxHours - (now.getTime() - d.getTime()) / HOUR);
  const clampH = (h) => Math.min(maxHours, Math.max(0, h));

  function fmtTime(iso) {
    if (!iso) return '';
    const d = iso instanceof Date ? iso : new Date(iso);
    return d.toISOString().slice(0, 19).replace('T', ' ');
  }

  function setStatus(text, busy) {
    const s = $('status');
    s.textContent = text || '';
    s.classList.toggle('busy', !!busy);
  }

  function showErrors(errors) {
    const box = $('errors');
    box.replaceChildren();
    for (const e of errors || []) box.append(el('div', { class: 'alert alert-error', text: e }));
  }

  // ------------------------------------------------------------- URL state
  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.scope = p.get('scope') || 'tenant';
    state.mg = state.scope === 'managementgroup' ? p.get('id') || '' : '';
    state.sub = ['subscription', 'resourcegroup'].includes(state.scope) ? p.get('id') || '' : '';
    state.rg = p.get('rg') || '';
    state.endH = p.get('end') ? clampH(dateToHour(new Date(p.get('end')))) : maxHours;
    state.startH = p.get('start') ? clampH(dateToHour(new Date(p.get('start')))) : Math.max(0, state.endH - 24 * 7);
    if (state.startH >= state.endH) state.startH = Math.max(0, state.endH - 1);
    state.hideTf = p.has('hide_tf') ? p.get('hide_tf') === '1' : config.tfe_filter_default;
    state.exclude = p.has('exclude')
      ? new Set(p.get('exclude').split(',').filter(Boolean))
      : defaultExclusions();
    state.top = parseInt(p.get('top') || config.top_n, 10);
    state.portalOnly = p.get('portal_only') === '1';
  }

  function defaultExclusions() {
    return new Set(scopes.subscriptions.filter((s) => s.excluded_by_default).map((s) => s.id));
  }

  function params() {
    const p = new URLSearchParams();
    p.set('scope', state.scope);
    if (state.scope === 'managementgroup') p.set('id', state.mg);
    if (state.scope === 'subscription' || state.scope === 'resourcegroup') p.set('id', state.sub);
    if (state.scope === 'resourcegroup') p.set('rg', state.rg);
    p.set('start', hourToDate(state.startH).toISOString());
    if (state.endH < maxHours) p.set('end', hourToDate(state.endH).toISOString());
    p.set('exclude', [...state.exclude].join(','));
    p.set('hide_tf', state.hideTf ? '1' : '0');
    if (opts.report) {
      p.set('top', String(state.top));
      p.set('portal_only', state.portalOnly ? '1' : '0');
    }
    return p;
  }

  // ------------------------------------------------------------ Scope UI
  function fillSelect(select, items, value) {
    select.replaceChildren(...items.map((i) => el('option', { value: i.id, text: i.name })));
    if (value && items.some((i) => i.id === value)) select.value = value;
  }

  async function loadResourceGroups() {
    const rgSel = $('scope-rg');
    if (!state.sub) return;
    const rgs = await api('/api/resource-groups?subscription=' + encodeURIComponent(state.sub));
    fillSelect(rgSel, rgs.map((r) => ({ id: r, name: r })), state.rg);
    state.rg = rgSel.value;
  }

  function syncScopeVisibility() {
    $('mg-wrap').hidden = state.scope !== 'managementgroup';
    $('sub-wrap').hidden = !['subscription', 'resourcegroup'].includes(state.scope);
    $('rg-wrap').hidden = state.scope !== 'resourcegroup';
    // Exclusions only matter when more than one subscription is in scope
    document.querySelector('.sub-exclusions').classList.toggle('disabled', !['tenant', 'managementgroup'].includes(state.scope));
  }

  function renderSubList() {
    const list = $('sub-list');
    list.replaceChildren(...scopes.subscriptions.map((s) => {
      const cb = el('input', { type: 'checkbox', checked: !state.exclude.has(s.id) });
      cb.addEventListener('change', () => {
        if (cb.checked) state.exclude.delete(s.id); else state.exclude.add(s.id);
        updateExcludedCount();
        scheduleLoad();
      });
      return el('label', { class: 'sub-item' + (s.excluded_by_default ? ' default-excluded' : '') },
        cb, el('span', { text: s.name }), el('code', { class: 'muted small', text: s.id }));
    }));
    updateExcludedCount();
  }

  function updateExcludedCount() {
    $('excluded-count').textContent = `${state.exclude.size} excluded`;
  }

  // ----------------------------------------------------------- Time slider
  function renderSlider() {
    const s = $('time-start'), e = $('time-end');
    s.value = state.startH; e.value = state.endH;
    const pct = (h) => (h / maxHours) * 100;
    const fill = $('track-fill');
    fill.style.left = pct(state.startH) + '%';
    fill.style.width = pct(state.endH - state.startH) + '%';
    const arg = $('track-arg');
    const argStart = Math.max(0, maxHours - 14 * 24);
    arg.hidden = !config.resource_graph;
    arg.style.left = pct(argStart) + '%';
    arg.style.width = pct(maxHours - argStart) + '%';
    $('time-start-label').textContent = fmtTime(hourToDate(state.startH)).slice(0, 16);
    $('time-end-label').textContent = state.endH >= maxHours ? 'now' : fmtTime(hourToDate(state.endH)).slice(0, 16);
    document.querySelectorAll('[data-preset]').forEach((b) => {
      const span = b.dataset.preset === 'all' ? maxHours : parseInt(b.dataset.preset, 10);
      b.classList.toggle('active', state.endH === maxHours && state.endH - state.startH === Math.min(span, maxHours));
    });
  }

  function bindSlider() {
    const s = $('time-start'), e = $('time-end');
    s.max = e.max = maxHours;
    s.addEventListener('input', () => {
      state.startH = Math.min(parseInt(s.value, 10), state.endH - 1);
      renderSlider();
    });
    e.addEventListener('input', () => {
      state.endH = Math.max(parseInt(e.value, 10), state.startH + 1);
      renderSlider();
    });
    s.addEventListener('change', scheduleLoad);
    e.addEventListener('change', scheduleLoad);
    document.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
      const span = b.dataset.preset === 'all' ? maxHours : parseInt(b.dataset.preset, 10);
      state.endH = maxHours;
      state.startH = Math.max(0, maxHours - span);
      renderSlider();
      load();
    }));
  }

  // ----------------------------------------------------------------- Load
  function scheduleLoad() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(load, 500);
  }

  async function load() {
    clearTimeout(debounceTimer);
    const p = params();
    history.replaceState(null, '', location.pathname + '?' + p.toString());
    $('export-csv').href = opts.csvPath + '?' + p.toString();
    if (controller) controller.abort();
    controller = new AbortController();
    setStatus('Loading…', true);
    const started = performance.now();
    try {
      const data = await api(opts.dataPath + '?' + p.toString(), { signal: controller.signal });
      showErrors(data.errors);
      opts.onData(data);
      setStatus(`Loaded in ${((performance.now() - started) / 1000).toFixed(1)}s`);
    } catch (err) {
      if (err.name === 'AbortError') return;
      setStatus('');
      showErrors([err.message]);
    }
  }

  // ----------------------------------------------------------------- Init
  async function init(options) {
    opts = options;
    try {
      [config, scopes] = await Promise.all([api('/api/config'), api('/api/scopes')]);
    } catch (err) {
      showErrors([err.message]);
      return;
    }
    now = new Date();
    now.setMinutes(0, 0, 0);
    now = new Date(now.getTime() + HOUR); // round up to the next hour so "now" is always included
    maxHours = config.max_lookback_days * 24;
    readUrl();

    $('default-patterns').textContent = config.default_excluded.join(', ') || '(none)';
    fillSelect($('scope-mg'), scopes.management_groups, state.mg);
    fillSelect($('scope-sub'), scopes.subscriptions, state.sub);
    state.mg = state.mg || $('scope-mg').value;
    state.sub = state.sub || $('scope-sub').value;
    $('scope-type').value = state.scope;
    $('hide-tf').checked = state.hideTf;
    $('hide-tf').disabled = !config.tfe_client_ids.length;
    if (!config.tfe_client_ids.length) $('hide-tf').parentElement.title = 'No TFE_CLIENT_IDS configured';
    if ($('top-n')) $('top-n').value = state.top;
    if ($('portal-only')) $('portal-only').checked = state.portalOnly;
    syncScopeVisibility();
    if (state.scope === 'resourcegroup') await loadResourceGroups().catch((e) => showErrors([e.message]));
    renderSubList();
    bindSlider();
    renderSlider();

    $('scope-type').addEventListener('change', async (ev) => {
      state.scope = ev.target.value;
      syncScopeVisibility();
      if (state.scope === 'resourcegroup') await loadResourceGroups().catch((e) => showErrors([e.message]));
      load();
    });
    $('scope-mg').addEventListener('change', (ev) => { state.mg = ev.target.value; load(); });
    $('scope-sub').addEventListener('change', async (ev) => {
      state.sub = ev.target.value;
      state.rg = '';
      if (state.scope === 'resourcegroup') await loadResourceGroups().catch((e) => showErrors([e.message]));
      load();
    });
    $('scope-rg').addEventListener('change', (ev) => { state.rg = ev.target.value; load(); });
    $('hide-tf').addEventListener('change', (ev) => { state.hideTf = ev.target.checked; load(); });
    if ($('top-n')) $('top-n').addEventListener('change', (ev) => { state.top = Math.max(1, parseInt(ev.target.value, 10) || config.top_n); load(); });
    if ($('portal-only')) $('portal-only').addEventListener('change', (ev) => { state.portalOnly = ev.target.checked; load(); });
    $('excl-defaults').addEventListener('click', () => { state.exclude = defaultExclusions(); renderSubList(); load(); });
    $('excl-none').addEventListener('click', () => { state.exclude = new Set(); renderSubList(); load(); });
    $('apply').addEventListener('click', load);
    $('refresh').addEventListener('click', async () => {
      try { await api('/api/refresh', { method: 'POST' }); } catch (e) { showErrors([e.message]); return; }
      load();
    });

    load();
  }

  return { init, el, fmtTime, state };
})();
