"""Exercise relocated app source with isolated Python and no AI calls."""
import argparse
import codecs
import hashlib
import json
from pathlib import Path
import subprocess
import sys

# -I ignores PYTHONUTF8/PYTHONIOENCODING. Set the pipe encoding explicitly
# rather than depending on the runner locale or relaxing isolated execution.
def isolated_python():
    return [sys.executable, '-I', '-B', '-X', 'utf8']


def run_checked(command, output, label, timeout):
    if label not in {'encoding', 'help', 'ga'}:
        raise ValueError('Unknown payload diagnostic stage')
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b'', error.stderr or b''
        failure = error
        returncode = None
    else:
        stdout, stderr = result.stdout, result.stderr
        failure = None
        returncode = result.returncode
    # Keep original bytes locally, including failures; never auto-upload them.
    (output / (label + '.stdout.log')).write_bytes(stdout)
    (output / (label + '.stderr.log')).write_bytes(stderr)
    if failure is not None or returncode != 0:
        # Do not print arbitrary child output, paths, environment, or secrets.
        kinds = ('UnicodeEncodeError', 'UnicodeDecodeError', 'ModuleNotFoundError',
                 'ImportError', 'FileNotFoundError', 'SyntaxError')
        diagnostic = {'stage': label, 'returncode': returncode,
            'timeout': failure is not None,
            'stderr_exception_markers': [kind for kind in kinds if kind.encode() in stderr],
            'stdout_bytes': len(stdout), 'stderr_bytes': len(stderr),
            'stdout_sha256': hashlib.sha256(stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(stderr).hexdigest()}
        (output / (label + '.failure.json')).write_text(json.dumps(diagnostic) + '\n', encoding='utf-8')
        print(json.dumps(diagnostic), flush=True)
        if failure is not None:
            raise failure
        result.check_returncode()
    return stdout.decode('utf-8'), stderr.decode('utf-8')


def check_child_encoding(output):
    code = ("import json,sys; print(json.dumps({'stdout':sys.stdout.encoding,"
            "'stderr':sys.stderr.encoding,'utf8_mode':sys.flags.utf8_mode,"
            "'isolated':sys.flags.isolated,'ignore_environment':sys.flags.ignore_environment}))")
    stdout, _ = run_checked(isolated_python() + ['-c', code], output, 'encoding', 30)
    state = json.loads(stdout)
    if (set(state) != {'stdout', 'stderr', 'utf8_mode', 'isolated', 'ignore_environment'}
            or any(codecs.lookup(state[key]).name != 'utf-8' for key in ('stdout', 'stderr'))
            or any(state[key] != 1 for key in ('utf8_mode', 'isolated', 'ignore_environment'))):
        raise ValueError('Child Python encoding/isolation contract failed')
    return state


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
    assert version.current()['sha'] == '3ce8fbf376e25eaea533bdc69c6a8c80e121e390'
    assert version.current()['source'] == 'VERSION.txt'
    blobs, provenance = code_snapshot(app)
    assert 'requirements.txt' in blobs and any(p.startswith('scripts/') for p in blobs)
    child_encoding = check_child_encoding(args.output)
    command = isolated_python() + [str(app / 'scripts/evolve.py')]
    help_stdout, _ = run_checked(command + ['--help'], args.output, 'help', 30)
    for flag in ('--project', '--template', '--out', '--generations', '--population',
                 '--seeds', '--processes', '--kappa', '--world-expansion'):
        assert flag in help_stdout
    ga_command = command + ['--project', str(app / 'projects/momotaro_plus3_new'),
        '--template', str(app / 'templates/momotaro_plus3'),
        '--out', str(args.output / 'ga'), '--generations', '1', '--population', '2',
        '--seeds', '1', '--processes', '1', '--kappa', '0', '--world-expansion', 'off']
    run_checked(ga_command, args.output, 'ga', 120)
    archive = json.loads((args.output / 'ga/archive.json').read_text(encoding='utf-8'))
    assert isinstance(archive, dict)
    result = {'scope': 'isolated source subprocess, not frozen GUI or LLM generation',
        'source_sha': version.current()['sha'], 'source_files': len(blobs),
        'requirements_in_snapshot': True, 'isolated_python': sys.version,
        'pyyaml': provenance['pyyaml'], 'cli_help': 'passed', 'minimal_ga': 'passed',
        'llm_calls_requested': 0, 'child_python': child_encoding}
    (args.output / 'payload-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
