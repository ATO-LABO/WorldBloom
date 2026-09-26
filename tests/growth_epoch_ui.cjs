// State-machine checks for the two small scripts WB-WORLDGROW-001 段階5c-2
// adds: viewer/static/run-settings.js's growth.mode toggle (select show/hide,
// forcing "世界の拡張" to expand+disabled) and viewer/static/epoch-chain.js's
// live strip update + stop/continue button activation. Hand-rolled DOM, like
// world_expansion_ui.cjs/raw_workspace_ui.cjs.
// Run: node tests/growth_epoch_ui.cjs [run-settings.js] [epoch-chain.js]
// (R6, Opus review: both args are optional, defaulting to the real files
// under viewer/static/ relative to the repo root, so this runs the same way
// as the other .cjs tests here -- `node tests/growth_epoch_ui.cjs`.)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const REPO_ROOT = path.resolve(__dirname, '..');

class El {
  constructor(opts = {}) {
    this.dataset = opts.dataset || {};
    this.value = opts.value ?? '';
    this.checked = opts.checked || false;
    this.hidden = opts.hidden || false;
    this.disabled = opts.disabled || false;
    this.textContent = opts.textContent || '';
    this._attrs = new Set(opts.attrs || []);
    this._closestMap = opts.closestMap || {};
    this.listeners = {};
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  hasAttribute(name) { return this._attrs.has(name); }
  closest(selectorList) {
    if (selectorList in this._closestMap) return this._closestMap[selectorList];
    const wanted = [...selectorList.matchAll(/\[([^\]]+)\]/g)].map(m => m[1]);
    return wanted.some(name => this._attrs.has(name)) ? this : null;
  }
}

const runSettingsPath = process.argv[2] || path.join(REPO_ROOT, 'viewer', 'static', 'run-settings.js');
const epochChainPath = process.argv[3] || path.join(REPO_ROOT, 'viewer', 'static', 'epoch-chain.js');
const runSettingsSource = fs.readFileSync(runSettingsPath, 'utf8');
const epochChainSource = fs.readFileSync(epochChainPath, 'utf8');

// ---------------------------------------------------------------- run-settings.js

function runSettingsPage() {
  const fields = {
    'project_id': new El(),
    'evolution.target_ending': new El({closestMap: {'.field': new El()}}),
    'evolution.generations': new El({value: '1'}),
    'evolution.population': new El({value: '1'}),
    'evolution.seeds': new El({value: '1'}),
    'evolution.coevolve': new El({checked: false}),
    'execution_limits.wall_seconds': new El({value: '60'}),
    'growth.mode': new El({value: 'off'}),
    'growth.epochs': new El({value: '3'}),
    'growth.auto_retire': new El({checked: false}),
    'evolution.world_expansion': new El({value: 'off'}),
  };
  const selectors = {
    '[data-ending-mode]': new El({value: 'default'}),
    '[data-growth-extra]': new El(),
    '[data-saved-config]': new El(),
    '[data-ending-description]': new El(),
    '[data-world-facts]': new El(),
    '[data-scale-equation]': new El(),
    '[data-individual-total]': new El(),
    '[data-total]': new El(),
    '[data-per-gen]': new El(),
    '[data-limit-summary]': new El(),
  };
  const selectorAlls = {
    '.rs-scale input[type=number]': [],
    '[data-world-link],[data-world-back]': [],
    '[data-error-for],[data-form-error]': [],
  };
  const formListeners = {};
  const form = {
    dataset: {worlds: '{}'},
    elements: {namedItem: name => fields[name]},
    querySelector: sel => selectors[sel] || null,
    querySelectorAll: sel => selectorAlls[sel] || [],
    addEventListener: (type, fn) => { (formListeners[type] ||= []).push(fn); },
  };
  const document = {querySelector: sel => sel === '[data-run-settings]' ? form : null};
  class MutationObserver { constructor(cb) { this.cb = cb; } observe() {} }
  vm.runInNewContext(runSettingsSource, {document, MutationObserver});
  const change = () => (formListeners.change || []).forEach(fn => fn());
  // fireOn: dispatches `type` on `el`'s OWN listeners first, then the
  // form's delegated listener -- the same order a real bubbling DOM event
  // reaches them in, and the order M1 (Opus review) depends on: a checkbox
  // click fires "input" before "change", so a listener that only marks
  // "touched" on "change" is too late to stop the form's "input" handler
  // (summary() -> growthSummary()) from re-applying the mode's default on
  // that same click.
  const fireOn = (el, type) => {
    (el.listeners[type] || []).forEach(fn => fn());
    (formListeners[type] || []).forEach(fn => fn());
  };
  return {fields, selectors, change, fireOn};
}

(() => {
  // 1. Default (off): the extra fields stay hidden, world_expansion stays enabled.
  const p = runSettingsPage();
  assert.equal(p.selectors['[data-growth-extra]'].hidden, true, 'off: extra hidden');
  assert.equal(p.fields['evolution.world_expansion'].disabled, false, 'off: expansion enabled');

  // 2. Switching to auto: reveals the extra fields, forces+disables
  //    world_expansion, and defaults auto_retire on (untouched).
  p.fields['evolution.world_expansion'].value = 'detect';
  p.fields['growth.mode'].value = 'auto';
  p.change();
  assert.equal(p.selectors['[data-growth-extra]'].hidden, false, 'auto: extra shown');
  assert.equal(p.fields['evolution.world_expansion'].value, 'expand', 'auto: forced to expand');
  assert.equal(p.fields['evolution.world_expansion'].disabled, true, 'auto: expansion disabled');
  assert.equal(p.fields['growth.auto_retire'].checked, true, 'auto: auto_retire defaults on');

  // 3. Switching to manual (still untouched): auto_retire follows to off.
  p.fields['growth.mode'].value = 'manual';
  p.change();
  assert.equal(p.fields['growth.auto_retire'].checked, false, 'manual: auto_retire defaults off');

  // 4. Once the user has touched auto_retire, mode switches stop overriding it.
  p.fields['growth.auto_retire'].checked = true;
  (p.fields['growth.auto_retire'].listeners.change || []).forEach(fn => fn());
  p.fields['growth.mode'].value = 'auto';
  p.change();
  assert.equal(p.fields['growth.auto_retire'].checked, true, 'touched: stays as the user left it');

  // 5. Switching back to off restores the world_expansion value it had
  //    before growth ever touched it, and re-enables the select.
  p.fields['growth.mode'].value = 'off';
  p.change();
  assert.equal(p.selectors['[data-growth-extra]'].hidden, true, 'off again: extra hidden');
  assert.equal(p.fields['evolution.world_expansion'].value, 'detect', 'off again: original value restored');
  assert.equal(p.fields['evolution.world_expansion'].disabled, false, 'off again: expansion re-enabled');

  // 6. M1 (Opus review) regression: a checkbox click fires "input" BEFORE
  //    "change" -- the very first click on 「自動で枯らす」 while mode is
  //    "auto" (checked defaults to true, untouched) must actually flip it
  //    to false, not silently bounce back because growthSummary() ran on
  //    the "input" event before the "touched" flag (previously set only on
  //    "change") was recorded.
  const q = runSettingsPage();
  q.fields['growth.mode'].value = 'auto';
  q.change();
  assert.equal(q.fields['growth.auto_retire'].checked, true, 'sanity: auto defaults checked');
  q.fields['growth.auto_retire'].checked = false;
  q.fireOn(q.fields['growth.auto_retire'], 'input');
  q.fireOn(q.fields['growth.auto_retire'], 'change');
  assert.equal(q.fields['growth.auto_retire'].checked, false, 'first click actually sticks');

  console.log('run-settings.js growth toggle: 6 scenarios ok');
})();

// ---------------------------------------------------------------- epoch-chain.js

function epochChainPage(initialChain) {
  const summary = new El();
  const stopBtn = new El({attrs: ['data-epoch-stop']});
  const continueBtn = new El({attrs: ['data-epoch-continue']});
  const stripQuery = {
    '[data-epoch-summary]': summary,
    '[data-epoch-stop]': stopBtn,
    '[data-epoch-continue]': continueBtn,
  };
  const strip = new El({dataset: {chainId: 'chain-x'}});
  strip.querySelector = sel => stripQuery[sel] || null;
  const document = {querySelectorAll: sel => sel === '[data-epoch-chain]' ? [strip] : []};

  let chain = initialChain;
  let broken = false;
  let failNextPostOnce = false;
  const calls = [];
  const fetchMock = async (url, opts = {}) => {
    const method = opts.method || 'GET';
    calls.push(`${method} ${url}`);
    if (broken) throw new Error('network down');
    if (url === '/api/epochs/current') return {ok: true, json: async () => ({chain})};
    if (failNextPostOnce && (url.endsWith('/stop') || url.endsWith('/continue'))) {
      failNextPostOnce = false;
      return {ok: false, json: async () => ({})};
    }
    if (url.endsWith('/stop')) { chain = {...chain, state: 'stopped'}; return {ok: true, json: async () => ({})}; }
    if (url.endsWith('/continue')) { chain = {...chain, state: 'running'}; return {ok: true, json: async () => ({})}; }
    return {ok: false, json: async () => ({})};
  };
  const timers = [];
  const setIntervalMock = fn => { timers.push(fn); return timers.length; };
  vm.runInNewContext(epochChainSource, {
    document, fetch: fetchMock, setInterval: setIntervalMock, encodeURIComponent,
  });
  const flush = async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); };
  const click = async button => { for (const fn of (strip.listeners.click || [])) await fn({target: button}); await flush(); };
  return {
    strip, summary, stopBtn, continueBtn, calls, timers, click, flush,
    setChain: c => { chain = c; },
    breakFetch: () => { broken = true; },
    failNextPost: () => { failNextPostOnce = true; },
  };
}

(async () => {
  // 1. Initial poll (fired synchronously by init()) renders the running
  //    state: stop shown, continue hidden.
  const p = epochChainPage({chain_id: 'chain-x', max_epochs: 3, state: 'running',
    epochs: [{step: 'propose', approved_rev: null, retired: []}]});
  await p.flush();
  assert.match(p.summary.textContent, /提案中/);
  assert.equal(p.stopBtn.hidden, false, 'running: stop shown');
  assert.equal(p.continueBtn.hidden, true, 'running: continue hidden');

  // 2. Clicking stop posts to .../stop, then re-polls and shows the new
  //    (stopped) state -- both buttons hidden.
  await p.click(p.stopBtn);
  assert.deepEqual(p.calls.filter(c => c.startsWith('POST')), ['POST /api/epochs/chain-x/stop']);
  assert.match(p.summary.textContent, /停止/);
  assert.equal(p.stopBtn.hidden, true, 'stopped: stop hidden');
  assert.equal(p.continueBtn.hidden, true, 'stopped: continue hidden');
  assert.equal(p.stopBtn.disabled, false, 'button re-enabled after the round trip');

  // 3. A waiting chain shows both buttons; clicking continue posts to
  //    .../continue and the strip flips to running (continue hides again).
  const q = epochChainPage({chain_id: 'chain-x', max_epochs: 2, state: 'waiting',
    epochs: [{step: 'approve', approved_rev: 'rev-1', retired: ['p-1']}]});
  await q.flush();
  assert.match(q.summary.textContent, /承認待ち/);
  assert.match(q.summary.textContent, /承認 1 件・淘汰 1 件/);
  assert.equal(q.stopBtn.hidden, false, 'waiting: stop shown');
  assert.equal(q.continueBtn.hidden, false, 'waiting: continue shown');
  await q.click(q.continueBtn);
  assert.deepEqual(q.calls.filter(c => c.startsWith('POST')), ['POST /api/epochs/chain-x/continue']);
  assert.equal(q.continueBtn.hidden, true, 'running again: continue re-hidden');
  assert.equal(q.stopBtn.hidden, false, 'running again: stop shown');

  // 4. A poll answer for a DIFFERENT chain_id (R3, Opus review: e.g. a
  //    stale response arriving after a new chain replaced this one) must
  //    not overwrite the summary text with foreign data, but DOES hide both
  //    buttons -- neither action can safely target data that isn't (or
  //    might not still be) this strip's own chain.
  const r = epochChainPage({chain_id: 'chain-x', max_epochs: 1, state: 'running',
    epochs: [{step: 'run', approved_rev: null, retired: []}]});
  await r.flush();
  r.setChain({chain_id: 'chain-other', max_epochs: 1, state: 'completed', epochs: [{step: 'done'}]});
  r.timers[0]();
  await r.flush();
  assert.match(r.summary.textContent, /実験中/, 'foreign chain_id: summary text untouched');
  assert.equal(r.stopBtn.hidden, true, 'foreign chain_id: stop hidden');
  assert.equal(r.continueBtn.hidden, true, 'foreign chain_id: continue hidden');

  // 5. A network failure during poll (fetch rejects) must not crash and
  //    must also hide both buttons rather than leave stale ones active.
  const s = epochChainPage({chain_id: 'chain-x', max_epochs: 1, state: 'waiting',
    epochs: [{step: 'approve', approved_rev: null, retired: []}]});
  await s.flush();
  assert.equal(s.continueBtn.hidden, false, 'sanity: waiting shows continue before the failure');
  s.breakFetch();
  s.timers[0]();
  await s.flush();
  assert.equal(s.stopBtn.hidden, true, 'network failure: stop hidden');
  assert.equal(s.continueBtn.hidden, true, 'network failure: continue hidden');

  // 6. A POST failure (stop/continue answers non-ok) shows a failure message
  //    instead of silently re-polling into a misleading state.
  const t = epochChainPage({chain_id: 'chain-x', max_epochs: 1, state: 'running',
    epochs: [{step: 'run', approved_rev: null, retired: []}]});
  await t.flush();
  t.failNextPost();
  await t.click(t.stopBtn);
  assert.match(t.summary.textContent, /操作に失敗しました/);

  console.log('epoch-chain.js strip updates: 6 scenarios ok');
})().catch(error => { console.error(error); process.exit(1); });
