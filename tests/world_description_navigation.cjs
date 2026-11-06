// Exercise the production saved-world renderer through its shared offline DOM fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const fixture = fs.readFileSync(path.join(__dirname, 'fixtures/people_settings_dom.cjs'), 'utf8');
const {setup, Event} = vm.runInNewContext(fixture + '\n({setup, Event});', {
  require, process, console, __dirname, structuredClone, URL, URLSearchParams, setImmediate,
});
function description(t, screen) {
  assert.equal(t.content.dataset.screen, screen);
  assert.equal(t.content.querySelector('h1').textContent, '初期物語');
  assert(t.content.querySelector('[data-view="story:intro"]'));
  assert(t.content.querySelector('[data-view="story:state"]'));
  const selected = t.root.querySelectorAll('.wp-nav [aria-current="page"]');
  assert.equal(selected.length, 1);
  assert.equal(selected[0].dataset.screen, 'description');
}
for (const editable of [true, false]) {
  for (const screen of ['description', 'story']) {
    const t = setup('#screen=' + screen, {editable});
    description(t, screen);
    assert.equal(t.location.hash, '#screen=' + screen, 'Keep both deep links stable');
    assert.equal(!!t.content.querySelector('[data-edit="intro"]'), editable);
    assert.equal(t.requests.length, 0, 'Opening a screen must not write');
  }
}
{
  const t = setup('#screen=overview');
  const before = JSON.stringify(t.current);
  for (let i = 0; i < 2; i++) {
    t.clickScreen('description');
    description(t, 'description');
    assert.equal(t.location.hash, '#screen=description');
    assert.equal(t.focused, t.content.querySelector('h1'));
  }
  t.content.querySelector('[data-view="story:state"]').click();
  assert(t.content.textContent.includes('開始時点の状況'));
  t.clickScreen('overview');
  t.history.back();
  description(t, 'description');
  assert(t.content.textContent.includes('開始時点の状況'), 'Retain selected story subview');
  t.jump('#screen=story');
  description(t, 'story');
  t.jump('#screen=description');
  description(t, 'description');
  t.jump('#screen=unknown');
  assert.equal(t.content.dataset.screen, 'overview');
  t.content.querySelector('[data-screen="story"]').click();
  description(t, 'story');
  assert.equal(t.root.querySelector('.wp-nav [data-screen="overview"]').getAttribute('aria-current'), null);
  t.content.querySelector('[data-view="story:intro"]').click();
  const edit = t.content.querySelector('[data-edit="intro"]');
  edit.click();
  const input = t.dialog.querySelector('[name="text"]');
  const original = input.value;
  input.value = 'Unsaved opening text';
  input.dispatchEvent(new Event('input'));
  t.dialog.querySelector('[data-close]').click();
  assert(t.dialog.open, 'Declining discard keeps the edit dialog open');
  assert.equal(input.value, 'Unsaved opening text');
  t.confirm(true);
  t.dialog.querySelector('[data-close]').click();
  assert(!t.dialog.open);
  assert.equal(t.focused, edit);
  edit.click();
  assert.equal(t.dialog.querySelector('[name="text"]').value, original);
  t.dialog.querySelector('[data-close]').click();
  assert.equal(JSON.stringify(t.current), before);
  assert.equal(t.requests.length, 0, 'Navigation and discarded edits must not write');
}
console.log('World description navigation passed: menu, both deep links, history, selection, focus, read-only and discarded edits.');
