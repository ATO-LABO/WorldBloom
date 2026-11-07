"""One serialized world format. Old saved worlds require offline conversion."""
VERSION=1
RETIRED_FIELDS=frozenset({"story_genres","story_genres_version"})
def validate(definition):
 if not isinstance(definition,dict) or type(definition.get("world_format_version")) is not int or definition["world_format_version"]!=VERSION:
  raise ValueError("旧世界形式は実行・編集できません。新形式の世界を選んでください。")
 if RETIRED_FIELDS & set(definition):
  raise ValueError("新形式に旧ジャンル設定は保存できません。")
 rules=definition.get("gapengine")
 if not isinstance(rules,dict) or not all(isinstance(rules.get(k),str) and rules[k] for k in ("action_graph","effects")):
  raise ValueError("新形式の共通ルール参照が必要です。")
 from engine.action_usage import VERSION as ACTION_VERSION,CONTRACT
 policy=definition.get("action_usage");mechanics=definition.get("mechanics")
 if not isinstance(policy,dict) or policy.get("version")!=ACTION_VERSION or policy.get("engine_contract")!=CONTRACT:
  raise ValueError("新形式の行動設定が必要です。")
 if not isinstance(mechanics,dict):
  raise ValueError("新形式の機能設定が必要です。")
 if definition.get("slot_execution") is not None or definition.get("joint_activities") is not None:
  raise ValueError("この実行方式の新形式世界設定は未対応です。")
def initial_policy(definition):
 from engine.verbs import HANDLED_VERBS
 from engine.action_types import REGISTRY
 optional={v for row in REGISTRY for v in row["actions"]}
 refs={"verb:"+v for v in HANDLED_VERBS-{"craft","respond"}-optional}
 refs|={"recipe:"+str(i["name"]) for i in definition.get("items",[]) if i.get("made_from")}
 refs|={"response:"+str(a["id"]) for a in (definition.get("responses") or {}).get("actions",[])}
 from engine.action_usage import VERSION as ACTION_VERSION,CONTRACT
 return {"version":ACTION_VERSION,"engine_contract":CONTRACT,"catalog_version":1,"enabled":sorted(refs)}
def initialize(definition):
 result=dict(definition);result["world_format_version"]=VERSION
 if RETIRED_FIELDS & set(result):raise ValueError("旧世界の変換は専用の退避済みコピーで行ってください")
 result["action_usage"]=initial_policy(result)
 result["mechanics"]={"version":1,"stamina_management":False,"budgeted_purchases":False}
 return result
