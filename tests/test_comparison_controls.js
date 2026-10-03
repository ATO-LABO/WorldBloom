/* Run with node --test tests/test_comparison_controls.js.
 * Exercise the shipped controller with a minimal DOM; this is not layout QA. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../viewer/static/comparison.js'), 'utf8');

function element(dataset = {}) {
  return {dataset, value: '', checked: false, disabled: false, hidden: false,
    listeners: {}, textContent: '', classList: {add() {}, remove() {}, toggle() {}},
    setAttribute(key, value) {this[key] = value;}, focus() {},
    addEventListener(type, fn) {this.listeners[type] = fn;},
    fire(type) {return this.listeners[type]?.({target: this});},
    showModal() {this.open = true;}, close() {this.open = false;},
    getAttribute() {return 'candidate';},
  };
}
function setup(bound, {storage = new Map(), conflict = false} = {}) {
  const ids = Array.from({length: 18}, (_, i) => `candidate-${i}`);
  const choices = ids.map((id, i) => Object.assign(element({cell: `cell-${i}`}), {value: id}));
  const cards = bound.map(id => {
    const card = element({cpCard: id}), note = element(), radio = element();
    radio.value = 'held';
    card.querySelector = () => note;
    card.querySelectorAll = () => [radio];
    card.note = note; card.radio = radio;
    return card;
  });
  const opens = bound.map(id => Object.assign(element({cpOpen: id}), {querySelector: () => null}));
  const removes = bound.map(id => element({cpRemove: id}));
  const singles = new Map();
  const get = selector => {
    if (!singles.has(selector)) singles.set(selector, element());
    return singles.get(selector);
  };
  const lists = {'[data-cp-open]': opens, '[data-cp-choice]': choices, '[data-cp-card]': cards, '[data-cp-remove]': removes};
  const root = element({initial: JSON.stringify({run_id: 'run', revision: 0, publication: 7,
    bound, base: '/exp/test/compare', items: ids.map(candidate_id => ({candidate_id, state: 'unclassified', note: ''}))})});
  root.querySelector = get;
  root.querySelectorAll = selector => lists[selector] || [];
  const location = {href: ''}, requests = [];
  vm.runInNewContext(source, {
    document: {querySelector: () => root}, window: {addEventListener() {}}, location,
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    URLSearchParams, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: !conflict, status: conflict ? 409 : 200,
        json: async () => url.endsWith('/candidates') ? {revision: 7} : {revision: 1, entries: []}};
    },
  });
  return {get, choices, cards, opens, removes, location, requests, storage};
}
const settle = async () => {await new Promise(resolve => setImmediate(resolve));};

for (const n of [0, 1, 2, 5, 10, 18]) {
  test(`picker accepts ${n} candidates and preserves publication binding`, async () => {
    const ui = setup([]);
    ui.get('[data-cp-picker]').fire('click');
    ui.choices.forEach((choice, i) => {choice.checked = i < n; choice.fire('change');});
    assert.equal(ui.get('[data-cp-apply]').disabled, false);
    assert.ok(ui.choices.every(choice => !choice.disabled));
    ui.get('[data-cp-apply]').fire('click');
    await settle();
    const url = new URL(ui.location.href, 'http://localhost');
    assert.equal(url.searchParams.getAll('candidate').length, n);
    assert.equal(url.searchParams.getAll('cell').length, n);
    assert.equal(url.searchParams.get('publication'), '7');
  });
}
test('remove works from two to one and one to zero', async () => {
  for (const bound of [['candidate-0', 'candidate-1'], ['candidate-0']]) {
    const ui = setup(bound);
    ui.removes[0].fire('click'); await settle();
    const url = new URL(ui.location.href, 'http://localhost');
    assert.deepEqual(url.searchParams.getAll('candidate'), bound.slice(1));
  }
});
test('changing comparison saves the current note before navigating', async () => {
  const ui = setup(['candidate-0']);
  ui.cards[0].note.value = 'keep my note'; ui.cards[0].note.fire('input');
  ui.removes[0].fire('click'); await settle();
  assert.ok(ui.requests[0].url.endsWith('/selection'));
  assert.equal(JSON.parse(ui.requests[0].options.body).changes[0].note, 'keep my note');
  assert.ok(ui.requests[1].url.endsWith('/candidates'));
  assert.ok(ui.location.href);
});
test('conflict blocks navigation and restores draft on a return visit', async () => {
  const ui = setup(['candidate-0'], {conflict: true});
  ui.cards[0].note.value = 'preserve this'; ui.cards[0].note.fire('input');
  ui.removes[0].fire('click'); await settle();
  assert.equal(ui.location.href, '');
  const returned = setup(['candidate-0'], {storage: ui.storage});
  assert.equal(returned.cards[0].note.value, 'preserve this');
  returned.removes[0].fire('click'); await settle();
  assert.equal(returned.location.href, '');
  assert.equal(returned.requests.length, 0);
  assert.equal(returned.get('[data-cp-check]').hidden, false);
});

test('reading selection switches cards without adopting and restores on return', () => {
  const ui = setup(['candidate-0', 'candidate-1']);
  assert.equal(ui.cards[0].hidden, false);
  assert.equal(ui.cards[1].hidden, true);
  ui.opens[1].fire('click');
  assert.equal(ui.cards[0].hidden, true);
  assert.equal(ui.cards[1].hidden, false);
  assert.equal(ui.opens[1]['aria-pressed'], 'true');
  ui.get('[data-cp-back]').fire('click');
  const returned = setup(['candidate-0', 'candidate-1'], {storage: ui.storage});
  assert.equal(returned.cards[1].hidden, false);
  assert.equal(ui.requests.length, 0);
});
