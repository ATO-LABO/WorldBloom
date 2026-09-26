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
  const zipInput = $('[data-zip-input]');
  const mode = () => $('input[name=mode]:checked').value;
  const source = () => initial.worlds.find(w => w.id === $('input[name=source]:checked')?.value);
  const zipFile = () => zipInput.files[0] || null;
  let busy = false, done = false, unknown = false;
  let originalGenre = genre.value;
  let editedGenre = false;
  const baseline = JSON.stringify([mode(), name.value, overview.value, id.value, source()?.id, genre.value, null]);
  const dirty = () => baseline !== JSON.stringify([mode(), name.value, overview.value, id.value, source()?.id, genre.value, zipFile()?.name ?? null]);
  function text(parent, tag, value) { const el = document.createElement(tag); el.textContent = value; parent.append(el); return el; }
  function preview() {
    const current = mode(), copying = current === 'copy', importing = current === 'import', chosen = source(), file = zipFile();
    $$('[data-copy-panel]').forEach(el => { el.hidden = !copying; });
    $$('[data-new-panel]').forEach(el => { el.hidden = copying || importing; });
    $$('[data-import-panel]').forEach(el => { el.hidden = !importing; });
    name.required = !importing;
    $('[data-name-required]').hidden = importing;
    $('[data-name-import-hint]').hidden = !importing;
    $('[data-preview-name]').textContent = name.value.trim() || (importing ? 'ZIPのworld.yamlのnameを使います' : '名前を入力してください');
    $('[data-preview-mode]').textContent = importing ? '取り込み' : copying ? '複製' : '新規作成';
    $('[data-preview-overview]').textContent = (copying ? chosen?.overview : importing ? '' : overview.value.trim()) || '概要はあとから追加できます。';
    $('[data-preview-heading]').textContent = copying ? '引き継ぐ内容' : importing ? '作成後に確認すること' : '作成後に設定すること';
    const dl = $('[data-preview-facts]'); dl.replaceChildren();
    const zipOversized = file && file.size > initial.zipMax;
    const pairs = copying && chosen ? [
      ['作成元', chosen.name || chosen.id], ['登場人物', `${chosen.subjects}人`], ['場所',chosen.places.join('・') || '未設定'],
      ['初期物語',chosen.initial_story ? '設定あり' : '未設定'], ['時間',chosen.days ? `${chosen.days}日間 ／ ${(chosen.slots || []).join('・')}` : '未設定'], ['ジャンル',genre.value === 'basic' ? '共通の基本ルール' : genre.value || '未設定']
    ] : importing ? [
      ['ファイル', file ? file.name : '未選択'],
      ['サイズ', file ? `${(file.size / 1024).toFixed(1)}KB${zipOversized ? `（上限${(initial.zipMax / 1024).toFixed(0)}KBを超えています）` : ''}` : '—'],
      ['ジャンル', 'ZIPの指定に従う'],
    ] : [['登場人物','未設定'],['場所','未設定'],['初期物語','未設定'],['時間','7日間 ／ 朝・昼・夕方・夜']];
    pairs.forEach(([label, value]) => { text(dl,'dt',label); const dd = text(dl,'dd',value); if (value === '未設定' || value === '未選択') dd.className = 'wc-unset'; });
    $('[data-preview-note]').textContent = copying ? '元の世界の設定は変更されません。'
      : importing ? '取り込み後、世界設定画面で内容と（未設定なら）ジャンルを確認してください。'
      : '人物・場所・初期物語は、空の状態から始まります。時間はあとで変更できます。';
    $('[data-zip-error]').textContent = zipOversized ? `ZIPは${(initial.zipMax / 1024).toFixed(0)}KB以内にしてください。` : '';
    submit.disabled = busy || unknown || (copying && (!chosen || !genre.value)) || (importing && (!file || zipOversized));
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
  function readAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
      reader.onerror = () => reject(reader.error || new Error('read failed'));
      reader.readAsDataURL(file);
    });
  }
  form.addEventListener('submit', async e => {
    e.preventDefault(); if (busy || unknown) return;
    if (!id.validity.valid) $('.wc-advanced').open = true;
    if (!form.reportValidity()) return;
    const error = $('[data-create-error]'), status = $('[data-create-status]'); error.replaceChildren();
    busy = true; fields.disabled = true; preview(); status.textContent = mode() === 'import' ? '世界を取り込み中…' : '世界を作成中…';
    try {
      let payload;
      if (mode() === 'new') payload = {mode:'new', world_id:id.value, name:name.value, overview:overview.value};
      else if (mode() === 'import') payload = {mode:'import', world_id:id.value, name:name.value, zip_base64: await readAsBase64(zipFile())};
      else payload = {mode:'copy', world_id:id.value, name:name.value, from_world_id:source()?.id, template_id:genre.value};
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
