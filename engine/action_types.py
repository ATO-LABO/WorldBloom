"""Display bundles over one explicit action/mechanics policy."""
from copy import deepcopy
import json
from pathlib import Path
REGISTRY=tuple(json.loads(Path(__file__).with_name("action_types.json").read_text(encoding="utf-8")))
def catalog():
 return deepcopy([r for r in REGISTRY if r.get("enabled",True)])
def owner(verb):
 return next((r for r in REGISTRY if verb in r["actions"]),None)
def features(definition):
 return {key for key in ("stamina_management","budgeted_purchases") if (definition.get("mechanics") or {}).get(key) is True}
def for_world(definition, source=None):
 enabled=set((definition.get("action_usage") or {}).get("enabled",[]));flags=features(definition)
 return [r["id"] for r in catalog() if any("verb:"+v in enabled for v in r["actions"]) or bool(flags & set(r.get("features",[])))]
def label(selected):
 return "＋".join(["基本",*(r["name"] for r in catalog() if r["id"] in selected)])
