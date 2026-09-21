"""WB-JEV-001 Stage 1 (revised 2026-09-18 18:10, design by Fable): render what
a subject knows into a coarse *situation class*, then render that class into
deterministic, quantization-free Japanese text.

Stage 1's first cut (``render_context``, rendering the concrete state --
exact names, exact item counts, exact confidence numbers) did not converge:
measured across seed 1-8, the distinct-context rate stayed at ~70% and
distinct (context, candidate) pairs kept growing linearly, because every
different companion roster or inventory count minted a new context. This
revision replaces the concrete renderer with ``situation()``, a hand-picked
coarse tuple (phase / who's-present-by-role / objective-holder-by-role /
recipe-readiness / a 3-bucket power comparison / vitality / which valued
facts are merely *suspected*), and ``render_situation()`` renders that tuple.
Companion names in candidate descriptions are likewise replaced by their
role (``describe_candidate_coarse``). Never leaks world.truth or the
antagonist's real strength; never renders a decimal number.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence, TYPE_CHECKING

import yaml

from engine.contest import believed_strength, strength
from gapengine.scenes import VERB_LABELS, _argument_text

if TYPE_CHECKING:
    from engine.subject import Subject
    from engine.world import World


def context_key(text: str) -> str:
    """First 16 hex digits of the text's sha256 -- a short, deterministic key
    for "is this the same rendered situation (or situation+candidate) as
    before"."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def describe_candidate(action: Any) -> str:
    """Verbatim rendering (real names, real numbers) -- kept for callers that
    want the concrete text. Mode A's probe uses ``describe_candidate_coarse``
    instead; see the module docstring for why."""

    label = VERB_LABELS.get(action.verb, action.verb)
    return f"{label}{_argument_text(list(action.args), {})}"


def load_common_knowledge(template_dir: Path) -> list[str]:
    """``templates/<genre>/rationality.yaml``'s ``common_knowledge`` list, or
    ``[]`` when the template has none (romance/detective, for instance)."""

    return _load_rationality_yaml(template_dir).get("common_knowledge", [])


def load_key_items(template_dir: Path) -> list[str]:
    """``templates/<genre>/rationality.yaml``'s ``key_items`` list -- items
    whose mere possession (not count) is worth calling out in a situation."""

    return _load_rationality_yaml(template_dir).get("key_items", [])


def load_describe_trial_grants(template_dir: Path) -> bool:
    """``templates/<genre>/rationality.yaml``'s ``describe_trial_grants`` flag
    (WB-JEV-004) -- default False, so a template that doesn't set it (every
    template before momotaro_plus) renders exactly as before."""

    return bool(_load_rationality_yaml(template_dir).get("describe_trial_grants", False))


def load_candidate_labels(template_dir: Path) -> dict[str, str]:
    """``templates/<genre>/rationality.yaml``'s ``candidate_labels`` mapping
    (WB-JEV-004 Stage 4b, requested after the 35b judge scored
    engine-verb-literal descriptions of the new momotaro_plus2 routes too low
    to tell them apart from an unrelated candidate -- see
    describe_candidate_coarse). Keys look like "<verb>:<first arg>" (e.g.
    "craft:鉄砲") or "trial:<trial id>" (e.g. "trial:brother_letter_trial");
    a matching candidate's whole rendered description is replaced by the
    value. Default {} so every template predating momotaro_plus2 renders
    byte-identically."""

    return dict(_load_rationality_yaml(template_dir).get("candidate_labels", {}))


def load_describe_negotiate_offer(template_dir: Path) -> bool:
    """``templates/<genre>/rationality.yaml``'s ``describe_negotiate_offer``
    flag (WB-JEV-004 Stage 4b) -- appends, to a ``negotiate`` candidate's
    description, which of the protagonist's items would make the antagonist's
    concede a trade (the same "attractive" test ``engine.actions``'s
    ``_concede_candidates`` applies). Default False, so every template
    predating momotaro_plus2 renders byte-identically."""

    return bool(_load_rationality_yaml(template_dir).get("describe_negotiate_offer", False))


def _load_rationality_yaml(template_dir: Path) -> dict[str, Any]:
    path = Path(template_dir) / "rationality.yaml"
    if not path.is_file():
        return {
            "common_knowledge": [],
            "key_items": [],
            "describe_trial_grants": False,
            "candidate_labels": {},
            "describe_negotiate_offer": False,
        }
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "common_knowledge": [str(v) for v in raw.get("common_knowledge", []) or []],
        "key_items": [str(v) for v in raw.get("key_items", []) or []],
        "describe_trial_grants": bool(raw.get("describe_trial_grants", False)),
        "candidate_labels": {
            str(key): str(value)
            for key, value in (raw.get("candidate_labels", {}) or {}).items()
        },
        "describe_negotiate_offer": bool(raw.get("describe_negotiate_offer", False)),
    }


def _goal_line(subject: Subject) -> str:
    if subject.goal.target is None:
        return "目的: なし"
    if subject.goal.deliver_to:
        return f"目的: {subject.goal.target}を{subject.goal.deliver_to}へ持ち帰る"
    return f"目的: {subject.goal.target}を得る"


def _defuzz_label(label: str) -> str:
    """A valued-fact label is a template like "鬼の力の要は{value}だ" -- meant
    to be filled in once the value is *believed*, not while it is merely
    suspected. Turn it into a value-free noun phrase suitable for
    "〜について見当がついている" by replacing {value} with "何か" and
    dropping a trailing "だ" (so "鬼の力の要は{value}だ" becomes "鬼の力の要は
    何か", not "…だについて…"). Labels without a placeholder pass through
    unchanged."""

    if "{value}" not in label:
        return label
    filled = label.replace("{value}", "何か")
    if filled.endswith("だ"):
        filled = filled[:-1]
    return filled


def _replace_names_in_text(subject: Subject, world: World, text: str) -> str:
    """A fact label is free Japanese text (e.g. "猿が知る鬼ヶ島の抜け道") and
    can bake in a companion's name where no per-argument substitution would
    ever catch it. Replace every *other* subject's id appearing in ``text``
    with their role relative to ``subject`` -- the protagonist and
    antagonist keep their real names (the one pair of names the spec
    allows)."""

    allowed = {subject.id, world.protagonist, world.antagonist}
    for candidate_id, candidate in world.subjects.items():
        if candidate_id in allowed or candidate_id not in text:
            continue
        role = world.target_role(subject, candidate)
        role_text = {"hostile": "敵対", "ally": "味方"}.get(role, "中立")
        text = text.replace(candidate_id, role_text)
    return text


def _fact_topic_text(subject: Subject, world: World, fact_id: str) -> str:
    """The value-free, name-free noun phrase for a fact/topic id, used both
    by the "見当がついていること" line and by describe_candidate_coarse's
    mislead/share_knowledge branches."""

    label = str(world.facts.get(fact_id, {}).get("label", fact_id))
    return _replace_names_in_text(subject, world, _defuzz_label(label))


def recipes_line(subject: Subject, world: World) -> str:
    """Static per (subject.knowledge, world) -- how to craft what, not
    whether the materials are on hand right now (that's situation()'s
    per-turn "recipes" bucket). Computed once per run, passed into
    render_situation unchanged."""

    pieces = []
    for product, materials in sorted(world.recipes.items()):
        definition = world.items.get(product, {})
        needed = (definition.get("requires") or {}).get("knowledge")
        if needed is not None and needed not in subject.knowledge:
            continue
        sources = []
        for material in sorted(materials):
            zones = sorted(
                {
                    str(source["zone"])
                    for source in world.items.get(material, {}).get("sources", []) or []
                    if source.get("zone")
                }
            )
            if zones:
                sources.append(f"{material}は{'・'.join(zones)}で調べると手に入る")
        craft_zone = definition.get("craft_zone")
        pieces.append(
            f"{product}は{'と'.join(f'{m}{n}' for m, n in sorted(materials.items()))}から"
            f"{f'{craft_zone}で' if craft_zone else ''}作れる"
            + (f"（{'、'.join(sources)}）" if sources else "")
        )
    return f"知っている作り方: {'／'.join(pieces) or 'なし'}"


def map_line(world: World) -> str:
    """Static per world -- computed once per run, passed into
    render_situation unchanged."""

    pieces = []
    for origin in sorted(world.routes):
        for route in world.routes[origin]:
            requirement = f"（{route.requires_item}が必要）" if route.requires_item else ""
            pieces.append(f"{origin}→{route.destination}{requirement}")
    return f"知っている地図: {'、'.join(pieces) or 'なし'}"


def _objective_state(subject: Subject, world: World) -> str:
    """none/self/ally/hostile/other -- identical to
    gapengine.precedent.ctx_key's objective_state derivation."""

    if subject.goal.target is None:
        return "none"
    holder = world.holder(subject.goal.target)
    if holder is None:
        return "none"
    if holder == subject.id:
        return "self"
    holder_subject = world.subjects.get(holder)
    if holder_subject is None:
        # holder is a delivery zone, not a subject id (see World.holder).
        return "other"
    role = world.target_role(subject, holder_subject)
    return role if role in ("ally", "hostile") else "other"


_VITALITY_LABELS = {"alive": "健在", "revived": "健在", "downed": "倒れている", "dead": "死亡"}


def _power_bucket(ratio: float) -> str:
    """r = strength(self) / believed_strength(敵) -- r<0.95 劣る, <=1.05 互角,
    else 優る (revised plan: a 3-bucket comparison, no number ever shown)."""

    if ratio < 0.95:
        return "劣る"
    if ratio <= 1.05:
        return "互角"
    return "優る"


def situation(
    subject: Subject,
    world: World,
    present: Sequence[Any],
    *,
    key_items: Sequence[str] = (),
) -> dict[str, Any]:
    """The coarse situation class for ``subject`` right now. Consumes no
    randomness. Every field is either a small enum, a bool, or a sorted
    tuple of fact ids -- never a name, a count, or a confidence number."""

    threshold_ids = {str(threshold["id"]) for threshold in world.thresholds}
    phases = tuple(sorted(phase for phase in subject.phase if phase in threshold_ids))
    pledged = any(phase.startswith("誓約:") for phase in subject.phase)

    peers = [peer for peer in present if peer.id != subject.id and peer.vitality != "dead"]
    roles = {world.target_role(subject, peer) for peer in peers}
    ally_present = "ally" in roles
    hostile_present = "hostile" in roles
    neutral_present = any(role not in ("ally", "hostile") for role in roles)

    recipes: dict[str, str] = {}
    for product, materials in sorted(world.recipes.items()):
        definition = world.items.get(product, {})
        needed = (definition.get("requires") or {}).get("knowledge")
        if needed is not None and needed not in subject.knowledge:
            continue
        if subject.inventory.get(product, 0) > 0:
            state = "所持"
        elif all(subject.inventory.get(material, 0) >= count for material, count in materials.items()):
            craft_zone = definition.get("craft_zone")
            state = "作れる" if craft_zone is None or subject.zone == craft_zone else "材料は揃っている"
        else:
            state = "材料不足"
        recipes[product] = state

    key_item_state = {item: subject.inventory.get(item, 0) > 0 for item in key_items}

    antagonist = world.subjects.get(world.antagonist)
    if antagonist is None or antagonist.vitality == "dead":
        power = "敵役なし"
    else:
        mine = strength(subject, world, list(present))
        theirs = believed_strength(subject, antagonist, world, list(present))
        ratio = mine / theirs if theirs else float("inf")
        power = _power_bucket(ratio)

    known_valued = tuple(sorted(subject.beliefs))
    # Rendered here (not in render_situation, which only sees the situation
    # dict) because turning a fact id into safe text needs `subject` (for
    # _replace_names_in_text's role lookups) and `world` (for the label).
    known_valued_text = "、".join(
        f"{_fact_topic_text(subject, world, fact_id)}について見当がついている"
        for fact_id in known_valued
    )

    return {
        "goal_line": _goal_line(subject),
        "zone": subject.zone,
        "phases": phases,
        "pledged": pledged,
        "ally_present": ally_present,
        "neutral_present": neutral_present,
        "hostile_present": hostile_present,
        "objective": _objective_state(subject, world),
        "recipes": recipes,
        "key_items": key_item_state,
        "power": power,
        "vitality": _VITALITY_LABELS.get(subject.vitality, subject.vitality),
        "known_valued": known_valued,
        "known_valued_text": known_valued_text,
        "disguised": subject.identity_displayed != subject.id,
    }


_OBJECTIVE_TEXT = {
    "none": "誰も持っていない",
    "self": "自分が持っている",
    "ally": "味方が持っている",
    "hostile": "敵対者が持っている",
    "other": "第三者が持っている",
}


def render_situation(
    sit: dict[str, Any],
    world: World,
    *,
    common_knowledge: Sequence[str] = (),
    recipe_lines: str,
    map_line: str,
) -> str:
    """Render a situation() dict into Japanese text. No name other than the
    protagonist/antagonist/a zone/an item ever appears; no number does
    either -- every continuous quantity was already bucketed by
    situation()."""

    companions = (
        ("味方がいる" if sit["ally_present"] else "味方はいない")
        + "、"
        + ("中立の相手がいる" if sit["neutral_present"] else "中立の相手はいない")
        + "、"
        + ("敵対者がいる" if sit["hostile_present"] else "敵対者はいない")
    )

    holdings = [
        f"{item}: {'あり' if has else 'なし'}" for item, has in sorted(sit["key_items"].items())
    ]
    holdings.extend(f"{product}: {state}" for product, state in sorted(sit["recipes"].items()))

    vitality_text = sit["vitality"] + ("（変装中）" if sit["disguised"] else "")
    phase_text = "、".join(sit["phases"]) or "なし"
    if sit["pledged"]:
        phase_text += "、誓約あり"

    return "\n".join(
        [
            sit["goal_line"],
            f"現在地: {sit['zone']}",
            f"通過段階: {phase_text}",
            f"同席: {companions}",
            f"目的物: {_OBJECTIVE_TEXT[sit['objective']]}",
            f"所持: {'、'.join(holdings) or 'なし'}",
            f"力関係: {sit['power']}",
            f"生命状態: {vitality_text}",
            f"見当がついていること: {sit['known_valued_text'] or 'なし'}",
            recipe_lines,
            map_line,
            f"世界の常識: {'／'.join(common_knowledge) if common_knowledge else 'なし'}",
        ]
    )


def _peer_role_text(subject: Subject, world: World, present: Sequence[Any], token: str) -> str:
    """token is usually a raw Action arg; if it happens to be another
    present subject's id, replace it with their role -- otherwise it is a
    zone/item name and is left untouched (both are allowed proper nouns)."""

    peer = next((candidate for candidate in present if candidate.id == token), None)
    if peer is None or peer.id == subject.id:
        return token
    role = world.target_role(subject, peer)
    return {"hostile": "敵対", "ally": "味方"}.get(role, "中立")


def describe_candidate_coarse(
    action: Any,
    subject: Subject,
    world: World,
    present: Sequence[Any],
    *,
    describe_trial_grants: bool = False,
    candidate_labels: Mapping[str, str] = {},
    describe_negotiate_offer: bool = False,
) -> str:
    """Like describe_candidate, but every companion name becomes a role
    (味方/中立/敵対) and every decimal is dropped (e.g. mislead's fabricated
    strength value) -- so genuinely-equivalent candidates ("give kibidango
    to whichever ally") collapse to one description instead of minting a
    new one per name.

    ``describe_trial_grants`` (WB-JEV-004, default False so every template
    predating momotaro_plus renders byte-identically) appends what a
    ``trial`` candidate grants -- e.g. "試練に挑んだ（中立、弟の手紙を得る）"
    -- so the judge/policy can tell two trials with the same giver apart.

    ``candidate_labels`` and ``describe_negotiate_offer`` (WB-JEV-004 Stage
    4b, both default to a no-op so every template predating momotaro_plus2
    renders byte-identically) were added after the 35b judge scored the
    engine-verb-literal wording of momotaro_plus2's new routes too low to
    tell apart from an unrelated candidate ("作った（鉄砲）" 0.07,
    "交渉した（敵対）" while holding the letter 0.17) -- rephrasing them into
    what the action actually accomplishes ("道中の商人から小判3枚で鉄砲を買っ
    た", "宝を譲るよう交渉した（敵対、差し出せる品: 弟の手紙）") scored 0.74.

    ``candidate_labels`` maps "<verb>:<first arg>" (craft) or
    "trial:<trial id>" (trial) to a full replacement string -- when a
    candidate matches, that string is returned as-is (no pieces, no
    describe_trial_grants addendum: the replacement wins over the addendum).
    ``describe_negotiate_offer`` replaces a ``negotiate`` candidate's whole
    description with which of the protagonist's items would make the
    antagonist's concede a trade, using the same "attractive" test as
    ``engine.actions``'s ``_concede_candidates``: the protagonist holds it,
    it is lootable, it carries a modifier, and the antagonist doesn't
    already have it."""

    label_key: str | None = None
    if action.verb == "craft" and action.args:
        label_key = f"craft:{action.args[0]}"
    elif action.verb == "trial":
        trial_id = action.meta.get("trial_id")
        if trial_id is not None:
            label_key = f"trial:{trial_id}"
    if label_key is not None and label_key in candidate_labels:
        return str(candidate_labels[label_key])

    if describe_negotiate_offer and action.verb == "negotiate":
        target_id = action.meta.get("target")
        target_text = _peer_role_text(subject, world, present, str(target_id))
        target = next(
            (peer for peer in present if peer.id == target_id),
            None,
        )
        offerable = sorted(
            item
            for item, count in subject.inventory.items()
            if count > 0
            and bool(world.items.get(item, {}).get("lootable", False))
            and bool(world.items.get(item, {}).get("modifier"))
            and (target is None or not target.has_item(item))
        )
        offer_text = (
            f"差し出せる品: {'、'.join(offerable)}" if offerable else "差し出せる品なし"
        )
        return f"宝を譲るよう交渉した（{target_text}、{offer_text}）"

    label = VERB_LABELS.get(action.verb, action.verb)
    if action.verb == "mislead":
        target_text = _peer_role_text(subject, world, present, str(action.meta.get("target")))
        if action.meta.get("belief_kind") == "strength":
            about_text = f"{action.meta.get('about')}の強さ"
        else:
            about_text = _fact_topic_text(subject, world, str(action.meta.get("about")))
        pieces = [target_text, about_text]
    elif action.verb == "share_knowledge" and len(action.args) >= 2:
        target_text = _peer_role_text(subject, world, present, str(action.args[0]))
        topic = str(action.args[1])
        topic_text = "雑談" if topic == "雑談" else _fact_topic_text(subject, world, topic)
        pieces = [target_text, topic_text]
    else:
        pieces = [
            _peer_role_text(subject, world, present, str(value))
            for value in action.args
            if not isinstance(value, float)
        ]

    if describe_trial_grants and action.verb == "trial":
        trial_id = action.meta.get("trial_id")
        trial = next(
            (t for t in world.trials if str(t["id"]) == str(trial_id)),
            None,
        )
        if trial is not None:
            grants = trial.get("grants") or {}
            granted_fact = grants.get("fact")
            if granted_fact is not None:
                pieces.append(f"{_fact_topic_text(subject, world, str(granted_fact))}を得る")
            granted_item = grants.get("item")
            if granted_item is not None:
                pieces.append(f"{granted_item}を得る")

    return f"{label}{('（' + '、'.join(pieces) + '）') if pieces else ''}"
