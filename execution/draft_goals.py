"""Finite draft goals compiled to existing engine predicates, never model code."""
from copy import deepcopy

from execution.provenance import ConfigError


FIELDS = {
    "reach": "place", "acquire": "item", "deliver": "item place",
    "meet": "person", "befriend": "person threshold", "defeat": "person",
    "survive": "day", "discover": "fact",
}


def compile_goal(raw, actor, people, places, items, facts, path, *, required=False):
    def bad(message, field=""):
        raise ConfigError(path + field, message)

    if not isinstance(raw, dict) or set(raw) != {"description", "conditions"}:
        bad("目標には description と conditions を指定してください")
    goal = deepcopy(raw)
    description = goal["description"]
    if (not isinstance(description, str) or len(description) > 2000
            or any(ord(c) < 32 and c not in '\n\t' for c in description)):
        bad("目標の説明は2000文字以内で入力してください", ".description")
    goal["description"] = description.strip()
    conditions = goal["conditions"]
    if not isinstance(conditions, list) or len(conditions) > 8:
        bad("達成条件は0〜8件で指定してください", ".conditions")
    if (required or conditions) and not goal["description"]:
        bad("目標を文章で入力してください", ".description")
    expressions = []
    for i, condition in enumerate(conditions):
        cp = f".conditions.{i}"
        kind = condition.get("type") if isinstance(condition, dict) else None
        if not isinstance(kind, str) or kind not in FIELDS:
            bad("対応する達成条件の種類を選んでください", cp)
        if set(condition) != {"type", *FIELDS[kind].split()}:
            bad("達成条件の項目を確認してください", cp)
        for field, names in (("person", people - {actor}), ("place", places),
                             ("item", items), ("fact", facts)):
            if field in condition and (not isinstance(condition[field], str) or condition[field] not in names):
                bad("登録済みの名前を指定してください", cp + "." + field)
        a = repr(actor)
        if kind == "reach":
            expression = f"zone({a}) == {condition['place']!r}"
        elif kind in ("acquire", "deliver"):
            expression = f"holds({a}, {condition['item']!r})"
            if kind == "deliver":
                expression += f" and zone({a}) == {condition['place']!r}"
        elif kind == "meet":
            b = repr(condition["person"])
            expression = f"zone({a}) == zone({b}) and vitality({b}) in ['alive', 'revived']"
        elif kind == "befriend":
            threshold = condition["threshold"]
            if type(threshold) not in (float, int) or not 0 < threshold <= 1:
                bad("親しさは0より大きく1以下の数値にしてください", cp + ".threshold")
            b = repr(condition["person"])
            expression = f"vitality({b}) in ['alive', 'revived'] and stance({a}, {b}) >= {threshold!r} and stance({b}, {a}) >= {threshold!r}"
        elif kind == "defeat":
            expression = f"vitality({condition['person']!r}) in ['downed', 'dead']"
        elif kind == "survive":
            day = condition["day"]
            if type(day) is not int or not 1 <= day <= 7:
                bad("生存日数は1〜7の整数で指定してください", cp + ".day")
            expression = f"turn >= {day * 4}"
        else:
            expression = f"known({a}, {condition['fact']!r})"
        expressions.append(f"({expression})")
    # A dead protagonist cannot satisfy an arrival, meeting or survival goal.
    predicate = (f"vitality({actor!r}) in ['alive', 'revived'] and " + " and ".join(expressions)) if expressions else None
    return goal, predicate


def subject_goal(goal):
    """Keep narrative intent and adapt an actual item objective for old policies."""
    result = deepcopy(goal)
    for condition in goal["conditions"]:
        if condition["type"] in ("acquire", "deliver"):
            result["target"] = condition["item"]
            if condition["type"] == "deliver":
                result["deliver_to"] = condition["place"]
            break
    return result
