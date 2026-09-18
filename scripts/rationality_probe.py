"""WB-JEV-001 Stage 0/1: does an Ollama model's logprob readout rank rational
moves above irrational ones, given only the protagonist's beliefs (never the
world's ground truth)? Stage 0 handed this a hand-built state-text renderer;
Stage 1's first cut (design by Fable, 2026-09-18) replaced it with a
concrete-state renderer, then a same-day revision (18:10) replaced *that*
with gapengine/knowledge_text.py's coarse *situation-class* renderer: seed
1-8 measurements showed the concrete renderer's distinct-context rate
stuck at ~70% (never converging), because every different companion roster
or exact item count minted a new context. situation()/render_situation()
collapse those into a handful of Japanese labels (see the module's
docstring); describe_candidate_coarse() likewise replaces companion names
in candidate descriptions with their role and drops decimals.

Mode A scores every real decision point of one deterministic momotaro run
(neutral genome, annotate-only policy -- same recipe as
scripts/random_baseline.py) against its full candidate list. Mode B scores
three hand-written synthetic prompts that isolate one thing at a time: does
adding "you can buy a gun" or "the antagonist has an estranged brother" to
the world raise the probability of the matching new candidate.

``--stats`` never calls the model: it replays seeds 1..12 and reports, seed
by seed, how many new situations and new (situation, candidate) pairs show
up on top of every earlier seed's tally.

``--bench`` measures whether Ollama's KV cache actually amortizes a shared
prompt prefix: it fires 15 consecutive noul calls against one context (only
the trailing candidate line differs) and reports call #1's latency against
the mean of calls #2-15.

Engine/gapengine/templates/projects are read-only here. Output (including
the throwaway layers.jsonl from the Mode A run) goes under
--out/<model with ':' -> '_'>/, never under the Drive-mounted repo.

Stage 2 (design by Fable, 2026-09-18) moved the Ollama call plumbing
(``_ollama_call``/``_p_yes``/the choice-mode label and chunking logic) into
``gapengine/rationality.py``, where ``Rationality``'s ``OllamaLogprobJudge``
now shares it; this module imports those names instead of redefining them.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from engine.sim import Simulation
from engine.world import World
from gapengine import gpu_guard
from gapengine.evolve import _load_subjects
from gapengine.genome import Genome
from gapengine.knowledge_text import (
    context_key,
    describe_candidate_coarse,
    load_common_knowledge,
    load_key_items,
    map_line as knowledge_map_line,
    recipes_line as knowledge_recipes_line,
    render_situation,
    situation,
)
from gapengine.ollama import DEFAULT_BASE_URL, build_request
from gapengine.policy import Policy
from gapengine.rationality import (
    CHOICE_QUESTION,
    LABELS,
    QUESTION,
    _label_masses,
    _measure_top_logprobs_limit,
    _normalize,
    _ollama_call,
    _p_yes,
)

# ---------------------------------------------------------------------------
# Action classes for the report's "行動クラス別の相対値" table (plan §2.3)
# ---------------------------------------------------------------------------

CLASS_ORDER = (
    "海へ移動",
    "森へ移動",
    "道中へ移動",
    "調べる",
    "造船術を伝える",
    "説得",
    "きびだんごを渡す",
    "雑談",
    "仲間と戦う",
    "誤った情報へ誘導",
    "変装",
    "休む",
    "退く",
)


def _action_class(action: Any, subject: Any, world: World) -> str | None:
    verb = action.verb
    args = action.args
    if verb == "move" and args:
        return {"海": "海へ移動", "森": "森へ移動", "道中": "道中へ移動"}.get(str(args[0]))
    if verb == "investigate":
        return "調べる"
    if verb == "share_knowledge" and len(args) >= 2:
        return {"造船術": "造船術を伝える", "雑談": "雑談"}.get(str(args[1]))
    if verb == "persuade":
        return "説得"
    if verb == "give_item" and len(args) >= 2 and args[1] == "きびだんご":
        return "きびだんごを渡す"
    if verb == "fight" and args:
        target = world.subjects.get(str(args[0]))
        if target is not None and world.target_role(subject, target) == "ally":
            return "仲間と戦う"
        return None
    if verb == "mislead":
        return "誤った情報へ誘導"
    if verb == "disguise":
        return "変装"
    if verb == "rest":
        return "休む"
    if verb == "withdraw":
        return "退く"
    return None


# ---------------------------------------------------------------------------
# Mode A: recording policy wrapper + run
# ---------------------------------------------------------------------------


class _RecordingPolicy:
    """Delegates every call to the wrapped neutral Policy unchanged, and
    renders/records the decision point *before* delegating so the captured
    situation reflects subject/world at decision time, not after later
    mutation (Subject/World objects are mutated in place as the run
    continues). Consumes no randomness itself.

    ``recipe_lines``/``map_line`` are computed once by the caller (they are
    static for a given subject/world -- see gapengine.knowledge_text) and
    just threaded through every render."""

    def __init__(
        self,
        inner: Policy,
        *,
        common_knowledge: list[str],
        key_items: list[str],
        recipe_lines: str,
        map_line: str,
    ) -> None:
        self._inner = inner
        self._common_knowledge = common_knowledge
        self._key_items = key_items
        self._recipe_lines = recipe_lines
        self._map_line = map_line
        self.decisions: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def reweight(self, subject, world, present, weighted, *, turn=0, day=0):
        sit = situation(subject, world, present, key_items=self._key_items)
        state_text = render_situation(
            sit,
            world,
            common_knowledge=self._common_knowledge,
            recipe_lines=self._recipe_lines,
            map_line=self._map_line,
        )
        self.decisions.append(
            {
                "point_id": str(turn),
                "turn": turn,
                "day": day,
                "zone": subject.zone,
                "state_text": state_text,
                "candidates": [
                    {
                        "desc": describe_candidate_coarse(action, subject, world, present),
                        "verb": action.verb,
                        "args": tuple(str(value) for value in action.args),
                        "class": _action_class(action, subject, world),
                    }
                    for action, _weight in weighted
                ],
                "antagonist_present": any(
                    peer.id == world.antagonist for peer in present
                ),
                "phase_empty": not subject.phase,
                "chosen": None,
            }
        )
        return self._inner.reweight(subject, world, present, weighted, turn=turn, day=day)

    def record(self, subject, action):
        if self.decisions:
            self.decisions[-1]["chosen"] = (
                action.verb,
                tuple(str(value) for value in action.args),
            )
        return self._inner.record(subject, action)


def _run_mode_a(
    *,
    project: Path,
    template: Path,
    seed: int,
    turns: int | None,
    run_out: Path,
) -> list[dict[str, Any]]:
    action_graph_path = template / "action_graph.yaml"
    action_graph_path = action_graph_path if action_graph_path.is_file() else None
    world = World.from_yaml(project / "world.yaml", action_graph_path=action_graph_path)
    subjects = _load_subjects(project / "subjects")
    # Bind now (Simulation() below re-binds harmlessly -- see
    # tests/test_gapengine.py's load_fixture()) so the protagonist's static
    # recipe/map lines see the fully-resolved world.
    world.bind_subjects(subjects)
    protagonist = subjects[world.protagonist]
    recipe_lines = knowledge_recipes_line(protagonist, world)
    map_line = knowledge_map_line(world)
    action_cfg = (
        yaml.safe_load(action_graph_path.read_text(encoding="utf-8"))
        if action_graph_path is not None
        else {"nodes": [], "edges": []}
    )
    policy = Policy(Genome.neutral(), precedent=None, cfg=action_cfg, annotate_only=True)
    recorder = _RecordingPolicy(
        policy,
        common_knowledge=load_common_knowledge(template),
        key_items=load_key_items(template),
        recipe_lines=recipe_lines,
        map_line=map_line,
    )
    Simulation(
        seed,
        world,
        subjects,
        run_out,
        policies={world.protagonist: recorder},
    ).run()

    decisions = recorder.decisions if turns is None else recorder.decisions[:turns]
    points = []
    for entry in decisions:
        # Group raw candidates by their coarse description: with companion
        # names replaced by role, "give kibidango to whichever ally" collapses
        # to one description regardless of how many allies are present. Only
        # this deduped list is ever handed to the model (see _score_points*);
        # raw_candidates below stays one-entry-per-raw-action for the
        # verb/args-based diagnostics in _mode_a_checks/_class_relative_values.
        order: list[str] = []
        reps: dict[str, dict[str, Any]] = {}
        for candidate in entry["candidates"]:
            desc = candidate["desc"]
            if desc not in reps:
                reps[desc] = dict(candidate, chosen=False)
                order.append(desc)
            if (candidate["verb"], candidate["args"]) == entry["chosen"]:
                reps[desc]["chosen"] = True
        raw_candidates = [reps[desc] for desc in order]
        candidates = [(desc, reps[desc]["chosen"]) for desc in order]
        points.append(
            {
                "mode": "A",
                "point_id": entry["point_id"],
                "label": f"turn={entry['turn']} day={entry['day']} zone={entry['zone']}",
                "state_text": entry["state_text"],
                "candidates": candidates,
                "raw_candidates": raw_candidates,
                "antagonist_present": entry["antagonist_present"],
                "phase_empty": entry["phase_empty"],
            }
        )
    return points


# ---------------------------------------------------------------------------
# Mode B: synthetic probes (plan §3.2)
# ---------------------------------------------------------------------------


def _mode_b_points() -> list[dict[str, Any]]:
    p1_state = (
        "目的: 鬼ヶ島の宝物を村へ持ち帰る\n"
        "所持: きびだんご3\n"
        "同席: おじいさん、おばあさん、犬\n"
        "通過段階: なし"
    )
    p1_candidates = [
        "変装する",
        "犬にきびだんごを与えて仲間にする",
        "海へ行って船の材料を集める",
        "鍛錬する",
        "休む",
        "鬼ヶ島へ向かう（船なし）",
    ]
    p2_state = (
        p1_state
        + "\n所持金: 50文\n"
        + "追加情報: 村の店で鉄砲が売られており、鉄砲があれば鬼と互角以上に戦える"
    )
    p2_candidates = [*p1_candidates, "店で鉄砲を買う"]
    p3_state = p2_state + "\n追加情報2: 鬼には生き別れの弟がおり、村に住んでいると聞いた"
    p3_candidates = [*p2_candidates, "鬼の弟を探して話を聞く"]
    return [
        {
            "mode": "B",
            "point_id": "P1",
            "label": "P1 村の出発直後",
            "state_text": p1_state,
            "candidates": [(text, None) for text in p1_candidates],
        },
        {
            "mode": "B",
            "point_id": "P2",
            "label": "P2 世界拡張（鉄砲）",
            "state_text": p2_state,
            "candidates": [(text, None) for text in p2_candidates],
        },
        {
            "mode": "B",
            "point_id": "P3",
            "label": "P3 世界拡張（弟）",
            "state_text": p3_state,
            "candidates": [(text, None) for text in p3_candidates],
        },
    ]


# ---------------------------------------------------------------------------
# Scoring (idempotent: skip (method, pair_key) rows already in probe.jsonl --
# revised plan §"context_key(text) は維持": the rationality table's key is
# context_key(render_situation(...) + "\n候補: " + describe_candidate_coarse(...)),
# i.e. one hash per (situation, candidate) pair rather than a separate
# situation digest plus a raw candidate string.)
# ---------------------------------------------------------------------------


def _pair_key(state_text: str, desc: str) -> str:
    return context_key(f"{state_text}\n候補: {desc}")


def _read_existing(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Rows lacking a "method" (Stage 0's original schema had none -- every
    call was a yes/no "noul" call) default to "noul" instead of raising a
    KeyError. Rows lacking a "pair_key" (any pre-revision schema) can't be
    addressed by this scheme at all and are skipped, not crashed on."""

    existing: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return existing
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            pair_key = row.get("pair_key")
            if pair_key is None:
                continue
            existing[(row.get("method", "noul"), pair_key)] = row
    return existing


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _score_points(
    points: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    timeout: float,
    probe_path: Path,
    stats: dict[str, Any],
) -> dict[tuple[str, str], float | None]:
    """``--method noul``: one yes/no call per candidate."""

    existing = _read_existing(probe_path)
    scores: dict[tuple[str, str], float | None] = {}
    for point in points:
        for desc, chosen in point["candidates"]:
            pair = _pair_key(point["state_text"], desc)
            key = ("noul", pair)
            if key in existing:
                scores[(point["point_id"], desc)] = existing[key]["p_yes"]
                continue

            prompt = f"{point['state_text']}\n\n候補: {desc}\n\n{QUESTION}"
            stats["calls"] += 1
            started = time.monotonic()
            try:
                data = _ollama_call(model, prompt, base_url=base_url, timeout=timeout)
            except (OSError, urllib.error.URLError, ValueError) as error:
                stats["errors"] += 1
                print(
                    f"WARN: call failed, will retry next run: "
                    f"{point['point_id']} / {desc}: {error}",
                    file=sys.stderr,
                )
                continue
            stats["elapsed"] += time.monotonic() - started

            p_yes, top = _p_yes(data)
            if p_yes is None:
                stats["missing"] += 1
            row = {
                "mode": point["mode"],
                "method": "noul",
                "turn": point["point_id"],
                "pair_key": pair,
                "candidate": desc,
                "p_yes": p_yes,
                "raw_top_logprobs": top,
                "chosen": bool(chosen),
            }
            _append_jsonl(probe_path, row)
            existing[key] = row
            scores[(point["point_id"], desc)] = p_yes
    return scores


def _score_points_choice(
    points: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    timeout: float,
    probe_path: Path,
    stats: dict[str, Any],
    chunk_size: int,
) -> dict[tuple[str, str], float | None]:
    """``--method choice``: one call per decision point (or per chunk, when a
    point has more candidates than the server's top_logprobs will return in
    one call -- plan §2.3). Chunk-internal normalization only; cross-chunk
    values are not comparable (noted in the report)."""

    existing = _read_existing(probe_path)
    scores: dict[tuple[str, str], float | None] = {}
    for point in points:
        descs = [desc for desc, _chosen in point["candidates"]]
        chosen_by_desc = dict(point["candidates"])
        pair_of = {desc: _pair_key(point["state_text"], desc) for desc in descs}
        if not descs:
            continue
        if len(descs) == 1:
            desc = descs[0]
            scores[(point["point_id"], desc)] = 1.0
            key = ("choice", pair_of[desc])
            if key not in existing:
                row = {
                    "mode": point["mode"],
                    "method": "choice",
                    "turn": point["point_id"],
                    "pair_key": pair_of[desc],
                    "candidate": desc,
                    "p_choice": 1.0,
                    "chosen": bool(chosen_by_desc.get(desc)),
                }
                _append_jsonl(probe_path, row)
                existing[key] = row
            continue

        for start in range(0, len(descs), chunk_size):
            chunk_descs = descs[start : start + chunk_size]
            keys = [("choice", pair_of[desc]) for desc in chunk_descs]
            if all(key in existing for key in keys):
                for desc, key in zip(chunk_descs, keys):
                    scores[(point["point_id"], desc)] = existing[key]["p_choice"]
                continue

            labels = LABELS[: len(chunk_descs)]
            lines = [point["state_text"], "", "候補:"]
            lines.extend(f"{label}: {desc}" for label, desc in zip(labels, chunk_descs))
            lines.extend(["", CHOICE_QUESTION])
            prompt = "\n".join(lines)

            stats["calls"] += 1
            started = time.monotonic()
            try:
                data = _ollama_call(
                    model,
                    prompt,
                    base_url=base_url,
                    timeout=timeout,
                    top_logprobs=max(len(labels), 10),
                )
            except (OSError, urllib.error.URLError, ValueError) as error:
                stats["errors"] += 1
                print(
                    f"WARN: choice call failed, will retry next run: "
                    f"{point['point_id']} chunk starting at {start}: {error}",
                    file=sys.stderr,
                )
                continue
            stats["elapsed"] += time.monotonic() - started

            probs = _normalize(_label_masses(data, labels))
            if all(value == 0.0 for value in probs.values()):
                stats["missing"] += 1
            for label, desc in zip(labels, chunk_descs):
                p_choice = probs[label]
                row = {
                    "mode": point["mode"],
                    "method": "choice",
                    "turn": point["point_id"],
                    "pair_key": pair_of[desc],
                    "candidate": desc,
                    "p_choice": p_choice,
                    "chosen": bool(chosen_by_desc.get(desc)),
                }
                _append_jsonl(probe_path, row)
                existing[("choice", pair_of[desc])] = row
                scores[(point["point_id"], desc)] = p_choice
    return scores


# ---------------------------------------------------------------------------
# Report (plan §3.3, §4)
# ---------------------------------------------------------------------------


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _judge(condition: bool | None) -> str:
    if condition is None:
        return "N/A（該当候補なし）"
    return "PASS" if condition else "FAIL"


def _mode_b_checks(scores: dict[tuple[str, str], float | None]) -> list[str]:
    disguise = scores.get(("P1", "変装する"))
    give = scores.get(("P1", "犬にきびだんごを与えて仲間にする"))
    boat = scores.get(("P1", "海へ行って船の材料を集める"))
    p1_ok = (
        disguise <= min(give, boat) - 0.3
        if None not in (disguise, give, boat)
        else None
    )

    gun = scores.get(("P2", "店で鉄砲を買う"))
    disguise2 = scores.get(("P2", "変装する"))
    p2_ok = gun >= 0.5 and gun > disguise2 if None not in (gun, disguise2) else None

    brother = scores.get(("P3", "鬼の弟を探して話を聞く"))
    p3_ok = brother >= 0.5 if brother is not None else None

    return [
        f"- P1: p(変装)={_fmt(disguise)} <= min(p(仲間){_fmt(give)}, "
        f"p(船材料){_fmt(boat)}) - 0.3 → {_judge(p1_ok)}",
        f"- P2: p(鉄砲)={_fmt(gun)} >= 0.5 かつ > p(変装){_fmt(disguise2)} "
        f"→ {_judge(p2_ok)}",
        f"- P3: p(弟を探す)={_fmt(brother)} >= 0.5 → {_judge(p3_ok)}",
    ]


def _is_good_a_candidate(verb: str, args: tuple[str, ...]) -> bool:
    if verb == "give_item" and len(args) >= 2:
        return args[0] in ("犬", "猿", "キジ") and args[1] == "きびだんご"
    if verb == "move" and len(args) >= 1:
        return args[0] == "海"
    if verb == "craft" and len(args) >= 1:
        return args[0] == "船"
    return False


def _mode_a_checks(
    points: list[dict[str, Any]],
    scores: dict[tuple[str, str], float | None],
) -> list[str]:
    rest_withdraw: list[float] = []
    disguise_empty_phase: list[float] = []
    per_point_good: list[tuple[str, float | None]] = []

    for point in points:
        pid = point["point_id"]
        best_good = None
        for candidate in point["raw_candidates"]:
            p_yes = scores.get((pid, candidate["desc"]))
            if p_yes is None:
                continue
            verb, args = candidate["verb"], candidate["args"]
            if (
                verb in ("rest", "withdraw")
                and not args
                and not point["antagonist_present"]
            ):
                rest_withdraw.append(p_yes)
            if verb == "disguise" and point["phase_empty"]:
                disguise_empty_phase.append(p_yes)
            if _is_good_a_candidate(verb, args):
                best_good = p_yes if best_good is None else max(best_good, p_yes)
        if best_good is not None:
            per_point_good.append((pid, best_good))

    median_rw = statistics.median(rest_withdraw) if rest_withdraw else None
    check1 = median_rw <= 0.3 if median_rw is not None else None

    disguise_value = (
        statistics.median(disguise_empty_phase) if disguise_empty_phase else None
    )
    check2 = disguise_value <= 0.3 if disguise_value is not None else None

    check3 = (
        all(value >= 0.6 for _pid, value in per_point_good)
        if per_point_good
        else None
    )
    check3_detail = ", ".join(
        f"{pid}={_fmt(value)}" for pid, value in per_point_good
    ) or "該当決定点なし"

    return [
        f"- Mode A 非対峙時の rest/withdraw p_yes 中央値={_fmt(median_rw)} "
        f"(n={len(rest_withdraw)}) <= 0.3 → {_judge(check1)}",
        f"- Mode A 通過段階が空での disguise p_yes 中央値={_fmt(disguise_value)} "
        f"(n={len(disguise_empty_phase)}) <= 0.3 → {_judge(check2)}",
        f"- Mode A 各決定点での give_item/move(海)/craft(船) 最大値: "
        f"{check3_detail} すべて>=0.6 → {_judge(check3)}",
    ]


def _class_relative_values(
    points: list[dict[str, Any]],
    scores: dict[tuple[str, str], float | None],
) -> dict[str, float | None]:
    """value = mean over every occurrence of (candidate p / that decision
    point's mean p) -- plan §2.3's "行動クラス別の相対値" table."""

    ratios: dict[str, list[float]] = {name: [] for name in CLASS_ORDER}
    for point in points:
        pid = point["point_id"]
        p_values = [
            scores.get((pid, candidate["desc"]))
            for candidate in point["raw_candidates"]
        ]
        p_values = [value for value in p_values if value is not None]
        if not p_values:
            continue
        mean_p = statistics.mean(p_values)
        if mean_p <= 0.0:
            continue
        for candidate in point["raw_candidates"]:
            class_name = candidate.get("class")
            if class_name not in ratios:
                continue
            p_value = scores.get((pid, candidate["desc"]))
            if p_value is None:
                continue
            ratios[class_name].append(p_value / mean_p)
    return {
        name: (statistics.mean(values) if values else None)
        for name, values in ratios.items()
    }


def _class_table_lines(relative: dict[str, float | None]) -> list[str]:
    lines = ["| クラス | 相対値 |", "|---|---|"]
    for name in CLASS_ORDER:
        lines.append(f"| {name} | {_fmt(relative.get(name))} |")
    return lines


def _stage1_checks(relative: dict[str, float | None]) -> list[str]:
    low_classes = ("仲間と戦う", "誤った情報へ誘導", "変装")
    low_values = [relative.get(name) for name in low_classes]
    check_low = (
        all(value is not None and value <= 0.6 for value in low_values)
        if all(value is not None for value in low_values)
        else None
    )

    high_classes = ("海へ移動", "造船術を伝える", "調べる")
    high_values = [relative.get(name) for name in high_classes]
    check_high = (
        any(value is not None and value >= 1.5 for value in high_values)
        if any(value is not None for value in high_values)
        else None
    )

    kibidango = relative.get("きびだんごを渡す")
    check_kibidango = kibidango >= 0.81 if kibidango is not None else None

    return [
        f"- 仲間と戦う/誤った情報へ誘導/変装 = "
        f"{', '.join(f'{name}={_fmt(relative.get(name))}' for name in low_classes)} "
        f"すべて<=0.6 → {_judge(check_low)}",
        f"- 海へ移動/造船術を伝える/調べる = "
        f"{', '.join(f'{name}={_fmt(relative.get(name))}' for name in high_classes)} "
        f"いずれか>=1.5 → {_judge(check_high)}",
        f"- きびだんごを渡す={_fmt(kibidango)} >= 0.81（Stage0 v2 基準） → "
        f"{_judge(check_kibidango)}",
    ]


def _write_report(
    path: Path,
    *,
    model: str,
    seed: int,
    turns: int,
    mode_b_points: list[dict[str, Any]],
    mode_a_points: list[dict[str, Any]],
    scores: dict[tuple[str, str], float | None],
    stats: dict[str, Any],
) -> None:
    lines: list[str] = [
        f"# Jev Stage1 rationality probe (noul) — model: {model}",
        "",
        "## 実行情報",
        f"- seed={seed} turns={turns}",
        f"- 呼び出し回数(このプロセスで新規に呼んだ数)={stats['calls']}",
        f"- 欠測(yes/no とも top_logprobs に無かった)={stats['missing']}",
        f"- 通信エラーでスキップ(次回再実行で拾う)={stats['errors']}",
        f"- 所要時間(このプロセスでの新規呼び出し合計)={stats['elapsed']:.1f}秒",
        "",
        "## Mode B",
    ]
    for point in mode_b_points:
        lines.append(f"\n### {point['label']}")
        lines.append("| 候補 | p_yes |")
        lines.append("|---|---|")
        for desc, _chosen in point["candidates"]:
            lines.append(f"| {desc} | {_fmt(scores.get((point['point_id'], desc)))} |")

    lines.append("\n## Mode A（先頭 %d 決定点）" % turns)
    for point in mode_a_points:
        lines.append(f"\n### {point['label']}")
        lines.append("```")
        lines.append(point["state_text"])
        lines.append("```")
        lines.append("| 候補 | p_yes | 実際の選択 |")
        lines.append("|---|---|---|")
        ranked = sorted(
            point["candidates"],
            key=lambda pair: (
                scores.get((point["point_id"], pair[0])) is None,
                -(scores.get((point["point_id"], pair[0])) or 0.0),
            ),
        )
        for desc, chosen in ranked:
            mark = "★" if chosen else ""
            lines.append(
                f"| {desc} | {_fmt(scores.get((point['point_id'], desc)))} | {mark} |"
            )

    lines.append("\n## 合格判定（§4, 機械的判定）")
    lines.extend(_mode_b_checks(scores))
    lines.extend(_mode_a_checks(mode_a_points, scores))

    relative = _class_relative_values(mode_a_points, scores)
    lines.append("\n## 行動クラス別の相対値（p ÷ 決定点平均 の平均）")
    lines.extend(_class_table_lines(relative))
    lines.append("\n## 合格判定（Stage1 §3 条件3）")
    lines.extend(_stage1_checks(relative))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = average_rank
        index = end + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    try:
        return statistics.correlation(_ranks(xs), _ranks(ys))
    except statistics.StatisticsError:
        return None


def _write_choice_report(
    path: Path,
    *,
    model: str,
    seed: int,
    turns: int,
    mode_a_points: list[dict[str, Any]],
    scores: dict[tuple[str, str], float | None],
    stats: dict[str, Any],
    probe_path: Path,
) -> None:
    lines: list[str] = [
        f"# Jev Stage1 rationality probe (choice) — model: {model}",
        "",
        "## 実行情報",
        f"- seed={seed} turns={turns}",
        f"- 実測した top_logprobs 上限={stats.get('measured_top_logprobs_limit')}"
        f"（チャンクサイズ={stats.get('chunk_size')}）",
        f"- 呼び出し回数(このプロセスで新規に呼んだ数)={stats['calls']}",
        f"- 欠測(候補ラベルが1つも top_logprobs に無かったチャンク)={stats['missing']}",
        f"- 通信エラーでスキップ(次回再実行で拾う)={stats['errors']}",
        f"- 所要時間(このプロセスでの新規呼び出し合計)={stats['elapsed']:.1f}秒",
        "",
        "## Mode A（先頭 %d 決定点）" % turns,
    ]
    for point in mode_a_points:
        lines.append(f"\n### {point['label']}")
        lines.append("```")
        lines.append(point["state_text"])
        lines.append("```")
        lines.append("| 候補 | p_choice | 実際の選択 |")
        lines.append("|---|---|---|")
        ranked = sorted(
            point["candidates"],
            key=lambda pair: (
                scores.get((point["point_id"], pair[0])) is None,
                -(scores.get((point["point_id"], pair[0])) or 0.0),
            ),
        )
        for desc, chosen in ranked:
            mark = "★" if chosen else ""
            lines.append(
                f"| {desc} | {_fmt(scores.get((point['point_id'], desc)))} | {mark} |"
            )

    existing = _read_existing(probe_path)
    lines.append(
        "\n## noul との順位相関（Spearman, 決定点ごと。合否ではなく Stage 2 の方式選定材料）"
    )
    correlations: list[float] = []
    for point in mode_a_points:
        pairs = []
        for desc, _chosen in point["candidates"]:
            choice_p = scores.get((point["point_id"], desc))
            noul_row = existing.get(("noul", _pair_key(point["state_text"], desc)))
            if choice_p is None or noul_row is None or noul_row.get("p_yes") is None:
                continue
            pairs.append((noul_row["p_yes"], choice_p))
        if len(pairs) < 2:
            lines.append(f"- {point['label']}: 比較可能な候補が不足（n={len(pairs)}）")
            continue
        noul_values = [pair[0] for pair in pairs]
        choice_values = [pair[1] for pair in pairs]
        rho = _spearman(noul_values, choice_values)
        if rho is not None:
            correlations.append(rho)
        lines.append(f"- {point['label']}: n={len(pairs)} spearman={_fmt(rho)}")
    if correlations:
        lines.append(f"- 決定点平均 spearman={_fmt(statistics.mean(correlations))}")
    else:
        lines.append(
            "- noul のデータが probe.jsonl に無い、または重なる候補が不足していたため相関を計算できなかった"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_stats(args: argparse.Namespace) -> int:
    """Never calls the model. Replays seeds 1..12 and reports, seed by seed,
    how many *new* situations and new (situation, candidate) pairs show up
    on top of everything every earlier seed already contributed (revised
    plan §"--stats: seed 1〜12 を順に回し...")."""

    template = args.template.resolve()
    project = args.project.resolve()

    seen_context: set[str] = set()
    seen_pairs: set[str] = set()
    rows: list[dict[str, Any]] = []
    for seed in range(1, 13):
        run_out = args.out.resolve() / f"stats_run_seed{seed}"
        points = _run_mode_a(
            project=project,
            template=template,
            seed=seed,
            turns=None,
            run_out=run_out,
        )
        seed_pairs_total = 0
        new_context = 0
        new_pairs = 0
        for point in points:
            digest = context_key(point["state_text"])
            if digest not in seen_context:
                seen_context.add(digest)
                new_context += 1
            for desc, _chosen in point["candidates"]:
                seed_pairs_total += 1
                pair = _pair_key(point["state_text"], desc)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    new_pairs += 1
        rows.append(
            {
                "seed": seed,
                "points": len(points),
                "new_context": new_context,
                "cum_context": len(seen_context),
                "seed_pairs_total": seed_pairs_total,
                "new_pairs": new_pairs,
                "cum_pairs": len(seen_pairs),
            }
        )

    lines = [
        "# Jev Stage1 --stats（seed 1〜12 累積）",
        "",
        "| seed | 決定点数 | 新規文脈数 | 累計文脈数 | 新規(文脈,候補)対数 | 新規対率 | 累計対数 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        rate = row["new_pairs"] / row["seed_pairs_total"] if row["seed_pairs_total"] else 0.0
        lines.append(
            f"| {row['seed']} | {row['points']} | {row['new_context']} | "
            f"{row['cum_context']} | {row['new_pairs']} | {rate:.3f} | {row['cum_pairs']} |"
        )

    # Informational only -- the revision's seed-12 pass/fail thresholds
    # (rate <= 25%, cumulative <= 3000) were withdrawn after review. Cost
    # control is Stage 2's concern (method choice + a call budget), not a
    # --stats gate.
    last = rows[-1]
    last_rate = last["new_pairs"] / last["seed_pairs_total"] if last["seed_pairs_total"] else 0.0
    lines.extend(
        [
            "",
            f"seed 12 時点の新規(文脈,候補)対の率={last_rate:.3f}（参考値。合否は付けない）",
            f"seed 12 時点の累計対数={last['cum_pairs']}（参考値。合否は付けない）",
        ]
    )

    out_path = args.out.resolve() / "stats.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def _run_bench(args: argparse.Namespace) -> int:
    """Fires 15 consecutive noul calls sharing one context (only the
    trailing candidate line differs) and reports call #1's latency against
    the mean of calls #2-15, to see whether Ollama's KV cache amortizes the
    shared prefix (revised plan §"--bench"). Always calls fresh -- this is a
    timing measurement, not something the probe.jsonl cache should shortcut."""

    points = _run_mode_a(
        project=args.project.resolve(),
        template=args.template.resolve(),
        seed=args.seed,
        turns=args.turns,
        run_out=args.out.resolve() / args.model.replace(":", "_") / "bench_run",
    )
    point = max(points, key=lambda candidate: len(candidate["candidates"]))
    descs = [desc for desc, _chosen in point["candidates"]][:15]

    timings: list[float] = []
    with gpu_guard.gpu_lease(f"jev-probe:{args.model}", wait_seconds=600):
        for desc in descs:
            prompt = f"{point['state_text']}\n\n候補: {desc}\n\n{QUESTION}"
            started = time.monotonic()
            _ollama_call(args.model, prompt, base_url=args.base_url, timeout=args.timeout)
            timings.append(time.monotonic() - started)

    first = timings[0] if timings else None
    rest = timings[1:]
    rest_mean = statistics.mean(rest) if rest else None
    check = rest_mean is not None and rest_mean <= 1.5

    lines = [
        f"# Jev Stage1 --bench — model: {args.model}",
        "",
        f"文脈: {point['label']}（候補 {len(descs)} 件を連続呼び出し）",
        f"1件目所要秒={_fmt(first)}",
        f"2件目以降の平均所要秒={_fmt(rest_mean)}（n={len(rest)}）",
        f"全呼び出し秒: {[round(value, 3) for value in timings]}",
        "",
        f"合格条件（2件目以降の平均 <= 1.5秒/コール）: {_judge(check)}",
    ]

    out_path = args.out.resolve() / args.model.replace(":", "_") / "bench.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Self-check (ponytail: one runnable check for the non-trivial logic)
# ---------------------------------------------------------------------------


def _selftest() -> None:
    from engine.actions import Action

    data = {
        "logprobs": [
            {
                "top_logprobs": [
                    {"token": "yes", "logprob": -0.05},
                    {"token": " No", "logprob": -3.0},
                ]
            }
        ]
    }
    p_yes, top = _p_yes(data)
    assert p_yes is not None and p_yes > 0.9, p_yes
    assert len(top) == 2

    missing = {"logprobs": [{"top_logprobs": [{"token": "maybe", "logprob": -0.1}]}]}
    p_missing, _ = _p_yes(missing)
    assert p_missing is None

    only_yes = {"logprobs": [{"top_logprobs": [{"token": "yes", "logprob": -0.1}]}]}
    p_only_yes, _ = _p_yes(only_yes)
    assert p_only_yes == 1.0

    assert context_key("state") == context_key("state")
    assert context_key("state") != context_key("state2")
    assert len(context_key("state")) == 16

    choice_data = {
        "logprobs": [
            {
                "top_logprobs": [
                    {"token": "A", "logprob": -0.1},
                    {"token": " B", "logprob": -2.0},
                ]
            }
        ]
    }
    probs = _normalize(_label_masses(choice_data, ["A", "B", "C"]))
    assert probs["A"] > probs["B"] > probs["C"] == 0.0
    assert abs(sum(probs.values()) - 1.0) < 1e-9

    assert _ranks([1.0, 2.0, 2.0, 4.0]) == [1.0, 2.5, 2.5, 4.0]
    assert _spearman([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
    assert _spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == -1.0
    assert _spearman([1.0], [1.0]) is None

    assert _is_good_a_candidate("give_item", ("犬", "きびだんご"))
    assert not _is_good_a_candidate("give_item", ("鬼", "きびだんご"))
    assert _is_good_a_candidate("move", ("海",))
    assert _is_good_a_candidate("craft", ("船",))

    assert _action_class(Action("move", ("海",)), None, None) == "海へ移動"
    assert _action_class(Action("investigate", ()), None, None) == "調べる"
    assert _action_class(Action("share_knowledge", ("犬", "雑談")), None, None) == "雑談"
    assert (
        _action_class(Action("share_knowledge", ("犬", "造船術")), None, None)
        == "造船術を伝える"
    )
    assert (
        _action_class(Action("give_item", ("犬", "きびだんご")), None, None)
        == "きびだんごを渡す"
    )
    assert _action_class(Action("rest", ()), None, None) == "休む"
    assert _action_class(Action("withdraw", ()), None, None) == "退く"
    assert _action_class(Action("mislead", ("犬",)), None, None) == "誤った情報へ誘導"
    assert _action_class(Action("disguise", ()), None, None) == "変装"
    assert _action_class(Action("move", ("村",)), None, None) is None
    print("selftest ok")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=False)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--turns", type=int, default=12)
    parser.add_argument("--out", type=Path, required=False)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--project", type=Path, default=ROOT / "projects" / "momotaro")
    parser.add_argument("--template", type=Path, default=ROOT / "templates" / "momotaro")
    parser.add_argument("--method", choices=["noul", "choice"], default="noul")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--bench", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        _selftest()
        return 0
    if args.stats:
        if not args.out:
            build_parser().error("--out is required for --stats")
        return _run_stats(args)
    if args.bench:
        if not args.model or not args.out:
            build_parser().error("--model and --out are required for --bench")
        return _run_bench(args)
    if not args.model or not args.out:
        build_parser().error(
            "--model and --out are required unless --selftest/--stats/--bench"
        )

    out_dir = args.out.resolve() / args.model.replace(":", "_")
    probe_path = out_dir / "probe.jsonl"
    run_out = out_dir / "run"

    stats = {"calls": 0, "missing": 0, "errors": 0, "elapsed": 0.0}

    mode_a_points = _run_mode_a(
        project=args.project.resolve(),
        template=args.template.resolve(),
        seed=args.seed,
        turns=args.turns,
        run_out=run_out,
    )

    if args.method == "noul":
        report_path = out_dir / "report.md"
        mode_b_points = _mode_b_points()
        scores: dict[tuple[str, str], float | None] = {}
        with gpu_guard.gpu_lease(f"jev-probe:{args.model}", wait_seconds=600):
            scores.update(
                _score_points(
                    mode_b_points,
                    model=args.model,
                    base_url=args.base_url,
                    timeout=args.timeout,
                    probe_path=probe_path,
                    stats=stats,
                )
            )
            scores.update(
                _score_points(
                    mode_a_points,
                    model=args.model,
                    base_url=args.base_url,
                    timeout=args.timeout,
                    probe_path=probe_path,
                    stats=stats,
                )
            )
        _write_report(
            report_path,
            model=args.model,
            seed=args.seed,
            turns=args.turns,
            mode_b_points=mode_b_points,
            mode_a_points=mode_a_points,
            scores=scores,
            stats=stats,
        )
    else:
        report_path = out_dir / "report-choice.md"
        with gpu_guard.gpu_lease(f"jev-probe:{args.model}", wait_seconds=600):
            limit = _measure_top_logprobs_limit(
                args.model, base_url=args.base_url, timeout=args.timeout
            )
            # Capped at len(LABELS): a chunk larger than the label alphabet
            # would silently drop the tail candidates in zip(labels, chunk_descs).
            chunk_size = min(limit if limit >= 2 else 10, len(LABELS))
            stats["measured_top_logprobs_limit"] = limit
            stats["chunk_size"] = chunk_size
            scores = _score_points_choice(
                mode_a_points,
                model=args.model,
                base_url=args.base_url,
                timeout=args.timeout,
                probe_path=probe_path,
                stats=stats,
                chunk_size=chunk_size,
            )
        _write_choice_report(
            report_path,
            model=args.model,
            seed=args.seed,
            turns=args.turns,
            mode_a_points=mode_a_points,
            scores=scores,
            stats=stats,
            probe_path=probe_path,
        )

    print(f"report={report_path} calls={stats['calls']} errors={stats['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
