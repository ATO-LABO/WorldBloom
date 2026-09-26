// State-machine checks for viewer/static/world-expansion.js (the propose/check
// job panel). Hand-rolled DOM, fake fetch and fake timers, like
// raw_workspace_ui.cjs. Run: node tests/world_expansion_ui.cjs [path-to-js]
// (defaults to viewer/static/world-expansion.js next to this repo).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const jsPath = process.argv[2] || path.join(__dirname, '..', 'viewer', 'static', 'world-expansion.js');
const source = fs.readFileSync(jsPath, 'utf8');

class Button {
  constructor(action, {disabled = false, dataset = {}} = {}) {
    this.dataset = {patchAction: action, ...dataset};
    this.disabled = disabled;
    this.hidden = false;
    this.listeners = {};
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  closest(selector) {
    if (selector.includes('data-patch-action')) {
      const wanted = [...selector.matchAll(/data-patch-action="([^"]+)"/g)].map(m => m[1]);
      return !wanted.length || wanted.includes(this.dataset.patchAction) ? this : null;
    }
    return null;   // no card / li: messages fall through to the job panel
  }
  get parentElement() { return null; }
}

function page({jobsList = [], replies = {}, buttons: customButtons, allowAlert = false} = {}) {
  const status = {textContent: ''};
  const cancel = new Button('cancel');
  delete cancel.dataset.patchAction;
  const panel = {hidden: true, dataset: {run: 'run-1'},
    querySelector: s => s === '[role="status"]' ? status : s === '[data-patch-job-cancel]' ? cancel : null};
  const buttons = customButtons || [new Button('propose', {dataset: {run: 'run-1', trigger: '1'}}),
    new Button('approve', {disabled: true}),                       // not approvable: rendered disabled
    new Button('reject'), new Button('check', {dataset: {run: 'run-1', patch: 'p-abc'}})];
  const clicks = [], calls = [], timers = [], alerts = [];
  let reloads = 0;
  const document = {
    addEventListener: (type, fn) => { if (type === 'click') clicks.push(fn); },
    querySelector: s => s === '[data-patch-job]' ? panel : null,
    querySelectorAll: s => {
      if (s === '[data-patch-action]:not([disabled])') return buttons.filter(b => !b.disabled);
      if (s === '[data-patch-action][data-patch-job-disabled]') return buttons.filter(b => b.dataset.patchJobDisabled);
      if (s === '[data-patch-action]') return buttons;
      return [];
    },
  };
  const reply = (status, body) => ({ok: status >= 200 && status < 300, status, json: async () => body});
  const fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    calls.push(`${method} ${url}`);
    const key = `${method} ${url}`;
    if (key in replies) {
      // A reply is [status, body] or 'throw'; a list of replies is consumed in order, the last one repeating.
      const queue = Array.isArray(replies[key]) && (Array.isArray(replies[key][0]) || replies[key][0] === 'throw');
      const next = queue ? (replies[key].length > 1 ? replies[key].shift() : replies[key][0]) : replies[key];
      if (next === 'throw') throw new Error('network');
      return reply(next[0], next[1]);
    }
    if (key === 'GET /api/jobs') return reply(200, {jobs: jobsList});
    return reply(404, {message: 'not found'});
  };
  const context = {document, fetch,
    alert: allowAlert ? (t => { alerts.push(t); }) : (t => { throw new Error('alert() must not be used: ' + t); }),
    confirm: () => true, crypto: require('node:crypto').webcrypto, URL, Date, history: {replaceState() {}},
    location: {href: 'http://localhost/exp/run-1/monitor?tab=demand', reload: () => { reloads += 1; }},
    setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout: () => {}};
  vm.runInNewContext(source, context);
  const flush = async () => { for (let i = 0; i < 20; i += 1) await new Promise(r => setImmediate(r)); };
  const click = async button => { for (const fn of clicks) await fn({target: button}); await flush(); };
  const tick = async () => { const due = timers.splice(0); for (const fn of due) fn(); await flush(); };
  return {buttons, panel, status, cancel, calls, timers, alerts, click, tick, flush, reloads: () => reloads};
}

const running = step => [200, {job_id: 'j1', kind: 'world_patch', run_id: 'run-1', state: 'running', progress: {step}}];

(async () => {
  // 1. A finished job re-enables what it disabled -- never the approve button
  //    that was rendered disabled because the proposal is not approvable.
  {
    const p = page({replies: {'POST /api/runs/run-1/world-patch': [202, {job_id: 'j1'}],
      'GET /api/jobs/j1': [running('generate'), [200, {job_id: 'j1', state: 'failed', progress: {step: 'failed', message: '生成できませんでした'}}]]}});
    await p.flush();
    const [propose, approve, reject] = p.buttons;
    await p.click(propose);
    assert.equal(propose.disabled, true, 'running: propose disabled');
    assert.equal(reject.disabled, true, 'running: reject disabled');
    assert.equal(p.panel.hidden, false);
    assert.match(p.status.textContent, /拡張を考えています/);
    await p.tick();
    assert.equal(p.status.textContent, '生成できませんでした');
    assert.equal(propose.disabled, false, 'finished: propose back');
    assert.equal(reject.disabled, false, 'finished: reject back');
    assert.equal(approve.disabled, true, 'finished: a non-approvable approve button stays disabled');
    assert.deepEqual(JSON.parse(JSON.stringify(p.calls.filter(c => c.startsWith('POST')))), ['POST /api/runs/run-1/world-patch']);
  }
  // 2. A 409 on submit: buttons come back (approve still disabled), and the
  //    reason lands in the panel, not in an alert().
  {
    const p = page({replies: {'POST /api/runs/run-1/world-patch': [409, {message: '他の処理が実行中です'}]}});
    await p.flush();
    await p.click(p.buttons[0]);
    assert.equal(p.buttons[0].disabled, false);
    assert.equal(p.buttons[1].disabled, true);
    assert.match(p.status.textContent, /他の処理が実行中です/);
    assert.equal(p.panel.hidden, false);
  }
  // 3. A double click sends one POST.
  {
    const p = page({replies: {'POST /api/runs/run-1/world-patch': [202, {job_id: 'j1'}], 'GET /api/jobs/j1': running('trial')}});
    await p.flush();
    await Promise.all([p.click(p.buttons[0]), p.click(p.buttons[0])]);
    assert.equal(p.calls.filter(c => c.startsWith('POST')).length, 1, 'one POST only');
  }
  // 4. One failed poll must not drop the panel or hand the buttons back.
  {
    const p = page({replies: {'POST /api/runs/run-1/world-patch': [202, {job_id: 'j1'}],
      'GET /api/jobs/j1': [running('generate'), [500, {message: 'storage_error'}], 'throw', [500, {}], running('holdout')]}});
    await p.flush();
    await p.click(p.buttons[0]);
    await p.tick();
    assert.equal(p.panel.hidden, false, 'panel stays after a 500');
    assert.equal(p.buttons[0].disabled, true, 'buttons stay disabled after a 500');
    assert.equal(p.timers.length, 1, 'still polling');
    await p.tick(); await p.tick();
    assert.match(p.status.textContent, /進み具合を取得できません/);
    assert.equal(p.buttons[0].disabled, true);
    await p.tick();
    assert.match(p.status.textContent, /承認用の seed で検査しています/, 'recovers when the server answers again');
  }
  // 5. Returning to the page: the running job for this run is picked up once,
  //    even when a click started tracking before the scan came back.
  {
    const p = page({jobsList: [{job_id: 'j1', kind: 'world_patch', run_id: 'run-1', state: 'running'}],
      replies: {'GET /api/jobs/j1': running('trial')}});
    await p.flush();
    assert.equal(p.panel.hidden, false);
    assert.match(p.status.textContent, /試走しています/);
    assert.equal(p.buttons[0].disabled, true);
    assert.equal(p.buttons[1].disabled, true);
    assert.equal(p.timers.length, 1, 'one poll chain');
  }
  // 6. Someone else's job: say so, no stop button, buttons off.
  {
    const p = page({jobsList: [{job_id: 'j9', kind: 'evolve', run_id: 'run-7', state: 'running'}],
      replies: {'GET /api/jobs/j9': [200, {job_id: 'j9', kind: 'evolve', run_id: 'run-7', state: 'running', progress: {}}]}});
    await p.flush();
    assert.match(p.status.textContent, /ほかの処理/);
    assert.equal(p.cancel.hidden, true);
    assert.equal(p.buttons[0].disabled, true);
  }
  // 7. A failed stop request must not keep claiming "stopping".
  {
    const p = page({replies: {'POST /api/runs/run-1/world-patch': [202, {job_id: 'j1'}], 'GET /api/jobs/j1': running('generate'),
      'POST /api/jobs/j1/cancel': [500, {message: '停止要求を保存できません'}]}});
    await p.flush();
    await p.click(p.buttons[0]);
    await p.cancel.listeners.click();
    await p.flush();
    assert.equal(p.status.textContent, '停止要求を保存できません');
    await p.tick();
    assert.match(p.status.textContent, /拡張を考えています/, 'back to the real step, not "停止しています…"');
  }
  // 8. No job management (read-only viewer): stay silent.
  {
    const p = page({replies: {'GET /api/jobs': [503, {message: '実行管理は未設定です'}]}});
    await p.flush();
    assert.equal(p.panel.hidden, true);
    assert.equal(p.buttons[0].disabled, false);
  }
  // 9. Success reloads once.
  {
    const p = page({replies: {'POST /api/runs/run-1/world-patch': [202, {job_id: 'j1'}],
      'GET /api/jobs/j1': [running('holdout'), [200, {job_id: 'j1', state: 'succeeded', progress: {step: 'done'}}]]}});
    await p.flush();
    await p.click(p.buttons[3]);
    await p.tick();
    assert.equal(p.reloads(), 1);
  }
  // 10. "ジャンルの資産にする" (export): posts {experiment}, tells the operator
  //     where the asset landed, then reloads (WB-WORLDGROW-001 段階5d).
  {
    const exportBtn = new Button('export', {dataset: {world: 'momotaro', patch: 'p-abc', experiment: 'exp-1'}});
    const p = page({allowAlert: true, buttons: [exportBtn],
      replies: {'POST /api/worlds/momotaro/patches/p-abc/export': [200, {ok: true, patch_id: 'p-abc', path: 'p-abc.yaml'}]}});
    await p.flush();
    await p.click(exportBtn);
    assert.deepEqual(p.calls.filter(c => c.startsWith('POST')), ['POST /api/worlds/momotaro/patches/p-abc/export']);
    assert.equal(p.reloads(), 1);
    assert.equal(p.alerts.length, 1);
    assert.match(p.alerts[0], /p-abc\.yaml/);
  }
  // 11. "この世界に取り込む" (import) on a stale head: the 409 message
  //     surfaces (this fake DOM has no message slot, so it lands in an
  //     alert()) and the button re-enables instead of reloading.
  {
    const importBtn = new Button('import', {dataset: {world: 'momotaro2', entry: 'p-abc', head: 'stale-head'}});
    const p = page({allowAlert: true, buttons: [importBtn],
      replies: {'POST /api/worlds/momotaro2/patches/import':
        [409, {message: '画面を開いたあとに内容が変わりました。再読み込みしてください'}]}});
    await p.flush();
    await p.click(importBtn);
    assert.deepEqual(p.calls.filter(c => c.startsWith('POST')), ['POST /api/worlds/momotaro2/patches/import']);
    assert.equal(p.reloads(), 0);
    assert.equal(importBtn.disabled, false, 'a failure re-enables the button');
    assert.equal(p.alerts.length, 1);
    assert.match(p.alerts[0], /再読み込みしてください/);
  }
  // 12. Import succeeds: {entry, seen:{head}} payload, plain reload, no alert.
  {
    const importBtn = new Button('import', {dataset: {world: 'momotaro2', entry: 'p-abc', head: 'the-head'}});
    const p = page({allowAlert: true, buttons: [importBtn],
      replies: {'POST /api/worlds/momotaro2/patches/import': [200, {ok: true, patch_id: 'p-abc'}]}});
    await p.flush();
    await p.click(importBtn);
    assert.equal(p.reloads(), 1);
    assert.equal(p.alerts.length, 0, 'no alert on a plain success');
  }
  console.log('world-expansion.js: 12 scenarios ok');
})().catch(error => { console.error(error); process.exit(1); });
