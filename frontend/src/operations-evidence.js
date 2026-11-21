const sessionToken = () => localStorage.getItem('pulp_token');
const endpoint = '/api/operations-evidence';
const element = (tag, text) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
};

async function request(path = '', body) {
  const response = await fetch(endpoint + path, {
    method: body ? 'POST' : 'GET',
    headers: {'Authorization': `Bearer ${sessionToken() || ''}`, 'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `读取或签认失败 (${response.status})`);
  return data;
}

const launch = element('button', '业务核验');
launch.id = 'operations-evidence-open';
launch.hidden = true;
const dialog = element('dialog');
dialog.id = 'operations-evidence';
dialog.setAttribute('aria-labelledby', 'operations-evidence-title');
const header = element('header');
const title = element('h2', '业务核验');
title.id = 'operations-evidence-title';
const close = element('button', '关闭');
close.addEventListener('click', () => dialog.close());
header.append(title, close);
const tabs = element('nav');
tabs.setAttribute('aria-label', '核验模块');
const status = element('p');
status.setAttribute('role', 'status');
const content = element('section');
dialog.append(header, tabs, status, content);
document.body.append(launch, dialog);

function showLaunch() {
  try { launch.hidden = !sessionToken(); }
  catch { launch.hidden = true; }
}
showLaunch();
window.addEventListener('storage', showLaunch);
setInterval(showLaunch, 600);

function csvDownload(view, rows) {
  const quote = value => {
    let text = String(value ?? '');
    if (typeof value === 'string' && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return `"${text.replaceAll('"', '""')}"`;
  };
  const lines = [view.columns.concat(['签认人', '核验说明']), ...rows.map(row => row.cells.concat([row.receipt?.signer, row.receipt?.note]))];
  const file = new Blob(['\uFEFF', lines.map(row => row.map(quote).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(file);
  const link = element('a');
  link.href = url;
  link.download = `${view.title}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

let activeRequest = 0;
async function selectModule(module, canAcknowledge) {
  const sequence = ++activeRequest;
  for (const tab of tabs.querySelectorAll('button')) tab.setAttribute('aria-current', tab.dataset.module === module ? 'true' : 'false');
  status.textContent = '正在查询业务记录…';
  content.replaceChildren();
  try {
    const view = await request(`/${module}`);
    if (sequence !== activeRequest) return;
    const filter = element('input');
    filter.type = 'search';
    filter.placeholder = '按业务记录筛选';
    filter.setAttribute('aria-label', '按业务记录筛选');
    const exportButton = element('button', '导出当前结果 CSV');
    const count = element('span');
    const tools = element('div');
    tools.className = 'evidence-tools';
    tools.append(filter, exportButton, count);
    const table = element('table');
    const container = element('div');
    container.className = 'evidence-table';
    container.append(table);
    const heading = table.createTHead().insertRow();
    for (const label of view.columns.concat('主管签认')) {
      const th = element('th', label);
      th.scope = 'col';
      heading.append(th);
    }
    const body = table.createTBody();
    const visible = () => view.rows.filter(row => row.cells.some(value => String(value ?? '').toLowerCase().includes(filter.value.trim().toLowerCase())));
    function renderRows() {
      const rows = visible();
      count.textContent = `${rows.length} / ${view.rows.length} 条${view.rows.length === view.limit ? ` · 本次最多 ${view.limit} 条` : ''}`;
      body.replaceChildren();
      for (const row of rows) {
        const tr = body.insertRow();
        for (const cell of row.cells) tr.insertCell().textContent = cell ?? '—';
        const receipt = tr.insertCell();
        if (row.receipt) receipt.textContent = `${row.receipt.signer} · ${row.receipt.note}`;
        else if (canAcknowledge) {
          const form = element('form');
          const note = element('input');
          note.placeholder = '核验说明'; note.required = true; note.minLength = 3; note.maxLength = 500;
          note.setAttribute('aria-label', '核验说明');
          const submit = element('button', '签认');
          form.append(note, submit);
          form.addEventListener('submit', async event => {
            event.preventDefault();
            submit.disabled = true;
            try {
              row.receipt = await request(`/${module}/acknowledgements`, {key: row.key, note: note.value});
              renderRows();
            } catch (error) { status.textContent = error.message; }
            finally { submit.disabled = false; }
          });
          receipt.append(form);
        } else receipt.textContent = '待签认';
      }
      if (!rows.length) {
        const empty = body.insertRow().insertCell();
        empty.colSpan = view.columns.length + 1;
        empty.textContent = '当前没有符合条件的业务记录';
      }
    }
    filter.addEventListener('input', renderRows);
    exportButton.addEventListener('click', () => csvDownload(view, visible()));
    content.append(element('h3', view.title), element('p', view.description), tools, container);
    status.textContent = '';
    renderRows();
  } catch (error) {
    if (sequence === activeRequest) status.textContent = error.message;
  }
}

launch.addEventListener('click', async () => {
  dialog.showModal();
  status.textContent = '正在加载业务核验模块…';
  try {
    const catalog = await request();
    tabs.replaceChildren(...catalog.modules.map(module => {
      const button = element('button', module.name);
      button.dataset.module = module.id;
      button.addEventListener('click', () => selectModule(module.id, catalog.can_acknowledge));
      return button;
    }));
    const initial = new URLSearchParams(location.search).get('evidence');
    const selected = catalog.modules.find(module => module.id === initial) || catalog.modules[0];
    await selectModule(selected.id, catalog.can_acknowledge);
  } catch (error) { status.textContent = error.message; }
});
if (new URLSearchParams(location.search).has('evidence') && !launch.hidden) launch.click();
