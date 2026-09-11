"""Phase 2 delayed effects, identity, rituals, and phase rules."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from engine.predicate import Predicate, compile_predicate

if TYPE_CHECKING:
    from engine.actions import Action
    from engine.subject import Subject
    from engine.world import World


_EFFECT_KINDS = frozenset(
    {
        "modifier",
        "neutralize",
        "stance",
        "reputation",
        "enable_verb",
    }
)

_EFFECT_REQUIRED_KEYS = {
    "modifier": frozenset(
        {"target", "source", "value", "kind"}
    ),
    "neutralize": frozenset({"target", "source"}),
    "stance": frozenset({"a", "b", "delta"}),
    "reputation": frozenset({"target", "delta"}),
    "enable_verb": frozenset({"verb"}),
}


def _as_sequence(
    value: Any,
    *,
    label: str,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a sequence")
    result: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError(f"{label} entries must be mappings")
        result.append(deepcopy(entry))
    return result


def _resolve_configured_path(
    configured: str | Path,
    source: Path,
    *,
    label: str,
) -> Path:
    path = Path(configured)
    project_root = Path(__file__).resolve().parents[1]
    candidates = (
        [path]
        if path.is_absolute()
        else [source.parent / path, project_root / path]
    )

    checked: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in checked:
            continue
        checked.append(resolved)
        if resolved.is_file():
            return resolved

    attempted = ", ".join(str(path) for path in checked)
    raise ValueError(f"{label} does not exist; tried: {attempted}")


def _load_effect_library(
    definition: dict[str, Any],
    source: Path,
) -> tuple[list[dict[str, Any]], Path | None]:
    raw_gapengine = definition.get("gapengine")
    if raw_gapengine is None:
        return [], None
    if not isinstance(raw_gapengine, dict):
        raise ValueError("world.gapengine must be a mapping")

    configured = raw_gapengine.get("effects")
    if configured is None:
        return [], None
    if not isinstance(configured, (str, Path)) or not str(configured):
        raise ValueError("world.gapengine.effects must be a path")

    path = _resolve_configured_path(
        configured,
        source,
        label="Effect library",
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _as_sequence(raw, label="effects"), path


def configure_phase2(
    world: World,
    definition: dict[str, Any],
    source: Path,
) -> None:
    """Load and syntax-check optional Phase 2 configuration."""

    raw_effects, effects_path = _load_effect_library(
        definition,
        source,
    )
    effect_ids = [str(entry.get("id", "")) for entry in raw_effects]
    if any(not effect_id for effect_id in effect_ids):
        raise ValueError("Effect id is required")
    if len(effect_ids) != len(set(effect_ids)):
        raise ValueError("Effect ids must be unique")

    effect_library: dict[str, dict[str, Any]] = {}
    for raw_effect in raw_effects:
        effect = deepcopy(raw_effect)
        effect_id = str(effect["id"])

        plant = effect.get("plant")
        payoff = effect.get("payoff")
        if not isinstance(plant, dict):
            raise ValueError(
                f"Effect plant must be a mapping: {effect_id}"
            )
        if not isinstance(payoff, dict):
            raise ValueError(
                f"Effect payoff must be a mapping: {effect_id}"
            )

        plant_verb = plant.get("verb")
        if not isinstance(plant_verb, str) or not plant_verb:
            raise ValueError(
                f"Effect plant verb is required: {effect_id}"
            )

        raw_when = plant.get("when")
        if raw_when is not None:
            if not isinstance(raw_when, str) or not raw_when.strip():
                raise ValueError(
                    f"Effect plant.when must be a predicate: {effect_id}"
                )
            plant["predicate_source"] = raw_when

        condition = payoff.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError(
                f"Effect payoff condition is required: {effect_id}"
            )
        payoff["predicate_source"] = condition

        mode = str(payoff.get("mode", ""))
        if mode not in {"auto", "chosen"}:
            raise ValueError(
                f"Unknown effect payoff mode: {effect_id}:{mode}"
            )
        payoff["mode"] = mode

        raw_payload = payoff.get("effect")
        if not isinstance(raw_payload, dict):
            raise ValueError(
                f"Effect payload must be a mapping: {effect_id}"
            )
        kinds = set(raw_payload)
        if len(kinds) != 1:
            raise ValueError(
                f"Effect payload requires exactly one kind: {effect_id}"
            )
        kind = next(iter(kinds))
        if kind not in _EFFECT_KINDS:
            raise ValueError(
                f"Unknown effect kind: {effect_id}:{kind}"
            )
        if not isinstance(raw_payload[kind], dict):
            raise ValueError(
                f"Effect payload body must be a mapping: "
                f"{effect_id}:{kind}"
            )
        missing = (
            _EFFECT_REQUIRED_KEYS[kind]
            - set(raw_payload[kind])
        )
        if missing:
            raise ValueError(
                f"Effect payload is missing required keys: "
                f"{effect_id}:{kind}:{sorted(missing)}"
            )

        effect["plant"] = plant
        effect["payoff"] = payoff
        effect_library[effect_id] = effect

    disguises = _as_sequence(
        definition.get("disguises"),
        label="world.disguises",
    )
    for index, disguise in enumerate(disguises):
        subject = disguise.get("subject")
        displayed = disguise.get("as")
        when = disguise.get("when")
        if not isinstance(subject, str) or not subject:
            raise ValueError(
                f"Disguise subject is required at index {index}"
            )
        if not isinstance(displayed, str) or not displayed:
            raise ValueError(
                f"Disguise as is required at index {index}"
            )
        if not isinstance(when, str) or not when.strip():
            raise ValueError(
                f"Disguise when is required at index {index}"
            )
        disguise["id"] = str(
            disguise.get("id", f"{subject}:{displayed}")
        )
        disguise["predicate_source"] = when

    trials = _as_sequence(
        definition.get("trials"),
        label="world.trials",
    )
    for index, trial in enumerate(trials):
        giver = trial.get("giver")
        requires = trial.get("requires", {}) or {}
        grants = trial.get("grants", {}) or {}
        if not isinstance(giver, str) or not giver:
            raise ValueError(
                f"Trial giver is required at index {index}"
            )
        if not isinstance(requires, dict):
            raise ValueError(
                f"Trial requires must be a mapping at index {index}"
            )
        if not isinstance(grants, dict) or not grants:
            raise ValueError(
                f"Trial grants must be a non-empty mapping at index {index}"
            )
        unknown_grants = set(grants) - {"fact", "item"}
        if unknown_grants:
            raise ValueError(
                f"Unknown trial grants: {sorted(unknown_grants)}"
            )
        trial["requires"] = requires
        trial["grants"] = grants
        grant_label = ",".join(
            f"{key}:{grants[key]}" for key in sorted(grants)
        )
        trial["id"] = str(
            trial.get("id", f"{giver}:{grant_label}")
        )

    phase_rules = _as_sequence(
        definition.get("phase_rules"),
        label="world.phase_rules",
    )
    phase_rule_ids: list[str] = []
    for index, rule in enumerate(phase_rules):
        rule_id = rule.get("id")
        when = rule.get("when")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(
                f"Phase rule id is required at index {index}"
            )
        if not isinstance(when, str) or not when.strip():
            raise ValueError(
                f"Phase rule when is required: {rule_id}"
            )
        phase_rule_ids.append(rule_id)
        rule["predicate_source"] = when
        rule["enable"] = sorted(
            str(verb) for verb in rule.get("enable", []) or []
        )
        rule["disable"] = sorted(
            str(verb) for verb in rule.get("disable", []) or []
        )
    if len(phase_rule_ids) != len(set(phase_rule_ids)):
        raise ValueError("Phase rule ids must be unique")

    world.effect_library = {
        effect_id: effect_library[effect_id]
        for effect_id in sorted(effect_library)
    }
    world.effects_source = effects_path
    world.disguises = sorted(
        disguises,
        key=lambda value: str(value["id"]),
    )
    world.trials = sorted(
        trials,
        key=lambda value: str(value["id"]),
    )
    world.phase_rules = sorted(
        phase_rules,
        key=lambda value: str(value["id"]),
    )


def bind_phase2(
    world: World,
    predicate_names: set[str],
) -> None:
    """Resolve references and compile Phase 2 predicates after subjects bind."""

    allowed_names = set(predicate_names) | {"target", "planter"}

    for effect_id, effect in sorted(world.effect_library.items()):
        plant = effect["plant"]
        plant_source = plant.get("predicate_source")
        if plant_source is not None:
            plant["predicate"] = compile_predicate(
                str(plant_source),
                allowed_names,
            )

        payoff = effect["payoff"]
        payoff["predicate"] = compile_predicate(
            str(payoff["predicate_source"]),
            allowed_names,
        )

        reveals = plant.get("reveals")
        if reveals is not None:
            if not isinstance(reveals, dict):
                raise ValueError(
                    f"Effect reveals must be a mapping: {effect_id}"
                )
            reveal_target = reveals.get("target")
            if (
                reveal_target is not None
                and str(reveal_target) not in world.subjects
            ):
                raise ValueError(
                    f"Unknown effect reveal target: "
                    f"{effect_id}:{reveal_target}"
                )

    aliases = {
        str(disguise["as"]) for disguise in world.disguises
    }
    collisions = aliases & (
        set(world.subjects)
        | set(world.items)
        | set(world.facts)
        | set(world.zones)
    )
    if collisions:
        raise ValueError(
            f"Disguise names collide with world identifiers: "
            f"{sorted(collisions)}"
        )

    for disguise in world.disguises:
        subject_id = str(disguise["subject"])
        if subject_id not in world.subjects:
            raise ValueError(
                f"Unknown disguise subject: {subject_id}"
            )
        disguise["predicate"] = compile_predicate(
            str(disguise["predicate_source"]),
            allowed_names,
        )

    for trial in world.trials:
        giver = str(trial["giver"])
        if giver not in world.subjects:
            raise ValueError(f"Unknown trial giver: {giver}")

        required_item = trial["requires"].get("item")
        if (
            required_item is not None
            and str(required_item) not in world.items
        ):
            raise ValueError(
                f"Unknown trial required item: "
                f"{trial['id']}:{required_item}"
            )

        granted_fact = trial["grants"].get("fact")
        if granted_fact is not None:
            definition = world.facts.get(str(granted_fact))
            if definition is None:
                raise ValueError(
                    f"Unknown trial fact: "
                    f"{trial['id']}:{granted_fact}"
                )
            if definition.get("values") is not None:
                raise ValueError(
                    f"Trial cannot grant valued fact directly: "
                    f"{trial['id']}:{granted_fact}"
                )

        granted_item = trial["grants"].get("item")
        if (
            granted_item is not None
            and str(granted_item) not in world.items
        ):
            raise ValueError(
                f"Unknown trial item: "
                f"{trial['id']}:{granted_item}"
            )

    for rule in world.phase_rules:
        rule["predicate"] = compile_predicate(
            str(rule["predicate_source"]),
            allowed_names,
        )


def disguise_aliases(world: World) -> set[str]:
    return {
        str(disguise["as"]) for disguise in world.disguises
    }


def perceived_name(
    world: World,
    observer_id: str,
    target_id: str,
) -> str:
    """Return the relationship/role name visible to one observer."""

    if target_id not in world.subjects:
        return target_id
    if observer_id == target_id:
        return target_id

    observer = world.subjects.get(observer_id)
    target = world.subjects[target_id]
    if observer is None:
        return target_id

    belief = observer.beliefs_about.get(target_id)
    if belief is not None and belief.identity_seen:
        return target_id
    return target.identity_displayed


def expose_identity(
    world: World,
    observer: Subject,
    target: Subject,
) -> bool:
    """Reveal a disguised target and switch the observer to true relations."""

    from engine.subject import BeliefAbout

    belief = observer.beliefs_about.setdefault(
        target.id,
        BeliefAbout(
            base_estimate=world.default_strength_prior
        ),
    )
    was_seen = belief.identity_seen
    displayed = target.identity_displayed
    disguised = displayed != target.id
    belief.identity_seen = True

    if not disguised or was_seen:
        return False

    world.relations.discard(observer.id, displayed)
    return True


def _predicate_namespace(
    world: World,
    planter: Subject,
    target_id: str,
    *,
    turn: int,
    day: int,
) -> Any:
    present = world.present_subjects(planter.zone)
    return world.namespace(
        planter,
        present,
        turn=turn,
        day=day,
        bindings={
            "planter": planter.id,
            "target": target_id,
        },
    )


def _predicate_matches(
    world: World,
    predicate: Predicate,
    planter: Subject,
    target_id: str,
    *,
    turn: int,
    day: int,
) -> bool:
    return predicate.evaluate(
        _predicate_namespace(
            world,
            planter,
            target_id,
            turn=turn,
            day=day,
        )
    )


def dedicated_plant_options(
    world: World,
    subject: Subject,
    *,
    turn: int,
    day: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for effect_id, effect in sorted(world.effect_library.items()):
        plant = effect["plant"]
        if plant["verb"] != "plant":
            continue
        predicate = plant.get("predicate")
        if (
            predicate is not None
            and not _predicate_matches(
                world,
                predicate,
                subject,
                subject.id,
                turn=turn,
                day=day,
            )
        ):
            continue
        if any(
            pending["library_id"] == effect_id
            and pending["target"] == subject.id
            for pending in world.pending_effects
        ):
            continue
        result.append(effect)
    return result


def ready_chosen_effects(
    world: World,
    subject: Subject,
    *,
    turn: int,
    day: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for pending in sorted(
        world.pending_effects,
        key=lambda value: str(value["id"]),
    ):
        if (
            pending["planted_by"] != subject.id
            or pending["mode"] != "chosen"
            or pending["resolved"]
        ):
            continue
        if _predicate_matches(
            world,
            pending["condition"],
            subject,
            str(pending["target"]),
            turn=turn,
            day=day,
        ):
            result.append(pending)
    return result


def ready_auto_effects(
    world: World,
    *,
    turn: int,
    day: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for pending in sorted(
        world.pending_effects,
        key=lambda value: str(value["id"]),
    ):
        if pending["mode"] != "auto" or pending["resolved"]:
            continue
        planter = world.subjects.get(str(pending["planted_by"]))
        if planter is None:
            continue
        if _predicate_matches(
            world,
            pending["condition"],
            planter,
            str(pending["target"]),
            turn=turn,
            day=day,
        ):
            result.append(pending)
    return result


def _plant_target(
    actor: Subject,
    action: Action,
) -> str:
    target = action.meta.get("target")
    if isinstance(target, str):
        return target
    return actor.id


def _matches_reveals(
    action: Action,
    details: dict[str, Any],
    reveals: dict[str, Any],
) -> bool:
    expected_target = reveals.get("target")
    if (
        expected_target is not None
        and str(action.meta.get("target", ""))
        != str(expected_target)
    ):
        return False

    expected_source = reveals.get(
        "source",
        reveals.get("modifier"),
    )
    if expected_source is not None:
        revealed = {
            str(value)
            for value in details.get("revealed", []) or []
        }
        if str(expected_source) not in revealed:
            return False

    expected_fact = reveals.get("fact")
    if expected_fact is not None:
        actual = details.get("fact", details.get("topic"))
        if str(actual) != str(expected_fact):
            return False
    return True


def _matches_plant(
    world: World,
    actor: Subject,
    action: Action,
    details: dict[str, Any],
    effect: dict[str, Any],
) -> bool:
    plant = effect["plant"]
    if action.verb != plant["verb"]:
        return False

    if action.verb == "plant":
        return action.meta.get("effect_id") == effect["id"]

    expected_item = plant.get("item")
    if (
        expected_item is not None
        and str(action.meta.get("item", ""))
        != str(expected_item)
    ):
        return False

    expected_zone = plant.get("zone")
    if (
        expected_zone is not None
        and actor.zone != str(expected_zone)
    ):
        return False

    expected_topic = plant.get("topic")
    if expected_topic is not None:
        actual_topic = details.get(
            "topic",
            action.meta.get("topic"),
        )
        if str(actual_topic) != str(expected_topic):
            return False

    raw_roles = plant.get("to_role")
    if raw_roles is not None:
        roles = (
            {str(raw_roles)}
            if isinstance(raw_roles, str)
            else {str(value) for value in raw_roles}
        )
        target_id = action.meta.get("target")
        target = (
            world.subjects.get(target_id)
            if isinstance(target_id, str)
            else None
        )
        if target is None:
            return False
        if world.target_role(actor, target) not in roles:
            return False

    reveals = plant.get("reveals")
    if reveals is not None:
        if not isinstance(reveals, dict):
            return False
        if not _matches_reveals(action, details, reveals):
            return False

    return True


def plant_effect(
    world: World,
    effect: dict[str, Any],
    planter: Subject,
    target_id: str,
    *,
    turn: int,
) -> dict[str, Any] | None:
    effect_id = str(effect["id"])
    if any(
        pending["library_id"] == effect_id
        and pending["target"] == target_id
        for pending in world.pending_effects
    ):
        return None

    payoff = effect["payoff"]
    pending = {
        "id": f"{effect_id}:{target_id}",
        "library_id": effect_id,
        "planted_by": planter.id,
        "planted_turn": turn,
        "target": target_id,
        "condition": payoff["predicate"],
        "description": str(payoff.get("description", "")),
        "effect": deepcopy(payoff["effect"]),
        "mode": str(payoff["mode"]),
        "resolved": False,
        "resolved_turn": None,
    }
    world.pending_effects.append(pending)
    world.pending_effects.sort(
        key=lambda value: str(value["id"])
    )
    return pending


def plant_from_action(
    world: World,
    actor: Subject,
    action: Action,
    result: str,
    details: dict[str, Any],
    *,
    turn: int,
) -> list[dict[str, Any]]:
    if result in {"invalid", "ignored"}:
        return []

    markers: list[dict[str, Any]] = []
    target_id = _plant_target(actor, action)
    for effect_id, effect in sorted(world.effect_library.items()):
        if not _matches_plant(
            world,
            actor,
            action,
            details,
            effect,
        ):
            continue
        pending = plant_effect(
            world,
            effect,
            actor,
            target_id,
            turn=turn,
        )
        if pending is None:
            continue
        markers.append(
            {
                "verb": "planted",
                "subject": actor.id,
                "details": {
                    "effect_id": pending["id"],
                    "library_id": effect_id,
                    "target": target_id,
                    "mode": pending["mode"],
                },
            }
        )
    return markers


def _effect_reference(
    pending: dict[str, Any],
    value: Any,
) -> str:
    if value in {"target", "$target"}:
        return str(pending["target"])
    if value in {
        "planter",
        "$planter",
        "self",
        "$self",
    }:
        return str(pending["planted_by"])
    return str(value)


def apply_effect(
    world: World,
    pending: dict[str, Any],
    *,
    turn: int,
) -> dict[str, Any]:
    """Apply one pending effect without consuming random numbers."""

    if pending["resolved"]:
        raise ValueError(
            f"Effect is already resolved: {pending['id']}"
        )

    payload = pending["effect"]
    kind = next(iter(payload))
    body = payload[kind]
    applied: dict[str, Any] = {"kind": kind}

    if kind == "modifier":
        from engine.subject import Modifier

        target_id = _effect_reference(
            pending,
            body["target"],
        )
        target = world.subjects[target_id]
        source = _effect_reference(
            pending,
            body["source"],
        )
        modifier_id = str(
            body.get("id", f"effect:{pending['id']}")
        )
        if not any(
            modifier.id == modifier_id
            for modifier in target.modifiers
        ):
            target.modifiers.append(
                Modifier(
                    id=modifier_id,
                    source=source,
                    value=float(body["value"]),
                    kind=str(body["kind"]),
                    visible=bool(body.get("visible", True)),
                    active=bool(body.get("active", True)),
                    lethal=bool(body.get("lethal", False)),
                    lethal_chance=float(
                        body.get("lethal_chance", 0.0)
                    ),
                )
            )
            target.modifiers.sort(
                key=lambda modifier: modifier.id
            )
        applied.update(
            {
                "target": target_id,
                "source": source,
                "modifier": modifier_id,
            }
        )

    elif kind == "neutralize":
        target_id = _effect_reference(
            pending,
            body["target"],
        )
        source = _effect_reference(
            pending,
            body["source"],
        )
        target = world.subjects[target_id]
        present = world.present_subjects(target.zone)
        matching = sorted(
            (
                modifier
                for modifier in target.all_modifiers(
                    world,
                    present,
                )
                if modifier.active and modifier.source == source
            ),
            key=lambda modifier: (modifier.id, modifier.kind),
        )
        for modifier in matching:
            modifier.active = False
            if not any(
                existing is modifier
                for existing in target.modifiers
            ):
                target.modifiers.append(modifier)
        target.modifiers.sort(
            key=lambda modifier: modifier.id
        )
        applied.update(
            {
                "target": target_id,
                "source": source,
                "count": len(matching),
            }
        )

    elif kind == "stance":
        observer = _effect_reference(pending, body["a"])
        target = _effect_reference(pending, body["b"])
        delta = float(body["delta"])
        world.relations.change(
            observer,
            target,
            affinity=delta,
        )
        applied.update(
            {
                "a": observer,
                "b": target,
                "delta": delta,
            }
        )

    elif kind == "reputation":
        target_id = _effect_reference(
            pending,
            body["target"],
        )
        target = world.subjects[target_id]
        delta = float(body["delta"])
        target.reputation = round(
            target.reputation + delta,
            4,
        )
        applied.update(
            {
                "target": target_id,
                "delta": delta,
            }
        )

    elif kind == "enable_verb":
        target_id = _effect_reference(
            pending,
            body.get("target", "$planter"),
        )
        target = world.subjects[target_id]
        verb = str(body["verb"])
        target.verbs.add(verb)
        applied.update(
            {
                "target": target_id,
                "verb": verb,
                "source": (
                    _effect_reference(pending, body["source"])
                    if body.get("source") is not None
                    else None
                ),
            }
        )

    else:
        raise ValueError(f"Unknown effect kind: {kind}")

    pending["resolved"] = True
    pending["resolved_turn"] = turn
    return {
        "effect_id": pending["id"],
        "library_id": pending["library_id"],
        "mode": pending["mode"],
        "description": pending["description"],
        "applied": applied,
    }


def dangling_effect_count(world: World) -> int:
    return sum(
        1
        for pending in world.pending_effects
        if pending["mode"] == "chosen"
        and not pending["resolved"]
    )


def available_verbs(
    world: World,
    subject: Subject,
    *,
    turn: int,
    day: int,
) -> set[str]:
    """Apply matching phase-rule allowlists and denylists."""

    if not world.phase_rules:
        return set(subject.verbs)

    present = world.present_subjects(subject.zone)
    namespace = world.namespace(
        subject,
        present,
        turn=turn,
        day=day,
    )
    matching = [
        rule
        for rule in world.phase_rules
        if rule["predicate"].evaluate(namespace)
    ]
    if not matching:
        return set(subject.verbs)

    enable_lists = [
        set(rule["enable"])
        for rule in matching
        if rule["enable"]
    ]
    disabled = {
        verb
        for rule in matching
        for verb in rule["disable"]
    }

    available = set(subject.verbs)
    if enable_lists:
        enabled = set().union(*enable_lists)
        available.intersection_update(enabled)
    available.difference_update(disabled)
    return available


def phase2_configured(world: World) -> bool:
    return any(
        (
            world.effect_library,
            world.disguises,
            world.trials,
            world.phase_rules,
        )
    )


def disguise_options(
    world: World,
    subject: Subject,
    *,
    turn: int,
    day: int,
) -> list[dict[str, Any]]:
    present = world.present_subjects(subject.zone)
    namespace = world.namespace(
        subject,
        present,
        turn=turn,
        day=day,
    )
    return [
        disguise
        for disguise in world.disguises
        if disguise["subject"] == subject.id
        and disguise["as"] != subject.identity_displayed
        and disguise["predicate"].evaluate(namespace)
    ]


def _trial_phase(trial: dict[str, Any]) -> str:
    return f"trial:{trial['id']}"


def trial_options(
    world: World,
    subject: Subject,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    present_ids = {
        target.id
        for target in world.present_subjects(
            subject.zone,
            include_downed=False,
        )
    }
    for trial in world.trials:
        giver = str(trial["giver"])
        if (
            giver == subject.id
            or giver not in present_ids
            or _trial_phase(trial) in subject.phase
        ):
            continue

        requires = trial["requires"]
        threshold = float(requires.get("stance", -1.0))
        if world.relations.stance(giver, subject.id) < threshold:
            continue

        required_item = requires.get("item")
        if (
            required_item is not None
            and not subject.has_item(str(required_item))
        ):
            continue
        result.append(trial)
    return result


def mark_trial_completed(
    subject: Subject,
    trial: dict[str, Any],
) -> None:
    subject.phase.add(_trial_phase(trial))


def grand_gesture_fact(
    world: World,
    subject: Subject,
    target: Subject,
) -> str | None:
    facts = [
        fact_id
        for fact_id, definition in sorted(world.facts.items())
        if str(definition.get("secret_of", "")) == target.id
        and fact_id in subject.knowledge
    ]
    return facts[0] if facts else None


def grand_gesture_asset(
    world: World,
    subject: Subject,
) -> str | None:
    assets = [
        item
        for item in sorted(subject.inventory)
        if subject.inventory[item] > 0
        and item not in world.objectives
        and not world.items[item].get("vehicle", False)
        and not world.items[item].get("keepsake", False)
    ]
    if not assets:
        return None

    def rank(item: str) -> tuple[int, float, str]:
        modifier = world.items[item].get("modifier")
        if not isinstance(modifier, dict):
            return (1, 0.0, item)
        return (
            0,
            -float(modifier.get("value", 0.0)),
            item,
        )

    return sorted(assets, key=rank)[0]