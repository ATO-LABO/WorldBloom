"""Output contracts for NEW generated drafts, not a migration of human data."""
from execution.draft_goals import FIELDS


def text(limit=120, *, empty=False):
    return {'type': 'string', 'minLength': 0 if empty else 1, 'maxLength': limit}


def obj(properties, *, optional=()):
    return {'type': 'object', 'properties': properties,
            'required': [k for k in properties if k not in optional],
            'additionalProperties': False}


def rows(item, maximum, minimum=0):
    return {'type': 'array', 'items': item, 'minItems': minimum, 'maxItems': maximum}


def draft_schema():
    """Constrain shapes; registered-name references and semantics remain checked locally."""
    conditions = []
    for kind, fields in FIELDS.items():
        properties = {'type': {'type': 'string', 'const': kind}}
        for field in fields.split():
            properties[field] = ({'type': 'integer', 'minimum': 1} if field == 'day'
                else {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1} if field == 'threshold'
                else text())
        conditions.append(obj(properties))
    goal = obj({'description': text(2000, empty=True),
                'conditions': rows({'oneOf': conditions}, 8)})
    person = obj({'name': text(), 'description': text(2000, empty=True),
                  'personality': text(2000, empty=True), 'entry': text(), 'goal': goal,
                  'relations': rows(obj({'person': text(),
                      'affinity': {'type': 'number', 'minimum': -1, 'maximum': 1},
                      'awareness': {'type': 'number', 'minimum': 0, 'maximum': 1},
                      'label': text(empty=True)}), 7)})
    return obj({'name': text(), 'overview': text(8000, empty=True),
                'initial_story': text(8000, empty=True),
                'places': rows(obj({'name': text(), 'description': text(2000, empty=True)}), 8, 1),
                'people': rows(person, 8, 2),
                'items': rows(obj({'name': text(), 'holders': rows(obj({'person': text(),
                    'count': {'type': 'integer', 'minimum': 1, 'maximum': 10000}}), 8)}), 8),
                'facts': rows(obj({'name': text(), 'description': text(2000), 'place': text()}), 8),
                'routes': rows(obj({'from': text(), 'to': text(),
                    'cost': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 10000},
                    'requires_item': text()}, optional=('cost', 'requires_item')), 40),
                'protagonist': text(), 'antagonist': text()})


def review_schema(draft):
    names = [p['name'] for p in draft['people']]
    return obj({'overview_matches': {'type': 'boolean'},
                'goals': rows(obj({'person': {'type': 'string', 'enum': names},
                    'matches': {'type': 'boolean'}, 'reason': text(1000)}), len(names), len(names)),
                'issues': rows(text(1000), 16)})


def instructions():
    return ("経路と目標条件は別の型です。routesは場所から場所への有向の道だけです。"
        '例 routes:[{"from":"村","to":"診療所"},{"from":"診療所","to":"村"}]。'
        "逆方向にも移動できるなら逆の道も登録します。type/item/placeは経路の項目ではありません。"
        '薬を届ける目標はその人物のgoal:{"description":"薬を診療所へ届ける",'
        '"conditions":[{"type":"deliver","item":"薬","place":"診療所"}]}に入れます。'
        '診療所へ到着しハルと仲良くなる目標はconditions:[{"type":"reach","place":"診療所"},'
        '{"type":"befriend","person":"ハル","threshold":0.6}]です。'
        "これらの場所・人物・品物名は例なので入力に流用しません。"
        "対応する条件で表せる具体的な到着・所持・配達・親交等の目標には、その条件を必ず入れます。"
        "表せるのに単にconditionsを空にしてはいけません。一方、継続的な護衛や複雑な内面の変化など"
        "現エンジンで判定できない目的は文章を残し、条件を空にします。無関係な条件を創作しません。"
        "概要に同じ人物の望みが複数ある場合は、ひとつだけ残してはいけません。"
        "『場所Bへ向かい、人物Bと親しくなりたい』は、場所Bへのreachと人物Bへのbefriendの両方を残します。"
        "その人物を場所Bから開始させて移動の目的を消してはいけません。"
        "全ての参照は今回定義する同じ名前を使います。開始位置・初期所持・初期関係だけで目標が成立しないよう確認します。\n")


def review_instructions():
    return ("概要を基準に、各人物の望みを一つずつ分けて照合してください。生成されたdescriptionだけを基準にすると、削られた望みを見逃します。"
        "概要の複数の望みのうち一部だけがdescriptionまたはconditionsに残る場合はmatchesをfalseにします。"
        "例えば『場所Bへ向かい、人物Bと親しくなりたい』に対し、親交の条件しかなく最初から場所Bにいる案は不一致です。"
        "到達と親交の両方が目標に残り、開始位置で到達部分を取り消していないか確認してください。"
        "reasonには概要中の各目的と、description・conditions・開始位置との対応または欠落を具体的に書きます。\n")
