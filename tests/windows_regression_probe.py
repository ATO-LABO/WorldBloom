"""Fixed, diagnostic-only Windows probes around the full regression suite.

Probe failures remain visible in evidence; the complete suite is the CI gate.
There are no retries and no changes to either test's internal deadline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

from historical_fixtures import load_historical_fixture


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before-full", "after-full", "original-after-full"), required=True)
    args = parser.parse_args()
    evidence = Path(os.environ["WB_REGRESSION_EVIDENCE_DIR"]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WB_REGRESSION_PHASE=args.phase,
               WB_REGRESSION_DIAGNOSTICS="1",
               WB_EPOCH_CAPTURE_STDERR="1" if args.phase == "before-full" else "0")
    env["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "tests"), str(ROOT), env.get("PYTHONPATH", "")))
    node = shutil.which("node")
    try:
        node_version = subprocess.run([node, "--version"], capture_output=True,
                                      timeout=15, check=False) if node else None
        node_version_error = None
    except (subprocess.TimeoutExpired, OSError) as exc:
        node_version = None
        node_version_error = repr(exc)
    runtime = {"phase": args.phase, "python": sys.version,
               "python_executable": sys.executable, "platform": platform.platform(),
               "node_executable": node,
               "node_version_error": node_version_error,
               "node_version": (node_version.stdout.decode("utf-8", "replace").strip()
                                if node_version else None)}
    print(json.dumps(runtime), flush=True)
    targets = ["test_raw_workspace.RawWorkspaceTests.test_clipboard_exact_text_and_denied_permission_fallback"]
    if args.phase in ("before-full", "original-after-full"):
        targets.append("test_epoch_chain.HeavyRealChainEndToEndTest.test_two_epoch_auto_chain_with_real_ga_and_real_approval")
    results = []
    for index, target in enumerate(targets):
        started = time.monotonic()
        script_sha256 = None
        try:
            command = [sys.executable, "-B", "-m", "unittest", target, "-v"]
            bound = 650
            if args.phase == "original-after-full" and index == 0:
                # Exact original assertion-bearing harness; no added phase I/O.
                original = load_historical_fixture("original_raw_workspace_ui.cjs")
                script = evidence / "original-raw-workspace-ui.cjs"
                script.write_bytes(original)
                script_sha256 = hashlib.sha256(original).hexdigest()
                command = [node, str(script), str(ROOT / "viewer/static/raw-workspace.js")]
                bound = 15
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                    timeout=bound, check=False)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", None
            timed_out = True
        prefix = f"{args.phase}-{index}"
        for label, value in (("stdout", stdout), ("stderr", stderr)):
            # These are owned synthetic tests; keep bounded raw diagnostic output.
            (evidence / f"{prefix}.{label}.log").write_bytes(value[-262144:])
        record = {"target": target, "seconds": time.monotonic() - started,
                  "returncode": code, "probe_outer_timeout": timed_out,
                  "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
                  "skipped": b"OK (skipped=1)" in stderr,
                  "original_harness_sha256": script_sha256,
                  "mode": "original" if args.phase == "original-after-full" else "observed"}
        results.append(record)
        print(json.dumps(record), flush=True)
        print(stdout[-65536:].decode("utf-8", "replace"), flush=True)
        print(stderr[-65536:].decode("utf-8", "replace"), flush=True)
    (evidence / f"{args.phase}-probes.json").write_text(
        json.dumps({"runtime": runtime, "observation_only": True, "results": results},
                   indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
