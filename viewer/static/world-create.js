(() => {
  "use strict";
  document.querySelectorAll('[data-world-basics]').forEach(form => {
    let dirty = false, busy = false;
    form.addEventListener('input', () => { dirty = true; });
    addEventListener('beforeunload', e => { if (dirty || busy) { e.preventDefault(); e.returnValue = ''; } });
    form.addEventListener('submit', async e => {
      e.preventDefault(); if (busy || !form.reportValidity()) return;
      const status = form.querySelector('[data-basics-status]');
      const payload = Object.fromEntries(new FormData(form));
      busy = true; form.querySelectorAll('input,textarea,button').forEach(el => { el.disabled = true; });
      status.textContent = '保存中…';
      try {
        const response = await fetch(`/api/worlds/${encodeURIComponent(form.dataset.worldBasics)}/basics`, {method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(payload)});
        const json = await response.json();
        if (!response.ok) throw new Error(json.message || '保存できませんでした');
        dirty = false; busy = false; location.reload();
      } catch (error) { status.textContent = error.message || '保存できませんでした。入力は残っています。'; }
      finally { busy = false; form.querySelectorAll('input,textarea,button').forEach(el => { el.disabled = false; }); }
    });
  });
  const root = document.querySelector('[data-world-create]'); if (!root) return;
  const $ = s => root.querySelector(s), $$ = s => [...root.querySelectorAll(s)];
  const initial = JSON.parse(root.dataset.initial), form = $('[data-create-form]');
  const fields = $('[data-create-fields]'), submit = $('button[type=submit]');
  const name = $('[data-field=name]'), overview = $('[data-field=overview]'), id = $('[data-field=world_id]'), genre = $('[data-field=template_id]');
  const mode = () => $('input[name=mode]:checked').value;
  const source = () => initial.worlds.find(w => w.id === $('input[name=source]:checked')?.value);
  let busy = false, done = false, unknown = false;
  let originalGenre = genre.value;
  let editedGenre = false;
  const baseline = JSON.stringify([mode(), name.value, overview.value, id.value, source()?.id, genre.value]);
  const dirty = () => baseline !== JSON.stringify([mode(), name.value, overview.value, id.value, source()?.id, genre.value]);
  function text(parent, tag, value) { const el = document.createElement(tag); el.textContent = value; parent.append(el); return el; }
  function preview() {
    const copying = mode() === 'copy', chosen = source();
    $$('[data-copy-panel]').forEach(el => { el.hidden = !copying; });
    $$('[data-new-panel]').forEach(el => { el.hidden = copying; });
    $('[data-preview-name]').textContent = name.value.trim() || '名前を入力してください';
    $('[data-preview-mode]').textContent = copying ? '複製' : '新規作成';
    $('[data-preview-overview]').textContent = (copying ? chosen?.overview : overview.value.trim()) || '概要はあとから追加できます。';
    $('[data-preview-heading]').textContent = copying ? '引き継ぐ内容' : '作成後に設定すること';
    const dl = $('[data-preview-facts]'); dl.replaceChildren();
    const pairs = copying && chosen ? [
      ['作成元', chosen.name || chosen.id], ['登場人物', `${chosen.subjects}人`], ['場所',chosen.places.join('・') || '未設定'],
      ['初期物語',chosen.initial_story ? '設定あり' : '未設定'], ['時間',chosen.days ? `${chosen.days}日間 ／ ${(chosen.slots || []).join('・')}` : '未設定'], ['ジャンル',genre.value === 'basic' ? '共通の基本ルール' : genre.value || '未設定']
    ] : [['登場人物','未設定'],['場所','未設定'],['初期物語','未設定'],['時間','7日間 ／ 朝・昼・夕方・夜']];
    pairs.forEach(([label, value]) => { text(dl,'dt',label); const dd = text(dl,'dd',value); if (value === '未設定') dd.className = 'wc-unset'; });
    $('[data-preview-note]').textContent = copying ? '元の世界の設定は変更されません。' : '人物・場所・初期物語は、空の状態から始まります。時間はあとで変更できます。';
    submit.disabled = busy || unknown || (copying && (!chosen || !genre.value));
  }
  form.addEventListener('input', preview);
  form.addEventListener('change', e => {
    if (e.target.name === 'source') {
      if (!editedGenre || genre.value === originalGenre) genre.value = source()?.genre || '';
      originalGenre = source()?.genre || ''; editedGenre = genre.value !== originalGenre;
    }
    if (e.target === genre) editedGenre = genre.value !== originalGenre;
    preview();
  });
  $('[data-source-search]').addEventListener('input', e => {
    const query = e.target.value.trim().toLocaleLowerCase();
    $$('.wc-source').forEach(el => { el.hidden = !el.textContent.toLocaleLowerCase().includes(query); });
    $('[data-no-sources]').hidden = $$('.wc-source').some(el => !el.hidden);
  });
  form.addEventListener('submit', async e => {
    e.preventDefault(); if (busy || unknown) return;
    if (!id.validity.valid) $('.wc-advanced').open = true;
    if (!form.reportValidity()) return;
    const payload = mode() === 'new' ? {mode:'new', world_id:id.value, name:name.value, overview:overview.value} : {mode:'copy',world_id:id.value,name:name.value,from_world_id:source()?.id,template_id:genre.value};
    const error = $('[data-create-error]'), status = $('[data-create-status]'); error.replaceChildren();
    busy = true; fields.disabled = true; preview(); status.textContent = '世界を作成中…';
    try {
      const response = await fetch('/api/worlds', {method:'POST',headers:{'Content-Type':'application/json','X-WorldBloom-Client':'1'},body:JSON.stringify(payload)});
      const json = await response.json();
      if (response.status !== 201 || !json.world_id) {
        error.textContent = json.message || '作成できませんでした。入力内容を確認してください。';
        if (json.field_errors?.world_id) $('.wc-advanced').open = true;
        status.textContent = '作成できませんでした。入力内容は残っています。';
        $('.wc-editor').scrollTop = 0;
      } else {
        done = true; location.href = `/worlds/${encodeURIComponent(json.world_id)}`;
      }
    } catch (_) {
      unknown = true;
      error.textContent = '作成結果を確認できませんでした。再作成の前に、世界一覧で確認してください。';
      const link = text(error,'a','世界一覧を確認 →'); link.href='/worlds';
      status.textContent = '通信結果が不明です。入力内容は残っています。';
      $('.wc-editor').scrollTop = 0;
    } finally { busy = false; fields.disabled = false; preview(); }
  });
  root.addEventListener('click', e => { if (busy && e.target.closest('a')) e.preventDefault(); });
  addEventListener('beforeunload', e => { if (!done && (busy || dirty())) { e.preventDefault(); e.returnValue=''; } });
  preview();
})();
