"""Template-driven deterministic action classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.actions import Action
    from engine.subject import Subject
    from engine.world import World


@dataclass(frozen=True)
class Classification:
    category: str | None
    subtype: str
    risk_class: str
    stance_sign: int
    target_role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subtype": self.subtype,
            "risk_class": self.risk_class,
            "stance_sign": self.stance_sign,
            "target_role": self.target_role,
        }


def _node_matches(node: Mapping[str, Any], action: Action) -> bool:
    condition = node.get("when")
    if condition is None:
        return True
    if condition == "gather":
        return bool(action.meta.get("gather", False))
    if condition == "crossing":
        return bool(action.meta.get("crossing", False))
    return False


def _target_id(action: Action) -> str | None:
    target = action.meta.get("target")
    if isinstance(target, str):
        return target
    if action.verb in {
        "concede",
        "confront",
        "fight",
        "give_item",
        "mislead",
        "negotiate",
        "neutralize",
        "observe",
        "persuade",
        "pledge",
        "rescue",
        "sabotage",
        "share_knowledge",
    }:
        if action.args and isinstance(action.args[0], str):
            return action.args[0]
    return None


def _is_hostile(
    subject: Subject,
    target_id: str,
    world: World,
) -> bool:
    target = world.subjects.get(target_id)
    if target is None:
        return False
    return world.target_role(subject, target) == "hostile"


def _target_role(
    action: Action,
    subject: Subject,
    world: World,
) -> str:
    target_id = _target_id(action)
    if target_id is None:
        return "none"
    target = world.subjects.get(target_id)
    if target is None:
        return "none"
    return world.target_role(subject, target)


def _hostile_present(
    subject: Subject,
    world: World,
    present: Sequence[Subject],
) -> bool:
    return any(
        peer.id != subject.id
        and peer.vitality != "dead"
        and _is_hostile(subject, peer.id, world)
        for peer in present
    )


def _hostile_at_destination(
    action: Action,
    subject: Subject,
    world: World,
) -> bool:
    destination = action.meta.get("dest")
    if not isinstance(destination, str) or destination not in world.zones:
        return False
    return any(
        peer.id != subject.id
        and _is_hostile(subject, peer.id, world)
        for peer in world.present_subjects(destination)
    )


def classify(
    action: Action,
    subject: Subject,
    world: World,
    present: Sequence[Subject],
    cfg: Mapping[str, Any],
) -> Classification:
    selected: Mapping[str, Any] | None = None
    for node in cfg.get("nodes", []) or []:
        if node.get("verb") != action.verb:
            continue
        if _node_matches(node, action):
            selected = node
            break

    if selected is None:
        category: str | None = None
        subtype = action.verb
        risk = "neutral"
        sign = 0
    else:
        raw_category = selected.get("category")
        category = (
            str(raw_category) if raw_category is not None else None
        )
        subtype = str(selected.get("subtype", action.verb))
        risk = str(selected.get("risk", "neutral"))
        sign = int(selected.get("sign", 0))

    meta_subtype = action.meta.get("subtype")
    if isinstance(meta_subtype, str) and meta_subtype:
        subtype = meta_subtype
    elif bool(action.meta.get("betrayal", False)):
        subtype = "betray"

    if "under_threat" in action.meta:
        under_threat = bool(action.meta["under_threat"])
        if action.verb == "fight" and under_threat:
            risk = "risky"
        elif action.verb in {"rest", "withdraw", "guard"}:
            risk = "safe_under_threat" if under_threat else "neutral"
    elif action.verb == "fight" and action.meta.get(
        "outmatched",
        False,
    ):
        risk = "risky"
    elif action.verb == "move" and _hostile_at_destination(
        action,
        subject,
        world,
    ):
        risk = "risky"
    elif action.verb in {"rest", "withdraw", "guard"}:
        risk = (
            "safe_under_threat"
            if _hostile_present(subject, world, present)
            else "neutral"
        )

    return Classification(
        category=category,
        subtype=subtype,
        risk_class=risk,
        stance_sign=sign,
        target_role=_target_role(action, subject, world),
    )
