"""Opt-in, RNG-free pressure weighting from explicitly actor-known deadlines.

Public/known_fact awareness is an authored disclosure of BOTH the referenced
clock deadline and the current logical clock. No world truth enters reweight().
"""
from copy import deepcopy
from dataclasses import dataclass
import math

PROFILE = {"version": 1, "enabled": True, "profile": "subjective_v1"}
MAX_SOURCES = 32
MAX_BYTES = 65536
FILENAME = "time-pressure.yaml"


def normalize_config(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - set(PROFILE) or type(raw.get("enabled")) is not bool:
        raise ValueError("time_pressure.enabled must be boolean")
    if "version" in raw and (type(raw["version"]) is not int or raw["version"] != 1):
        raise ValueError("Unsupported time pressure version")
    if "profile" in raw and raw["profile"] != PROFILE["profile"]:
        raise ValueError("Unsupported time pressure profile")
    return deepcopy(PROFILE) if raw["enabled"] else None


def _id(value):
    if (type(value) is not str or not value.strip() or len(value) > 120
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Invalid time pressure reference id")
    return value


def _fact_ids(raw):
    if isinstance(raw, dict):
        return set(raw)
    return {f["id"] if isinstance(f, dict) else f for f in raw}


def _parts(world, subjects=None):
    if isinstance(world, dict):
        goals = world.get("goal_rules") or {}
        planning = world.get("planning") or {}
        time = world.get("time") or {}
        people = set(subjects or ())
        return (world.get("protagonist"), goals, planning, world.get("slot_execution"),
                time.get("days"), time.get("slots") or [], world.get("scheduled_events") or [],
                _fact_ids(world.get("facts") or []), people)
    people = set(subjects if subjects is not None else getattr(world, "subjects", {}))
    return (world.protagonist, getattr(getattr(world, "goal_rules", None), "config", {}),
            getattr(getattr(world, "planner", None), "config", {}), world.slot_execution,
            world.days, world.slots, world.scheduled_events, set(world.facts), people)


def validate_runtime(world, subjects=None):
    protagonist, goals, planning, slot, days, slots, events, facts, people = _parts(world, subjects)
    if (slot is not None or goals.get("version") != 1 or planning.get("version") != 1
            or protagonist not in planning.get("actors", []) or protagonist not in people):
        raise ValueError("Time pressure requires ordinary protagonist planning and v1 goals")
    if type(days) is not int or days < 1 or not slots:
        raise ValueError("Time pressure requires the existing logical clock")


def _catalog(world, subjects=None):
    protagonist, goals, planning, slot, days, slots, events, facts, people = _parts(world, subjects)
    definitions = {d["condition"]: d for d in goals.get("definitions", [])}
    event_defs = {e.get("id"): e for e in events}
    return protagonist, definitions, event_defs, facts, people, days, list(slots)


def validate_sources(raw, world, subjects=None):
    if (not isinstance(raw, dict) or set(raw) != {"version", "sources"}
            or type(raw["version"]) is not int or raw["version"] != 1
            or not isinstance(raw["sources"], list) or len(raw["sources"]) > MAX_SOURCES):
        raise ValueError("Invalid time pressure source document")
    protagonist, goals, events, facts, people, days, slots = _catalog(world, subjects)
    seen = set()
    for source in raw["sources"]:
        if (not isinstance(source, dict) or not {"id", "anchor", "response_goal_id", "actor_ids", "awareness"} <= set(source)
                or set(source) - {"id", "anchor", "response_goal_id", "actor_ids", "awareness", "estimate"}):
            raise ValueError("Invalid time pressure source fields")
        ident = _id(source["id"])
        if ident in seen:
            raise ValueError("Duplicate time pressure source id")
        seen.add(ident)
        root = _id(source["response_goal_id"])
        if root not in goals:
            raise ValueError("Unknown time pressure response goal")
        actors = source["actor_ids"]
        if (not isinstance(actors, list) or actors != [protagonist] or protagonist not in people):
            raise ValueError("Time pressure currently supports the registered protagonist only")
        anchor = source["anchor"]
        if not isinstance(anchor, dict) or set(anchor) != {"kind", "id"}:
            raise ValueError("Invalid time pressure anchor")
        _id(anchor["id"])
        if anchor["kind"] == "goal_period":
            if anchor["id"] != root or root not in goals:
                raise ValueError("Goal anchor must match the response goal")
            end = goals[root].get("period", {}).get("end")
            if type(end) is not int or end < 1:
                raise ValueError("Goal deadline is not a logical tick")
        elif anchor["kind"] == "scheduled_event":
            if anchor["id"] not in events:
                raise ValueError("Unknown time pressure scheduled event")
            event = events[anchor["id"]]
            if (type(event.get("day")) is not int or type(days) is not int or not 1 <= event["day"] <= days or event.get("slot") not in (None, "", *slots)):
                raise ValueError("Event deadline is not a logical tick")
        else:
            raise ValueError("Unknown time pressure anchor kind")
        awareness = source["awareness"]
        if not isinstance(awareness, dict):
            raise ValueError("Invalid time pressure awareness")
        if awareness.get("kind") == "public":
            if set(awareness) != {"kind"}:
                raise ValueError("Invalid public awareness")
        elif awareness.get("kind") == "known_fact":
            if set(awareness) != {"kind", "fact_id"} or _id(awareness["fact_id"]) not in facts:
                raise ValueError("Unknown time pressure awareness fact")
        else:
            raise ValueError("Unknown time pressure awareness kind")
        if "estimate" in source:
            estimate = source["estimate"]
            if (not isinstance(estimate, dict) or set(estimate) != {"duration", "fact_id"}
                    or type(estimate["duration"]) is not int or not 1 <= estimate["duration"] <= 10000
                    or _id(estimate["fact_id"]) not in facts):
                raise ValueError("Invalid or unknown actor duration estimate")
    return deepcopy(raw)


def completion_at(turn, steps):
    """Adapt existing planner duration/completion_delay semantics, no clock guess.

    Instant duration=2/completion_delay=1 at turn 6 finishes at turn 7.
    Timed duration=2/completion_delay=2 finishes at 8, as ActionTiming does.
    """
    if type(turn) is not int or turn < 1 or not isinstance(steps, list) or not steps:
        raise ValueError("Invalid pressure completion window")
    elapsed = 0
    for index, step in enumerate(steps):
        duration = step.get("duration")
        delay = step.get("completion_delay", duration - 1 if type(duration) is int else None)
        if (type(duration) is not int or not 1 <= duration <= 10000
                or type(delay) is not int or not 0 <= delay <= duration):
            raise ValueError("Invalid pressure step timing")
        if index == len(steps) - 1:
            return turn + elapsed + delay
        elapsed += duration


@dataclass(frozen=True)
class ActorTimeEvidence:
    actor_id: str
    source_id: str
    goal_id: str
    observed_tick: int
    perceived_now: int
    perceived_deadline: int
    completion_at: int
    duration: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self):
        for name in ("actor_id", "source_id", "goal_id"):
            _id(getattr(self, name))
        if (any(type(v) is not int for v in (self.observed_tick, self.perceived_now,
                self.perceived_deadline, self.completion_at, self.duration))
                or self.observed_tick < 1 or self.perceived_now < 1 or self.duration < 1
                or self.completion_at < self.perceived_now or type(self.evidence_refs) is not tuple
                or not self.evidence_refs):
            raise ValueError("Invalid immutable actor time evidence")
        for ref in self.evidence_refs:
            if (type(ref) is not str or not ref or len(ref) > 256
                    or any(ord(c) < 32 or ord(c) == 127 for c in ref)):
                raise ValueError("Invalid time evidence reference")


def reweight(weighted, actor_id, now, evidence, *, record=False):
    """Only actor evidence and candidate metadata; cannot read objective world."""
    multipliers = []
    reasons = []
    for action, weight in weighted:
        plan = action.meta.get("goal_plan", {})
        root = plan.get("root")
        matches = []
        for packet in evidence:
            if packet.actor_id != actor_id or packet.observed_tick > now:
                raise ValueError("Wrong actor or future pressure evidence")
            if (root != packet.goal_id or plan.get("status") != "planned" or plan.get("assumptions")
                    or not plan.get("steps") or action.verb == "rest"
                    or (plan["steps"][0].get("verb"), tuple(plan["steps"][0].get("args", []))) != (action.verb, action.args)):
                continue
            remaining = packet.perceived_deadline - packet.perceived_now
            slack = packet.perceived_deadline - packet.completion_at
            urgency = min(1.0, max(0.0, 1.0 - slack / packet.duration))
            matches.append((urgency, packet.source_id, packet, remaining, slack))
        matches.sort(key=lambda entry: (-entry[0], entry[1]))
        chosen = matches[0] if matches else None
        multiplier = math.exp(math.log(4.0) * chosen[0]) if chosen else 1.0
        multipliers.append(multiplier)
        if record:
            reasons.append({"verb": action.verb, "args": list(action.args), "multiplier": multiplier,
                            "source_id": chosen[1] if chosen else None,
                            "reason": "known_goal_progress" if chosen else "estimate_or_relation_unknown",
                            "perceived_remaining": chosen[3] if chosen else None,
                            "subjective_slack": chosen[4] if chosen else None,
                            "estimated_duration": chosen[2].duration if chosen else None,
                            "observed_tick": chosen[2].observed_tick if chosen else None,
                            "perceived_now": chosen[2].perceived_now if chosen else None,
                            "perceived_deadline": chosen[2].perceived_deadline if chosen else None,
                            "completion_at": chosen[2].completion_at if chosen else None,
                            "evidence_refs": list(chosen[2].evidence_refs) if chosen else []})
    if not any(m != 1.0 for m in multipliers):
        return weighted, ({"applied": False, "candidates": reasons} if record else None)
    weights = [float(w) for _, w in weighted]
    if any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("Pressure weights must be finite and nonnegative")
    maximum = max(weights, default=0)
    if maximum <= 0:
        return weighted, ({"applied": False, "candidates": reasons} if record else None)
    result = [(action, (weight / maximum) * multiplier)
              for (action, _), weight, multiplier in zip(weighted, weights, multipliers)]
    if record:
        for row, old, (_, new) in zip(reasons, weights, result):
            row.update(original_weight=old, sampling_weight=new)
    return result, ({"applied": True, "common_divisor": maximum, "candidates": reasons} if record else None)


class TimePressure:
    def __init__(self, config, sources, world, subjects):
        self.config = normalize_config(config)
        if self.config is None:
            raise ValueError("Do not construct pressure runtime when disabled")
        validate_runtime(world, subjects)
        self.sources = validate_sources(sources, world, subjects)["sources"]
        if not self.sources:
            raise ValueError("Enabled time pressure needs at least one source")

    def _deadline(self, world, source):
        anchor = source["anchor"]
        if anchor["kind"] == "goal_period":
            return world.goal_rules.definitions[anchor["id"]]["period"]["end"]
        event = next(e for e in world.scheduled_events if e["id"] == anchor["id"])
        # Scheduled events run before choices: completing at the event's own
        # choice tick would already be late, so the last earlier tick is due.
        index = world.slots.index(event["slot"]) if event.get("slot") else 0
        return (event["day"] - 1) * len(world.slots) + index

    def apply(self, world, actor, weighted, turn, *, record=False):
        if actor.id != world.protagonist:
            return weighted, None, None
        packets = []
        known = []
        for source in self.sources:
            awareness = source["awareness"]
            if awareness["kind"] == "known_fact" and awareness["fact_id"] not in actor.knowledge:
                continue
            root = source["response_goal_id"]
            # Actor-scoped only. Do not consult world objective outcome records.
            state = world.goal_rules.actor_records.get(actor.id, {}).get(root, {})
            period = world.goal_rules.definitions[root]["period"]
            if state.get("status") in {"achieved", "failed"} or not period["start"] <= turn <= period["end"]:
                continue
            known.append(source)
            estimate = source.get("estimate")
            if estimate is None or estimate["fact_id"] not in actor.knowledge:
                continue  # Planner ETA is NOT proof the actor knows a duration.
            duration = estimate["duration"]
            finish = completion_at(turn, [{"duration": duration}])
            refs = (("public:" + source["id"],) if awareness["kind"] == "public"
                    else ("fact:" + awareness["fact_id"],)) + ("estimate:" + estimate["fact_id"],)
            packets.append(ActorTimeEvidence(actor.id, source["id"], root, turn, turn,
                self._deadline(world, source), finish, duration, refs))
        result, actor_log = reweight(weighted, actor.id, turn, tuple(packets), record=record)
        if record:
            actor_log["known_sources"] = [s["id"] for s in known]
            actor_log["source_reasons"] = [{"source_id": s["id"],
                "reason": ("estimate_unknown" if "estimate" not in s else
                           "estimate_not_known" if s["estimate"]["fact_id"] not in actor.knowledge else "actor_estimate_known")}
                for s in known]
        return result, actor_log, None

    def review(self, world, selected, turn):
        """Author review AFTER selection; never an input to actor weighting."""
        plan = selected.meta.get("goal_plan", {})
        forecast = completion_at(turn, plan["steps"]) if plan.get("steps") else None
        return {"unit": "logical_step", "now_tick": turn, "selected_goal": plan.get("root"),
                "planner_duration_forecast_ticks": sum(step["duration"] for step in plan.get("steps", [])) if plan.get("steps") else None,
                "planner_duration_forecast_completion_at": forecast,
                "forecast_is_actual_outcome": False,
                "sources": [{"source_id": s["id"], "deadline_tick": self._deadline(world, s),
                    "remaining_ticks": self._deadline(world, s) - turn,
                    "planner_duration_forecast_slack": self._deadline(world, s) - forecast
                        if forecast is not None and s["response_goal_id"] == plan.get("root") else None,
                    "objective_completion_at": None, "objective_slack": None,
                    "reason": "actual_response_outcome_not_known_at_selection"} for s in self.sources]}


def load_sources(project, world, subjects):
    from pathlib import Path
    import yaml
    path = Path(project) / FILENAME
    if path.is_symlink() or path.resolve().parent != Path(project).resolve():
        raise ValueError("Unsafe time pressure source path")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("Time pressure source document too large")
    return validate_sources(yaml.safe_load(data), world, subjects)
