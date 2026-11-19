"""Explicit CLI only. An absent/disabled config performs no process or network work."""
import argparse
from pathlib import Path
import sys

from .contract import canonical, strict_json
from .controller import Controller
from .errors import DeliveryError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source-sha")
    args = parser.parse_args()
    try:
        config = strict_json(args.config.read_bytes()) if args.config else {"enabled": False}
        if not isinstance(config, dict):
            raise DeliveryError("local_config")
        result = Controller(config).check_once(apply_update=args.apply, source_sha=args.source_sha)
        sys.stdout.buffer.write(canonical(result))
        return 0
    except (DeliveryError, OSError) as exc:
        code = exc.code if isinstance(exc, DeliveryError) else "local_io_error"
        # Do not echo response bodies, credentials, paths or stack traces to Studio.
        sys.stdout.buffer.write(canonical({"status": "blocked", "code": code, "can_launch": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
