"""Exercise relocated app source with isolated Python and no AI calls."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(app))
    from viewer import app_desktop, version
    from execution.provenance import code_snapshot
    assert app_desktop._looks_like_app_root(app)
    assert version.current()['sha'] == '36bb15c383ed35c615b5505425c905cb2a612eb4'
    assert version.current()['source'] == 'VERSION.txt'
    blobs, provenance = code_snapshot(app)
    assert 'requirements.txt' in blobs and any(p.startswith('scripts/') for p in blobs)
    command = [sys.executable, '-I', '-B', str(app / 'scripts/evolve.py')]
    help_result = subprocess.run(command + ['--help'], capture_output=True, text=True,
                                 encoding='utf-8', timeout=30, check=True)
    for flag in ('--project', '--template', '--out', '--generations', '--population',
                 '--seeds', '--processes', '--kappa', '--world-expansion'):
        assert flag in help_result.stdout
    ga_command = command + ['--project', str(app / 'projects/momotaro_plus3_new'),
        '--template', str(app / 'templates/momotaro_plus3'),
        '--out', str(args.output / 'ga'), '--generations', '1', '--population', '2',
        '--seeds', '1', '--processes', '1', '--kappa', '0', '--world-expansion', 'off']
    ga = subprocess.run(ga_command, capture_output=True, text=True, encoding='utf-8',
                        timeout=120)
    (args.output / 'ga.stdout.log').write_text(ga.stdout, encoding='utf-8')
    (args.output / 'ga.stderr.log').write_text(ga.stderr, encoding='utf-8')
    ga.check_returncode()
    archive = json.loads((args.output / 'ga/archive.json').read_text(encoding='utf-8'))
    assert isinstance(archive, dict)
    result = {'scope': 'isolated source subprocess, not frozen GUI or LLM generation',
        'source_sha': version.current()['sha'], 'source_files': len(blobs),
        'requirements_in_snapshot': True, 'isolated_python': sys.version,
        'pyyaml': provenance['pyyaml'], 'cli_help': 'passed', 'minimal_ga': 'passed',
        'llm_calls_requested': 0}
    (args.output / 'payload-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
