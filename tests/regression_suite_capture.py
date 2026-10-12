"""Stream the complete suite and retain its output without changing its result."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ["WB_REGRESSION_EVIDENCE_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / "full-suite.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
