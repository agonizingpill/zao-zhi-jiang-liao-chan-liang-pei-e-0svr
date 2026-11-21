const token = () => localStorage.getItem('pulp_token');
const endpoint = '/api/operational-cases';
const statusLabel = {submitted: '待主管复核', returned: '已退回', closed: '已关闭'};

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function request(path = '', body) {
  const response = await fetch(endpoint + path, {
    method: body ? 'POST' : 'GET',
    headers: {'Authorization': `Bearer ${token() || ''}`, 'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `操作失败 (${response.status})`);
  return data;
}

const launcher = element('button', '配额结算与发料交接');
launcher.type = 'button';
launcher.id = 'operational-cases-open';
launcher.hidden = true;
const dialog = element('dialog');
dialog.id = 'operational-cases';
dialog.setAttribute('aria-labelledby', 'operational-cases-title');
const header = element('header');
const title = element('h2', '配额结算与发料交接');
title.id = 'operational-cases-title';
const close = element('button', '关闭');
close.addEventListener('click', () => dialog.close());
header.append(title, close);
const controls = element('section');
const selector = element('select');
selector.setAttribute('aria-label', '处置类型');
const sourceSelect = element('select');
sourceSelect.setAttribute('aria-label', '业务记录');
const sourceSummary = element('p');
const feedback = element('p');
feedback.setAttribute('role', 'status');
const current = element('section');
controls.className = 'casework-controls';
controls.append(selector, sourceSelect);
dialog.append(header, controls, feedback, sourceSummary, current);
document.body.append(launcher, dialog);

let index;
let sequence = 0;
let active = false;
function use(path) { return `/${selector.value}/${path}?key=${encodeURIComponent(sourceSelect.value)}`; }
function sessionChanged() {
  let signedIn = false;
  try { signedIn = Boolean(token()); } catch { /* corrupted session will be handled by the login page */ }
  launcher.hidden = !signedIn;
  if (!signedIn && dialog.open) dialog.close();
}
sessionChanged();
window.addEventListener('storage', sessionChanged);
setInterval(sessionChanged, 600);

function collectField(form, label, control) {
  const field = element('label', label);
  field.append(control);
  form.append(field);
  return control;
}

function render(view) {
  const workflow = index.workflows[selector.value];
  const latest = view.history[0];
  sourceSummary.textContent = view.source?.source_summary || '源业务记录已不可用';
  const state = element('h3', latest ? statusLabel[latest.status] : '待登记');
  current.replaceChildren(state);
  const form = element('form');
  const submitMode = index.role !== 'viewer' && view.source && (!latest || latest.status === 'returned');
  const reviewMode = index.role === 'supervisor' && latest?.status === 'submitted';
  let outcome, reference;
  if (submitMode) {
    outcome = collectField(form, '处置结果', element('select'));
    for (const [key, label] of Object.entries(workflow.outcomes)) outcome.add(new Option(label, key));
    reference = collectField(form, workflow.reference_label, element('input'));
    reference.required = true;
    reference.minLength = 3;
    reference.maxLength = 120;
  }
  if (submitMode || reviewMode) {
    const note = collectField(form, submitMode ? '处置依据' : '复核依据', element('textarea'));
    note.required = true;
    note.minLength = 4;
    note.maxLength = 500;
    for (const [value, text] of submitMode ? [['submit', '提交处置']] : [['approve', '验收关闭'], ['return', '退回补证']]) {
      const button = element('button', text);
      button.type = 'submit';
      button.value = value;
      form.append(button);
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (active || !event.submitter) return;
      const requestSequence = sequence;
      active = true;
      event.submitter.disabled = true;
      feedback.textContent = '正在保存…';
      try {
        const updated = await request(use('actions'), {action: event.submitter.value, revision: view.revision,
          outcome: outcome?.value || '', reference: reference?.value || '', note: note.value});
        if (requestSequence !== sequence) return;
        feedback.textContent = '已保存处置记录';
        render(updated);
      } catch (error) {
        if (requestSequence === sequence) feedback.textContent = error.message;
      } finally {
        active = false;
        event.submitter.disabled = false;
      }
    });
    current.append(form);
  }
  if (view.history.length) {
    const history = element('ol');
    for (const event of view.history) {
      const item = element('li');
      item.append(element('strong', `${statusLabel[event.status]} · ${event.actor}`),
        element('p', `${workflow.outcomes[event.outcome] || event.outcome} · ${event.reference} · ${event.note}`),
        element('small', `${event.event_at} · ${event.source_summary}`));
      history.append(item);
    }
    current.append(element('h4', '处置历史'), history);
  }
}

async function loadSource(seq) {
  if (!sourceSelect.value) return;
  try {
    const view = await request(use('history'));
    if (seq === sequence) render(view);
  } catch (error) {
    if (seq === sequence) feedback.textContent = error.message;
  }
}
async function loadWorkflow() {
  const seq = ++sequence;
  sourceSelect.replaceChildren();
  sourceSummary.textContent = '';
  feedback.textContent = '正在读取源业务记录…';
  current.replaceChildren();
  try {
    const rows = await request(`/${selector.value}/sources`);
    if (seq !== sequence) return;
    for (const row of rows) sourceSelect.add(new Option(row.subject, row.source_key));
    feedback.textContent = rows.length ? `${rows.length} 条源业务记录` : '当前没有相关源业务记录';
    await loadSource(seq);
  } catch (error) {
    if (seq === sequence) feedback.textContent = error.message;
  }
}
selector.addEventListener('change', loadWorkflow);
sourceSelect.addEventListener('change', () => { current.replaceChildren(); feedback.textContent = ''; loadSource(++sequence); });
launcher.addEventListener('click', async () => {
  dialog.showModal();
  feedback.textContent = '正在加载处置台…';
  try {
    index = await request();
    selector.replaceChildren(...Object.entries(index.workflows).map(([key, value]) => new Option(value.title, key)));
    await loadWorkflow();
  } catch (error) {
    feedback.textContent = error.message;
  }
});
