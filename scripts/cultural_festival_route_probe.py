"""Read-only check of b787363's route model against the frozen festival inputs.

Requires a separate detached checkout. Does not merge or replace route code.
These model surveys are not character candidate logs or a pattern-0 replay.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.cultural_festival_probe import (
    dump, digest, input_manifest, load, loaded_parameters, protected_manifest,
)

DEPENDENCY_COMMIT = "b787363a64a5d4dd490edf7f0fd5682290304b2c"


def dependency_modules(checkout):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout).decode().strip()
    if sha != DEPENDENCY_COMMIT:
        raise ValueError(f"Expected dependency {DEPENDENCY_COMMIT}, got {sha}")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=checkout).strip():
        raise ValueError("Dependency checkout must be clean")
    import gapengine
    for name in ("route_goals", "route_search"):
        key = "gapengine." + name
        spec = importlib.util.spec_from_file_location(key, checkout / "gapengine" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules["gapengine.route_goals"], sys.modules["gapengine.route_search"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-checkout", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    checkout, out = args.route_checkout.resolve(), args.out.resolve()
    if ROOT == out or ROOT in out.parents or checkout == out or checkout in out.parents:
        parser.error("Output must be outside both checkouts")
    if out.exists():
        parser.error("Output must be new")
    # No bytecode/cache files in the dependency tree.
    sys.dont_write_bytecode = True
    baseline = input_manifest()
    protected = protected_manifest()
    goals, route = dependency_modules(checkout)
    queries = {
        "primary_at_15": [{"predicate": "at", "zone": "体育館"}],
        "primary_and_partner_at_15": [{"predicate": "at", "zone": "体育館"},
                                     {"predicate": "at", "subject": "蓮", "zone": "体育館"}],
        "ren_request_known_at_15": [{"predicate": "at", "zone": "体育館"},
                                    {"predicate": "knows", "fact": "終盤短縮希望"}],
    }
    results = {}
    for name, predicates in queries.items():
        world, subjects = load()
        before = digest(loaded_parameters(subjects))
        query = {"actor": "蒼", "all": predicates,
                 "time": {"mode": "at", "day": 1, "slot": "15:00"}}
        cpu, wall = time.process_time(), time.perf_counter()
        result = route.search(world, subjects, goals.resolve_goal(query, world, subjects),
                              {"max_expansions": 1000})
        result.pop("start")  # Subject objects are only an internal model return.
        results[name] = {"query": query, "result": result,
            "cpu_seconds": time.process_time() - cpu,
            "wall_seconds": time.perf_counter() - wall,
            "loaded_initial_parameters_sha256": before,
            "input_state_unchanged": before == digest(loaded_parameters(subjects))}
    primary = results["primary_at_15"]["result"]
    assert primary["status"] == "found" and primary["reached"]["slot"] == "15:00"
    assert primary["cost"] == 18
    assert all(step["at"]["slot"] < "15:00" for step in primary["steps"])
    assert all(step["at"]["slot"] >= "08:00" for step in primary["steps"] if step["kind"] == "move")
    assert results["primary_and_partner_at_15"]["result"]["status"] == "unreachable"
    assert results["ren_request_known_at_15"]["result"]["status"] == "unreachable"
    assert all(r["input_state_unchanged"] for r in results.values())
    assert baseline == input_manifest() and protected == protected_manifest()
    output = {"dependency_commit": DEPENDENCY_COMMIT, "dependency_branch": "feat/generic-route-goals",
        "dependency_merged": False, "input_manifest": baseline, "results": results,
        "scope": "read-only model surveys on identical frozen settings; NOT candidate search or native replay",
        "usable_for": "15:00 primary location reachability, adjacent next-slot moves and early-arrival waiting",
        "not_supported": ["other characters autonomous choices", "consultation and knowledge sharing",
                          "practice/costume/sound/exhibit preparation", "personality-driven story candidate search"],
        "pattern0_pass": False, "native_contract_status": "BLOCKED",
        "interpretation": "unreachable applies only to the route projection, not the festival world",
        "protected_files_unchanged": True}
    dump(out / "generic-route-validation.json", output)
    print({name: r["result"]["status"] for name, r in results.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
