"""Read-only "world demand" report CLI (WB-WORLDGROW-001, stage 2).

Thin wrapper around gapengine.world_demand.build_report(): prints a Japanese
zone table plus the "拡張トリガー" section (zones/verbs that whiff almost
every time and eat a real share of the story's decisions -- candidates for
"the world's content is thin here").

Writes nothing under the experiment directory unless --save is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.world_demand import build_report


def _print_table(report: dict) -> None:
    print(f"読んだファイル数: {report['files']}（スキップ {report['skipped_paths']}）"
          f" / 主人公の決定数: {report['subject_decisions']}")
    print()
    header = ("ゾーン", "滞在決定数", "滞在シェア", "繰り返し率", "空振り率", "前例確率", "候補数")
    print("\t".join(header))
    for z in report["zones"]:
        print("\t".join(str(v) for v in (
            z["zone"], z["dwell"], z["dwell_share"], z["repeat_rate"], z["ineffective_rate"],
            z["mean_p_prec"], z["mean_candidates"])))
        verbs = "、".join(f"{v}×{c}(空振り{w:.0%})" for v, c, w in z["verbs"]) or "(なし)"
        reasons = "、".join(f"{r}×{c}" for r, c in z["ineffective_reasons"]) or "(なし)"
        print(f"    主な行動: {verbs} / 空振り理由: {reasons}")
    archive = report.get("archive")
    if archive:
        print()
        print(f"archive: セル数={archive['cells']} / 世代数={archive['generations']} "
              f"/ セルの最終改善世代: {archive['last_improved']}")

    print()
    print("拡張トリガー:")
    triggers = report.get("triggers") or []
    if not triggers:
        print("    拡張トリガーなし")
    # WB-WORLDGROW-002 S1: "ignorance"/"blocked" (route-wired runs only)
    # are shaped differently from the original "whiff" trigger -- printed
    # plainly here (stage 2-4 owns the screen/proposal wording).
    for t in triggers:
        kind = t.get("kind", "whiff")
        if kind == "whiff":
            print(f"    [whiff] {t['zone']} で {t['verb']}: {t['count']} 回中 {t['whiffs']} 回が空振り"
                  f"（空振り率 {t['whiff_rate']:.0%}、全滞在決定の {t['wasted_share']:.1%}）")
        elif kind == "ignorance":
            print(f"    [ignorance] {t['zone']}: 手探り {t['count']} 回"
                  f"（その場所の道筋付き決定の {t['share']:.1%}）")
        elif kind == "blocked":
            print(f"    [blocked] {t['requirement']}: 見通しなし {t['count']} 回 / {t['runs']} ラン"
                  f"（全見通しなし決定の {t['share']:.1%}、全道筋付き決定の {t['lost_share']:.1%}、"
                  f"詰まった場所 {t['stuck_zones']}、入手元 {t['source_zones']}、"
                  f"持ち主 {t['held_by']}、理由 {t['reason']}）")
        else:
            print(f"    [{kind}] {t}")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment_dir", type=Path)
    parser.add_argument("--all", action="store_true",
                         help="scan every layers.jsonl instead of just archive exemplars")
    parser.add_argument("--subject", help="decision subject to track (default: each file's protagonist)")
    parser.add_argument("--out", type=Path, help="also write the JSON report here")
    parser.add_argument("--save", action="store_true",
                         help="also write <experiment_dir>/world_demand.json (backfill for an existing run)")
    args = parser.parse_args(argv)

    experiment_dir = args.experiment_dir.resolve()
    if args.out is not None:
        out_resolved = args.out.resolve()
        if out_resolved == experiment_dir or experiment_dir in out_resolved.parents:
            raise ValueError("--out must not be inside experiment_dir")

    report = build_report(experiment_dir, use_all=args.all, subject=args.subject)

    _print_table(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    if args.save:
        (experiment_dir / "world_demand.json").write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
