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
        command = ([sys.executable, "-B", "tests/regression_partition.py", *sys.argv[1:]]
                   if len(sys.argv) > 1 else
                   [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"])
        if sys.platform == 'win32' and os.environ.get('WB_PHASE_DIAGNOSTICS') == '1':
            command = [sys.executable, '-B', 'tests/ci_phase_suite.py', *sys.argv[1:]]
        process = subprocess.Popen(
            command,
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
