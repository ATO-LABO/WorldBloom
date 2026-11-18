"""Typed editing adapters for the existing ending namespace; no new engine predicates."""
import ast
import math

NUMERIC = {'strength', 'believed_strength', 'stance', 'awareness', 'bonds', 'turn', 'day'}
BOOL_ARGS = {'holds': ['actor', 'item'], 'present': ['actor'], 'known': ['actor', 'fact'],
             'knows_modifier': ['actor', 'other', 'source'], 'hostile_present': ['actor'],
             'confront_success': ['actor', 'fact'], 'proposal_decided': ['proposal', 'status'],
             'proposal_state': ['key', 'value'], 'objective_achieved': ['condition']}
ARGS = {**BOOL_ARGS, 'zone': ['actor'], 'holder': ['item'], 'vitality': ['actor'],
        'strength': ['actor'], 'believed_strength': ['actor', 'other'],
        'stance': ['actor', 'other'], 'awareness': ['actor', 'other'], 'bonds': ['actor']}
COMPARISONS = {'==': '等しい', '!=': '等しくない', '<': 'より小さい', '<=': '以下', '>': 'より大きい', '>=': '以上'}
STATES = {'alive': '生存', 'downed': '戦闘不能', 'revived': '復活', 'dead': '死亡'}


def options(values):
    result = []
    for value in values:
        if isinstance(value, str) and value and value not in [r[0] for r in result]: result.append([value, value])
    return result


def kinds(world, people):
    people_opts = options(p.get('id') for p in people)
    items = world.get('items') or []
    if isinstance(items, dict): items = [dict(v, name=k) if isinstance(v, dict) else {'name': k} for k, v in items.items()]
    zones = world.get('zones') or []
    if isinstance(zones, dict): zones = list(zones)
    facts = world.get('facts') or []
    if isinstance(facts, dict): facts = [dict(v, id=k) if isinstance(v, dict) else {'id': k} for k, v in facts.items()]
    boolean_facts = options(f.get('id') for f in facts if isinstance(f, dict) and f.get('values') is None)
    valued_facts = options(f.get('id') for f in facts if isinstance(f, dict) and f.get('values') is not None)
    sources = []
    for p in people:
        sources.append(p.get('id'))  # ally-derived sources use actor IDs
        for m in p.get('modifiers') or []:
            if isinstance(m, dict): sources.append(m.get('source'))
        beliefs = p.get('beliefs_about') or {}
        if isinstance(beliefs, dict):
            for b in beliefs.values():
                if isinstance(b, dict): sources.extend(b.get('known_modifiers') or [])
    sources.extend(i.get('name') for i in items if isinstance(i, dict) and i.get('modifier') is not None)
    protagonist = next((p for p in people if p.get('id') == world.get('protagonist')), {})
    phases = options([t.get('id') for t in world.get('thresholds') or [] if isinstance(t, dict)] + list(protagonist.get('phase') or []))
    deliberation = world.get('deliberation') or {}
    proposals = deliberation.get('proposals') or [] if isinstance(deliberation, dict) else []
    proposal_opts = options(p.get('id') for p in proposals if isinstance(p, dict))
    state = deliberation.get('state') or {} if isinstance(deliberation, dict) else {}
    state_values = {k: options([v]) for k, v in state.items()} if isinstance(state, dict) else {}
    for proposal in proposals:
        if not isinstance(proposal, dict): continue
        for effects in [proposal.get('requires_state') or {}, *(proposal.get('outcomes') or {}).values()]:
            if isinstance(effects, dict):
                for key, value in effects.items():
                    if key in state_values and [value, value] not in state_values[key] and isinstance(value, str): state_values[key].append([value, value])
    concerns = world.get('concerns') or {}
    concern_ids = {row.get('id') for row in concerns.get('conditions') or [] if isinstance(row, dict)} if isinstance(concerns, dict) else set()
    goals = world.get('goal_rules') or {}
    objective_opts = options(d.get('condition') for d in goals.get('definitions') or [] if isinstance(d, dict) and d.get('condition') in concern_ids) if isinstance(goals, dict) else []
    def ref(name, label, values): return {'name': name, 'label': label, 'type': 'select', 'options': values}
    def actor(): return ref('actor', '人物', people_opts)
    def other(): return ref('other', '相手の人物', people_opts)
    def number(integer=False): return {'name': 'value', 'label': '比較する数値', 'type': 'number', 'integer': integer}
    def cmp(values=None): return ref('cmp', '比較', [[k, COMPARISONS.get(k, {'in': '選んだ状態のいずれか', 'not in': '選んだ状態以外'}[k] if k in ['in','not in'] else k)] for k in (values or COMPARISONS)])
    boolean = ref('negate', '判定', [[False, '満たす'], [True, '満たさない']])
    item_opts = options(i.get('name') if isinstance(i, dict) else i for i in items)
    zone_opts = options(z.get('name') if isinstance(z, dict) else z for z in zones)
    rows = [
        ('zone', '指定場所にいる', [actor(), ref('place','場所',zone_opts), cmp(['==','!='])], '{actor}が{place}にいる（{cmp}）', '人物の場所を比較します。移動中を厳密に除外する条件とは異なります。'),
        ('holds','品物を持つ',[actor(),ref('item','品物',item_opts),boolean],'{actor}が{item}を1個以上持つ','品物の個数は指定しません。'),
        ('holder','品物の代表所持者・配達先',[ref('item','品物',item_opts),ref('value','所持者・配達先',people_opts+zone_opts+[[None,'誰も持たず、配達もされていない']]),cmp(['==','!='])],'{item}の代表所持者・配達先が{value}（{cmp}）','配達先を優先します。複数所持者がいる場合はID順の代表1人です。'),
        ('present','主人公の場所に同席',[actor(),boolean],'{actor}が主人公の場所に同席する','結末評価での主人公の同席集合です。死亡・移動中の人物は除外されます。'),
        ('vitality','生存・戦闘不能などの状態',[actor(),{'name':'states','label':'状態','type':'enum-list','options':list(STATES.items())},cmp(['in','not in','==','!='])],'{actor}の状態が{states}（{cmp}）','生存、戦闘不能、復活、死亡。体力の残量ではありません。'),
        ('known','情報を知っている',[actor(),ref('fact','情報',boolean_facts),boolean],'{actor}が{fact}を知っている','boolean情報の知識への所属です。真実だという保証ではありません。'),
        ('knows_modifier','相手の能力情報を知る',[actor(),other(),ref('source','能力情報の出所',options(sources)),boolean],'{actor}が{other}の能力情報（{source}）を知っている','modifier IDではなくsourceを参照します。将来知り得るかは別の検証です。'),
        ('strength','戦闘力',[actor(),cmp(),number()],'{actor}の戦闘力が{value}（{cmp}）','現在の同席文脈・有効補正を含む値です。勝率ではありません。'),
        ('believed_strength','相手の戦闘力の見積り',[actor(),other(),cmp(),number()],'{actor}による{other}の戦闘力見積りが{value}（{cmp}）','観測者の既知・可視の補正と見積り値を使います。'),
        ('hostile_present','敵対者がいる',[actor(),boolean],'{actor}にとって敵対する人物がいる','同席集合は主人公の場所です。任意の人物の周囲とは限りません。'),
        ('confront_success','問いただしに成功済み',[actor(),ref('fact','値を持つ情報・問い',valued_facts),boolean],'{actor}が{fact}の問いただしに成功済み','正しい相手へのconfront成功の実行内記録です。任意の行動成功ではありません。'),
        ('stance','一方向の好意度',[actor(),other(),cmp(),number()],'{actor}から{other}への好意度が{value}（{cmp}）','向きがあります。実値の範囲は−1〜1です。'),
        ('awareness','一方向の認知度',[actor(),other(),cmp(),number()],'{actor}から{other}への認知度が{value}（{cmp}）','向きがあります。実値の範囲は0〜1です。'),
        ('bonds','他者からの正の好意の合計',[actor(),cmp(),number()],'{actor}に向けられた正の好意の合計が{value}（{cmp}）','友人数ではありません。各人物からの正の好意だけを合計します。'),
        ('proposal_decided','議案の決定結果',[ref('proposal','登録議案',proposal_opts),ref('status','決定結果',[[s,l] for s,l in [('approved','承認'),('rejected','否決'),('deferred','保留')]]),boolean],'{proposal}の結果が{status}','議案機能が設定された世界で使います。'),
        ('proposal_state','議案機能の共有状態',[ref('key','登録された状態キー',options(state_values)),{'name':'value','label':'状態の値','type':'select','options_by_key':state_values,'depends_on':'key'},boolean],'{key}の値が{value}','議案機能の状態です。任意の世界設定値へのアクセスではありません。'),
        ('objective_achieved','世界目標の達成済み記録',[ref('condition','登録済み世界目標',objective_opts),boolean],'{condition}の世界目標が達成済み','goal_rulesのconditionと接続した達成記録です。現在値の単純判定とは異なります。'),
        ('turn','行動ターン',[cmp(),number(True)],'行動ターンが{value}（{cmp}）','分や時間ではありません。'),
        ('day','日数',[cmp(),number(True)],'日数が{value}（{cmp}）','世界の実行日数カウンタです。固定のターン換算をしません。'),
        ('phase','主人公の段階',[ref('phase','主人公の段階',phases),boolean],'主人公が{phase}の段階に到達している','主人公のphase所属です。他の人物の段階ではありません。'),
    ]
    result = []
    for key, label, fields, summary, hint in rows:
        missing = [f['label'] for f in fields if f['type'] in ['select','enum-list'] and not f.get('options') and not any(f.get('options_by_key',{}).values())]
        result.append({'kind':key,'label':label,'fields':fields,'summary':summary,'hint':hint,'available':not missing,'unavailable_reason':('登録が必要：'+'・'.join(missing)) if missing else None})
    return result


def validate_atom(atom, catalog, fail):
    spec = next((r for r in catalog if r['kind'] == atom.get('kind')), None)
    if spec is None: fail('when','対応していない条件の種類です')
    if not spec['available']: fail('when', spec['unavailable_reason'])
    allowed = {'kind','expression','index'} | {f['name'] for f in spec['fields']}
    if not set(atom) <= allowed: fail('when','条件の入力項目を確認してください')
    for field in spec['fields']:
        key = field['name'];value = atom.get(key, False if key=='negate' else '==' if key=='cmp' else None)
        if field['type']=='number':
            if type(value) not in [int,float] or not math.isfinite(value) or field['integer'] and (type(value) is not int or abs(value)>9007199254740991): fail('when',field['label']+'は有限の'+('整数' if field['integer'] else '数値')+'で指定してください')
        else:
            opts=field.get('options',field.get('options_by_key',{}).get(atom.get(field.get('depends_on')),[]))
            values=[o[0] for o in opts]
            supplied=value if field['type']=='enum-list' and isinstance(value,list) else [value]
            if field['type']=='enum-list' and (not isinstance(value,list) or not value or not all(isinstance(v,str) for v in value) or len(set(value))!=len(value)): fail('when','状態を1つ以上、重複せず選択してください')
            if any(not any(type(v) is type(candidate) and v==candidate for candidate in values) for v in supplied): fail('when',field['label']+'は登録済みの候補から選択してください')
    if atom['kind']=='vitality' and atom.get('cmp','in') in ['==','!='] and len(atom['states'])!=1: fail('when','等しい・等しくないでは状態を1つ選択してください')


def parse(source, world, people, fail, existing=False):
    try: tree=ast.parse(source, mode='eval')
    except (SyntaxError,ValueError,RecursionError): fail('when','条件式の書式を確認してください')
    catalog=kinds(world,people);atoms=[]
    def literal(node):
        if isinstance(node,ast.Name) and existing and node.id not in {'self','target','planter'}: return node.id
        try:return ast.literal_eval(node)
        except (ValueError,TypeError,SyntaxError):fail('when','条件の値を文字列・数値として指定してください')
    def function(node):
        if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.keywords or node.func.id not in ARGS: fail('when','対応する条件関数を指定してください')
        name=node.func.id;fields=ARGS[name]
        if len(node.args)!=len(fields):fail('when','条件関数の引数の数を確認してください')
        return {'kind':name,**{k:literal(v) for k,v in zip(fields,node.args)}}
    comparison={ast.Eq:'==',ast.NotEq:'!=',ast.Lt:'<',ast.LtE:'<=',ast.Gt:'>',ast.GtE:'>=',ast.In:'in',ast.NotIn:'not in'}
    def visit(node,depth=0):
        if isinstance(node,ast.BoolOp):
            if not isinstance(node.op,(ast.And,ast.Or)) or depth>=2:fail('when','AND・ORの組み合わせは2段までです')
            return {'kind':'all' if isinstance(node.op,ast.And) else 'any','children':[visit(c,depth+1) for c in node.values]}
        negative=isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not)
        raw=node.operand if negative else node
        if isinstance(raw,ast.Call):
            atom=function(raw)
            if atom['kind'] not in BOOL_ARGS:fail('when','値を返す条件には比較を指定してください')
            atom['negate']=negative
        elif not negative and isinstance(raw,ast.Compare) and len(raw.ops)==1 and len(raw.comparators)==1 and type(raw.ops[0]) in comparison:
            op=comparison[type(raw.ops[0])]
            if isinstance(raw.comparators[0],ast.Name) and raw.comparators[0].id=='phase' and op in ['in','not in']:
                atom={'kind':'phase','phase':literal(raw.left),'negate':op=='not in'}
            else:
                atom={'kind':raw.left.id} if isinstance(raw.left,ast.Name) and raw.left.id in ['turn','day'] else function(raw.left)
                atom['cmp']=op;value=literal(raw.comparators[0])
                if atom['kind']=='zone':atom['place']=value
                elif atom['kind']=='vitality':atom['states']=list(value) if isinstance(value,(list,tuple,set)) else [value]
                elif atom['kind'] in NUMERIC or atom['kind']=='holder':atom['value']=value
                else:fail('when','この条件の比較形式は参照専用です')
        else:fail('when','この複雑な条件は参照専用です')
        validate_atom(atom,catalog,fail);atom.update(expression=ast.unparse(node),index=len(atoms));atoms.append(atom)
        if len(atoms)>8:fail('when','達成条件は8件以内で指定してください')
        return atom
    return visit(tree.body),atoms


def ui_tree(node):
    if node['kind'] in ['all','any']:return {'op':'and' if node['kind']=='all' else 'or','children':[ui_tree(n) for n in node['children']]}
    return {k:v for k,v in node.items() if k not in ['expression','index']}
