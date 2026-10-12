/* Input schema and result labels also used by the Node regression tests. */
const RouteGoalInput = (() => {
  const labels = {
    found: '到達 · モデル内の最短経路',
    unreachable: '設定期間・対応モデル内では到達不能（世界全体の証明ではありません）',
    cutoff: '探索上限で打ち切り · 到達可能性は未判定',
    unsupported: '未対応のモデル・設定', invalid_config: '設定が不正です',
    legacy: '従来の配送目標', ok: '配送の概算手順', no_plan: '概算手順なし',
    no_goal: '目標なし', no_route_config: '経路設定なし'
  };
  function input(type, actor, rows, mode, slot) {
    if (type === 'legacy') return null;
    if (type !== 'generic') throw new Error('目標の種類が不正です');
    const all = rows.map(row => {
      const field = {at:'zone', holds:'item', knows:'fact'}[row.predicate];
      if (!field) throw new Error('未対応の条件です');
      const result = {predicate:row.predicate, subject:row.subject, [field]:row.value};
      if (field === 'item') result.quantity = row.quantity === '' ? null : Number(row.quantity);
      return result;
    });
    return {actor, all, time: mode === 'any' ? {mode} : {mode, day:1, slot}};
  }
  return {input, label:status => labels[status] || '判定結果を確認してください'};
})();
if (typeof module !== 'undefined') module.exports = RouteGoalInput;

if (typeof document !== 'undefined') (() => {
  const root = document.querySelector('[data-goal-editor]');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  let saved = JSON.parse($('#goal-data').textContent), dirty = false, busy = false;
  const form = $('[data-goal-form]'), error = $('[data-goal-error]'), status = $('[data-goal-status]');
  const type = $('[data-goal-type]'), actor = $('[data-goal-actor]'), mode = $('[data-goal-mode]'), slot = $('[data-goal-slot]');
  const conditions = $('[data-goal-conditions]');
  const base = '/api/worlds/' + encodeURIComponent(saved.world_id) + '/route-goal';
  function options(select, values, value) {
    select.replaceChildren();
    for (const key of values) {
      const option = document.createElement('option'); option.value = key; option.textContent = key; select.append(option);
    }
    if (value != null && !values.includes(value)) {
      const missing = document.createElement('option'); missing.value = value; missing.textContent = '未登録: ' + value; select.append(missing);
    }
    if (value != null) select.value = value;
  }
  function markDirty() { dirty = true; error.textContent = ''; status.textContent = '未保存の変更があります'; $('[data-goal-result]').replaceChildren(); }
  function visibility() {
    $('[data-goal-generic]').hidden = type.value !== 'generic';
    $('[data-goal-time]').hidden = mode.value === 'any';
  }
  function row(condition = {}) {
    const div = document.createElement('div'); div.className = 'goal-condition';
    const make = (label, element) => { const l = document.createElement('label'); l.append(document.createTextNode(label + ' '), element); div.append(l); return element; };
    const person = make('条件の人物', document.createElement('select'));
    options(person, saved.people, condition.subject || actor.value);
    const predicate = make('条件', document.createElement('select'));
    for (const [value,text] of [['at','場所にいる'],['holds','品物を持つ'],['knows','情報を知る']]) {
      const option = document.createElement('option'); option.value = value; option.textContent = text; predicate.append(option);
    }
    predicate.value = condition.predicate || 'at';
    const value = make('場所・品物・情報', document.createElement('select'));
    const quantity = make('必要な個数', document.createElement('input')); quantity.type = 'number'; quantity.min = '1'; quantity.step = '1'; quantity.value = condition.quantity ?? 1;
    const update = (initial) => {
      const field = {at:'zone',holds:'item',knows:'fact'}[predicate.value];
      options(value, saved[{at:'zones',holds:'items',knows:'facts'}[predicate.value]], initial ? condition[field] : null);
      quantity.parentElement.hidden = predicate.value !== 'holds';
    };
    update(true);
    predicate.addEventListener('change', () => { update(false); markDirty(); });
    for (const element of [person,value,quantity]) element.addEventListener('input', markDirty);
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '条件を削除';
    remove.addEventListener('click', () => { div.remove(); markDirty(); }); div.append(remove);
    div.read = () => ({predicate:predicate.value,subject:person.value,value:value.value,quantity:quantity.value});
    conditions.append(div);
  }
  function render() {
    options(actor, saved.people, saved.goal?.actor || saved.protagonist);
    options(slot, saved.slots, saved.goal?.time?.slot);
    type.value = saved.goal == null ? 'legacy' : 'generic'; mode.value = saved.goal?.time?.mode || 'any';
    conditions.replaceChildren(); if (!saved.error) for (const condition of saved.goal?.all || []) row(condition);
    visibility(); dirty = false;
    error.textContent = saved.error ? '保存済み目標は未対応または不正です。詳細設定のYAMLを確認してください: ' + saved.error : '';
    $('[data-goal-source]').textContent = '現在の経路設定: ' + (saved.source === 'world' ? '世界専用（ジャンルとの部分合成はしません）' : 'ジャンル設定');
    status.textContent = saved.editable ? '保存済み目標を表示しています' : '閲覧専用です';
    buttons();
  }
  function buttons() {
    $('[data-goal-controls]').disabled = busy || !saved.editable || !!saved.error;
    $('[data-goal-save]').hidden = !saved.editable;
    $('[data-goal-save]').disabled = busy || !!saved.error;
    $('[data-goal-validate]').disabled = busy || !!saved.error;
    $('[data-goal-cancel]').disabled = busy;
    $('[data-goal-reload]').disabled = busy;
  }
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(body)});
    const json = await response.json(); if (!response.ok) throw new Error(json.message || '要求を完了できません'); return json;
  }
  function body() { return {revision:saved.revision,goal:RouteGoalInput.input(type.value,actor.value,[...conditions.children].map(div => div.read()),mode.value,slot.value)}; }
  function result(data) {
    const out = $('[data-goal-result]'); out.replaceChildren();
    const title = document.createElement('h2'); title.textContent = RouteGoalInput.label(data.status); out.append(title);
    const p = document.createElement('p'); p.textContent = data.message || ''; out.append(p);
    for (const assumption of data.assumptions || []) { const text = document.createElement('p'); text.textContent = assumption; out.append(text); }
    const ol = document.createElement('ol');
    for (const step of data.steps || []) { const li = document.createElement('li'); li.textContent = (step.next ? step.next.day + '日目 ' + step.next.slot + ' · ' : '') + step.text; ol.append(li); }
    out.append(ol);
  }
  async function run(action) {
    if (busy) return;
    busy = true; buttons(); error.textContent = '';
    try {
      const data = await request(action === 'save' ? base : base + '/validate', body());
      if (action === 'save') { saved = {...data,editable:saved.editable}; render(); $('[data-goal-result]').replaceChildren(); status.textContent = '目標を保存しました。保存済みを再読込して確認できます。'; }
      else { result(data); status.textContent = '未保存の入力を検証しました。'; }
    } catch (failure) { error.textContent = failure.message; }
    finally { busy = false; buttons(); }
  }
  type.addEventListener('change', () => { if (type.value === 'generic' && !conditions.children.length) row(); visibility(); markDirty(); });
  mode.addEventListener('change', () => { visibility(); markDirty(); });
  actor.addEventListener('change', markDirty); slot.addEventListener('change', markDirty);
  $('[data-goal-add]').addEventListener('click', () => { if (conditions.children.length < 100) { row(); markDirty(); } });
  $('[data-goal-validate]').addEventListener('click', () => run('validate'));
  form.addEventListener('submit', e => { e.preventDefault(); run('save'); });
  const discard = () => !dirty || window.confirm('未保存の変更を破棄しますか？');
  $('[data-goal-cancel]').addEventListener('click', () => { if (discard()) { render(); $('[data-goal-result]').replaceChildren(); } });
  $('[data-goal-reload]').addEventListener('click', async () => {
    if (busy || !discard()) return; busy = true; buttons();
    try { saved = {...await request(base),editable:saved.editable}; render(); $('[data-goal-result]').replaceChildren(); }
    catch (failure) { error.textContent = failure.message; }
    finally { busy = false; buttons(); }
  });
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  render();
})();
