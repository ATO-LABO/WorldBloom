"""WB-JEV-001 Stage 0: does an Ollama model's yes/no logprob rank rational
moves above irrational ones, given only the protagonist's beliefs (never the
world's ground truth)? See docs handed down as
`jev-stage0-plan.md` (design by Fable, 2026-09-18) for the full spec.

Mode A scores every real decision point of one deterministic momotaro run
(neutral genome, annotate-only policy -- same recipe as
scripts/random_baseline.py) against its full candidate list. Mode B scores
three hand-written synthetic prompts that isolate one thing at a time: does
adding "you can buy a gun" or "the antagonist has an estranged brother" to
the world raise the probability of the matching new candidate.

Engine/gapengine/templates/projects are read-only here. Output (including
the throwaway layers.jsonl from the Mode A run) goes under
--out/<model with ':' -> '_'>/, never under the Drive-mounted repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from engine.contest import believed_strength, strength
from engine.sim import Simulation
from engine.world import World
from gapengine.evolve import _load_subjects
from gapengine.genome import Genome
from gapengine.ollama import DEFAULT_BASE_URL, build_request
from gapengine.policy import Policy
from gapengine.reader_summary import _grounds_text
from gapengine.scenes import VERB_LABELS, _argument_text

QUESTION = (
    "質問: 本人の知る限りで、この行動は目的に近づく手段として筋が通っているか。"
    "yes か no の1語だけで答えよ。"
)

# ---------------------------------------------------------------------------
# Ollama call + logprob readout (plan §2)
# ---------------------------------------------------------------------------


def _ollama_call(
    model: str, prompt: str, *, base_url: str, timeout: float
) -> dict[str, Any]:
    url, payload = build_request(
        {
            "base_url": base_url,
            "model": model,
            "think": False,
            "options": {"num_predict": 1, "temperature": 0, "seed": 0},
        },
        prompt,
    )
    payload["logprobs"] = True
    payload["top_logprobs"] = 10
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _p_yes(data: dict[str, Any]) -> tuple[float | None, list[Any]]:
    """(p_yes, raw top_logprobs). None when neither a yes- nor no-token is
    among the top logprobs at all (plan §2 "欠測")."""

    logprobs = data.get("logprobs")
    if not isinstance(logprobs, list) or not logprobs:
        return None, []
    top = logprobs[0].get("top_logprobs")
    if not isinstance(top, list):
        return None, []

    yes_mass = no_mass = 0.0
    found_yes = found_no = False
    for entry in top:
        token = entry.get("token")
        logprob = entry.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)):
            continue
        normalized = token.strip().lower()
        if normalized == "yes":
            yes_mass += math.exp(logprob)
            found_yes = True
        elif normalized == "no":
            no_mass += math.exp(logprob)
            found_no = True

    if not found_yes and not found_no:
        return None, top
    total = yes_mass + no_mass
    return (yes_mass / total if total > 0 else 0.0), top


# ---------------------------------------------------------------------------
# State-text rendering shared building blocks
# ---------------------------------------------------------------------------


def _map_text(world: World) -> str:
    parts = []
    for origin in sorted(world.routes):
        for route in world.routes[origin]:
            requirement = (
                f"（{route.requires_item}が必要）" if route.requires_item else ""
            )
            parts.append(f"{origin}→{route.destination}{requirement}")
    return "、".join(parts)


def _known_dict(subject: Any) -> dict[str, Any]:
    valued_beliefs = {
        fact_id: {"value": belief.value, "confidence": belief.confidence}
        for fact_id, belief in subject.beliefs.items()
    }
    belief = {
        target: {"known_modifiers": sorted(about.known_modifiers)}
        for target, about in subject.beliefs_about.items()
        if about.known_modifiers
    }
    return {"valued_beliefs": valued_beliefs, "belief": belief}


def _facts_text(subject: Any, world: World) -> str:
    pieces = [
        str(world.facts.get(fact_id, {}).get("label", fact_id))
        for fact_id in sorted(subject.knowledge)
    ]
    grounds = _grounds_text({"knowledge": _known_dict(subject)})
    if grounds:
        pieces.append(grounds)
    return "／".join(pieces)


def _describe_candidate(action: Any) -> str:
    label = VERB_LABELS.get(action.verb, action.verb)
    return f"{label}{_argument_text(list(action.args), {})}"


def _dedupe(pairs: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    seen: dict[str, int] = {}
    out = []
    for desc, action in pairs:
        seen[desc] = seen.get(desc, 0) + 1
        text = desc if seen[desc] == 1 else f"{desc}#{seen[desc]}"
        out.append((text, action))
    return out


def _state_text_mode_a(subject: Any, world: World, present: list[Any]) -> str:
    antagonist = world.subjects[world.antagonist]
    my_strength = strength(subject, world, present)
    enemy_believed = believed_strength(subject, antagonist, world, present)
    holder = world.holder(subject.goal.target) if subject.goal.target else None
    companions = sorted(
        peer.identity_displayed for peer in present if peer.id != subject.id
    )
    goal_line = (
        f"目的: {subject.goal.target}を{subject.goal.deliver_to}へ持ち帰る"
        if subject.goal.deliver_to
        else f"目的: {subject.goal.target}を得る"
    )
    inventory_text = "、".join(
        f"{item}{count}" for item, count in sorted(subject.inventory.items())
        if count > 0
    )
    # 本人が作り方を知っている品だけ（requires.knowledge を knowledge に持つ、
    # または knowledge 要件なし）。材料の採取場所も本人が知る地図の一部として渡す。
    recipe_parts = []
    for product, materials in sorted(world.recipes.items()):
        definition = world.items.get(product, {})
        needed = (definition.get("requires") or {}).get("knowledge")
        if needed is not None and needed not in subject.knowledge:
            continue
        sources = []
        for material in sorted(materials):
            zones = sorted(
                {str(s["zone"]) for s in world.items.get(material, {}).get("sources", []) or []}
            )
            if zones:
                sources.append(f"{material}は{'・'.join(zones)}で調べると手に入る")
        craft_zone = definition.get("craft_zone")
        recipe_parts.append(
            f"{product}は{'と'.join(f'{m}{n}' for m, n in sorted(materials.items()))}から"
            f"{f'{craft_zone}で' if craft_zone else ''}作れる"
            + (f"（{'、'.join(sources)}）" if sources else "")
        )
    return "\n".join(
        [
            goal_line,
            f"現在地: {subject.zone}",
            f"通過段階: {'、'.join(sorted(subject.phase)) or 'なし'}",
            f"同席: {'、'.join(companions) or 'なし'}",
            f"目的物の所持者: {holder or '不明'}",
            f"所持品: {inventory_text or 'なし'}",
            f"知っている作り方: {'／'.join(recipe_parts) or 'なし'}",
            f"自分の強さ(自己認識): {my_strength}",
            f"{world.antagonist}の強さ(本人の推定): {enemy_believed}",
            f"知っている事実: {_facts_text(subject, world) or 'なし'}",
            f"知っている地図: {_map_text(world)}",
        ]
    )


# ---------------------------------------------------------------------------
# Mode A: recording policy wrapper + run
# ---------------------------------------------------------------------------


class _RecordingPolicy:
    """Delegates every call to the wrapped neutral Policy unchanged, and
    renders/records the decision point *before* delegating so the captured
    state text reflects subject/world at decision time, not after later
    mutation (Subject/World objects are mutated in place as the run
    continues). Consumes no randomness itself."""

    def __init__(self, inner: Policy) -> None:
        self._inner = inner
        self.decisions: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def reweight(self, subject, world, present, weighted, *, turn=0, day=0):
        candidates = _dedupe(
            [(_describe_candidate(action), action) for action, _weight in weighted]
        )
        self.decisions.append(
            {
                "point_id": str(turn),
                "turn": turn,
                "day": day,
                "zone": subject.zone,
                "state_text": _state_text_mode_a(subject, world, present),
                "candidates": [
                    {
                        "desc": desc,
                        "verb": action.verb,
                        "args": tuple(str(value) for value in action.args),
                    }
                    for desc, action in candidates
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
    *, project: Path, template: Path, seed: int, turns: int, run_out: Path
) -> list[dict[str, Any]]:
    action_graph_path = template / "action_graph.yaml"
    action_graph_path = action_graph_path if action_graph_path.is_file() else None
    world = World.from_yaml(project / "world.yaml", action_graph_path=action_graph_path)
    subjects = _load_subjects(project / "subjects")
    action_cfg = (
        yaml.safe_load(action_graph_path.read_text(encoding="utf-8"))
        if action_graph_path is not None
        else {"nodes": [], "edges": []}
    )
    policy = Policy(Genome.neutral(), precedent=None, cfg=action_cfg, annotate_only=True)
    recorder = _RecordingPolicy(policy)
    Simulation(
        seed,
        world,
        subjects,
        run_out,
        policies={world.protagonist: recorder},
    ).run()

    points = []
    for entry in recorder.decisions[:turns]:
        candidates = [
            (
                candidate["desc"],
                (candidate["verb"], candidate["args"]) == entry["chosen"],
            )
            for candidate in entry["candidates"]
        ]
        points.append(
            {
                "mode": "A",
                "point_id": entry["point_id"],
                "label": f"turn={entry['turn']} day={entry['day']} zone={entry['zone']}",
                "state_text": entry["state_text"],
                "candidates": candidates,
                "raw_candidates": entry["candidates"],
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
# Scoring (idempotent: skip (state_digest, candidate) pairs already in
# probe.jsonl)
# ---------------------------------------------------------------------------


def _digest(mode: str, point_id: str, state_text: str) -> str:
    return hashlib.sha256(
        f"{mode}|{point_id}|{state_text}".encode("utf-8")
    ).hexdigest()[:16]


def _read_existing(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    existing: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return existing
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            existing[(row["state_digest"], row["candidate"])] = row
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
    existing = _read_existing(probe_path)
    scores: dict[tuple[str, str], float | None] = {}
    for point in points:
        digest = _digest(point["mode"], point["point_id"], point["state_text"])
        for desc, chosen in point["candidates"]:
            key = (digest, desc)
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
                "turn": point["point_id"],
                "state_digest": digest,
                "candidate": desc,
                "p_yes": p_yes,
                "raw_top_logprobs": top,
                "chosen": bool(chosen),
            }
            _append_jsonl(probe_path, row)
            existing[key] = row
            scores[(point["point_id"], desc)] = p_yes
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
        f"# Jev Stage0 rationality probe — model: {model}",
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

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Self-check (ponytail: one runnable check for the non-trivial logic)
# ---------------------------------------------------------------------------


def _selftest() -> None:
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

    assert _digest("A", "1", "state") == _digest("A", "1", "state")
    assert _digest("A", "1", "state") != _digest("A", "2", "state")

    assert _is_good_a_candidate("give_item", ("犬", "きびだんご"))
    assert not _is_good_a_candidate("give_item", ("鬼", "きびだんご"))
    assert _is_good_a_candidate("move", ("海",))
    assert _is_good_a_candidate("craft", ("船",))
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
    parser.add_argument("--selftest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        _selftest()
        return 0
    if not args.model or not args.out:
        build_parser().error("--model and --out are required unless --selftest")

    out_dir = args.out.resolve() / args.model.replace(":", "_")
    probe_path = out_dir / "probe.jsonl"
    report_path = out_dir / "report.md"
    run_out = out_dir / "run"

    stats = {"calls": 0, "missing": 0, "errors": 0, "elapsed": 0.0}

    mode_b_points = _mode_b_points()
    mode_a_points = _run_mode_a(
        project=args.project.resolve(),
        template=args.template.resolve(),
        seed=args.seed,
        turns=args.turns,
        run_out=run_out,
    )

    scores: dict[tuple[str, str], float | None] = {}
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
    print(f"report={report_path} calls={stats['calls']} errors={stats['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
