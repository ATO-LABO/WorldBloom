"""World action usage editor with read-only links to authored action details."""
from __future__ import annotations

import html
import json
from urllib.parse import quote

from engine import action_types
from viewer import action_catalog, action_settings_copy, pages, ui
from viewer.settings_navigation import navigation


def _e(value):
    return html.escape(str(value), quote=True)


def _names(values):
    if isinstance(values, str):
        return values
    if isinstance(values, dict):
        return '、'.join(f'{ {"knowledge":"知識", "item":"品物"}.get(key,key)}：{value}' for key, value in values.items())
    return '、'.join(str(v) for v in (values or []))


def _quantities(values):
    return '、'.join(f'{key} × {value}' for key, value in values.items()) if isinstance(values, dict) else _names(values)


def _effect_text(effect):
    kind, value = effect.get('kind'), effect.get('value', '未設定')
    actor = effect.get('actor') or '人物未設定'
    if kind == 'knowledge':
        return f'{actor}が「{effect.get("fact", "情報未設定")}」を' + ('知る' if value is True else '知らない状態にする' if value is False else 'どう知るかは未設定')
    if kind == 'inventory':
        return f'{actor}の「{effect.get("item", "品物未設定")}」の所持数を{value}にする'
    if kind == 'zone':
        return f'{actor}を「{value}」へ移す'
    if kind == 'relation':
        return f'{effect.get("observer", "人物未設定")}から{effect.get("target", "相手未設定")}への親密度の指定値：{value}'
    if kind == 'vitality':
        value = {'alive':'生存', 'downed':'倒れている状態', 'revived':'救助後の生存', 'dead':'死亡'}.get(value,value)
        return f'{actor}の生存状態の指定：{value}（世界の生死ルールに従う）'
    if kind == 'state':
        return f'状態「{effect.get("key", "状態未設定")}」を「{value}」にする'
    return 'この変化の説明は未設定。対処の設定で確認してください。'


def action_rows(world, repo, current):
    """Project saved definitions without creating records or executable candidates."""
    base = '/worlds/' + quote(world['id'], safe='')
    w = current['world']
    rows = []
    response = w.get('responses') or {}
    for a in response.get('actions', []) if isinstance(response, dict) else []:
        if not isinstance(a, dict):
            continue
        parts = [f'「{a["zone"]}」で作業する' if a.get('zone') else '作業場所は未設定',
                 f'「{_names(a["requires"])}」を知っている' if a.get('requires') else '',
                 _quantities(a['items'])+'を持っている' if a.get('items') else '',
                 f'完了まで作業：{a["steps"]}回' if a.get('steps') is not None else '作業回数は未設定',
                 f'きっかけ：{a["signal"]}' if a.get('signal') else '本人の目的から行動' if response.get('version')==2 else 'きっかけ未指定',
                 f'状態：{_names(a["preconditions"])}' if a.get('preconditions') else '',
                 f'本人が重視する条件：{a["condition"]}' if a.get('condition') else '',
                 f'承認：{a["authorization"].get("proposal", "")}' if isinstance(a.get('authorization'), dict) else '']
        effects = []
        for effect in a.get('effects') or []:
            if not isinstance(effect, dict):
                continue
            effects.append(_effect_text(effect))
        rows.append({'name':a.get('label') or '名前未設定の対処', 'kind':'個別・対処', 'verb':'respond', 'usage_ref':'response:'+str(a.get('id','')),
                     'source':'世界に登録した対処', 'relation':'「現場で対処する」の個別設定', 'actors':list(a.get('actors') or []), 'actor_note':'対処に登録した人物',
                     'conditions':' ／ '.join(p for p in parts if p), 'condition_lines':[p for p in parts if p],
                     'result':' ／ '.join(effects) or ('完了時の状態変化なし（空の設定）' if a.get('effects')==[] else '完了時の変化は未設定'),
                     'status':'登録済み', 'href':base+'?view=advanced#responses', 'operation':'対処の設定',
                     'timing':f'完了まで作業 {a.get("steps", "未設定")}回。開始できる時点は既存の条件に従います。'})
    definitions = w.get('items') or []
    if isinstance(definitions, dict):
        definitions = [dict(value, name=key) for key, value in definitions.items() if isinstance(value, dict)]
    for item in definitions:
        if not isinstance(item, dict) or not item.get('made_from'):
            continue
        reference = item.get('name') or item.get('id') or '名前未設定の品物'
        name = item.get('display_name') or reference
        parts = [_quantities(item['made_from'])+'を持っている']
        if item.get('requires'):
            requires = item['requires']
            if isinstance(requires,dict) and requires.get('knowledge'):
                parts.append(f'「{requires["knowledge"]}」を知っている')
            for key,value in requires.items() if isinstance(requires,dict) else []:
                if key!='knowledge':parts.append(f'その他の前提：{key}：{value}（設定元で確認）')
        if item.get('craft_zone'):
            parts.append(f'「{item["craft_zone"]}」にいる')
        else:parts.append('製作場所の制限なし')
        rows.append({'name':str(name)+'を作る', 'kind':'製作', 'verb':'craft', 'usage_ref':'recipe:'+str(reference), 'source':'品物の製作設定', 'relation':'「材料から品物を作る」の個別レシピ',
                     'conditions':' ／ '.join(parts), 'condition_lines':parts,
                     'result':_quantities(item['made_from'])+f'を使い、{name} × 1を得る',
                     'status':'登録済み', 'href':base+'#screen=items&entity='+quote(str(reference), safe=''),
                     'operation':'品物の設定', 'timing':'所要と実行条件は品物の製作設定で確認します。時間への自動換算はしません。'})
    engine_world, error = action_catalog._build_world(repo, world)
    if engine_world is None:
        return rows, [], error
    people = current['people']
    granted = engine_world.global_action_grants()
    union = {str(v) for p in people for v in p.get('verbs') or []} | set(granted)
    from engine.stamina_catalog import labels as action_labels
    names = action_labels()
    labels = {}
    status_labels = {'active':'利用人物あり', 'unused':'利用人物なし', 'pruned':'使用設定で無効' if engine_world.action_usage is not None else 'ジャンルで無効', 'unimplemented':'未対応'}
    catalog = action_catalog._load_catalog()
    for verb, entry in catalog.get('verbs', {}).items():
        kinds = entry.get('kinds') or []
        labels[verb] = names.get(verb, verb)
        status = action_catalog._verb_status(verb, engine_world, union)
        name,conditions,result=action_settings_copy.describe(verb,engine_world)
        branches=[]
        if len(kinds)>1:
            for kind in kinds:
                label,condition,effect=action_settings_copy.VARIANTS.get((verb,kind.get('id')),(kind.get('label') or '名前未設定の分岐','この分岐の説明は未設定。図鑑で確認してください。','この分岐の変化の説明は未設定。'))
                branches.append(dict(name=label,conditions=condition,result=effect))
        children=[r['name'] for r in rows if r['verb']==verb]
        rows.append({'name':name,'kind':'共通','verb':verb,'source':'基本の行動' if status!='unimplemented' else '図鑑の構想',
                     'relation':'個別設定：'+'、'.join(children) if children else '', 'branches':branches,
                     'conditions':conditions,'condition_lines':[conditions],'result':result,
                     'status':status_labels[status], 'href':base+'?view=advanced#actions',
                     'operation':'行動図鑑', 'timing':'行動ごとの既存ルールに従います。共通の所要時間編集は未対応です。'})
    for row in rows:
        verb=row['verb'];owner=action_types.owner(verb)
        row.update(genre_key=owner['id'] if owner else 'common',genre=owner['name'] if owner else '共通')
        row['genre_state']=('有効' if engine_world.action_enabled(verb) else '無効') if owner else ('適用あり' if engine_world.genre_allows(verb) else '基本ルールで対象外')
        if verb not in action_catalog._engine_verb_methods():row['genre_state']='実行処理は未対応'
        row['disabled']=row['genre_state'] in ('無効','基本ルールで対象外')
        row.setdefault('actors',[str(p['id']) for p in people if p.get('id') and (verb in (p.get('verbs') or []) or verb in granted)])
        row.setdefault('actor_note','利用設定がある人物')
        row.setdefault('branches',[])
    person_rows = []
    for p in people:
        registered = [str(v) for v in p.get('verbs') or []]
        effective = list(dict.fromkeys(registered + sorted(granted)))
        person_rows.append({'name':p.get('id') or '名前未設定',
                            'registered':[(names.get(v, v), status_labels[action_catalog._verb_status(v, engine_world, union)] if v in catalog.get('verbs', {}) else '図鑑の対象外・専用設定を確認') for v in registered],
                            'granted':[labels.get(v, v) for v in sorted(granted) if v not in registered],
                            'knowledge':_names(p.get('knowledge')) or 'なし', 'effective':effective})
    order = {'登録済み':0,'利用人物あり':1,'利用人物なし':2,'ジャンルで無効':3,'未対応':4}
    rows.sort(key=lambda row:order.get(row['status'],9))
    # Stable partition: retain the existing order within both groups.
    rows.sort(key=lambda row:row.get('disabled',False))
    return rows, person_rows, None


def _description(text):
    text = str(text)
    if len(text)<=100:
        return _e(text)
    return '<details><summary>'+_e(text[:85])+'…</summary><p>'+_e(text)+'</p></details>'


def _lines(values):
    return '<ul class="as-lines">'+''.join('<li>'+_e(value)+'</li>' for value in values)+'</ul>'


def _actor_details(row):
    people=row.get('actors') or []
    label=row.get('actor_note','利用設定がある人物')
    if not people:
        return '<p class="ui-hint">'+_e(label)+'：未設定（登録人物なし）</p>'
    if len(people)<=2:
        return '<p class="ui-hint">'+_e(label)+'：'+_e('、'.join(people))+'</p>'
    return '<details class="as-actors"><summary>'+_e(label)+'：'+str(len(people))+'人</summary><p>'+_e('、'.join(people))+'</p></details>'


def _branches(row):
    values=row.get('branches') or []
    if not values:return ''
    return '<details class="as-branches"><summary>同じ行動の条件・結果の違い（'+str(len(values))+'件）</summary>'+''.join('<section><h3>'+_e(value['name'])+'</h3><p>主な条件：'+_e(value['conditions'])+'</p><p>起きる変化：'+_e(value['result'])+'</p></section>' for value in values)+'</details>'


def _table(headings, rows, *, class_name=''):
    return '<div class="as-table-scroll" tabindex="0" aria-label="'+_e('・'.join(headings))+'"><table class="ui-settings-table '+class_name+'"><thead><tr>'+''.join('<th scope="col">'+_e(h)+'</th>' for h in headings)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'


CATEGORIES = [
    ('rest','生理・休息', {'rest','withdraw'}),
    ('basic','基本行動', {'move','share_knowledge','investigate','observe','rethink'}),
    ('agreement','交渉・合意形成', {'negotiate','concede','persuade','pledge','submit_proposal','review_proposal','amend_proposal','present_proposal','request_decision','decide_proposal'}),
    ('items','品物・製作・労働', {'craft','give_item','buy','labor'}),
    ('response','依頼・対処', {'respond','request_response','answer_response','await_response','inspect_response','rescue'}),
    ('conflict','対立・防衛', {'fight','guard','confront','neutralize','sabotage','disguise','mislead'}),
    ('development','訓練・試練', {'train','trial'}),
    ('relationship','関係・還元', {'start_relationship','grand_gesture','donate','sacrifice'}),
    ('effects','伏線・効果', {'plant','payoff'}),
    ('unsupported','未対応', {'steal_credit','pursue_target'}),
    ('other','その他', set()),
]


def category(verb):
    return next(((key,label) for key,label,verbs in CATEGORIES if verb in verbs),('other','その他'))


def render(world, store, jobs):
    from execution.world_editor import snapshot
    from execution.action_usage_settings import view
    current = snapshot(store, world['id'])
    rows, people, error = action_rows(world, store.repo, current)
    usage = view(store,world['id'])
    editable = jobs is not None and usage.get('supported',False)
    enabled = set(usage.get('enabled',[])); required=set(usage.get('required',[]))
    for row in rows:
        row['ref']=row.get('usage_ref') or ('verb:'+row['verb'] if row['verb'] not in {'craft','respond'} and row['status']!='未対応' else '')
        row['category_key'],row['category']=category(row['verb'])
        if row['ref']:
            row['disabled']=row['ref'] not in enabled
            row['actors']=usage.get('effective_actors',{}).get(row['ref'],row.get('actors',[]))
            if row['status'] in {'利用人物あり','利用人物なし'}:row['status']='利用人物あり' if row['actors'] else '利用人物なし'
        elif row['verb'] in {'craft','respond'}:
            prefix='recipe:' if row['verb']=='craft' else 'response:'
            row['disabled']=not any(ref.startswith(prefix) for ref in enabled)
    rows.sort(key=lambda r:(r.get('disabled',False),[c[0] for c in CATEGORIES].index(r['category_key'])))
    base = '/worlds/' + quote(world['id'], safe='')
    body = '<div class="ui-workspace ui-workspace--nav as-workspace" data-action-settings>'
    body += ui.navigation(navigation(world['id'], active='actions'), label='世界設定')
    body += '<main class="as-main ui-settings-theme"><div class="as-frame-wrap"><section class="ui-settings-frame ui-settings-theme as-settings-frame">'
    help_text='この世界で使う行動を選びます。使用ONでも人物の許可・知識・材料・場所などの条件が必要です。分類は表示の整理で、実行条件を変えません。行動の種類と行動一覧は上部の確認からまとめて保存し、人物と時間帯は行末の設定から変更します。知識・材料・場所・役割の条件は引き続き必要です。休息のグレーのチェックは待機に必要なため変更できません。製作と対処は個別設定単位で選びます。食事・水分補給などの独立した生理行動は未対応です。保存した変更は次の実行に使われ、過去の実行結果は変わりません。'
    body += '<header class="ui-settings-header"><div class="ui-settings-title"><div class="ui-settings-name"><h1>行動設定</h1><span data-as-help data-as-help-text="'+_e(help_text)+'"><button class="ui-help-button" type="button" aria-label="行動設定の説明" title="'+_e(help_text)+'">?</button></span></div></div><div class="ui-settings-actions">'
    body += '<p class="ui-settings-status" data-as-save-status data-settings-state="saved" role="status">'+('変更なし' if editable else '閲覧専用')+'</p>'+ui.button('変更を戻す',attrs={'data-as-reset':True,'disabled':True})+ui.button('変更を確認',kind='confirm',attrs={'data-as-review':True,'disabled':not editable})+ui.button('保存状態を確認',attrs={'data-as-status':True,'hidden':True})+'</div><p class="ui-settings-error ui-error" data-as-detail-status role="alert" hidden></p></header>'
    body += ui.tabs(''.join(ui.button(label, attrs={'id':'as-tab-'+key, 'role':'tab', 'aria-selected':'true' if key=='types' else 'false', 'aria-controls':'as-panel-'+key, 'data-as-tab':key, 'tabindex':'0' if key=='types' else '-1'}) for key,label in [('types','行動の種類'),('actions','行動一覧')]), label='行動設定の切り替え', attrs={'role':'tablist'}).replace('class="ui-tabs"','class="ui-tabs ui-settings-tabs"',1)
    body += '<div class="ui-settings-body as-body"><section id="as-panel-actions" data-as-panel="actions" role="tabpanel" aria-labelledby="as-tab-actions" hidden>'
    if not usage.get('supported'):
        body += '<p class="ui-notice">この実行方式の行動切替は未対応です。現在の設定を保ち、参照用に表示しています。'+_e(usage.get('error',''))+'</p>'
    genre_options='<option value="all">すべて</option>'+''.join('<option value="'+key+'">'+label+'</option>' for key,label,_ in CATEGORIES)
    body += '<div class="as-toolbar">'+ui.field('行動を検索','<input type="search" data-as-search placeholder="行動名・分類・条件・変化で検索">')+ui.field('分類で絞り込む','<select data-as-genre-filter>'+genre_options+'</select>',class_name='as-genre-filter')
    if jobs is not None:
        body += '<details class="as-add"><summary class="ui-button">＋ 行動の設定先を選ぶ</summary><div class="as-add-links">'+ui.button('対処を追加・編集', href=base+'?view=advanced#responses')+ui.button('品物の製作を設定', href=base+'#screen=items')+'</div></details>'
    body += '</div><p class="ui-hint" data-as-count role="status">'+str(len(rows))+'件</p>'
    table_rows = []
    for r in rows:
        search=' '.join(str(r.get(k,'')) for k in ('name','relation','category','genre','genre_state','conditions','result','status'))+' '+' '.join(r.get('actors') or [])+' '+' '.join(str(v) for b in r.get('branches',[]) for v in b.values())
        name='<strong>'+_e(r['name'])+'</strong>'
        if r.get('relation'):name+='<small class="as-relation">'+_e(r['relation'])+'</small>'
        name+='<small class="as-status">'+_e(r['status'])+'</small>'
        if r['verb'] in {'craft','respond'} and not r['ref']:
            name+='<small class="as-relation">概要：個別レシピで選択</small>' if r['verb']=='craft' else '<small class="as-relation">概要：個別の対処設定で選択</small>'
        ref=r['ref']
        if ref:
            locked=ref in required
            usage_cell='<label class="as-use"><input type="checkbox" data-as-use="'+_e(ref)+'" aria-label="'+_e(r['name']+'をこの世界で使う')+'"'+(' aria-describedby="as-required-reason" title="待機に必要なため変更できません"' if locked else '')+(' checked' if ref in enabled else '')+(' disabled' if locked or not editable else '')+'></label>'
        else:
            usage_cell=''
        row_attrs=' class="as-row-disabled" data-as-disabled="true"' if r.get('disabled') else ''
        name+='<small class="as-status" data-as-row-state'+('' if r.get('disabled') else ' hidden')+'>無効</small>'
        group=(r.get('disabled',False),r['category_key'])
        if not table_rows or group!=previous_group:
            table_rows.append('<tr class="as-category-heading" data-as-group="'+_e(r['category_key'])+'" data-as-group-disabled="'+str(group[0]).lower()+'"><th colspan="5"><h2>'+_e(r['category'])+('（無効）' if group[0] else '')+'</h2></th></tr>')
        previous_group=group
        table_rows.append('<tr data-as-row'+row_attrs+' data-as-ref="'+_e(ref)+'" data-as-parent="'+(r['verb'] if r['verb'] in {'craft','respond'} and not ref else '')+'" data-as-genre="'+_e(r['category_key'])+'" data-as-category-label="'+_e(r['category'])+'" data-as-search-text="'+_e(search.casefold())+'"><td>'+usage_cell+'</td><th scope="row">'+name+'</th><td>'+_lines(r.get('condition_lines') or [r['conditions']])+_actor_details(r)+'</td><td>'+_lines([r['result']])+_branches(r)+'</td><td>'+(ui.button('⚙',attrs={'data-as-config':ref,'aria-label':r['name']+'の設定','title':r['name']+'の設定'}) if ref else ui.button(r['operation'] if jobs else r['operation']+'を確認',href=r['href']))+'</td></tr>')
    body += '<p id="as-required-reason" hidden>実行できる行動がないときの待機に必要です。体力管理OFFでも使うため、この使用設定は変更できません。</p>'
    body += _table(['使用','行動名','主な条件','起きる変化','設定'],table_rows,class_name='as-actions-table')
    body += '<p class="ui-notice" data-as-empty hidden>一致する行動はありません。検索や分類の絞り込みを変えてください。</p></section>'
    body += '<section id="as-panel-types" data-as-panel="types" role="tabpanel" aria-labelledby="as-tab-types"><p class="ui-hint">種類のチェックは、関連する行動と機能をまとめて切り替えます。行動一覧で個別に変えた場合は「一部有効」と表示します。</p><div class="as-types-grid">'
    for kind in usage.get('type_bundles',[]):
        ident=kind['id']
        controls={'data-as-type':ident,'aria-label':kind['name']+'を使う','disabled':not editable}
        if ident=='stamina':controls['data-as-mechanic']='stamina_management'
        initial=usage.get('mechanics',{}).get('stamina_management',False) if ident=='stamina' else all('verb:'+v in enabled for v in kind['actions']) and all(usage.get('mechanics',{}).get(f,False) for f in kind.get('features',[]))
        attributes=''.join(' '+k+('' if v is True else '="'+_e(v)+'"') for k,v in controls.items() if v is not False)
        body += '<section class="ui-settings-card as-type-card"><header class="ui-section-head"><h2><label><input type="checkbox"'+attributes+(' checked' if initial else '')+'> '+_e(kind['name'])+'</label></h2><span class="ui-hint" data-as-type-state="'+ident+'" role="status"></span><span data-as-type-help data-as-help-name="'+_e(kind['name'])+'の説明" data-as-help-text="'+_e(kind['description'])+'"></span></header>'
        if ident=='combat':
            body += '<label><input type="checkbox" data-as-mechanic="allow_death"'+(' checked' if usage['world_rules']['vitality']['allow_death'] else '')+(' disabled' if not editable else '')+'> 生死あり（戦闘による死亡を許可）</label>'
        if ident=='economy':
            body += '<label><input type="checkbox" data-as-mechanic="budgeted_purchases"'+(' checked' if usage['mechanics']['budgeted_purchases'] else '')+(' disabled' if not editable else '')+'> 購入時に資産を支払う</label>'+ui.button('金銭・資産の詳細',href=base+'?view=advanced#economy')
        if ident=='stamina':
            body += ui.button('体力の詳細',href=base+'#screen=stamina')
        if ident=='romance':
            body += '<p class="ui-hint">人物の許可は行動一覧で設定します。ONだけでは能力を追加しません。</p>'
        body += '</section>'
    body += '</div><p class="ui-hint">必須の休息は行動一覧で常にONです。種類をOFFにしても体力・価格・人物などの保存値は保持します。</p></section>'
    if error:
        body += '<p class="ui-error" role="alert">行動図鑑を読み込めませんでした。設定ファイルを確認してください。詳細：'+_e(error)+'</p>'
    body += '</div></section></div>'+ui.footer('<span>過去の実行結果は変わりません。</span>'+ui.button('概要へ',href=base),class_name='as-footer')+'</main></div>'
    body += '<dialog class="as-confirm ui-settings-theme" data-as-dialog aria-labelledby="as-confirm-title"><h2 id="as-confirm-title">行動設定の変更を確認</h2><div data-as-preview></div><div class="as-confirm-actions">'+ui.button('編集に戻る',attrs={'data-as-back':True})+ui.button('確認して保存',kind='confirm',attrs={'data-as-save':True})+'</div></dialog>'
    body += '<dialog class="ui-edit-dialog ui-settings-theme as-config-dialog" data-as-config-dialog aria-labelledby="as-config-title"><header class="as-config-header"><h2 id="as-config-title">行動の設定：<span data-as-config-name></span></h2>'+ui.button('閉じる',attrs={'data-as-config-close':True})+'</header><div class="as-config-body"><section class="as-config-people"><div class="as-config-section-title"><h3>対象の人物</h3><span role="status" data-as-person-count></span><span data-as-person-help></span></div><div class="as-person-toolbar"><label class="ui-field"><input type="search" aria-label="人物を検索" placeholder="人物を検索" data-as-person-search></label><label><input type="checkbox" data-as-person-selected> 選択済みのみ</label></div><div class="as-person-list" data-as-person-list role="group" aria-label="使用を許可する人物"></div><p data-as-person-empty hidden>一致する人物はいません。</p></section><section class="as-config-time"><div class="as-config-section-title"><h3>実行時間</h3><span data-as-time-help></span></div><label class="as-time-mode"><input type="radio" name="as-time-mode" value="any" data-as-time-mode> 制限なし</label><label class="as-time-mode"><input type="radio" name="as-time-mode" value="limited" data-as-time-mode> 制限あり</label><p class="ui-hint" id="as-time-selection-hint" data-as-time-selection-hint hidden>チェックした時間帯のみ実行できます。</p><div data-as-time-slots class="as-slot-list" role="group" aria-label="実行できる時間帯" aria-describedby="as-time-selection-hint"></div><div class="as-config-section-title"><h3>その他の条件</h3><span data-as-conditions-help></span></div><nav class="as-config-links" aria-label="詳細設定"><a class="ui-button" data-as-config-detail>その他の条件を設定</a><a class="ui-button" href="'+base+'?view=advanced#actions">行動図鑑を見る</a></nav></section><p class="ui-notice" data-as-config-required hidden>必須休息は人物・時間帯で制限できません。</p><p class="ui-error" role="alert" data-as-config-error hidden></p></div><footer class="ui-edit-dialog-actions"><span class="ui-hint as-config-save-hint">適用後、上部の「変更を確認」から保存</span>'+ui.button('取消',attrs={'data-as-config-cancel':True})+ui.button('適用',kind='confirm',attrs={'data-as-config-apply':True})+'</footer></dialog>'
    model={'id':world['id'],'editable':editable,**usage,'actions':{r['ref']:{'name':r['name'],'conditions':r['conditions'],'result':r['result'],'verb':r['verb']} for r in rows if r['ref']}}
    body += '<script type="application/json" id="as-data">'+json.dumps(model,ensure_ascii=False).replace('<','\\u003c')+'</script>'
    return pages.document('行動設定', body, world={'id':world['id'],'name':world.get('name') or world['id']}, phase='world', page_class='settings-workspace', job_store=jobs, stylesheets=('action-settings.css',), scripts=('ui-settings-frame.js','ui-help.js','action-settings.js','action-usage.js'))
