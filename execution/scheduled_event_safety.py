"""Validate changed references in a draft's scheduled events."""

from engine.actions import FORCE_ARG_KINDS
from execution.provenance import ConfigError


def _bad(field, message):
    raise ConfigError(field, message, code="bad_request")


def _changed_references(pairs, runtime):
    for event, previous in pairs:
        previous = previous or {}
        name = event.get("label") or event["id"]
        grant = event.get("grants_item")
        if grant != previous.get("grants_item") and grant is not None:
            prior_grant = previous.get("grants_item")
            prior_grant = prior_grant if isinstance(prior_grant, dict) else {}
            if not isinstance(grant, dict):
                _bad("item_name", "出来事「" + name + "」で付与する品物の形式を確認してください。")
            if any(key not in prior_grant or grant[key] != prior_grant[key] for key in set(grant) - {"name", "count"}):
                _bad("item_name", "出来事「" + name + "」の品物に未対応の項目を追加できません。")
            if grant.get("name") != prior_grant.get("name") and (not isinstance(grant.get("name"), str) or grant["name"] not in runtime.items):
                _bad("item_name", "出来事「" + name + "」で付与する品物は登録品物を選んでください。")
            if (grant.get("count", 1) != prior_grant.get("count", 1)
                    or type(grant.get("count", 1)) is not type(prior_grant.get("count", 1)) or not prior_grant):
                if type(grant.get("count", 1)) is not int or not 1 <= grant.get("count", 1) <= 999:
                    _bad("item_count", "出来事「" + name + "」の品物の個数は1〜999の整数で指定してください。")
        force = event.get("force_action")
        if force:
            prior_force = previous.get("force_action") or {}
            same_verb = isinstance(prior_force, dict) and prior_force.get("verb") == force.get("verb")
            prior_args = prior_force.get("args", []) if same_verb else []
            for index, kind in enumerate(FORCE_ARG_KINDS[force["verb"]]):
                args = force.get("args", [])
                if kind != "item" or index >= len(args):
                    continue  # Omitted suffix arguments retain runtime candidate selection.
                value = args[index]
                if same_verb and index < len(prior_args) and value == prior_args[index]:
                    continue
                if not isinstance(value, str) or value not in runtime.items:
                    _bad("args", "出来事「" + name + "」の行動引数には登録品物を選んでください。")
        fact = event.get("grants_fact")
        if fact != previous.get("grants_fact") and fact is not None:
            if not isinstance(fact, str) or fact not in runtime.facts or runtime.facts[fact].get("values") is not None:
                _bad("grants_fact", "出来事「" + name + "」で知らせる情報は登録済みの値を持たない情報を選んでください。")
        place = event.get("move_to")
        if place != previous.get("move_to") and place is not None:
            if not isinstance(place, str) or place not in runtime.zones:
                _bad("move_to", "出来事「" + name + "」の移動先は登録場所を選んでください。")
