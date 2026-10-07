(() => {
  const messages = __PANEL_MESSAGES__;
  const query = document.querySelector('[data-filter]');
  const engine = document.querySelector('[data-engine-filter]');
  const bucket = document.querySelector('[data-bucket-filter]');
  const cluster = document.querySelector('[data-cluster-filter]');
  const tables = [...document.querySelectorAll('[data-row-limit]')];
  function apply(table) {
    const q = (query?.value || '').toLocaleLowerCase('ru');
    let count = 0;
    const rows = [...table.tBodies[0].rows];
    const matches = rows.filter(row => !row.hasAttribute('data-query') || (
      row.dataset.query.toLocaleLowerCase('ru').includes(q) &&
      (!engine?.value || row.dataset.engine === engine.value) &&
      (!bucket?.value || row.dataset.bucket === bucket.value) &&
      (!cluster?.value || row.dataset.cluster === cluster.value)
    ));
    const selected = new Set(matches);
    rows.forEach(row => { row.hidden = !selected.has(row) || (table.dataset.expanded !== 'true' && count++ >= Number(table.dataset.rowLimit)); });
    const controls = table.closest('.table-wrap').querySelector(':scope > .table-controls');
    const button = controls.querySelector('button');
    button.hidden = matches.length <= Number(table.dataset.rowLimit);
    button.textContent = table.dataset.expanded === 'true' ? messages.collapse : messages.expand.replace('{count}', matches.length);
    button.setAttribute('aria-expanded', String(table.dataset.expanded === 'true'));
    controls.querySelector('.table-count').textContent = messages.shown.replace('{shown}', rows.filter(r => !r.hidden).length).replace('{count}', matches.length);
  }
  tables.forEach(table => {
    table.closest('.table-wrap').querySelector(':scope > .table-controls > [data-expand]').addEventListener('click', () => {
      table.dataset.expanded = String(table.dataset.expanded !== 'true'); apply(table);
    });
    table.tHead.querySelectorAll('th button[data-sort]').forEach((button, index) => {
      button.addEventListener('click', () => {
        const th = button.parentElement;
        const direction = th.getAttribute('aria-sort') === 'descending' ? 1 : -1;
        table.tHead.querySelectorAll('th').forEach(h => h.removeAttribute('aria-sort'));
        th.setAttribute('aria-sort', direction === 1 ? 'ascending' : 'descending');
        const rows = [...table.tBodies[0].rows];
        rows.sort((a, b) => {
          const av = a.cells[index].dataset.sortValue ?? a.cells[index].textContent.trim();
          const bv = b.cells[index].dataset.sortValue ?? b.cells[index].textContent.trim();
          if (av === '') return bv === '' ? 0 : 1;
          if (bv === '') return -1;
          const an = Number(av), bn = Number(bv);
          return direction * (Number.isFinite(an) && Number.isFinite(bn) ? an - bn : av.localeCompare(bv, 'ru', {numeric:true}));
        });
        rows.forEach(row => table.tBodies[0].append(row)); apply(table);
      });
    });
    apply(table);
  });
  [query, engine, bucket, cluster].filter(Boolean).forEach(control => control.addEventListener('input', () => {
    tables.forEach(table => { table.dataset.expanded = 'false'; apply(table); });
  }));
  document.querySelectorAll('[data-segment]').forEach(button => button.addEventListener('click', () => {
    if (bucket) bucket.value = button.dataset.segment;
    if (engine) engine.value = button.dataset.engine;
    tables.forEach(table => { table.dataset.expanded = 'false'; apply(table); });
    document.querySelector('.filters')?.scrollIntoView({block:'center', behavior:'smooth'});
  }));
})();

(() => {
  document.querySelectorAll('.serp-view').forEach(view => {
    const selects = ['engine','device','region','market'].map(k => view.querySelector(`select[data-serp-${k}]`));
    function apply() {
      let visible = 0;
      view.querySelectorAll('[data-serp-panel]').forEach(panel => {
        panel.hidden = selects.some((select, i) => select && panel.dataset[['serpEngine','serpDevice','serpRegion','serpMarket'][i]] !== select.value);
        if (!panel.hidden) visible++;
      });
      const empty = view.querySelector('[data-serp-empty]');
      if (empty) empty.hidden = visible > 0;
    }
    selects.filter(Boolean).forEach(select => select.addEventListener('change', () => {
      if (select === selects[3]) {
        const first = [...view.querySelectorAll('[data-serp-panel]')].find(panel => panel.dataset.serpMarket === select.value);
        if (first) ['serpEngine','serpDevice','serpRegion'].forEach((key, i) => {
          if (selects[i]) selects[i].value = first.dataset[key];
        });
      }
      apply();
    }));
    apply();
  });
  document.querySelectorAll('[data-serp-domain]').forEach(link => link.addEventListener('click', () => {
    const details = document.getElementById(link.hash.slice(1));
    if (details) details.open = true;
  }));
})();
