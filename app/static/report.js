/* Click-ops report page. */
(() => {
  const { el, fmtTime } = ADT;
  const $ = (id) => document.getElementById(id);

  function stat(label, value, cls) {
    return el('div', { class: 'stat ' + (cls || '') }, el('div', { class: 'stat-value', text: value }), el('div', { class: 'stat-label', text: label }));
  }

  function render(r) {
    const pct = r.total_changes ? Math.round((r.clickops_changes / r.total_changes) * 100) : 0;
    const types = r.by_caller_type || {};
    $('stats').replaceChildren(
      stat('Total changes', r.total_changes),
      stat('Click-ops changes', r.clickops_changes, 'stat-warn'),
      stat('Click-ops share', pct + '%', 'stat-warn'),
      stat('People making changes', r.total_users),
      stat('Terraform changes', types.Terraform || 0, 'stat-good'),
      stat('Other automation', types['Service Principal'] || 0),
    );

    const max = r.top.length ? r.top[0].total : 1;
    const board = $('leaderboard');
    board.replaceChildren(...r.top.map((u, i) => {
      const bar = el('div', { class: 'bar' });
      bar.style.width = Math.max(2, (u.total / max) * 100) + '%';
      return el('div', { class: 'lb-row' },
        el('div', { class: 'lb-rank', text: `#${i + 1}` }),
        el('div', { class: 'lb-name', title: u.caller, text: u.caller_name || u.caller }),
        el('div', { class: 'lb-bar' }, bar),
        el('div', { class: 'lb-count', text: u.total }));
    }));
    if (!r.top.length) board.append(el('p', { class: 'muted', text: 'No click-ops changes in this range. Nice.' }));

    $('report-body').replaceChildren(...r.top.map((u, i) => el('tr', {},
      el('td', { text: i + 1 }),
      el('td', {}, el('div', { class: 'strong', text: u.caller_name || u.caller }), u.caller_name ? el('div', { class: 'muted small', text: u.caller }) : null),
      el('td', { class: 'num strong', text: u.total }),
      el('td', { class: 'num', text: u.creates }),
      el('td', { class: 'num', text: u.updates }),
      el('td', { class: 'num', text: u.deletes }),
      el('td', { class: 'num', text: u.actions }),
      el('td', { class: 'num', text: u.resources }),
      el('td', { class: 'small', text: Object.entries(u.clients).map(([k, v]) => `${k}: ${v}`).join(', ') }),
      el('td', { class: 'small', text: u.subscriptions.join(', ') }),
      el('td', { class: 'nowrap', text: fmtTime(u.last_change) }),
    )));
  }

  ADT.init({ dataPath: '/api/report', csvPath: '/api/report.csv', report: true, onData: render });
})();
