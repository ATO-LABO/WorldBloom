"""World-local money labels and explicit purchase policy; no FX or resale."""
from copy import deepcopy

MAX_AMOUNT = 1_000_000_000
PRESETS = (
    {'id':'JPY','name':'日本円','unit':'円','meal':1000},
    {'id':'USD','name':'米ドル','unit':'ドル','meal':10},
    {'id':'CNY','name':'人民元','unit':'元','meal':30},
    {'id':'EUR','name':'ユーロ','unit':'ユーロ','meal':10},
    {'id':'custom','name':'独自通貨','unit':'ゴールド','meal':None},
)


def amount(value, *, nullable=False):
    if nullable and value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_AMOUNT:
        raise ValueError('金額は0〜1000000000の整数で指定してください')
    return value


def text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>80:
        raise ValueError('通貨・基準項目の名前と表示単位は1〜80文字で指定してください')
    return value.strip()


def validate(world):
    raw=world.get('economy')
    if raw is None:
        return None
    if not isinstance(raw,dict) or set(raw)!={'version','currency','reference_prices'} or type(raw['version']) is not int or raw['version']!=1:
        raise ValueError('金銭設定の形式を確認してください')
    currency=raw['currency'];reference=raw['reference_prices']
    if not isinstance(currency,dict) or not {'preset','name','unit','asset'} <= set(currency) or not set(currency) <= {'preset','name','unit','asset','custom'}:
        raise ValueError('通貨設定を確認してください')
    if not isinstance(currency['preset'],str) or currency['preset'] not in {p['id'] for p in PRESETS}:
        raise ValueError('通貨プリセットを確認してください')
    text(currency['name']);text(currency['unit'])
    if 'custom' in currency:
        custom=currency['custom']
        if not isinstance(custom,dict) or set(custom)!={'name','unit'}:raise ValueError('独自通貨の保存値を確認してください')
        text(custom['name']);text(custom['unit'])
    names={i if isinstance(i,str) else i['name'] for i in world.get('items',[]) or []}
    if not isinstance(currency['asset'],str) or currency['asset'] not in names:
        raise ValueError('支払いに使う通貨資産を確認してください')
    if not isinstance(reference,dict) or set(reference)!={'meal'} or not isinstance(reference['meal'],dict) or set(reference['meal'])!={'name','amount'}:
        raise ValueError('食事の基準価格を確認してください')
    text(reference['meal']['name']);amount(reference['meal']['amount'],nullable=True)
    return deepcopy(raw)


def assets(world):
    """Infer only existing payment/labor assets, never ordinary possessions."""
    items=[{'name':i} if isinstance(i,str) else i for i in world.get('items',[]) or []]
    names={i['name'] for i in items}
    candidates={name for i in items for name in (i.get('price') or {}) if name in names}
    candidates.update(i['name'] for i in items if any(s.get('type')=='labor' for s in i.get('sources',[]) or []))
    configured=(world.get('economy') or {}).get('currency',{}).get('asset')
    if configured in names:
        candidates.add(configured)
    return sorted(candidates)


def summary(world, people):
    settings=validate(world)
    candidates=assets(world)
    currency=deepcopy(settings['currency']) if settings else {'preset':'JPY','name':'日本円','unit':'円','asset':candidates[0] if len(candidates)==1 else None}
    actor=next((p for p in people if p.get('id')==world.get('protagonist')),None)
    return {'configured':settings is not None,'currency':currency,'asset_choices':candidates,
            'protagonist':actor['id'] if actor else None,
            'balance':(actor.get('inventory') or {}).get(currency['asset'],0) if actor and currency['asset'] else None,
            'meal':deepcopy(settings['reference_prices']['meal']) if settings else {'name':'食事','amount':None}}


def purchasable(definition, budgeted):
    """An explicit unknown price never becomes free when money is disabled."""
    if definition.get('monetary_trade') is False or not definition.get('shop_zone'):
        return False
    price=definition.get('price')
    if 'monetary_trade' in definition:
        return bool(price)
    return bool(price) or not budgeted
