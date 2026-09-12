"""One-shot reader-summary generation. No retries and no automatic approval."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from gapengine.reader_summary import (VERSION, artifact_name, build_packet, build_prompt,
    digest, parse_summary, read_json, review_path, verified_summary, write_new)
from gapengine.synopsis import generate_text, load_settings
from viewer.data import RunRepository, cell_explanation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--runs", type=Path, required=True)
    gen.add_argument("--experiment", required=True)
    gen.add_argument("--cell", required=True)
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--settings", type=Path, default=ROOT / "settings.json")
    gen.add_argument("--backend", choices=("none", "codex-cli", "claude-cli"), default="none")
    gen.add_argument("--timeout", type=int, default=180)
    approve = sub.add_parser("approve")
    approve.add_argument("artifact", type=Path)
    approve.add_argument("--expected-sha256", required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    if args.command == "approve":
        artifact = read_json(args.artifact)
        if digest(artifact) != args.expected_sha256:
            raise ValueError("review target changed")
        review = {"status": "approved", "artifact_sha256": args.expected_sha256,
                  "reviewer": args.reviewer, "note": args.note,
                  "at": datetime.now(timezone.utc).isoformat()}
        verified_summary(artifact, review, artifact["packet"])
        write_new(review_path(args.artifact), review)
        print("Editorial review saved; the viewer checks source freshness again.")
        return 0
    if not 1 <= args.timeout <= 240:
        raise ValueError("timeout must be 1..240 seconds")
    repo = RunRepository(args.runs)
    packet = build_packet(cell_explanation(repo, repo.experiment(args.experiment), args.cell))
    prompt = build_prompt(packet)
    if len(prompt) > 16000:
        raise ValueError("input exceeds 16000 characters")
    settings, warning = load_settings(args.settings)
    if warning:
        raise ValueError(warning)
    config = settings.get("output", {}).get(args.backend, {})
    model = config.get("model", "backend-default")
    if args.backend != "none" and not config.get("model"):
        raise ValueError("pilot requires explicit output backend model")
    destination = args.out / args.experiment / artifact_name(args.cell)
    # Reserve before calling: a repeated command cannot silently spend another call.
    write_new(destination.with_suffix(".request.json"), {
        "version": VERSION, "packet": packet, "prompt": prompt,
        "backend": args.backend, "model": model, "settings_sha256": digest(settings),
        "timeout_seconds": args.timeout, "max_calls": 1,
        "at": datetime.now(timezone.utc).isoformat()})
    started = time.monotonic()
    artifact = {"version": VERSION, "packet": packet, "prompt_sha256": digest(prompt),
                "backend": args.backend, "model": model, "settings_sha256": digest(settings)}
    try:
        result = generate_text(args.backend, prompt, settings_path=args.settings, timeout=args.timeout)
        artifact.update(status=result.status, response=result.text, warning=result.warning)
        if result.status == "ok":
            parse_summary(result.text, packet)
            artifact["status"] = "generated"
    except Exception as error:
        artifact.update(status="rejected", error=type(error).__name__)
    artifact["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_new(destination, artifact)
    print(json.dumps({"path": str(destination), "status": artifact["status"],
                      "artifact_sha256": digest(artifact)}, ensure_ascii=False))
    return 0 if artifact["status"] in {"generated", "prompt_only"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
