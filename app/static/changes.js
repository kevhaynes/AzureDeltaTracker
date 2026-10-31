/* Change feed page. */
(() => {
  const { el, fmtTime } = ADT;
  const $ = (id) => document.getElementById(id);
  const PAGE_SIZE = 100;
  let all = [];
  let page = 0;
  const expanded = new Set();

  function stat(label, value, cls) {
    return el('div', { class: 'stat ' + (cls || '') }, el('div', { class: 'stat-value', text: value }), el('div', { class: 'stat-label', text: label }));
  }

  function renderStats(changes) {
    const users = new Set(changes.filter((c) => c.is_clickops).map((c) => c.caller.toLowerCase()));
    const count = (fn) => changes.filter(fn).length;
    $('stats').replaceChildren(
      stat('Changes', changes.length),
      stat('Click-ops changes', count((c) => c.is_clickops), 'stat-warn'),
      stat('People', users.size),
      stat('Terraform changes', count((c) => c.is_terraform), 'stat-good'),
      stat('Other automation', count((c) => c.caller_type === 'Service Principal')),
      stat('Deletes', count((c) => c.change_type === 'Delete'), 'stat-bad'),
    );
  }

  function filtered() {
    const q = $('search').value.trim().toLowerCase();
    const ct = $('filter-change-type').value;
    const caller = $('filter-caller-type').value;
    return all.filter((c) => {
      if (ct && c.change_type !== ct) return false;
      if (caller && c.caller_type !== caller) return false;
      if (!q) return true;
      return [c.resource_name, c.resource_type, c.caller, c.caller_name, c.operation, c.operation_name,
        c.subscription_name, c.resource_group, c.client, c.resource_id].some((v) => (v || '').toLowerCase().includes(q));
    });
  }

  function fmtValue(v) {
    if (v === null || v === undefined || v === '') return el('span', { class: 'muted', text: '(none)' });
    return String(v);
  }

  function detailRow(c) {
    let body;
    if (c.property_changes.length) {
      body = el('table', { class: 'diff' },
        el('thead', {}, el('tr', {}, el('th', { text: 'Property' }), el('th', { text: 'Before' }), el('th', { text: 'After' }))),
        el('tbody', {}, c.property_changes.map((p) => el('tr', {},
          el('td', {}, el('code', { text: p.path })),
          el('td', { class: 'before' }, fmtValue(p.before)),
          el('td', { class: 'after' }, fmtValue(p.after)),
        ))));
    } else {
      const reason = c.has_details
        ? 'Resource Graph recorded this change but no property-level differences (typical for creates and deletes).'
        : (c.change_type === 'Action'
          ? 'Actions (restart, regenerate key, etc.) do not have before/after property values.'
          : 'No before/after values available. Resource Graph only keeps change history for 14 days.');
      body = el('p', { class: 'muted', text: reason });
    }
    const meta = el('dl', { class: 'meta' },
      el('dt', { text: 'Resource ID' }), el('dd', {}, el('code', { text: c.resource_id })),
      el('dt', { text: 'Operation' }), el('dd', {}, el('code', { text: c.operation })),
      el('dt', { text: 'Correlation ID' }), el('dd', {}, el('code', { text: c.correlation_id })),
      el('dt', { text: 'Client app ID' }), el('dd', {}, el('code', { text: c.client_id || '-' })),
    );
    return el('tr', { class: 'detail' }, el('td', { colspan: 7 }, body, meta));
  }

  function row(c) {
    const tr = el('tr', { class: 'change-row' + (expanded.has(c.id) ? ' open' : '') },
      el('td', { class: 'nowrap', text: fmtTime(c.timestamp) }),
      el('td', {}, el('div', { class: 'strong', text: c.resource_name }), el('div', { class: 'muted small', text: c.resource_type })),
      el('td', {}, el('span', { class: 'pill pill-' + c.change_type.toLowerCase(), text: c.change_type }),
        c.property_changes.length ? el('span', { class: 'muted small', text: ` ${c.property_changes.length} prop${c.property_changes.length > 1 ? 's' : ''}` }) : null),
      el('td', {}, c.operation_name),
      el('td', {},
        el('div', { class: 'strong', text: c.caller_name || c.caller }),
        c.caller_name ? el('div', { class: 'muted small', text: c.caller }) : null,
        el('span', { class: 'pill pill-caller-' + c.caller_type.toLowerCase().replace(/\s+/g, '-'), text: c.caller_type })),
      el('td', { text: c.client || '-' }),
      el('td', {}, el('div', { text: c.subscription_name }), el('div', { class: 'muted small', text: c.resource_group })),
    );
    tr.addEventListener('click', () => {
      if (expanded.has(c.id)) expanded.delete(c.id); else expanded.add(c.id);
      renderTable();
    });
    return expanded.has(c.id) ? [tr, detailRow(c)] : [tr];
  }

  function renderTable() {
    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.min(page, pages - 1);
    const slice = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    const body = $('changes-body');
    if (!slice.length) {
      body.replaceChildren(el('tr', {}, el('td', { colspan: 7, class: 'empty', text: 'No changes found for this scope and time range.' })));
    } else {
      body.replaceChildren(...slice.flatMap(row));
    }
    $('row-count').textContent = rows.length === all.length ? `${rows.length} changes` : `${rows.length} of ${all.length} changes`;

    const pager = $('pager');
    pager.replaceChildren();
    if (pages > 1) {
      const go = (p) => () => { page = p; renderTable(); window.scrollTo({ top: $('changes-body').closest('.panel').offsetTop - 10 }); };
      pager.append(
        el('button', { class: 'btn btn-small', disabled: page === 0, onclick: go(page - 1), text: '‹ Newer' }),
        el('span', { class: 'muted small', text: `Page ${page + 1} of ${pages}` }),
        el('button', { class: 'btn btn-small', disabled: page >= pages - 1, onclick: go(page + 1), text: 'Older ›' }),
      );
    }
  }

  ['search', 'filter-change-type', 'filter-caller-type'].forEach((id) =>
    $(id).addEventListener('input', () => { page = 0; renderTable(); }));

  ADT.init({
    dataPath: '/api/changes',
    csvPath: '/api/changes.csv',
    onData: (data) => {
      all = data.changes;
      page = 0;
      expanded.clear();
      renderStats(all);
      renderTable();
    },
  });
})();
