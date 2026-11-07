"""Scoped money edits over the existing journalled world/person transaction."""
from copy import deepcopy
from engine import economy
from execution.provenance import ConfigError


def apply(files, operation, target, values):
    candidate=deepcopy(files);world=candidate['world.yaml']
    try:
        configured=economy.validate(world)
        if operation=='economy-settings':
            changes=values.get('changes')
            fields={'economy-currency':{'preset','name','unit','asset'},'economy-balance':{'amount'},'economy-reference':{'name','amount'},'economy-item':{'trade','amount','shop_zone'},'economy-income':{'entries'}}
            if target is not None or not isinstance(changes,list) or not 1<=len(changes)<=len(world.get('items',[]))+4:raise ValueError('保存する金銭設定を確認してください')
            seen=set()
            for change in changes:
                if not isinstance(change,dict) or set(change)!={'operation','target','values'}:raise ValueError('金銭設定の保存項目を確認してください')
                op=change['operation'];v=change['values'];item=change['target']
                if not isinstance(op,str) or op not in fields or not isinstance(v,dict) or not fields[op]<=set(v) or not set(v)<=fields[op]|({'custom'} if op=='economy-currency' else set()):raise ValueError('金銭設定の入力項目を確認してください')
                if item is not None and not isinstance(item,str):raise ValueError('品物の対象を確認してください')
                key=(op,item)
                if key in seen:raise ValueError('保存する項目が重複しています')
                seen.add(key)
            order={'economy-currency':0,'economy-balance':1,'economy-reference':2,'economy-item':3,'economy-income':4}
            for change in sorted(changes,key=lambda c:(order[c['operation']],c['target'] or '')):
                candidate=apply(candidate,change['operation'],change['target'],change['values'])
            return candidate
        if operation not in ('economy-item','person-balance') and target is not None:
            raise ValueError('金銭設定の対象を確認してください')
        if operation=='economy-currency':
            preset=next((p for p in economy.PRESETS if p['id']==values['preset']),None)
            if preset is None:raise ValueError('通貨プリセットを確認してください')
            name=economy.text(values['name']);unit=economy.text(values['unit'])
            custom=deepcopy(configured['currency'].get('custom')) if configured else None
            if configured and configured['currency']['preset']=='custom':custom={'name':configured['currency']['name'],'unit':configured['currency']['unit']}
            if 'custom' in values:
                raw=values['custom']
                if not isinstance(raw,dict) or set(raw)!={'name','unit'}:raise ValueError('独自通貨の保存値を確認してください')
                custom={'name':economy.text(raw['name']),'unit':economy.text(raw['unit'])}
            if preset['id']=='custom':custom={'name':name,'unit':unit}
            if preset['id']!='custom':name,unit=preset['name'],preset['unit']
            asset=values['asset']
            if not isinstance(asset,str):raise ValueError('支払資産を確認してください')
            choices=economy.assets(world)
            if configured:
                existing=configured['currency']['asset']
                if asset not in ('',existing):raise ValueError('通貨の表示変更では支払資産を変更できません')
                asset=existing
            elif asset and asset not in choices:
                raise ValueError('既存の支払資産を選択してください')
            elif not asset:
                if len(choices)>1:raise ValueError('複数の支払資産があるため、使用する資産を選択してください')
                if choices:asset=choices[0]
                else:
                    names={i['name'] for i in world.get('items',[])}
                    asset='currency';index=1
                    while asset in names:index+=1;asset='currency_'+str(index)
                    world.setdefault('items',[]).append({'name':asset,'display_name':name})
            meal=deepcopy(configured['reference_prices']['meal']) if configured else {'name':'食事','amount':None}
            world['economy']={'version':1,'currency':{'preset':preset['id'],'name':name,'unit':unit,'asset':asset},'reference_prices':{'meal':meal}}
            if custom is not None:world['economy']['currency']['custom']=custom
        elif operation=='person-balance':
            if not configured:raise ValueError('先に支払いに使う通貨を設定してください')
            if not isinstance(values,dict) or set(values)!={'amount'}:raise ValueError('人物の所持金だけを指定してください')
            if not isinstance(target,str) or not target:raise ValueError('登録された人物を指定してください')
            actors=[p for rel,p in candidate.items() if rel!='world.yaml' and p.get('id')==target]
            if len(actors)!=1:raise ValueError('所持金を設定する人物を一意に指定してください')
            actor=actors[0]
            if 'inventory' in actor and not isinstance(actor['inventory'],dict):raise ValueError('人物の既存の持ち物の形式を確認してください')
            inventory=actor.get('inventory',{})
            if any(not isinstance(item,str) or not item or type(count) is not int or count<0 for item,count in inventory.items()):
                raise ValueError('人物の既存の持ち物の数量を確認してください')
            asset=configured['currency']['asset'];value=economy.amount(values['amount'])
            if value:actor.setdefault('inventory',{})[asset]=value
            elif 'inventory' in actor:inventory.pop(asset,None)
        elif operation=='economy-balance':
            state=economy.summary(world,[p for rel,p in candidate.items() if rel!='world.yaml'])
            asset=state['currency']['asset'];value=economy.amount(values['amount'])
            actors=[p for rel,p in candidate.items() if rel!='world.yaml' and p.get('id')==world.get('protagonist')]
            if not asset or len(actors)!=1:raise ValueError('通貨と主人公を一意に設定してください')
            inventory=deepcopy(actors[0].get('inventory') or {})
            actors[0]['inventory']=inventory
            if value:inventory[asset]=value
            else:inventory.pop(asset,None)
        elif operation=='economy-reference':
            if not configured:raise ValueError('先に通貨を設定してください')
            world['economy']['reference_prices']['meal']={'name':economy.text(values['name']),'amount':economy.amount(values['amount'],nullable=True)}
        elif operation=='economy-income':
            if not configured:raise ValueError('先に通貨を設定してください')
            from execution.income_editor import apply as apply_income
            apply_income(world,values['entries'])
        elif operation=='economy-item':
            if not configured:raise ValueError('先に通貨を設定してください')
            matches=[i for i in world.get('items',[]) if i['name']==target]
            if len(matches)!=1:raise ValueError('登録された品物を指定してください')
            definition=matches[0];trade=values['trade'];shop=values['shop_zone']
            if type(trade) is not bool:raise ValueError('金銭取引の可否を指定してください')
            zones={z if isinstance(z,str) else z['name'] for z in world.get('zones',[])}
            if shop is not None and (not isinstance(shop,str) or shop not in zones):raise ValueError('購入できる場所を指定してください')
            if trade and shop is None:raise ValueError('取引を許可する場合は購入できる場所を指定してください')
            value=economy.amount(values['amount'],nullable=True);asset=configured['currency']['asset']
            if asset==target:raise ValueError('支払通貨そのものに自身で支払う価格は設定できません')
            prices=deepcopy(definition.get('price') or {})
            if value is None:prices.pop(asset,None)
            else:prices[asset]=value
            definition['monetary_trade']=trade
            if shop is None:definition.pop('shop_zone',None)
            else:definition['shop_zone']=shop
            if prices:definition['price']=prices
            else:definition.pop('price',None)
        else:raise ValueError('金銭設定の編集項目を確認してください')
        economy.validate(world)
    except (ValueError,TypeError,KeyError) as error:
        raise ConfigError('economy',str(error),code='bad_request') from error
    return candidate
