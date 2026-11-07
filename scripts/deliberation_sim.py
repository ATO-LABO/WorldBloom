"""Sample proposal deliberation with all predeclared outcomes retained."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gapengine.deliberation_episode import run_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=24)
    args = parser.parse_args()
    if not 1 <= args.trials <= 1000:
        parser.error("--trials must be in 1..1000")
    try:
        result = run_batch(args.world, args.out, list(range(args.trials)))
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "invalid_config_or_output", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return int(result["counts"].get("execution_failure", 0) > 0)


if __name__ == "__main__":
    raise SystemExit(main())
