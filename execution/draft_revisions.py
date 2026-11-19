"""Explicit, idempotent AI revision previews; applying remains a separate action."""
from copy import deepcopy
import json

from execution.provenance import ConfigError, atomic_json, canonical, directory_lock, read_json, sha256


def fail(field, message, code="invalid_config"):
    raise ConfigError(field, message, code=code)


def target_for(draft, target):
    if not isinstance(target, dict) or not {"section", "entity"} <= set(target) or set(target)-{"section","entity","field","peer"}:
        fail("target", "修正する対象を選んでください")
    section, entity = target["section"], target["entity"]
    if not isinstance(section, str) or not isinstance(entity, str):
        fail("target", "修正する対象を選んでください")
    key = {"people": "people", "places": "places", "items": "items", "facts": "facts", "relations": "people"}.get(section)
    exists=(key and any(row["name"] == entity for row in draft.get(key, []))) or (section=='routes' and entity.isdecimal() and int(entity)<len(draft['routes'])) or (section in ('overview','story') and not entity)
    if not exists:fail("target", "修正する人物・場所・経路が見つかりません")
    if 'field' in target:
        allowed={'overview':{'roles'},'people':{'profile','goal'},'places':{'description'},'items':{'holder'},'facts':{'factDescription','factPlace'},'story':{'story'},'relations':{'relation'},'routes':{'route'}}
        if not isinstance(target['field'],str) or target['field'] not in allowed.get(section,set()):fail('target','編集する項目を確認してください')
        if section=='relations':
            person=next(p for p in draft['people'] if p['name']==entity)
            if not isinstance(target.get('peer'),str) or not any(r['person']==target['peer'] for r in person['relations']):fail('target','編集する関係を確認してください')
        elif 'peer' in target:fail('target','編集する項目を確認してください')
    elif 'peer' in target:fail('target','編集する項目を確認してください')
    return target


def scope_paths(draft,target):
    if 'field' not in target:return None
    section,field=target['section'],target['field']
    if section=='overview':return ['/protagonist','/antagonist']
    if section=='story':return ['/initial_story']
    if section=='routes':return ['/routes/'+target['entity']]
    key='people' if section=='relations' else section
    index=next(i for i,p in enumerate(draft[key]) if p['name']==target['entity'])
    prefix=f'/{key}/{index}/'
    if field=='profile':return [prefix+k for k in ('description','personality','entry')]
    if field=='relation':
        ri=next(i for i,r in enumerate(draft[key][index]['relations']) if r['person']==target['peer'])
        return [prefix+f'relations/{ri}']
    return [prefix+{'goal':'goal','description':'description','holder':'holders' if 'holders' in draft[key][index] else 'holder','factDescription':'description','factPlace':'place'}[field]]


def scoped_candidate(base,candidate,target):
    if base.get('settings') != candidate.get('settings'):
        fail('settings','この項目のAI修正では実行設定を変更できません')
    priorities=lambda d:{p['name']:p['commitments'] for p in d['people'] if 'commitments' in p}
    if priorities(base) != priorities(candidate):
        fail('commitments','この項目のAI修正では人物の優先度・必須指定を変更できません')
    paths=scope_paths(base,target)
    if paths is None:return
    permitted=deepcopy(base)
    try:
        for path in paths:
            parts=path[1:].split('/');owner=permitted;value=candidate
            for part in parts[:-1]:
                owner=owner[int(part)] if isinstance(owner,list) else owner[part]
                value=value[int(part)] if isinstance(value,list) else value[part]
            leaf=int(parts[-1]) if isinstance(owner,list) else parts[-1]
            owner[leaf]=deepcopy(value[leaf])
    except (KeyError,IndexError,TypeError,ValueError):fail('revision','編集する項目の形式を確認してください')
    if permitted!=candidate:fail('revision','選択した編集項目の外に変更が含まれています。この候補は反映できません')


def patch_draft(draft, response):
    from execution.world_drafts import parse_response
    patch = parse_response(response)
    if not isinstance(patch, dict) or set(patch) != {"changes"} or not isinstance(patch["changes"], list) or not 1 <= len(patch["changes"]) <= 32:
        fail("revision", "AIの修正案には1〜32件の変更を指定してください")
    candidate = deepcopy(draft)
    seen, changes = [], []
    for change in patch["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "value"}:
            fail("revision", "AIの修正案の形式を確認してください")
        path = change["path"]
        if not isinstance(path, str) or not path.startswith("/"):
            fail("revision", "変更する項目の指定を確認してください")
        parts = path[1:].split("/")
        if parts[0] not in ("people", "places", "items", "routes", "facts", "initial_story", "protagonist", "antagonist"):
            fail("revision", "世界名と元の概要はAIでは変更できません")
        if any(path == old or path.startswith(old + "/") or old.startswith(path + "/") for old in seen):
            fail("revision", "同じ項目を重複して変更できません")
        seen.append(path)
        owner = candidate
        try:
            for part in parts[:-1]:
                owner = owner[int(part)] if isinstance(owner, list) and part.isdecimal() else owner[part]
            leaf = int(parts[-1]) if isinstance(owner, list) and parts[-1].isdecimal() else parts[-1]
            optional = (parts == ['facts'] or (len(parts) == 3 and parts[0] == 'routes' and parts[1].isdecimal() and parts[2] in ('cost', 'requires_item')))
            before = deepcopy(owner.get(leaf)) if optional and isinstance(owner, dict) else deepcopy(owner[leaf])
        except (IndexError, KeyError, TypeError, ValueError):
            fail("revision", "下案に存在しない項目は変更できません")
        owner[leaf] = deepcopy(change["value"])
        if before != change["value"]:
            changes.append({"path": path, "before": before, "after": deepcopy(change["value"])})
    if not changes:
        fail("revision", "AIの応答に実際の変更がありません。要望を具体的にして再度依頼してください")
    return candidate, changes


def revision_prompt(name, overview, draft, target, instruction):
    path='/initial_story'
    guide='目標はdescriptionとconditionsを揃えます。conditionsは全条件の同時成立です。条件の型はreach(place), acquire(item), deliver(item,place), meet(person), befriend(person,threshold), defeat(person), survive(day), discover(fact)です。自動判定できない行為や内面の変化はdescriptionを残しconditionsを空にします。'
    if target['section']=='routes':
        path='/routes/'+target['entity']+'/requires_item'
        guide=('今回は選択した経路を修正します。人物の紹介・目標は修正対象ではありません。'
            '経路routesの各項目はfrom,toと任意のcost,requires_itemだけです。必要な持ち物はrequires_itemに既存の品物名を1つ指定します。'
            '未設定のcostとrequires_itemも追加できます。conditionsやrequired_items等の別のキーを作ってはいけません。')
    elif target['section'] in ('people','relations'):
        index=next(i for i,p in enumerate(draft['people']) if p['name']==target['entity'])
        path=f'/people/{index}/personality'
    elif target['section'] in ('places','items','facts'):
        key=target['section'];index=next(i for i,p in enumerate(draft[key]) if p['name']==target['entity'])
        path=f'/{key}/{index}/'+('description' if key=='places' else 'holder')
    value_example='修正後の値'
    allowed_paths=scope_paths(draft,target)
    if allowed_paths:
        path=allowed_paths[0]
        if target['field']=='goal':
            current=next(p['goal'] for p in draft['people'] if p['name']==target['entity'])
            value_example=deepcopy(current) if 'description' in current else {'description':current['target']+'を'+current['deliver_to']+'へ届ける','conditions':[{'type':'deliver','item':current['target'],'place':current['deliver_to']}] if current['target'] else []}
            guide+='今回は目標と達成条件だけを編集します。goal全体をdescriptionとconditionsの2項目で置き換えます。旧形式のtargetやdeliver_toは残しません。目標が品物を届ける状態なら必ずdeliverを使い、acquireとreachだけで代用してはいけません。到着だけの目標ならreachを使います。人物の紹介や開始位置は変更禁止です。'
        if target['field']=='holder':
            item=next(p for p in draft['items'] if p['name']==target['entity'])
            value_example=deepcopy(item.get('holders',item.get('holder')))
            guide+=('初期所持だけを変更します。holdersはpersonとcountの一覧です。personは既存の人物名、countは1〜10000の整数、同じ人物は重複不可です。所持者なしは空の一覧にします。'
                    if 'holders' in item else '初期所持だけを変更します。holderは既存の人物名を文字列で指定します。')
        guide+='変更できる範囲は'+json.dumps(allowed_paths,ensure_ascii=False)+'だけです。その外の変更は返してはいけません。'
    return (
        "物語世界の下案を、利用者の修正要望に従って修正してください。JSONだけを返します。"
        + '形式は '+json.dumps({'changes':[{'path':path,'value':value_example}]},ensure_ascii=False)+'。'
        "pathは下案JSONの既存項目を示し、配列は0始まりです。変更しない項目は返さず、その値を保持します。"
        "対象を中心に必要な変更だけを出します。対象以外も変更するなら、その項目もchangesに明示します。"
        "nameとoverviewは元の入力なので変更禁止です。既存のキー・型・人物や場所の参照名を守ります。"
        +guide+"関係はperson,affinity(-1〜1),awareness(0〜1),labelです。逆方向の関係は別です。"
        "実行コード・命令・Markdownは出しません。以下は編集対象のデータと利用者の要望です。\n"
        + json.dumps({"name": name, "overview": overview, "draft": draft, "target": target, "instruction": instruction}, ensure_ascii=False)
    )


def preview(store, token, revision, draft, target, instruction, request_id):
    from execution.world_drafts import _text, validate
    store._path(token)
    store._path(request_id)  # same canonical identifier boundary as drafts
    path = store.root / "revisions" / token / (request_id + ".json")
    instruction = _text(instruction, "instruction", 4000)
    # Reject malformed references and invalid direct edits before contacting a model.
    proposal, _, _, _ = validate(store.repo, draft)
    target = target_for(proposal, target)
    digest = sha256(canonical({"revision": revision, "draft": proposal, "target": target, "instruction": instruction}))
    revise = getattr(store.provider, "revise", None)
    if store.mode != "live" or revise is None:
        fail("generation", "AI修正は実モデル接続時に利用できます。表示用サンプルではAIを呼びません", "unavailable")
    with directory_lock(store.root):
        record = store.get(token)
        store._editable(record, revision)
        if proposal["name"] != record["name"] or proposal["overview"] != record["overview"]:
            fail("draft", "世界名と元の概要は変更できません")
        if path.exists():
            old = read_json(path)
            if old["input_hash"] != digest:
                fail("request_id", "同じIDで修正内容を変更できません", "conflict")
            return old
        request = {"request_id": request_id, "input_hash": digest, "base_revision": revision,
                   "base_draft_hash": sha256(canonical(proposal)), "target": target, "instruction": instruction,
                   "state": "generating", "changes": [], "candidate": None, "errors": {}}
        atomic_json(path, request)
    try:
        context_revise=getattr(store.provider,'revise_with_context',None)
        if record.get('authoring') is not None and context_revise is None:fail('authoring','制作メモを含むAI修正が接続されていません')
        response = context_revise(record['name'],record['overview'],deepcopy(proposal),target,instruction,deepcopy(record['authoring'])) if record.get('authoring') is not None else revise(record["name"], record["overview"], deepcopy(proposal), target, instruction)
        candidate, changes = patch_draft(proposal, response)
        candidate, _, _, _ = validate(store.repo, candidate)
        scoped_candidate(proposal,candidate,target)
        result = {"state": "ready", "candidate": candidate, "changes": changes}
    except ConfigError as error:
        result = {"state": "invalid", "errors": error.field_errors}
    except Exception:
        result = {"state": "unknown", "errors": {"generation": "AI修正の結果を確定できませんでした。自動再送はしません"}}
    with directory_lock(store.root):
        # A cancellation or simultaneous save must never turn a stale response into an applicable proposal.
        current = store.get(token)
        try:
            store._editable(current, revision)
        except ConfigError:
            result = {"state": "stale", "errors": {"revision": "下案が変更・取消済みです。この修正案は反映できません"}}
        request.update(result)
        atomic_json(path, request)
    return request


def apply(store, token, revision, request_id, draft, candidate=None):
    from execution.world_drafts import validate
    store._path(token)
    store._path(request_id)
    base, _, _, _ = validate(store.repo, draft)
    path = store.root / "revisions" / token / (request_id + ".json")
    with directory_lock(store.root):
        try:
            request = read_json(path)
        except FileNotFoundError:
            fail("request_id", "修正案が見つかりません", "not_found")
        current = store.get(token)
        if request["state"] == "applied" and current.get("revision") == request.get("applied_revision"):
            return current
        store._editable(current, revision)
        if request["state"] != "ready" or request["base_revision"] != revision or request["base_draft_hash"] != sha256(canonical(base)):
            fail("revision", "修正案の作成後に下案が変わりました。最新の内容から依頼し直してください", "conflict")
        accepted=request['candidate'] if candidate is None else candidate
        if candidate is not None and 'field' not in request['target']:fail('target','項目を指定した編集から候補を反映してください')
        scoped_candidate(base,accepted,request['target'])
        request["state"] = "applying"
        atomic_json(path, request)
    try:
        result = store.update(token, revision, accepted)
    except ConfigError as error:
        with directory_lock(store.root):
            request.update(state="invalid", errors=error.field_errors)
            atomic_json(path, request)
        raise
    except Exception:
        with directory_lock(store.root):
            request.update(state="unknown", errors={"revision": "反映結果を確認してください。自動再送はしません"})
            atomic_json(path, request)
        raise
    with directory_lock(store.root):
        request.update(state="applied", applied_revision=result["revision"])
        atomic_json(path, request)
    return store.get(token)


def get(store,token,request_id):
    """Read-only recovery of an explicit revision request; no provider call."""
    store._path(token);store._path(request_id)
    try:return read_json(store.root/'revisions'/token/(request_id+'.json'))
    except FileNotFoundError:fail('request_id','修正案が見つかりません','not_found')
