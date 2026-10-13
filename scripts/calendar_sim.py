"""Run an explicitly configured minute-calendar project without action search.

Freeze creates a new immutable input closure and stops. Running it is a
separate explicit invocation with the frozen world path and choices JSON.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.calendar_checkpoint import strict_json
from engine.calendar_inputs import freeze_project, load_project
from engine.sim import Simulation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--choices', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--until-minute', type=int)
    parser.add_argument('--freeze-to', type=Path)
    args = parser.parse_args()
    try:
        if args.freeze_to:
            if args.choices or args.out or args.resume or args.until_minute is not None:
                raise ValueError('Freeze is a separate operation; run the frozen world explicitly afterward')
            version = freeze_project(args.world, args.freeze_to)
            result = {'status': 'frozen_only', 'world': str(args.freeze_to/version['world']),
                      'version_id': version['id'], 'api_calls': 0, 'gpu': False}
        else:
            if args.choices is None or args.out is None:
                raise ValueError('Run requires --choices and --out')
            if args.choices.is_symlink() or args.choices.stat().st_size > 8*1024*1024:
                raise ValueError('Choices must be a bounded regular JSON file')
            commands = strict_json(args.choices.read_text(encoding='utf-8'))
            world, people = load_project(args.world)
            simulation = Simulation(0, world, people, args.out)
            simulation.slot_runtime.configure(commands)
            path = simulation.slot_runtime.run(resume=args.resume, until=args.until_minute)
            result = {'status': 'completed' if simulation.slot_runtime.done else 'checkpointed',
                      'layers': str(path), 'api_calls': 0, 'gpu': False}
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'invalid_config_or_resume', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
