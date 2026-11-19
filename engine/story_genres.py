"""Application-maintained action scopes. Common actions are always available."""
from copy import deepcopy
import json
from pathlib import Path


def load_registry(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    ids, verbs = set(), set()
    if not isinstance(rows, list):
        raise ValueError("genre registry must be a list")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids or row["id"] == "common":
            raise ValueError("genre IDs must be unique")
        if not row.get("name") or not isinstance(row.get("actions"), list) or type(row.get("enabled", True)) is not bool:
            raise ValueError("invalid genre definition")
        if any(not isinstance(v, str) or not v or v in verbs for v in row["actions"]) or len(set(row["actions"])) != len(row["actions"]):
            raise ValueError("an action must belong to exactly one genre")
        if not set(row.get("grant_actions", [])) <= set(row["actions"]):
            raise ValueError("granted actions must belong to the genre")
        ids.add(row["id"])
        verbs.update(row["actions"])
    return tuple(rows)


# Retire entries with enabled=false, preserving their action ownership and IDs.
# This registry is maintained by developers; no user route writes it.
REGISTRY = load_registry(Path(__file__).with_name("story_genres.json"))


def catalog():
    return deepcopy([r for r in REGISTRY if r.get("enabled", True)])


def validate(value):
    IDS = {r["id"] for r in catalog()}
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in IDS for v in value) or len(set(value)) != len(value):
        raise ValueError("用意されたジャンルから重複なく選んでください")
    return [row["id"] for row in catalog() if row["id"] in value]


def resolve(definition, graph):
    if "story_genres" in definition:
        value = definition["story_genres"]
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError("story_genres must be a list of IDs")
        return [r["id"] for r in catalog() if r["id"] in value]
    # Preserve existing combat behavior without rewriting saved world files.
    # Romance's new relationship action is opt-in, separate from older pledges.
    nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("verb") == "fight"]
    if not nodes:
        return [r["id"] for r in catalog() if r["id"] == "combat"]
    genres = graph.get("genre", [])
    genres = {genres} if isinstance(genres, str) else set(genres or [])
    tagged = any(not genres or n.get("genres") is None or bool(genres & ({n["genres"]} if isinstance(n["genres"], str) else set(n["genres"]))) for n in nodes)
    roles = (graph.get("permission") or {}).get("fight", {})
    allowed = any(roles.get(role, "allow") != "deny" for role in ("hostile", "neutral", "ally"))
    return [r["id"] for r in catalog() if r["id"] == "combat"] if nodes and tagged and allowed else []


def for_world(definition, source):
    if "story_genres" in definition:
        return resolve(definition, {})
    from engine.world import _load_action_graph
    try:
        graph, _ = _load_action_graph(definition, source)
    except (OSError, ValueError, TypeError):
        # Metadata must stay readable while an imported basic rule is repaired.
        return []
    return resolve(definition, graph)


def label(selected):
    return "＋".join(["共通", *(row["name"] for row in catalog() if row["id"] in selected)])


def owner(verb):
    return next((r for r in REGISTRY if verb in r["actions"]), None)


def allows(selected, verb):
    row = owner(verb)
    return row is None or row.get("enabled", True) and row["id"] in selected


def granted_actions(selected):
    return {v for r in catalog() if r["id"] in selected for v in r.get("grant_actions", [])}
