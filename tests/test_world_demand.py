import json
import tempfile
import unittest
from pathlib import Path

from gapengine import world_demand


def _row(**fields):
    base = {"turn": 1, "day": 1, "slot": "day", "args": [], "result": "ok",
            "effective": True, "details": {}, "policy": None}
    base.update(fields)
    return base


def _write_layers(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    craft_args = ["silver", "stick"]
    rows = [
        {"kind": "header", "protagonist": "たろう", "antagonist": "鬼", "world": "村", "seed": 1},
        # 海 zone: same craft args 3 times -> 1 first + 2 repeats; the 3rd is also a whiff.
        _row(kind="decision", subject="たろう", verb="craft", args=craft_args, result="crafted",
             explanation={"zone": "海", "selection": {"total_candidates": 5}}),
        _row(kind="decision", subject="たろう", verb="craft", args=craft_args, result="crafted",
             explanation={"zone": "海", "selection": {"total_candidates": 4}}),
        _row(kind="decision", subject="たろう", verb="craft", args=craft_args, result="crafted",
             effective=False, details={"reason": "missing_material"},
             explanation={"zone": "海", "selection": {"total_candidates": 3}}),
        # 村 zone: a single distinct dwell action.
        _row(kind="decision", subject="たろう", verb="trade", args=["fish"], result="traded",
             explanation={"zone": "村", "selection": {"total_candidates": 2}}),
        # move never counts as dwell, and here it also has no explanation -> "(unknown)".
        _row(kind="decision", subject="たろう", verb="move", args=["village"], result="moved"),
        # a dwell action with no explanation also lands in "(unknown)".
        _row(kind="decision", subject="たろう", verb="observe", args=[], result="observed"),
        # another subject's identical craft in 海 must not be counted at all.
        _row(kind="decision", subject="花子", verb="craft", args=craft_args, result="crafted",
             explanation={"zone": "海"}),
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class WorldDemandTest(unittest.TestCase):
    def test_collect_aggregates_by_zone_for_the_protagonist_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layers_path = Path(tmp) / "g0" / "ind-0" / "seed-1" / "layers.jsonl"
            _write_layers(layers_path)

            report = world_demand.collect([layers_path])

            self.assertEqual(report["files"], 1)
            self.assertEqual(report["skipped_paths"], 0)
            # 3 craft + 1 trade + 1 move + 1 observe = 6; 花子's row is excluded.
            self.assertEqual(report["subject_decisions"], 6)

            zones_by_name = {z["zone"]: z for z in report["zones"]}

            sea = zones_by_name["海"]
            self.assertEqual(sea["dwell"], 3)  # not 4 -- 花子's craft does not count
            self.assertEqual(sea["repeat_rate"], round(2 / 3, 4))
            self.assertEqual(sea["ineffective_rate"], round(1 / 3, 4))
            self.assertEqual(sea["ineffective_reasons"], [("missing_material", 1)])
            self.assertEqual(sea["verbs"], [("craft", 3, round(1 / 3, 4))])

            village = zones_by_name["村"]
            self.assertEqual(village["dwell"], 1)
            self.assertEqual(village["repeat_rate"], 0.0)
            self.assertEqual(village["ineffective_rate"], 0.0)

            unknown = zones_by_name[world_demand.UNKNOWN_ZONE]
            self.assertEqual(unknown["decisions"], 2)  # move + observe
            self.assertEqual(unknown["dwell"], 1)  # move is excluded from dwell

            # dwell-share-descending order, ties broken by zone name. Total
            # dwell is 海=3, 村=1, unknown=1 -- unknown and 村 tie on share
            # and are broken alphabetically ("(unknown)" < "村").
            self.assertEqual([z["zone"] for z in report["zones"]],
                              ["海", world_demand.UNKNOWN_ZONE, "村"])

            # 海's craft whiffs once out of 3 (33%): below WHIFF_RATE_MIN, no trigger.
            self.assertEqual(report["triggers"], [])

    def test_zone_is_tracked_from_snapshots_and_deltas_without_explanations(self) -> None:
        rows = [
            {"kind": "header", "protagonist": "たろう"},
            {"kind": "snapshot", "subject": "たろう", "layers": {"zone": "村"}},
            _row(kind="decision", subject="たろう", verb="train"),
            # the move itself is decided in 村; only later decisions see 海.
            _row(kind="decision", subject="たろう", verb="move", args=["海"],
                 delta={"actor": {"zone": "海"}}),
            _row(kind="decision", subject="たろう", verb="rest"),
            _row(kind="decision", subject="たろう", verb="craft"),
            # someone else's event drags the protagonist along.
            {"kind": "event", "subject": "花子", "delta": {"targets": {"たろう": {"zone": "森"}}}},
            _row(kind="decision", subject="たろう", verb="investigate"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layers.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                            encoding="utf-8")
            zones = {z["zone"]: z for z in world_demand.collect([path])["zones"]}

        self.assertNotIn(world_demand.UNKNOWN_ZONE, zones)
        self.assertEqual((zones["村"]["decisions"], zones["村"]["dwell"]), (2, 1))
        # rest is passive: counted as a decision, not as dwelling.
        self.assertEqual((zones["海"]["decisions"], zones["海"]["dwell"]), (2, 1))
        self.assertEqual(zones["森"]["verbs"], [("investigate", 1, 0.0)])

    def test_collect_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layers_path = Path(tmp) / "layers.jsonl"
            _write_layers(layers_path)

            first = world_demand.collect([layers_path])
            second = world_demand.collect([layers_path])
            self.assertEqual(first, second)

    def test_verb_counts_keeps_every_verb_not_just_the_top_five(self) -> None:
        rows = [{"kind": "header", "protagonist": "たろう"}]
        verbs = ["investigate", "craft", "trade", "haggle", "train", "forage"]
        for i, verb in enumerate(verbs):
            # descending counts so a top-5 cutoff would have to drop exactly one.
            count = len(verbs) - i
            for _ in range(count):
                rows.append(_row(kind="decision", subject="たろう", verb=verb,
                                  effective=(verb != "forage"),
                                  result="invalid" if verb == "forage" else "ok",
                                  explanation={"zone": "海"}))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layers.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                            encoding="utf-8")
            report = world_demand.collect([path])

        zone = next(z for z in report["zones"] if z["zone"] == "海")
        self.assertEqual(len(zone["verbs"]), 5)  # report_zones["verbs"] caps at the top 5
        self.assertEqual(set(report["verb_counts"]["海"]), set(verbs))  # verb_counts keeps all 6
        self.assertEqual(report["verb_counts"]["海"]["forage"], [1, 1])  # count=1, whiffs=1

    def test_triggers_require_both_thresholds_and_exclude_unknown_zone(self) -> None:
        # 500 total dwell decisions spread over three zones so wasted_share is
        # easy to control: 海 has 50 investigate, all whiffs (rate=1.0,
        # wasted_share=50/500=0.10 -- every threshold cleared, triggers).
        # 村 has 10 investigate, all whiffs (rate=1.0, wasted_share=10/500=0.02
        # -- exactly at WASTED_SHARE_MIN and WHIFFS_MIN, still triggers: inclusive).
        # (unknown) has 25 investigate, all whiffs (rate=1.0, wasted_share=0.05)
        # but must never trigger -- it isn't a real zone to expand.
        rows = [{"kind": "header", "protagonist": "たろう"}]
        for _ in range(50):
            rows.append(_row(kind="decision", subject="たろう", verb="investigate",
                              effective=False, result="invalid", explanation={"zone": "海"}))
        for _ in range(10):
            rows.append(_row(kind="decision", subject="たろう", verb="investigate",
                              effective=False, result="invalid", explanation={"zone": "村"}))
        for _ in range(25):
            rows.append(_row(kind="decision", subject="たろう", verb="investigate",
                              effective=False, result="invalid"))
        # 390 more dwell decisions elsewhere, all effective, to pad total_dwell to 500
        # (50 + 10 + 25 investigate + 390 trade + 25 haggle below = 500).
        for _ in range(390):
            rows.append(_row(kind="decision", subject="たろう", verb="trade",
                              explanation={"zone": "町"}))
        # A verb below the whiff-rate threshold (50% exactly is the boundary;
        # 40% must not trigger even though it clears wasted_share).
        for i in range(25):
            rows.append(_row(kind="decision", subject="たろう", verb="haggle",
                              effective=(i >= 10), result="ok" if i >= 10 else "invalid",
                              explanation={"zone": "町"}))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layers.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                            encoding="utf-8")
            report = world_demand.collect([path])

        triggers = {(t["zone"], t["verb"]) for t in report["triggers"]}
        self.assertEqual(triggers, {("海", "investigate"), ("村", "investigate")})
        self.assertNotIn((world_demand.UNKNOWN_ZONE, "investigate"), triggers)
        self.assertNotIn(("町", "haggle"), triggers)  # whiff_rate 40% < 50% minimum

        # order: -wasted_share, then zone, then verb.
        self.assertEqual([(t["zone"], t["verb"]) for t in report["triggers"]],
                          [("海", "investigate"), ("村", "investigate")])
        sea_trigger = next(t for t in report["triggers"] if t["zone"] == "海")
        self.assertEqual(sea_trigger["whiff_rate"], 1.0)
        self.assertEqual(sea_trigger["wasted_share"], 0.1)
        self.assertEqual(sea_trigger["count"], 50)
        self.assertEqual(sea_trigger["whiffs"], 50)

    def test_a_handful_of_whiffs_never_triggers(self) -> None:
        def triggers(whiffs):
            rows = [{"kind": "header", "protagonist": "たろう"}] + [
                _row(kind="decision", subject="たろう", verb="investigate",
                     effective=False, explanation={"zone": "海"}) for _ in range(whiffs)]
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "layers.jsonl"
                path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                encoding="utf-8")
                return world_demand.collect([path])["triggers"]

        self.assertEqual(triggers(world_demand.WHIFFS_MIN - 1), [])
        self.assertEqual(len(triggers(world_demand.WHIFFS_MIN)), 1)

    def test_an_investigation_that_found_something_is_not_a_whiff(self) -> None:
        # The engine reports effective=False for a learned plain fact (knowledge
        # is not part of the layer snapshot); details still say what was found.
        rows = [
            {"kind": "header", "protagonist": "たろう"},
            _row(kind="decision", subject="たろう", verb="investigate", effective=False,
                 details={"learned": ["潮の知識"], "gathered": []}, explanation={"zone": "海"}),
            _row(kind="decision", subject="たろう", verb="investigate", effective=False,
                 details={"learned": [], "gathered": []}, explanation={"zone": "海"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "layers.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                            encoding="utf-8")
            sea = world_demand.collect([path])["zones"][0]

        self.assertEqual(sea["verbs"], [("investigate", 2, 0.5)])
        self.assertEqual(sea["ineffective_rate"], 0.5)

    def test_all_mode_excludes_lineage_rerun_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            experiment_dir = Path(tmp)
            run_path = experiment_dir / "g0" / "ind-0" / "seed-1" / "layers.jsonl"
            _write_layers(run_path)
            lineage_path = experiment_dir / "lineage" / "x" / "seed-1" / "layers.jsonl"
            _write_layers(lineage_path)

            report = world_demand.build_report(experiment_dir, use_all=True)

            self.assertEqual(report["files"], 1)
            self.assertEqual(report["population"], {"mode": "all", "files": 1, "skipped": 0})

    def test_build_report_adds_schema_and_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            experiment_dir = Path(tmp)
            layers_path = experiment_dir / "g0" / "ind-0" / "seed-1" / "layers.jsonl"
            _write_layers(layers_path)

            report = world_demand.build_report(experiment_dir, use_all=True)

            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["thresholds"], {
                "whiff_rate_min": world_demand.WHIFF_RATE_MIN,
                "wasted_share_min": world_demand.WASTED_SHARE_MIN,
                "whiffs_min": world_demand.WHIFFS_MIN,
            })
            self.assertIsNone(report["archive"])  # no archive.json in this fixture


if __name__ == "__main__":
    unittest.main()
