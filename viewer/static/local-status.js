/* AIT-91: measured GPU trends and model residency. No chart dependency. */
(function (scope, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof document !== 'undefined') {
    const boot = () => api.mount(document);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
  }
})(typeof window !== 'undefined' ? window : {}, function () {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const timeLabel = at => new Date(at * 1000).toLocaleTimeString('ja-JP', {hour: '2-digit', minute: '2-digit'});
  const temperature = value => finite(value) ? `${Math.round(value)}°C` : '—';
  const states = {loaded: '✓ ロード済み', unloaded: '○ 未ロード', loading: '◌ ロード中…',
    unknown: '状態取得不可', not_installed: '未インストール', error: 'ロード失敗'};
  const placements = {gpu: 'GPU', cpu: 'CPU', mixed: 'GPU＋CPU（一部GPU）', unknown: '未取得'};

  function historyFor(history, id, start, end) {
    return (history || []).filter(s => finite(s.at) && s.at >= start && s.at <= end).map(s => {
      const device = (s.devices || []).find(d => d.id === id);
      return {at: s.at, value: finite(device?.temperature_c) ? device.temperature_c : null};
    }).sort((a, b) => a.at - b.at);
  }

  function trend(points, interval = 2) {
    if (!points.length || !finite(points[points.length - 1].value)) return {label: '比較不可（温度未取得）', delta: null};
    const last = points[points.length - 1], target = last.at - 60;
    const margin = Math.max(6, interval * 3);
    const baseline = points.filter(p => Math.abs(p.at - target) <= margin)
      .sort((a, b) => Math.abs(a.at - target) - Math.abs(b.at - target))[0];
    if (!baseline || last.at - points[0].at < 60) return {label: '直近1分の推移を収集中', delta: null};
    const segment = points.filter(p => p.at >= baseline.at);
    if (segment.some((p, i) => !finite(p.value) || (i > 0 && p.at - segment[i - 1].at > margin))) {
      return {label: '比較不可（直近1分に欠測）', delta: null};
    }
    const delta = last.value - baseline.value;
    return {delta, label: `直近1分 ${delta > 0 ? '↗ ＋' : delta < 0 ? '↘ −' : '→ '}${Math.abs(delta).toFixed(0)}°C`};
  }

  function segments(points, gap = 6) {
    const result = [];
    let current = [];
    points.forEach((p, i) => {
      if (!finite(p.value) || (i > 0 && p.at - points[i - 1].at > gap)) {
        if (current.length) result.push(current);
        current = [];
      }
      if (finite(p.value)) current.push(p);
    });
    if (current.length) result.push(current);
    return result;
  }

  class Poller {
    constructor(load, options) {
      this.load = load; this.options = options; this.timer = null; this.controller = null;
      this.version = 0; this.running = false;
      this.schedule = options.schedule || ((f, ms) => setTimeout(f, ms));
      this.cancel = options.cancel || (id => clearTimeout(id));
    }
    stop() {
      this.version++; this.running = false;
      if (this.timer !== null) this.cancel(this.timer);
      this.timer = null;
      this.controller?.abort(); this.controller = null;
    }
    start() { this.stop(); this.run(this.version); }
    async run(version) {
      if (version !== this.version || !this.options.active() || this.running) return;
      this.running = true;
      const controller = new AbortController(); this.controller = controller;
      this.options.pending?.(true);
      try {
        const value = await this.load(controller.signal);
        if (version === this.version && this.options.active()) this.options.success(value);
      } catch (error) {
        if (version === this.version && this.options.active()) this.options.error(error);
      } finally {
        if (version === this.version) {
          this.running = false; this.controller = null; this.options.pending?.(false);
          if (this.options.active()) this.timer = this.schedule(() => {
            this.timer = null; this.run(version);
          }, 2000);
        }
      }
    }
  }

  function mount(doc) {
    const dialog = doc.getElementById('local-status-dialog');
    const trigger = doc.querySelector('[data-sheet="local-status-dialog"]');
    if (!dialog || !trigger || dialog.dataset.statusMounted || typeof dialog.showModal !== 'function') return;
    dialog.dataset.statusMounted = 'true';
    const q = selector => dialog.querySelector(selector);
    const set = (selector, value) => { const el = q(selector); if (el) el.textContent = value; };
    const svg = q('[data-ls-chart]'), picker = q('[data-ls-gpu]'), range = q('[data-ls-range]');
    const refresh = q('[data-local-status-refresh]'), errorEl = q('[data-ls-error]');
    let status = null, selectedGpu = '', providerKey = '', mutation = false, plotted = [], actionError = '', currentAvailable = false;

    function metric(kind, value, total) {
      const meter = q(`[data-ls-${kind}-meter]`), fill = q(`[data-ls-${kind}-fill]`);
      const valid = finite(value) && finite(total) && total > 0;
      meter.hidden = !valid;
      if (valid) {
        const percent = Math.min(100, Math.max(0, value / total * 100));
        meter.setAttribute('aria-valuenow', String(value)); meter.setAttribute('aria-valuemax', String(total));
        fill.style.width = `${percent}%`;
      } else meter.removeAttribute('aria-valuenow');
    }

    function draw() {
      const w = Math.floor(svg.clientWidth);
      if (w < 100) return;
      const height = Math.max(160, svg.clientHeight), left = 42, right = w - 14, top = 28, bottom = height - 39;
      svg.setAttribute('viewBox', `0 0 ${w} ${height}`);
      svg.replaceChildren();
      const ns = 'http://www.w3.org/2000/svg';
      function el(tag, attrs, text) {
        const item = doc.createElementNS(ns, tag);
        Object.entries(attrs).forEach(([k, v]) => item.setAttribute(k, String(v)));
        if (text !== undefined) item.textContent = text;
        svg.append(item); return item;
      }
      const gpu = status?.gpu;
      const end = Math.max(Date.now() / 1000, status?.checked_at_epoch || 0);
      const start = end - Number(range.value) * 60;
      const points = historyFor(gpu?.history, selectedGpu, start, end);
      const values = points.filter(p => finite(p.value)).map(p => p.value);
      const pause = status?.thermal?.pause_at;
      const min = Math.min(30, ...values), max = Math.max(90, ...values, finite(pause) ? pause : 0);
      const low = Math.max(0, Math.floor(min / 10) * 10), high = Math.ceil(max / 10) * 10;
      const x = at => left + (at - start) / (end - start) * (right - left);
      const y = v => bottom - (v - low) / (high - low) * (bottom - top);
      const title = values.length ? `GPU温度、直近${range.value}分。${trend(points, gpu?.interval_seconds).label}` : 'GPU温度の履歴はまだ取得できていません';
      el('title', {}, title);
      el('text', {x: 5, y: 16}, '°C');
      const step = high - low > 80 ? 20 : 10;
      for (let v = low; v <= high; v += step) {
        el('line', {x1: left, x2: right, y1: y(v), y2: y(v), class: 'ls-chart-grid'});
        el('text', {x: left - 8, y: y(v) + 4, 'text-anchor': 'end'}, String(v));
      }
      const ticks = w < 400 ? 2 : 3;
      for (let i = 0; i <= ticks; i++) {
        el('text', {x: x(start + (end - start) * i / ticks), y: height - 18,
          'text-anchor': i === 0 ? 'start' : i === ticks ? 'end' : 'middle'}, timeLabel(start + (end - start) * i / ticks));
      }
      el('text', {x: right, y: height - 2, 'text-anchor': 'end'}, '時刻');
      // The heat guard governs GPU0, not an arbitrary selected adapter.
      const device = (gpu?.devices || []).find(d => d.id === selectedGpu);
      if (status?.thermal?.enabled && device?.index === 0 && finite(pause)) {
        el('line', {x1: left, x2: right, y1: y(pause), y2: y(pause), class: 'ls-chart-threshold'});
        el('text', {x: right - 4, y: y(pause) - 5, 'text-anchor': 'end'}, `停止 ${pause}°C`);
      }
      const gap = Math.max(6, (gpu?.interval_seconds || 2) * 3);
      const paths = segments(points, gap);
      paths.forEach(part => {
        if (part.length === 1) el('circle', {cx: x(part[0].at), cy: y(part[0].value), r: 3, class: 'ls-chart-dot'});
        else el('path', {d: part.map((p, i) => `${i ? 'L' : 'M'}${x(p.at).toFixed(2)},${y(p.value).toFixed(2)}`).join(' '), class: 'ls-chart-path'});
      });
      const event = (gpu?.events || []).filter(e => e.at >= start && e.at <= end).slice(-1)[0];
      if (event) {
        el('line', {x1: x(event.at), x2: x(event.at), y1: top, y2: bottom, class: 'ls-chart-event'});
        el('text', {x: Math.min(right, Math.max(left + 115, x(event.at))), y: 16, 'text-anchor': 'end'}, event.label);
      }
      if (!values.length) el('text', {x: (left + right) / 2, y: 105, 'text-anchor': 'middle'}, '温度データ未取得');
      plotted = points.map(p => ({...p, x: x(p.at)}));
      const latest = points[points.length - 1];
      const stale = !currentAvailable || !gpu?.at || Date.now() / 1000 - gpu.at > Math.max(10, (gpu.interval_seconds || 2) * 4);
      set('[data-ls-temp]', stale ? '—' : temperature(device?.temperature_c));
      set('[data-ls-delta]', stale ? '温度データ未取得' : trend(points, gpu?.interval_seconds).label);
      set('[data-ls-history-note]', gpu?.started_at ? `収集開始 ${timeLabel(gpu.started_at)} · 最大60分 · 欠測区間は接続しません` : '履歴を収集中（過去の温度は保存されていません）');
      set('[data-ls-readout]', latest ? `${timeLabel(latest.at)} · ${temperature(latest.value)}` : '—');
    }

    function renderGpu() {
      const gpu = status?.gpu, latestDevices = gpu?.devices || [];
      const catalog = new Map();
      (gpu?.history || []).forEach(s => (s.devices || []).forEach(d => catalog.set(d.id, d)));
      latestDevices.forEach(d => catalog.set(d.id, d));
      const devices = Array.from(catalog.values()).sort((a, b) => a.index - b.index);
      const ids = JSON.stringify(devices.map(d => [d.id, d.name]));
      if (picker.dataset.ids !== ids) {
        picker.dataset.ids = ids; picker.replaceChildren();
        devices.forEach(d => { const o = doc.createElement('option'); o.value = d.id; o.textContent = `GPU ${d.index} · ${d.name}`; picker.append(o); });
      }
      if (!devices.some(d => d.id === selectedGpu)) selectedGpu = devices[0]?.id || '';
      picker.value = selectedGpu; picker.hidden = devices.length < 2;
      const d = devices.find(item => item.id === selectedGpu);
      const stale = !currentAvailable || !gpu?.at || Date.now() / 1000 - gpu.at > Math.max(10, (gpu.interval_seconds || 2) * 4);
      const current = stale ? null : latestDevices.find(item => item.id === selectedGpu);
      set('[data-ls-gpu-name]', d ? `GPU ${d.index} · ${d.name}` : 'GPU情報を取得できません（NVIDIA GPU / nvidia-smiを確認）');
      set('[data-ls-util]', finite(current?.utilization_percent) ? `${Math.round(current.utilization_percent)}%` : '—');
      metric('util', current?.utilization_percent, 100);
      set('[data-ls-activity]', finite(current?.utilization_percent) ? 'GPU全体の計算使用率' : '計算使用率を取得できません');
      const used = current?.memory_used_mib, total = current?.memory_total_mib;
      const validMemory = finite(used) && finite(total) && total > 0;
      set('[data-ls-vram]', validMemory ? `${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} GiB` : '—');
      set('[data-ls-memory]', validMemory ? `空き ${((total - used) / 1024).toFixed(1)} GiB · GPU全体` : 'VRAM使用量を取得できません');
      metric('vram', validMemory ? used : null, validMemory ? total : null);
      const thermal = status?.thermal;
      set('[data-ls-thermal]', thermal?.enabled ? `熱保護：GPU 0 · ${thermal.pause_at}°Cで停止／${thermal.resume_at}°C未満で再開` : '熱保護：自動停止は無効');
      draw();
    }

    function node(tag, className, text) {
      const el = doc.createElement(tag); if (className) el.className = className;
      if (text !== undefined) el.textContent = text; return el;
    }
    function actions(row) {
      const group = node('div', 'ls-model-actions');
      const add = (text, endpoint, body) => {
        const button = node('button', 'ls-action', text); button.type = 'button';
        button.disabled = mutation || row.actions_disabled;
        button.addEventListener('click', () => runAction(endpoint, body)); group.append(button);
      };
      if (row.can_preload_selected) add('送信対象をロード', '/api/status/local/preload', {});
      if (row.can_unload) add(row.id === 'ollama' ? 'Ollamaのモデルを解放' : 'モデルを解放', '/api/status/local/unload',
        {backend: row.id === 'llama_server' ? 'llama-server' : 'ollama'});
      return group;
    }

    function renderModels(force = false) {
      const rows = (status?.rows || []).filter(r => r.id === 'ollama' || r.id === 'llama_server');
      const key = JSON.stringify([rows, mutation]);
      if (!force && key === providerKey) return;
      providerKey = key;
      const providers = rows.map(row => {
        const section = node('section', 'ls-provider');
        const head = node('div', 'ls-provider-head');
        head.append(node('h5', '', row.label));
        const state = {up: '● サーバー稼働中', down: '接続できません', unconfigured: '未設定'}[row.server_state] || '取得不可';
        head.append(node('span', 'ls-server-state', state), actions(row)); section.append(head);
        const list = node('ul', 'ls-model-list');
        (row.models || []).forEach(model => {
          const item = node('li', 'ls-model-item');
          const main = node('div', 'ls-model-main');
          main.append(node('span', 'ls-model-name', model.name));
          if (model.selected) main.append(node('span', 'ls-model-selected', '文章生成の送信対象'));
          main.append(node('span', `ls-model-state state-${model.state}`, states[model.state] || '取得不可'));
          const detail = node('div', 'ls-model-detail');
          if (model.state === 'loaded') {
            detail.append(node('span', '', `配置：${placements[model.placement] || '未取得'}`));
            if (finite(model.vram_bytes)) detail.append(node('span', '', `モデルVRAM ${(model.vram_bytes / 1073741824).toFixed(1)} GiB`));
            detail.append(node('span', '', '実行状態：未取得'));
          } else if (model.state === 'unloaded') detail.append(node('span', '', 'ローカルに保存済み'));
          else if (model.state === 'unknown') detail.append(node('span', '', 'ロード状態を確認できません'));
          item.append(main, detail); list.append(item);
        });
        if (!list.children.length) list.append(node('li', 'ls-model-detail', row.server_state === 'unconfigured' ? 'モデル未設定' : 'モデル情報なし'));
        section.append(list); return section;
      });
      q('[data-local-status-rows]').replaceChildren(...providers);
    }

    function render() {
      set('[data-local-status-backend]', status.backend_label || '文章生成の送り先：未設定');
      const lease = status.lease;
      set('[data-ls-lease]', !lease?.enabled ? 'WorldBloomのGPU利用予約：監視無効' : lease.busy ?
        `WorldBloomのGPU利用予約：処理中 · ${lease.owner || '所有者未取得'}` : 'WorldBloomのGPU利用予約：空き');
      set('[data-local-status-time]', `最終取得 ${status.checked_at || '—'}`);
      set('[data-ls-update]', '● 自動更新');
      if (!mutation) { errorEl.textContent = actionError || status.preload_error || ''; errorEl.hidden = !errorEl.textContent; }
      renderGpu(); renderModels();
    }

    function failed() {
      currentAvailable = false;
      if (!status) draw();
      set('[data-ls-update]', '取得失敗 · 再試行中');
      errorEl.textContent = '状態を取得できません。表示中の履歴は過去の取得結果です。'; errorEl.hidden = false;
      set('[data-ls-temp]', '—'); set('[data-ls-delta]', '現在値を取得できません');
      set('[data-ls-util]', '—'); set('[data-ls-vram]', '—');
      metric('util', null, null); metric('vram', null, null);
      set('[data-ls-activity]', '現在の使用率は取得不可'); set('[data-ls-memory]', '現在の使用量は取得不可');
      set('[data-local-status-backend]', '文章生成の送信先：取得不可');
      set('[data-ls-lease]', 'WorldBloomのGPU利用予約：取得不可');
      // Keep historical curves, but never leave actionable or apparently live model rows.
      q('[data-local-status-rows]').replaceChildren(node('p', 'ls-error', 'サーバー・モデル状態：取得不可'));
      providerKey = '';
    }

    const poller = new Poller(async signal => {
      const deadline = new AbortController();
      const cancel = () => deadline.abort();
      signal.addEventListener('abort', cancel, {once: true});
      const timeout = setTimeout(cancel, 15000);
      try {
        const response = await fetch(dialog.dataset.fetch, {signal: deadline.signal, headers: {'X-WorldBloom-Client': '1'}, cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value = await response.json();
        if (!value || !Array.isArray(value.rows) || !value.gpu || !Array.isArray(value.gpu.history)) throw new Error('Invalid status');
        return value;
      } finally { clearTimeout(timeout); signal.removeEventListener('abort', cancel); }
    }, {
      active: () => dialog.open && !doc.hidden && !mutation,
      pending: flag => { refresh.disabled = flag || mutation; },
      success: value => { currentAvailable = true; status = value; render(); }, error: failed,
    });

    async function runAction(endpoint, body) {
      if (mutation) return;
      mutation = true; poller.stop(); refresh.disabled = true; renderModels(true);
      actionError = ''; errorEl.hidden = true;
      try {
        const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-WorldBloom-Client': '1'},
          body: JSON.stringify(body), signal: AbortSignal.timeout(20000)});
        if (!response.ok) {
          const value = await response.json().catch(() => ({})); throw new Error(value.message || `HTTP ${response.status}`);
        }
      } catch (error) {
        actionError = `${error.message || '操作に失敗しました'} · 最新の状態を確認してください。`;
        errorEl.textContent = actionError; errorEl.hidden = false;
      } finally {
        mutation = false; providerKey = ''; refresh.disabled = false;
        if (dialog.open) poller.start();
      }
    }

    picker.addEventListener('change', () => { selectedGpu = picker.value; renderGpu(); });
    range.addEventListener('change', draw);
    svg.addEventListener('pointermove', event => {
      if (!plotted.length) return;
      const x = event.clientX - svg.getBoundingClientRect().left;
      const p = plotted.reduce((a, b) => Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a);
      set('[data-ls-readout]', `${timeLabel(p.at)} · ${temperature(p.value)}`);
    });
    refresh.addEventListener('click', () => { if (!mutation && !poller.running) poller.start(); });
    trigger.addEventListener('click', event => {
      event.preventDefault(); if (!dialog.open) dialog.showModal();
      currentAvailable = false; providerKey = '';
      set('[data-local-status-backend]', '文章生成の送信先：確認中…');
      set('[data-ls-lease]', 'WorldBloomのGPU利用予約：確認中…');
      q('[data-local-status-rows]').replaceChildren(node('p', 'ls-error', 'モデル状態を確認中…'));
      renderGpu(); poller.start();
    });
    dialog.addEventListener('close', () => { poller.stop(); refresh.disabled = false; });
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) poller.stop(); else if (dialog.open) poller.start(); });
    window.addEventListener('pagehide', () => poller.stop());
    new ResizeObserver(() => { if (dialog.open) draw(); }).observe(svg);
  }
  return {historyFor, trend, segments, Poller, mount};
});
