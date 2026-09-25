"""Regenerate (or check) the `python <script> --help` snippets used by reference/cli.md.

Usage:
    python docsite/gen_cli.py            # write docsite/snippets/cli/<name>.txt
    python docsite/gen_cli.py --check    # exit 1 if the committed snippets are stale

jev_* and rationality_probe.py are development-only tooling and are deliberately
excluded (WB-DOCS-003).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(__file__).resolve().parent / "snippets" / "cli"

TARGETS = [
    "scripts/evolve.py",
    "scripts/synopsize.py",
    "scripts/narrate.py",
    "scripts/random_baseline.py",
    "scripts/readable.py",
    "scripts/world_patch.py",
    "scripts/world_demand.py",
    "scripts/export_static.py",
    "scripts/pack_samples.py",
    "viewer/server.py",
]


def render(target: str) -> str:
    """The --help text for one script, with a stable width and no path leakage."""
    env = dict(os.environ, COLUMNS="100", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, target, "--help"],
        cwd=ROOT, env=env, capture_output=True, encoding="utf-8", check=True,
    )
    return result.stdout.replace("\r\n", "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                         help="Fail instead of writing, if any snippet is out of date.")
    args = parser.parse_args()

    stale = []
    for target in TARGETS:
        name = Path(target).stem
        text = render(target)
        out_path = OUT_DIR / f"{name}.txt"
        if args.check:
            current = out_path.read_text(encoding="utf-8") if out_path.exists() else None
            if current != text:
                stale.append(str(out_path.relative_to(ROOT)))
            continue
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8", newline="\n")

    if args.check and stale:
        print("CLI スニペットが古くなっています（python docsite/gen_cli.py で更新）:")
        for path in stale:
            print(f"  - {path}")
        return 1
    if not args.check:
        print(f"OK: wrote {len(TARGETS)} snippets to {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
