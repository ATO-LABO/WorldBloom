"""Extract reviewable explanations without changing source runs."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from gapengine.explanations import extract_explanation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--experiment", default="")
    parser.add_argument("--cell", default="")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("output already exists; choose a new review output")
    value = extract_explanation(args.layers, experiment=args.experiment, cell=args.cell)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(f"explanation={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
