// Focused DOM lifecycle checks; no browser, model calls, or saved-world writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const workspaceSource = fs.readFileSync(path.join(__dirname, '../viewer/static/draft-workspace.js'), 'utf8');
const editingSource = fs.readFileSync(path.join(__dirname, '../viewer/static/draft-editing.js'), 'utf8');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.attributes = {}; this.listeners = {}; this._text = ''; this.disabled = false;
  }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) { for (const node of this.children) node.parentNode = null; this.children = []; this._text = ''; this.append(...nodes); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    return descendants(this).filter(node => selector.split(',').some(part => {
      part = part.trim();
      if (part.startsWith('#')) return node.id === part.slice(1);
      if (part === '[data-field-pencil]') return node.dataset.fieldPencil !== undefined;
      return node.tagName === part.toUpperCase();
    }));
  }
  focus() { this.focused = true; }
  close() { this.open = false; }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); this.parentNode = null; }
  // Only the existing, fixed workspace skeleton may use HTML. Any data passed
  // to an HTML sink, including a future facts renderer regression, fails here.
  set innerHTML(value) {
    assert.ok(this.allowSkeleton && value.startsWith('<div class="ui-workspace '), 'Untrusted HTML sink');
    this.replaceChildren();
    for (const id of ['draft-nav', 'draft-content', 'section-position', 'previous-section', 'next-section']) {
      const node = new Element(id.includes('section') ? 'button' : 'div'); node.id = id; this.append(node);
    }
  }
}
const descendants = root => root.children.flatMap(node => [node, ...descendants(node)]);
const copy = value => JSON.parse(JSON.stringify(value));
const original = {
  name: 'Review', overview: 'Human overview', protagonist: 'A', antagonist: 'B', initial_story: 'Start',
  people: ['A', 'B'].map(name => ({name, description: name, personality: '', entry: 'Home', relations: [], goal: {description: 'Explore', conditions: []}})),
  places: [{name: 'Home', description: 'Home'}, {name: 'Archive', description: 'Archive'}], items: [], routes: [],
  facts: [{name: 'Clue', description: 'A clue', place: 'Archive'}]
};

function environment(draft = original) {
  const root = new Element('div'); root.allowSkeleton = true;
  const events = {}, entries = [{url: new URL('http://localhost/worlds/new?draft=abc#section=overview'), state: {initial: true}}];
  let position = 0, renders = 0, guardCalls = 0, confirmations = 0, discard = false;
  const location = {};
  function updateLocation(url) { for (const key of ['href', 'hash', 'search', 'pathname']) location[key] = url[key]; }
  updateLocation(entries[0].url);
  const history = {
    get state() { return entries[position].state; },
    pushState(state, _title, url) { entries.splice(position + 1); entries.push({url: new URL(url, location.href), state}); position++; updateLocation(entries[position].url); },
    replaceState(state, _title, url) { entries[position] = {url: new URL(url, location.href), state}; updateLocation(entries[position].url); },
    go() { throw Error('History reconciliation must not queue compensating traversals'); }
  };
  const context = {
    document: {createElement: tag => new Element(tag), createElementNS: (_ns, tag) => new Element(tag), createTextNode: value => { const node = new Element('#text'); node.textContent = value; return node; }},
    location, history, Node: Element, URLSearchParams, structuredClone,
    confirm() { confirmations++; return discard; },
    addEventListener(name, callback) { (events[name] ||= []).push(callback); },
    fetch() { throw Error('Review/navigation must not contact a backend'); }
  };
  context.window = context;
  vm.runInNewContext(editingSource, context);
  vm.runInNewContext(workspaceSource, context);
  const editing = new context.WorldBloomDraftEditing({});
  const workspace = new context.WorldBloomDraftWorkspace(root, {
    beforeNavigate() { guardCalls++; return editing.canLeave(); },
    afterRender() { renders++; if (editing.active) editing.dispose(editing.active); }
  });
  workspace.render(draft);
  function traverse(delta) {
    if (position + delta < 0 || position + delta >= entries.length) return;
    position += delta; updateLocation(entries[position].url);
    for (const callback of events.popstate || []) callback({state: history.state});
  }
  return {
    root, workspace, editing, location, history, back: () => traverse(-1), forward: () => traverse(1),
    get renders() { return renders; }, get guards() { return guardCalls; }, get confirmations() { return confirmations; },
    allowDiscard(value) { discard = value; },
    content: () => root.querySelector('#draft-content'),
    section: () => root.querySelector('#draft-nav').children.find(node => node.attributes['aria-current'] === 'page')?.dataset.section,
    edit({pending = false} = {}) {
      const dialog = new Element('dialog'), input = new Element('textarea'), pencil = new Element('button');
      input.value = 'Unconfirmed human input'; dialog.open = true; dialog.append(input); this.content().append(dialog);
      editing.active = {dialog, pencil, pending, changed: true, alive: true};
      return {dialog, input, pencil, active: editing.active};
    }
  };
}

// A denied Back leaves the exact dialog, text input, and rendered nodes intact.
const refused = environment();
refused.workspace.select('facts'); refused.workspace.select('story');
const unsaved = refused.edit(), nodes = refused.content().children.slice(), rendered = refused.renders, guards = refused.guards;
refused.back();
assert.equal(refused.guards, guards + 1); assert.equal(refused.confirmations, 1);
assert.equal(refused.renders, rendered); assert.equal(refused.section(), 'story');
assert.equal(refused.location.hash, '#section=story'); assert.equal(refused.location.search, '?draft=abc');
assert.equal(refused.editing.active, unsaved.active); assert.equal(unsaved.dialog.open, true);
assert.equal(unsaved.input.value, 'Unconfirmed human input'); assert.deepEqual(refused.content().children, nodes);
// Ordinary section buttons use the same leave guard.
refused.workspace.select('facts'); assert.equal(refused.section(), 'story'); assert.equal(refused.confirmations, 2);
refused.forward(); assert.equal(refused.section(), 'story'); assert.equal(refused.editing.active, unsaved.active);
assert.equal(refused.renders, rendered);

// Accepted Back and Forward close the discarded editor and render the target.
const accepted = environment();
accepted.workspace.select('facts'); accepted.workspace.select('story');
accepted.allowDiscard(true); const backEdit = accepted.edit(); accepted.back();
assert.equal(accepted.section(), 'facts'); assert.equal(accepted.location.hash, '#section=facts');
assert.equal(accepted.editing.active, null); assert.equal(backEdit.dialog.open, false); assert.equal(backEdit.active.alive, false);
const forwardEdit = accepted.edit(); accepted.forward();
assert.equal(accepted.section(), 'story'); assert.equal(accepted.location.hash, '#section=story');
assert.equal(accepted.editing.active, null); assert.equal(forwardEdit.dialog.open, false); assert.equal(accepted.confirmations, 2);

// Pending generation/application cannot be discarded even if confirmation would
// allow it. Repeated navigation reconciles URLs synchronously, without bounce.
const pending = environment(); pending.workspace.select('facts'); pending.workspace.select('story'); pending.workspace.select('facts');
pending.allowDiscard(true); const inFlight = pending.edit({pending: true}), pendingRenders = pending.renders;
for (const navigate of [pending.back, pending.back, pending.forward, pending.forward]) {
  navigate(); assert.equal(pending.section(), 'facts'); assert.equal(pending.location.hash, '#section=facts');
  assert.equal(pending.editing.active, inFlight.active); assert.equal(inFlight.input.value, 'Unconfirmed human input');
  assert.equal(inFlight.dialog.open, true); assert.equal(pending.renders, pendingRenders);
}
assert.equal(pending.confirmations, 0);

// Facts are visible, complete literal text with a usable place link. Rendering
// and navigation never alter the submitted draft or create editing controls.
const factDraft = copy(original), unsafe = '<img src=x onerror="alert(1)"><script>bad()</script>\n';
factDraft.facts = [{name: '<b>Secret</b>', description: unsafe + '長い情報\n'.repeat(300), place: 'Archive'}, {name: 'Second', description: 'Another clue', place: 'Home'}];
const before = JSON.stringify(factDraft), facts = environment(factDraft);
facts.workspace.select('facts');
assert.equal(facts.section(), 'facts');
const factNodes = descendants(facts.content());
assert.ok(factNodes.some(node => node.tagName === 'H3' && node.textContent === '<b>Secret</b>'));
assert.ok(factNodes.some(node => node.tagName === 'P' && node.textContent === factDraft.facts[0].description));
assert.ok(factNodes.some(node => node.textContent === 'Another clue'));
assert.ok(!factNodes.some(node => ['INPUT', 'TEXTAREA', 'SCRIPT', 'IMG'].includes(node.tagName) || node.dataset.editField));
const placeLink = factNodes.find(node => node.tagName === 'BUTTON' && node.textContent === 'Archive');
assert.ok(placeLink); placeLink.onclick(); assert.equal(facts.section(), 'places'); assert.equal(facts.location.hash, '#section=places&entity=Archive');
facts.back(); assert.equal(facts.section(), 'facts'); assert.equal(JSON.stringify(factDraft), before);
for (const draft of [{...original, facts: []}, Object.fromEntries(Object.entries(original).filter(([key]) => key !== 'facts'))]) {
  const empty = environment(draft); empty.workspace.select('facts'); assert.match(empty.content().textContent, /情報の指定はありません/);
}
// Prototype property names and unknown sections never dispatch object methods.
for (const section of ['constructor', '__proto__', 'toString', '<script>bad()</script>']) {
  const invalid = environment();
  invalid.workspace.select(section);
  assert.equal(invalid.section(), 'overview');
  assert.match(invalid.content().textContent, /下案の全体像/);
  invalid.workspace.select('story'); invalid.back();
  assert.equal(invalid.section(), 'overview');
}
console.log('Draft workspace review: guarded Back/Forward, pending input retention, literal facts/place review, invalid section fallback, and unchanged draft passed.');
