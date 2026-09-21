"""Creation workspace and small persistent text editors for world metadata."""
import json
import uuid
import yaml
from viewer import pages, data
from execution.provenance import ConfigError
E = pages._escape


def source_models(store):
    result = []
    for world in store.worlds():
        try:
            raw = yaml.safe_load(store.read("world", world["id"], "world.yaml")) or {}
        except (ConfigError, ValueError, OSError, yaml.YAMLError):
            raw = {}
        if not isinstance(raw, dict): raw = {}
        period = raw.get("time")
        if not isinstance(period, dict): period = {}
        zones = raw.get("zones")
        if not isinstance(zones, list): zones = []
        slots = period.get("slots")
        if not isinstance(slots, list): slots = []
        result.append({**world, "overview":str(raw.get("overview") or ""),
            "initial_story":str(raw.get("initial_story") or ""),
            "places":[str(z.get("name")) for z in zones if isinstance(z,dict) and z.get("name")],
            "days":period.get("days"), "slots":[str(slot) for slot in slots]})
    return result


def render(store, jobs, *, from_id=None, genre_id=None, copy_mode=False):
    worlds = source_models(store)
    genres = store.genres()
    copy_mode = copy_mode and bool(worlds)
    selected = next((w for w in worlds if w['id']==from_id), worlds[0] if worlds else None)
    initial = E(json.dumps({'worlds':worlds,'source':selected['id'] if selected else None,'genre':genre_id},ensure_ascii=False))
    source_rows = ''.join(f'<label class="wc-source"><input type="radio" name="source" value="{E(w["id"])}"'+(' checked' if selected and w['id']==selected['id'] else '')+f'><span><strong>{E(w["name"] or w["id"])}</strong><small>{E(w["subjects"])}人 · {E(w["genre"] or "ジャンル未設定")}</small></span></label>' for w in worlds)
    options = ''.join(f'<option value="{E(g["id"])}"'+(' selected' if g['id']==genre_id else '')+f'>{E("共通の基本ルール" if g["id"]=="basic" else g.get("name") or g["id"])}</option>' for g in genres)
    auto_id = "world-" + uuid.uuid4().hex[:12]
    mode = lambda name: ' checked' if (name=='copy') == copy_mode else ''
    body = f'''<div class="wc" data-world-create data-initial="{initial}">
      <header class="wc-heading"><div><h1>新しい世界を作る</h1><p>まずは名前と概要から。人物や場所は、あとから自由に設定できます。</p></div><a href="/worlds">世界一覧へ戻る</a></header>
      <form data-create-form novalidate>
        <div class="wc-layout"><div class="wc-editor">
          <fieldset class="wc-fields" data-create-fields><legend class="wc-sr">世界の作成</legend>
            <div class="wc-modes" role="group" aria-label="作成方法">
              <label><input type="radio" name="mode" value="new"{mode('new')}><span><strong>新しく作る</strong><small>オリジナルの世界を、一から</small></span></label>
              <label><input type="radio" name="mode" value="copy"{mode('copy')}{'' if worlds else ' disabled'}><span><strong>既存の世界から作る</strong><small>{'今ある設定をコピーして始める' if worlds else '作成元の世界がまだありません'}</small></span></label>
            </div>
            <p class="wc-error" data-create-error role="alert"></p>
            <section data-copy-panel{'' if copy_mode else ' hidden'}><h2>作成元の世界</h2><label class="wc-search">世界を探す<input type="search" data-source-search placeholder="名前で検索"></label><div class="wc-sources">{source_rows}</div><p data-no-sources hidden>一致する世界がありません。</p><p class="wc-hint">選んだ世界の設定をコピーします。元の世界は変わりません。</p></section>
            <h2>世界の基本情報</h2>
            <label class="wc-field" for="f-name">世界の名前 <small class="wc-required">必須</small><input id="f-name" name="name" data-field="name" maxlength="120" required placeholder="例：星を運ぶ街"></label><p class="wc-hint">世界設定から、あとで変更できます。</p>
            <div data-new-panel{ ' hidden' if copy_mode else ''}><label class="wc-field" for="f-overview">世界の概要 <small>任意</small><textarea id="f-overview" name="overview" data-field="overview" maxlength="8000" rows="3" placeholder="舞台や雰囲気など、思いついたことから書いてください。"></textarea></label>
            <div class="wc-note"><strong>物語の基本ルール <small>初期設定</small></strong><p>共通の基本ルールで始めます。必要に応じて、あとから変更できます。</p></div></div>
            <div data-copy-panel{'' if copy_mode else ' hidden'}><label class="wc-field" for="f-genre">ジャンル<select id="f-genre" data-field="template_id">{options}</select></label><p class="wc-hint">作成元のルールを初期選択します。</p></div>
            <details class="wc-advanced"><summary>詳細設定 <small>世界IDは自動入力</small></summary><label class="wc-field" for="f-world_id">世界ID<input id="f-world_id" data-field="world_id" value="{auto_id}" pattern="[A-Za-z0-9][A-Za-z0-9_-]{{0,95}}" maxlength="96" required></label><p class="wc-hint">半角英数字・ハイフン・アンダースコアが使えます。</p></details>
          </fieldset>
        </div><aside class="wc-preview" aria-label="作成する世界"><h2>作成する世界</h2><h3 data-preview-name>名前を入力してください</h3><span class="wc-chip" data-preview-mode>{'複製' if copy_mode else '新規作成'}</span><p data-preview-overview class="wc-copy">概要はあとから追加できます。</p><section><h2 data-preview-heading>作成後に設定すること</h2><dl data-preview-facts></dl><p class="wc-hint" data-preview-note>人物・場所・初期物語は、空の状態から始まります。</p></section><div class="wc-note"><strong>作成後は世界設定へ進みます。</strong><p>設定を整えてから、実行に進めます。</p></div></aside></div>
        <footer class="wc-footer"><p data-create-status role="status">名前を付けて、世界づくりを始めましょう。</p><a href="/worlds" class="wc-cancel">キャンセル</a><button class="wc-primary" type="submit">世界を作成して設定へ →</button></footer>
      </form><noscript>この作成画面を利用するにはJavaScriptを有効にしてください。</noscript></div>'''
    doc = pages.document('新しい世界を作る',body,phase='world',job_store=jobs,pin=data.pinned_target(jobs),page_class='run-observer')
    return doc.replace('</head>','<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/world-create.css"><script src="/static/world-create.js" defer></script></head>')


def basics_editor(world_id, world_yaml, *, story=False):
    fields = [('initial_story','導入・開始時点の状況')] if story else [('name','世界の名前'),('overview','世界の概要')]
    controls=[]
    for key,label in fields:
        value=E(world_yaml.get(key) or '')
        control=(f'<input name="{key}" value="{value}" maxlength="120" required>' if key=='name' else f'<textarea name="{key}" rows="4" maxlength="8000">{value}</textarea>')
        controls.append(f'<label class="wc-field">{label}{control}</label>')
    return f'<details class="world-basics-editor"><summary>{"導入文を編集" if story else "名前・概要を編集"}</summary><form data-world-basics="{E(world_id)}">'+''.join(controls)+'<button type="submit">保存</button><p role="status" data-basics-status></p></form></details>'
