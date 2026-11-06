"""Human-authored story-shape notes. Metadata, never engine constraints."""
from copy import deepcopy
from execution.provenance import ConfigError,canonical

def normalize(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'version','type','outline'} or type(value['version']) is not int or value['version']!=1:
        raise ConfigError('authoring','制作メモの保存形式を確認してください')
    if not isinstance(value['type'],str) or len(value['type'])>80 or not isinstance(value['outline'],dict) or len(value['outline'])>32:
        raise ConfigError('authoring','型と制作メモを確認してください')
    if any(not isinstance(k,str) or not k or len(k)>80 or not isinstance(v,str) or len(v)>1600 for k,v in value['outline'].items()) or len(canonical(value))>64000:
        raise ConfigError('authoring','制作メモの文字数を確認してください')
    return deepcopy(value)

def prompt(value):
    import json
    return '\n人が入力した制作メモ（世界全体へ強制する実行ルールではありません。人物の目的や個人的な関心を置き換えず、構造化された設定を推測で追加しません）:\n'+json.dumps(normalize(value),ensure_ascii=False) if value is not None else ''
