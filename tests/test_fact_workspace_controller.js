/* Execute the shipped controller; HTTP worker coverage lives in test_fact_workspace.py. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../viewer/static/fact-workspace.js'), 'utf8');
let uuidSerial = 0;

function element(dataset = {}) {
  return {dataset, value: '', disabled: false, hidden: false, textContent: '',
    matches() {return true;}, closest() {return this;}};
}
function setup({storage = new Map(), jobs = new Map(), losses = 0, revision = 1, readonly = false} = {}) {
  const cfg = {run_id: 'run', candidate_id: 'candidate', config_id: 'config', revision,
    package_sha256: 'sealed-package-' + revision, readonly,
    defaults: {synopsize: 'あらすじの既定値', narrate: '本文の既定値'}};
  const singles = new Map();
  const get = s => {if (!singles.has(s)) singles.set(s, element()); return singles.get(s);};
  const styles = ['synopsize', 'narrate'].map(k => Object.assign(element({factStyle: k}), {value: cfg.defaults[k]}));
  const buttons = ['synopsize', 'narrate'].map(k => Object.assign(element({factAction: 'generate', kind: k}), {textContent: k}));
  const listeners = {}, requests = [];
  const host = {dataset: {facts: JSON.stringify(cfg)}, querySelector: get,
    querySelectorAll(s) {return s.includes('fact-style') ? styles : s.includes('fact-action') ? buttons : [];},
    addEventListener(type, fn) {listeners[type] = fn;}};
  let remaining = losses;
  const location = {href: '', reload() {this.reloaded = true;}};
  const sessionStorage = {getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)};
  vm.runInNewContext(source, {document: {querySelector: () => host}, location, sessionStorage,
    crypto: {randomUUID: () => 'uuid-' + (++uuidSerial)},
    fetch: async (url, options) => {
      const body = JSON.parse(options.body); requests.push({url, body});
      if (url.endsWith('/generate')) {
        let job = jobs.get(body.request_id);
        if (!job) {job = {job_id: 'job-' + jobs.size, state: 'succeeded'}; jobs.set(body.request_id, job);}
        if (remaining-- > 0) throw new TypeError('response lost after acceptance');
        return {ok: true, json: async () => ({job, url: '/jobs/' + job.job_id})};
      }
      return {ok: true, json: async () => ({inputs: {synopsize: {}, narrate: {}}, prompts: {synopsize: 'summary', narrate: 'story'}, conflicts: {synopsize: [], narrate: []}})};
    }});
  return {storage, jobs, requests, location, get, styles, buttons,
    click: (action, kind = 'narrate') => listeners.click({target: action === 'generate' ? buttons.find(b => b.dataset.kind === kind) : element({factAction: action, kind})}),
    changeStyle: () => listeners.input({target: styles[1]})};
}

test('lost completed-job response retries the same id after reload', async () => {
  const storage = new Map(), jobs = new Map();
  const ui = setup({storage, jobs, losses: 1});
  await ui.click('generate');
  const first = ui.requests[0].body.request_id;
  assert.equal(jobs.size, 1);
  const reloaded = setup({storage, jobs});
  await reloaded.click('generate');
  assert.equal(reloaded.requests[0].body.request_id, first);
  assert.equal(jobs.size, 1, 'uncertain retry must not generate twice');
  assert.equal(reloaded.location.href, '/jobs/job-0');
});

test('same-page uncertain retry reuses id; acknowledged intentional generation uses a new id', async () => {
  const ui = setup({losses: 1});
  await ui.click('generate'); await ui.click('generate');
  assert.equal(ui.requests[0].body.request_id, ui.requests[1].body.request_id);
  assert.equal(ui.jobs.size, 1);
  await ui.click('generate');
  assert.notEqual(ui.requests[1].body.request_id, ui.requests[2].body.request_id);
  assert.equal(ui.jobs.size, 2);
});

test('retry identities are separate for generation kind and saved revision', async () => {
  const storage = new Map();
  const ui = setup({storage, losses: 2});
  await ui.click('generate', 'narrate'); await ui.click('generate', 'synopsize');
  assert.notEqual(ui.requests[0].body.request_id, ui.requests[1].body.request_id);
  const edited = setup({storage, revision: 2});
  await edited.click('generate');
  assert.notEqual(edited.requests[0].body.request_id, ui.requests[0].body.request_id);
});

test('unsaved edits and readonly history cannot generate; reset keeps history as a draft', async () => {
  const ui = setup();
  ui.styles[1].value = '新しい本文の書き方'; ui.changeStyle();
  await ui.click('generate');
  assert.equal(ui.requests.length, 0);
  assert.ok(ui.buttons.every(b => b.disabled));
  await ui.click('reset');
  assert.equal(ui.styles[1].value, '本文の既定値');
  assert.ok(ui.buttons.every(b => b.disabled));
  const history = setup({readonly: true});
  await history.click('generate');
  assert.equal(history.requests.length, 0);
});
