import json
import tempfile
import unittest
from pathlib import Path

from scripts import world_demand


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

            # demand-descending order, ties broken by zone name.
            self.assertEqual([z["zone"] for z in report["zones"]],
                              ["海", world_demand.UNKNOWN_ZONE, "村"])

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


if __name__ == "__main__":
    unittest.main()
