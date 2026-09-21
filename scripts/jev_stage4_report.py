"""WB-JEV-004 Stage 4: does momotaro_plus's genome actually pick a different
*route* to the treasure depending on personality, now that "buy a gun with
money" and "trade the antagonist's estranged brother's goodwill" are both
rational alternatives to fighting?

Classifies every reached run of one or more ``scripts/evolve.py --keep all``
experiment directories (built against ``projects/momotaro_plus``) into one of
five routes, by replaying its ``layers.jsonl``:

    letter_trade -- got 弟の手紙 (the brother_letter_trial) and used it (or a
                    comparably valuable item) to make 鬼's concede a trade
    gun_trade    -- crafted 鉄砲 and used it to make 鬼's concede a trade
    gun_fight    -- crafted 鉄砲 and won the treasure by fight (no concede
                    at all in the run)
    goodwill     -- won the treasure via a concede whose mode is "goodwill"
                    (no attractive item was ever offered)
    classic      -- reached with none of the above (beat 鬼 outright, or by
                    some other path this classifier doesn't recognize)

A run that never reaches the target ending is not assigned a route (it is
still counted towards the unconditional "crafted the gun"/"got the letter"
rates in (a), since those are worth knowing even when the run didn't finish).

Priority when a run matches more than one condition: letter_trade > gun_trade
> gun_fight > goodwill > classic (WB-JEV-004 plan §3).

Usage:
    python scripts/jev_stage4_report.py --runs k0=<dir> [...] --out report.md

Each experiment directory is expected to hold ``g<N>/results.json`` (as
scripts/evolve.py writes it) and, optionally, ``archive.json``. Missing or
corrupt files are skipped with a warning on stderr; this script never raises
for that (same policy as scripts/jev_stage3_report.py).
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402

from gapengine.qd import Archive  # noqa: E402

ROUTES = ("letter_trade", "gun_trade", "gun_fight", "goodwill", "classic")
GEN_DIR_RE = re.compile(r"^g(\d+)$")
GENOME_SCALARS = ("risk_tolerance", "stance_shift_bias", "novelty_drive")
CATEGORIES = ("I", "II", "III", "IV", "V", "VI")


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _load_json(path: Path) -> Any | None:
    if not path.exists():
        _warn(f"missing file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"unreadable file {path}: {exc}")
        return None


def _iter_layer_rows(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _generation_dirs(exp_dir: Path) -> list[tuple[int, Path]]:
    if not exp_dir.is_dir():
        _warn(f"experiment directory not found: {exp_dir}")
        return []
    found = []
    for entry in exp_dir.iterdir():
        match = entry.is_dir() and GEN_DIR_RE.fullmatch(entry.name)
        if match:
            found.append((int(match.group(1)), entry))
    found.sort(key=lambda pair: pair[0])
    return found


def _load_generations(exp_dir: Path) -> list[list[dict[str, Any]]]:
    generations = []
    for _generation, gen_dir in _generation_dirs(exp_dir):
        individuals = _load_json(gen_dir / "results.json")
        if individuals is not None:
            generations.append(individuals)
    return generations


def classify_rows(rows: list[dict[str, Any]], *, reached: bool) -> tuple[str | None, bool, bool]:
    """(route, crafted_gun, got_letter). route is None when ``reached`` is
    False -- an unfinished run has no route, only the two unconditional
    flags."""

    crafted_gun = False
    got_letter = False
    concedes: list[dict[str, Any]] = []

    for row in rows:
        if row.get("kind") != "decision":
            continue
        verb = row.get("verb")
        result = row.get("result")
        details = row.get("details") or {}
        if verb == "craft" and result == "crafted" and details.get("item") == "鉄砲":
            crafted_gun = True
        elif (
            verb == "trial"
            and result == "trial_completed"
            and details.get("trial_id") == "brother_letter_trial"
        ):
            got_letter = True
        elif verb == "concede" and result == "conceded":
            concedes.append(
                {
                    "mode": details.get("mode"),
                    "assets": set((details.get("assets") or {}).keys()),
                }
            )

    if not reached:
        return None, crafted_gun, got_letter

    if got_letter and any(
        c["mode"] == "trade" and "弟の手紙" in c["assets"] for c in concedes
    ):
        return "letter_trade", crafted_gun, got_letter
    if crafted_gun and any(c["mode"] == "trade" and "鉄砲" in c["assets"] for c in concedes):
        return "gun_trade", crafted_gun, got_letter
    if crafted_gun and not concedes:
        return "gun_fight", crafted_gun, got_letter
    if any(c["mode"] == "goodwill" for c in concedes):
        return "goodwill", crafted_gun, got_letter
    return "classic", crafted_gun, got_letter


def _classify_run_file(
    exp_dir: Path, layers_path: str, *, reached: bool
) -> tuple[str | None, bool, bool] | None:
    full_path = exp_dir / layers_path
    if not full_path.exists():
        _warn(f"missing layers file: {full_path}")
        return None
    return classify_rows(list(_iter_layer_rows(full_path)), reached=reached)


def _protagonist_from_rows(rows: list[dict[str, Any]]) -> str | None:
    """``layers.jsonl``'s first (``header``) row names the protagonist --
    used by ``funnel_row`` to know whose ``rest`` decisions count towards
    the vitality-at-rest signal, without this script having to load the
    world.yaml itself."""

    for row in rows:
        if row.get("kind") == "header":
            protagonist = row.get("protagonist")
            return str(protagonist) if protagonist is not None else None
    return None


def funnel_row(
    rows: list[dict[str, Any]], protagonist: str | None
) -> dict[str, Any]:
    """WB-JEV-004 Stage 4b plan §3: per-run funnel signals -- how far into
    the money->gun / brother's-letter->trade routes a run got, regardless of
    whether it reached the target ending (mirrors ``classify_rows``'s
    "counted for every run" unconditional flags). ``concede_events`` is one
    entry per ``concede`` decision (usually zero or one per run)."""

    koban_gained = 0
    crafted_gun = False
    got_letter = False
    negotiated = False
    concede_events: list[dict[str, Any]] = []
    protagonist_rests = 0
    protagonist_rests_not_alive = 0

    for row in rows:
        if row.get("kind") != "decision":
            continue
        verb = row.get("verb")
        result = row.get("result")
        details = row.get("details") or {}

        # Companions gather their own 小判 on the road; only the protagonist's
        # count says whether the gun was affordable.
        if (
            verb == "investigate"
            and result == "investigated"
            and (protagonist is None or row.get("subject") == protagonist)
        ):
            koban_gained += sum(
                int(item.get("count", 1))
                for item in details.get("gathered") or []
                if item.get("item") == "小判"
            )
        elif verb == "craft" and result == "crafted" and details.get("item") == "鉄砲":
            crafted_gun = True
        elif (
            verb == "trial"
            and result == "trial_completed"
            and details.get("trial_id") == "brother_letter_trial"
        ):
            got_letter = True
        elif verb == "negotiate" and result == "offered":
            negotiated = True
        elif verb == "concede" and result == "conceded":
            concede_events.append(
                {
                    "mode": details.get("mode"),
                    "day": row.get("day"),
                    "ship": "船" in (details.get("assets") or {}),
                }
            )

        if (
            protagonist is not None
            and verb == "rest"
            and row.get("subject") == protagonist
        ):
            policy_meta = row.get("policy")
            ctx = policy_meta.get("ctx") if isinstance(policy_meta, dict) else None
            # ctx[3] is subject.vitality at decision time (see
            # gapengine.precedent.ctx_key) -- absent for the no-candidates
            # fallback rest (engine/sim.py's choose_action returns
            # Action("rest") with no policy meta at all in that case), which
            # is excluded from both the numerator and the denominator.
            if isinstance(ctx, list) and len(ctx) >= 4:
                protagonist_rests += 1
                # "revived" never returns to "alive" (engine/vitality.py), so
                # only "downed" -- the forced rest -- measures days lost.
                if ctx[3] == "downed":
                    protagonist_rests_not_alive += 1

    return {
        "koban_gained": koban_gained,
        "crafted_gun": crafted_gun,
        "got_letter": got_letter,
        "negotiated": negotiated,
        "concede_events": concede_events,
        "protagonist_rests": protagonist_rests,
        "protagonist_rests_not_alive": protagonist_rests_not_alive,
    }


def compute_experiment(name: str, exp_dir: Path) -> dict[str, Any]:
    generations = _load_generations(exp_dir)

    total_runs = 0
    route_counts: Counter[str] = Counter()
    crafted_gun_runs = 0
    got_letter_runs = 0
    route_genomes: dict[str, list[dict[str, Any]]] = {route: [] for route in ROUTES}
    reached_genomes: list[dict[str, Any]] = []
    qd_cross: dict[tuple[str, str], Counter[str]] = {}

    protagonist: str | None = None
    koban_at_least_1_runs = 0
    koban_all_3_runs = 0
    negotiated_runs = 0
    concede_mode_runs: Counter[str] = Counter()
    concede_days: list[int] = []
    concede_with_ship_runs = 0
    reached_after_concede_runs = 0
    protagonist_rest_total = 0
    protagonist_rest_not_alive_total = 0

    archive = None
    archive_path = exp_dir / "archive.json"
    if archive_path.exists():
        try:
            archive = Archive.load(archive_path)
        except (OSError, ValueError, KeyError) as exc:
            _warn(f"unreadable archive {archive_path}: {exc}")

    for individuals in generations:
        for individual in individuals:
            genome = individual.get("genome")
            for run in individual.get("runs", []):
                total_runs += 1
                reached = bool(run.get("reached"))
                layers_path = run.get("layers_path")
                if not layers_path:
                    continue
                full_path = exp_dir / layers_path
                if not full_path.exists():
                    _warn(f"missing layers file: {full_path}")
                    continue
                rows = list(_iter_layer_rows(full_path))
                if protagonist is None:
                    protagonist = _protagonist_from_rows(rows)

                route, crafted_gun, got_letter = classify_rows(rows, reached=reached)
                if crafted_gun:
                    crafted_gun_runs += 1
                if got_letter:
                    got_letter_runs += 1

                funnel = funnel_row(rows, protagonist)
                if funnel["koban_gained"] >= 1:
                    koban_at_least_1_runs += 1
                if funnel["koban_gained"] >= 3:
                    koban_all_3_runs += 1
                if funnel["negotiated"]:
                    negotiated_runs += 1
                for mode in {
                    str(event["mode"])
                    for event in funnel["concede_events"]
                    if event.get("mode")
                }:
                    concede_mode_runs[mode] += 1
                concede_days.extend(
                    int(event["day"])
                    for event in funnel["concede_events"]
                    if event.get("day") is not None
                )
                if any(event["ship"] for event in funnel["concede_events"]):
                    concede_with_ship_runs += 1
                if funnel["concede_events"] and reached:
                    reached_after_concede_runs += 1
                protagonist_rest_total += funnel["protagonist_rests"]
                protagonist_rest_not_alive_total += funnel["protagonist_rests_not_alive"]

                if not reached or route is None:
                    continue

                route_counts[route] += 1
                if genome is not None:
                    reached_genomes.append(genome)
                    route_genomes[route].append(genome)

                category = run.get("category")
                volatility = run.get("volatility")
                if category is not None and volatility is not None and archive is not None:
                    try:
                        bin_name = archive.bin_for(float(volatility))
                    except RuntimeError:
                        bin_name = None
                    if bin_name is not None:
                        cell = (str(category), bin_name)
                        qd_cross.setdefault(cell, Counter())[route] += 1

    archive_elite_routes: dict[str, str | None] = {}
    if archive is not None:
        for cell, elite in sorted(archive.cells.items()):
            layers_path = elite.exemplar.get("layers_path")
            if not layers_path:
                continue
            classified = _classify_run_file(exp_dir, str(layers_path), reached=True)
            archive_elite_routes["|".join(cell)] = classified[0] if classified else None

    reached_runs = sum(route_counts.values())
    funnel_stats = {
        "koban_at_least_1_runs": koban_at_least_1_runs,
        "koban_all_3_runs": koban_all_3_runs,
        "negotiated_runs": negotiated_runs,
        "concede_mode_runs": concede_mode_runs,
        "concede_runs": sum(concede_mode_runs.values()),
        "concede_event_count": len(concede_days),
        "concede_day_median": statistics.median(concede_days) if concede_days else None,
        "concede_with_ship_runs": concede_with_ship_runs,
        "reached_after_concede_runs": reached_after_concede_runs,
        "protagonist_rest_total": protagonist_rest_total,
        "protagonist_rest_not_alive_total": protagonist_rest_not_alive_total,
    }
    return {
        "name": name,
        "dir": exp_dir,
        "funnel": funnel_stats,
        "total_runs": total_runs,
        "reached_runs": reached_runs,
        "route_counts": route_counts,
        "crafted_gun_runs": crafted_gun_runs,
        "got_letter_runs": got_letter_runs,
        "route_genomes": route_genomes,
        "reached_genomes": reached_genomes,
        "qd_cross": qd_cross,
        "archive_elite_routes": archive_elite_routes,
    }


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "-"
    return f"{100.0 * numerator / denominator:.1f}% ({numerator}/{denominator})"


def _genome_means(genomes: list[dict[str, Any]]) -> dict[str, float] | None:
    if not genomes:
        return None
    means: dict[str, float] = {}
    for field in GENOME_SCALARS:
        values = [float(g[field]) for g in genomes if field in g]
        if values:
            means[field] = statistics.mean(values)
    for category in CATEGORIES:
        values = [
            float(g["category_weight"][category])
            for g in genomes
            if isinstance(g.get("category_weight"), dict) and category in g["category_weight"]
        ]
        if values:
            means[f"category_weight.{category}"] = statistics.mean(values)
    return means


def _markdown_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _route_table(stats: dict[str, Any]) -> str:
    rows = []
    for route in ROUTES:
        count = stats["route_counts"].get(route, 0)
        rows.append([route, _pct(count, stats["reached_runs"])])
    rows.append(["(到達ラン計)", str(stats["reached_runs"])])
    lines = [_markdown_table(["経路", "到達ラン中の割合"], rows), ""]
    lines.append(
        f"鉄砲を作ったラン（到達不問）: {_pct(stats['crafted_gun_runs'], stats['total_runs'])}"
    )
    lines.append(
        f"弟の手紙を得たラン（到達不問）: {_pct(stats['got_letter_runs'], stats['total_runs'])}"
    )
    return "\n".join(lines)


def _genome_table(stats: dict[str, Any]) -> str:
    overall = _genome_means(stats["reached_genomes"])
    fields = list(GENOME_SCALARS) + [f"category_weight.{c}" for c in CATEGORIES]
    header = ["経路", *fields]
    rows = []
    for route in ROUTES:
        means = _genome_means(stats["route_genomes"][route])
        if means is None:
            rows.append([route, *(["-"] * len(fields))])
            continue
        cells = []
        for field in fields:
            value = means.get(field)
            if value is None or overall is None or field not in overall:
                cells.append("-" if value is None else f"{value:.3f}")
                continue
            diff = value - overall[field]
            cells.append(f"{value:.3f} ({diff:+.3f})")
        rows.append([route, *cells])
    return _markdown_table(header, rows)


def _qd_cross_table(stats: dict[str, Any]) -> str:
    cells = sorted(stats["qd_cross"])
    if not cells:
        return "(QD マスの記録なし)"
    rows = []
    for cell in cells:
        counts = stats["qd_cross"][cell]
        row = ["|".join(cell)] + [str(counts.get(route, 0)) for route in ROUTES]
        rows.append(row)
    return _markdown_table(["QDマス", *ROUTES], rows)


def _archive_table(stats: dict[str, Any]) -> str:
    if not stats["archive_elite_routes"]:
        return "(アーカイブなし、またはセル0件)"
    rows = [
        [cell, route or "(分類不能)"]
        for cell, route in sorted(stats["archive_elite_routes"].items())
    ]
    return _markdown_table(["セル", "経路"], rows)


def _funnel_table(stats: dict[str, Any]) -> str:
    """WB-JEV-004 Stage 4b plan §3: how far into the new routes every run
    got, regardless of whether it reached the target ending (unlike (a)'s
    route table, which only classifies *reached* runs)."""

    funnel = stats["funnel"]
    total = stats["total_runs"]
    rows = [
        ["小判を1枚以上得た", _pct(funnel["koban_at_least_1_runs"], total)],
        ["小判を3枚そろえた", _pct(funnel["koban_all_3_runs"], total)],
        ["鉄砲をcraftした", _pct(stats["crafted_gun_runs"], total)],
        ["鬼の弟の試練を完了した", _pct(stats["got_letter_runs"], total)],
        ["negotiateを申し出た", _pct(funnel["negotiated_runs"], total)],
        [
            "鬼がconcedeした（trade）",
            _pct(funnel["concede_mode_runs"].get("trade", 0), total),
        ],
        [
            "鬼がconcedeした（goodwill）",
            _pct(funnel["concede_mode_runs"].get("goodwill", 0), total),
        ],
        [
            "concedeで渡した品に船が含まれた",
            _pct(funnel["concede_with_ship_runs"], total),
        ],
        ["concede後に到達した", _pct(funnel["reached_after_concede_runs"], total)],
    ]
    lines = [_markdown_table(["段階", "全ラン中の割合"], rows), ""]
    median = funnel["concede_day_median"]
    lines.append(
        "concedeの時点の日付の中央値: "
        + (
            f"{median:g}日目（n={funnel['concede_event_count']}）"
            if median is not None
            else "-"
        )
    )
    lines.append(
        "主人公のrestのうちdowned（倒れて強制休息）の割合: "
        + _pct(
            funnel["protagonist_rest_not_alive_total"],
            funnel["protagonist_rest_total"],
        )
    )
    return "\n".join(lines)


def _judgement(stats: dict[str, Any]) -> list[tuple[str, str]]:
    reached = stats["reached_runs"]
    route_counts = stats["route_counts"]

    routes_over_10pct = [
        route
        for route in ROUTES
        if reached > 0 and route_counts.get(route, 0) / reached >= 0.10
    ]
    check1 = len(routes_over_10pct) >= 2

    max_diff = 0.0
    means_by_route = {
        route: _genome_means(stats["route_genomes"][route])
        for route in ROUTES
        if stats["route_genomes"][route]
    }
    routes_with_genome = list(means_by_route)
    for i, route_a in enumerate(routes_with_genome):
        for route_b in routes_with_genome[i + 1 :]:
            for field in ("risk_tolerance", "stance_shift_bias"):
                value_a = means_by_route[route_a].get(field)
                value_b = means_by_route[route_b].get(field)
                if value_a is None or value_b is None:
                    continue
                max_diff = max(max_diff, abs(value_a - value_b))
    check2 = max_diff >= 0.15

    archive_routes = {
        route for route in stats["archive_elite_routes"].values() if route is not None
    }
    check3 = len(archive_routes) >= 2

    return [
        (
            "到達ランに2種類以上の経路が各10%以上ある",
            f"PASS ({', '.join(routes_over_10pct)})" if check1 else "FAIL",
        ),
        (
            "経路間でrisk_toleranceかstance_shift_biasの平均差が0.15以上ある",
            f"PASS (max diff={max_diff:.3f})" if check2 else f"FAIL (max diff={max_diff:.3f})",
        ),
        (
            "アーカイブのエリートに2種類以上の経路がある",
            f"PASS ({', '.join(sorted(archive_routes))})" if check3 else "FAIL",
        ),
    ]


def build_report(stats_list: list[dict[str, Any]]) -> str:
    lines = ["# Jev Stage 4 経路レポート (WB-JEV-004)", ""]
    lines.append(
        "対象実験: " + ", ".join(f"{s['name']} ({s['dir']})" for s in stats_list)
    )
    lines.append("")

    for stats in stats_list:
        lines.append(f"## {stats['name']}")
        lines.append("")
        lines.append("### (a) 経路別の到達ラン数と割合")
        lines.append("")
        lines.append(_route_table(stats))
        lines.append("")
        lines.append("### (b) 経路別の遺伝子平均（全到達ランの平均との差）")
        lines.append("")
        lines.append(_genome_table(stats))
        lines.append("")
        lines.append("### (c) 経路 x QDマス クロス表")
        lines.append("")
        lines.append(_qd_cross_table(stats))
        lines.append("")
        lines.append("### (d) 最終アーカイブの各エリートの経路")
        lines.append("")
        lines.append(_archive_table(stats))
        lines.append("")
        lines.append("### (e) 判定")
        lines.append("")
        lines.append(
            _markdown_table(
                ["判定基準", "結果"],
                [[label, result] for label, result in _judgement(stats)],
            )
        )
        lines.append("")
        lines.append("### (f) 経路の各段階の通過数（到達不問・全ラン対象）")
        lines.append("")
        lines.append(_funnel_table(stats))
        lines.append("")

    return "\n".join(lines)


def _parse_runs(pairs: list[str]) -> list[tuple[str, Path]]:
    runs = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--runs entries must look like name=dir, got: {pair!r}")
        name, _, directory = pair.partition("=")
        runs.append((name, Path(directory)))
    return runs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", nargs="+", required=True, metavar="NAME=DIR",
        help="one or more name=directory pairs, e.g. k0=runs/jev-stage4/smoke-k0",
    )
    parser.add_argument("--out", type=Path, required=True, help="Markdown report path")
    args = parser.parse_args(argv)

    runs = _parse_runs(args.runs)
    stats_list = [compute_experiment(name, directory) for name, directory in runs]
    report = build_report(stats_list)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
