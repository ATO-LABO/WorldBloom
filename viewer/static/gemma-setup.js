/* Gemma setup is read-only until the existing explicit settings save. */
(function (factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else api.mount(document);
})(function () {
  'use strict';
  const MODEL = 'gemma4:e4b-it-qat';
  function catalogState(catalog) {
    if (!catalog || catalog.reason) return catalog && catalog.reason === 'server_unreachable' ? 'unreachable' : 'unknown';
    if (catalog.live !== true || !Array.isArray(catalog.models)
      || catalog.models.some(name => typeof name !== "string" || !name.trim())) return 'unknown';
    return catalog.models.includes(MODEL) ? 'available' : 'missing';
  }
  function linked(settings) {
    return !!settings && settings.backend === 'ollama' && settings.model === MODEL;
  }
  function mount(doc) {
    const root = doc.querySelector('[data-global-settings]');
    const section = root && root.querySelector('[data-gemma-setup]');
    if (!section) return;
    const guide = section.querySelector('[data-gemma-guide]');
    const unreachable = section.querySelector('[data-gemma-unreachable]');
    const state = section.querySelector('[data-gemma-state]');
    const binding = section.querySelector('[data-gemma-link]');
    const refresh = section.querySelector('[data-gemma-refresh]');
    let seq = 0;
    let previousStatus;
    async function read(path) {
      const response = await fetch(path, {cache:'no-store'});
      if (!response.ok) throw new Error('read failed');
      return response.json();
    }
    async function check() {
      const token = ++seq;
      refresh.disabled = true;
      state.textContent = '導入状態を確認中…';
      binding.textContent = '保存済みの紐付けを確認中…';
      const results = await Promise.allSettled([
        read('/api/settings/output/models?backend=ollama'), read('/api/settings/output')
      ]);
      if (token !== seq) return;
      const catalog = results[0].status === 'fulfilled' ? results[0].value : null;
      const status = catalogState(catalog);
      section.dataset.state = status;
      if (status !== previousStatus) guide.open = status === 'missing';
      previousStatus = status;
      state.textContent = {
        available:'導入済み。保存済みのOllama接続先でGemma 4を利用できます。',
        missing:'未導入。このOllama接続先にGemma 4がありません。',
        unreachable:'Ollamaに接続できません。起動状況と接続先を確認してください。',
        unknown:'導入状況は不明です。モデル一覧を取得できませんでした。'
      }[status];
      unreachable.hidden = status !== 'unreachable';
      const settings = results[1].status === 'fulfilled' ? results[1].value : null;
      if (!settings || typeof settings.backend !== 'string'
          || (settings.model !== null && typeof settings.model !== 'string')) {
        binding.textContent = '保存済みの紐付けを確認できません。再確認してください。';
      } else {
        binding.textContent = linked(settings)
          ? (status === 'available' ? '保存済みの生成先はGemma 4です。導入済みモデルとの紐付けを確認しました。' : '生成先はGemma 4として保存済みですが、利用可能な状態はまだ確認できません。')
          : 'Gemma 4は現在の保存済み生成先には選ばれていません。使う場合は「Gemma 4を選ぶ」を押し、設定を保存してください。';
      }
      refresh.disabled = false;
    }
    refresh.addEventListener('click', check);
    root.addEventListener('worldbloom:output-saved', check);
    check();
  }
  return {MODEL,catalogState,linked,mount};
});
