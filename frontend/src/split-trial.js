// 整批拆周试算前端模块：挂在「周配额试算」页内，复用 app.js 的请求与渲染工具。
(function () {
  const app = () => window.pulpApp;
  const splitStatusText = {
    draft: '草稿',
    trial_failed: '试算失败',
    ready: '可送审',
    submitted: '待审批',
    approved: '已批准',
    returned: '已整单退回',
  };
  const constraintText = { forecast_lower: '预测下界', machine_remaining: '机台余量', '': '—' };

  function badge(value) {
    const P = app();
    return `<span class="status status-${P.esc(value)}">${P.esc(splitStatusText[value] || value)}</span>`;
  }

  async function fetchAll() {
    const P = app();
    const [orders, machines, overview] = await Promise.all([
      P.api('/split-trials'),
      P.api('/machine-runs'),
      P.api('/split-trials/quota-overview'),
    ]);
    return { orders, machines, overview };
  }

  function overviewPanel(overview) {
    const P = app();
    const rows = overview.length
      ? overview.map((r) => `
        <tr>
          <td><b>${P.esc(r.week_start)}</b></td>
          <td>${P.esc(r.run_code)}<br><small>${P.esc(r.machine_name)}</small></td>
          <td>${P.fmt(r.safety_capacity_tons)} t</td>
          <td>${P.fmt(r.split_allocated_tons)} t</td>
          <td class="${Number(r.split_gap_tons) > 0 ? 'gap-cell' : ''}">${P.fmt(r.split_gap_tons)} t</td>
          <td>${P.fmt(r.occupied_tons)} t</td>
          <td>${P.fmt(r.remaining_tons)} t</td>
        </tr>`).join('')
      : '<tr><td colspan="7" class="subtle">暂无拆周试算数据</td></tr>';
    return `
      <div class="panel split-panel">
        <div class="panel-head"><div><h3>分周配额与缺口</h3>
        <p class="subtle">按机台 / 周汇总拆周排量、缺口、已占用与剩余余量。</p></div></div>
        <table class="ledger"><thead><tr>
          <th>周起始</th><th>纸机</th><th>安全容量</th><th>拆周排量</th><th>缺口</th><th>已占用</th><th>剩余余量</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div>`;
  }

  function weekRows(order) {
    const P = app();
    return order.weeks.map((w) => `
      <tr>
        <td>第${w.week_index}周 · ${P.esc(w.week_start)}</td>
        <td>${P.fmt(w.forecast_lower_tons)} t</td>
        <td>${P.fmt(w.machine_remaining_tons)} t</td>
        <td>${P.fmt(w.cap_tons)} t<br><small>受限于${constraintText[w.constrained_by] || '—'}</small></td>
        <td><b>${P.fmt(w.allocated_tons)} t</b>${w.occupied ? '<br><small>已占用</small>' : ''}</td>
        <td class="${Number(w.gap_tons) > 0 ? 'gap-cell' : ''}">${P.fmt(w.gap_tons)} t</td>
      </tr>`).join('');
  }

  function orderCard(order, canEdit, isSupervisor) {
    const P = app();
    const editable = canEdit && ['draft', 'trial_failed', 'ready', 'returned'].includes(order.status);
    const lowerInputs = order.weeks.map((w) => `
      <label>第${w.week_index}周预测下界 t
        <input type="number" step="0.1" data-lower="${order.id}:${w.week_index}" value="${w.forecast_lower_tons}">
      </label>`).join('');
    const actions = `
      <div class="toolbar-actions" style="margin-top:10px;gap:8px;flex-wrap:wrap">
        ${editable ? `<button class="btn small warn" data-retry="${order.id}">重新试算（不重复生成周计划）</button>` : ''}
        ${canEdit && order.status === 'ready' ? `<button class="btn small" data-submit="${order.id}">送审</button>` : ''}
        ${isSupervisor && order.status === 'submitted' ? `<button class="btn small" data-approve="${order.id}">主管批准</button>` : ''}
      </div>`;
    return `
      <div class="panel split-panel">
        <div class="panel-head">
          <div><h3>${P.esc(order.order_code)} ${badge(order.status)}</h3>
          <p class="subtle">${P.esc(order.run_code)} · 订单 ${P.fmt(order.order_tons)} t ·
          已排 ${P.fmt(order.total_allocated_tons)} t · 缺口
          <span class="${Number(order.total_gap_tons) > 0 ? 'gap-cell' : ''}">${P.fmt(order.total_gap_tons)} t</span>
          · 试算次数 ${order.trial_attempts}${order.reject_reason ? ` · <em>${P.esc(order.reject_reason)}</em>` : ''}</p></div>
        </div>
        <table class="ledger"><thead><tr>
          <th>周次 / 起始</th><th>预测下界</th><th>机台余量</th><th>单周上限</th><th>排量</th><th>缺口</th>
        </tr></thead><tbody>${weekRows(order)}</tbody></table>
        ${editable ? `<form id="retry-form-${order.id}" class="form-grid" style="margin-top:12px">${lowerInputs}</form>` : ''}
        ${actions}
      </div>`;
  }

  function createPanel(machines) {
    const P = app();
    const canEdit = P.state.user.role !== 'viewer';
    if (!canEdit) {
      return `<div class="panel split-panel"><h3>拆周试算单</h3><p class="subtle">查看员只读，可查看分周吨位与缺口，不能创建或送审。</p></div>`;
    }
    const machineOptions = machines.map((m) =>
      `<option value="${m.id}">${P.esc(m.run_code)} · ${P.esc(m.machine_name)} · 安全容量 ${m.safety_capacity_tons}t</option>`).join('');
    return `
      <div class="panel split-panel">
        <h3>新建整批拆周试算</h3>
        <p class="subtle">填订单吨位，选同一纸机的两个周次和各自工况（预测下界）。首周尽量多交，
        单周排量同时受预测下界与「安全容量 − 已批占用」的机台余量约束。</p>
        <form id="split-create-form" class="form-grid" style="margin-top:14px">
          <label>试算单编号<input name="order_code" value="ST-2611-${String(Date.now()).slice(-4)}" required></label>
          <label>订单吨位 t<input name="order_tons" type="number" step="0.1" value="100" required></label>
          <label class="full">纸机（两周同一台）<select name="machine_run_id">${machineOptions}</select></label>
          <label>首周起始日<input name="w1_start" type="date" value="2026-11-30" required></label>
          <label>首周预测下界 t<input name="w1_lower" type="number" step="0.1" value="60" required></label>
          <label>次周起始日<input name="w2_start" type="date" value="2026-12-07" required></label>
          <label>次周预测下界 t<input name="w2_lower" type="number" step="0.1" value="70" required></label>
          <label class="full">备注<input name="notes" value="整批拆周试算"></label>
          <button class="btn full" type="submit">提交两周试算</button>
        </form>
        <div class="callout danger" style="margin-top:14px">两周排量凑不足订单吨位时试算失败并记录缺口，不能送审；可调整工况后重试，重试不会重复生成周计划。</div>
      </div>`;
  }

  async function mount() {
    const P = app();
    if (!P || P.state.page !== 'quotas') return;
    const host = document.querySelector('#page-content');
    // render() 每次都会重建 #page-content；这里再做一次防御性去重，避免重复挂载。
    if (!host || host.querySelector('.split-wrap')) return;
    let data;
    try {
      data = await fetchAll();
    } catch (err) {
      P.toast(err.message, true);
      return;
    }
    const canEdit = P.state.user.role !== 'viewer';
    const isSupervisor = P.state.user.role === 'supervisor';
    const ordersHtml = data.orders.length
      ? data.orders.map((o) => orderCard(o, canEdit, isSupervisor)).join('')
      : '<div class="panel split-panel subtle">暂无拆周试算单</div>';
    const wrapper = document.createElement('div');
    wrapper.className = 'split-wrap';
    wrapper.innerHTML = overviewPanel(data.overview) + ordersHtml + createPanel(data.machines);
    host.prepend(wrapper);
    bind(wrapper, data);
  }

  function bind(wrapper, data) {
    const P = app();
    const createForm = wrapper.querySelector('#split-create-form');
    if (createForm) {
      createForm.onsubmit = async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target));
        const machine = Number(f.machine_run_id);
        const body = {
          order_code: f.order_code,
          order_tons: Number(f.order_tons),
          notes: f.notes,
          weeks: [
            { week_start: f.w1_start, machine_run_id: machine, forecast_lower_tons: Number(f.w1_lower) },
            { week_start: f.w2_start, machine_run_id: machine, forecast_lower_tons: Number(f.w2_lower) },
          ],
        };
        try {
          await P.api('/split-trials', { method: 'POST', body: JSON.stringify(body) });
          P.toast('两周试算已完成');
          P.render();
        } catch (err) { P.toast(err.message, true); }
      };
    }

    wrapper.querySelectorAll('[data-retry]').forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.retry;
        const order = data.orders.find((o) => String(o.id) === String(id));
        const weeks = order.weeks.map((w) => ({
          week_start: w.week_start,
          machine_run_id: w.machine_run_id,
          forecast_lower_tons: Number(wrapper.querySelector(`[data-lower="${id}:${w.week_index}"]`).value),
        }));
        try {
          await P.api(`/split-trials/${id}/retry`, { method: 'POST', body: JSON.stringify({ weeks }) });
          P.toast('已按当前机台余量重新试算，未重复生成周计划');
          P.render();
        } catch (err) { P.toast(err.message, true); }
      };
    });

    wrapper.querySelectorAll('[data-submit]').forEach((btn) => {
      btn.onclick = async () => {
        try {
          await P.api(`/split-trials/${btn.dataset.submit}/submit`, { method: 'POST' });
          P.toast('试算单已送审');
          P.render();
        } catch (err) { P.toast(err.message, true); }
      };
    });

    wrapper.querySelectorAll('[data-approve]').forEach((btn) => {
      btn.onclick = async () => {
        try {
          await P.api(`/split-trials/${btn.dataset.approve}/approve`, { method: 'POST' });
          P.toast('拆周试算单已批准，两周余量已占用');
          P.render();
        } catch (err) { P.toast(err.message, true); }
      };
    });
  }

  window.splitTrialHook = mount;
})();
