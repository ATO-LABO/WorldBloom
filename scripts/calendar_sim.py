"""Run a minute-calendar project with explicit situation commands.

Worlds declaring planning/calendar objectives select later actions through the
shared goal planner; other worlds retain explicit-command execution.

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
    parser.add_argument('--storage', choices=('legacy', 'native'), default='legacy',
                        help='Native requires an existing dedicated --out directory')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--expected-size', type=int)
    parser.add_argument('--expected-stream-id')
    parser.add_argument('--expected-ordinal', type=int)
    args = parser.parse_args()
    native_fields = (args.expected_sha256, args.expected_size,
                     args.expected_stream_id, args.expected_ordinal)
    progress = None
    try:
        if args.storage != 'native' and any(value is not None for value in native_fields):
            raise ValueError('Independent expected reference fields require --storage native')
        if args.freeze_to:
            if (args.choices or args.out or args.resume or args.until_minute is not None
                    or args.storage != 'legacy' or any(value is not None for value in native_fields)):
                raise ValueError('Freeze is a separate operation; run the frozen world explicitly afterward')
            version = freeze_project(args.world, args.freeze_to)
            result = {'status': 'frozen_only', 'world': str(args.freeze_to/version['world']),
                      'version_id': version['id'], 'api_calls': 0, 'gpu': False}
        elif args.storage == 'native':
            from engine.calendar_generation_runtime import GenerationProgress
            from execution.calendar_native import run_native_calendar, selected_from_values
            selection = selected_from_values(resume=args.resume, sha256=args.expected_sha256,
                size=args.expected_size, stream_id=args.expected_stream_id, ordinal=args.expected_ordinal)
            if args.choices is None or args.out is None:
                raise ValueError('Run requires --choices and --out')
            progress = GenerationProgress()
            result = run_native_calendar(args.world, args.choices, args.out,
                selection=selection, until=args.until_minute, progress=progress)
        else:
            if args.choices is None or args.out is None:
                raise ValueError('Run requires --choices and --out')
            from execution.calendar_native import capture_choices
            commands = capture_choices(args.choices).commands()
            world, people = load_project(args.world)
            simulation = Simulation(0, world, people, args.out)
            simulation.slot_runtime.configure(commands)
            path = simulation.slot_runtime.run(resume=args.resume, until=args.until_minute)
            result = {'status': 'completed' if simulation.slot_runtime.done else 'checkpointed',
                      'layers': str(path), 'api_calls': 0, 'gpu': False}
    except (Exception, KeyboardInterrupt) as exc:
        if args.storage != 'native' and not isinstance(exc, (ValueError, OSError)):
            raise  # Preserve legacy exception behavior.
        failure = {'status': 'cancelled' if isinstance(exc, KeyboardInterrupt) else
                   'invalid_config_or_resume', 'error': str(exc)}
        if args.storage == 'native':
            from execution.calendar_native import selection_value
            failure.update(storage='generic_calendar_native_generation_v1',
                selected_generation=selection_value(None if progress is None else progress.selection),
                generation_saved=False if progress is None else progress.saved,
                publication_accepted=False,
                failed_attempt='unknown; do not infer absence or retry automatically')
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
