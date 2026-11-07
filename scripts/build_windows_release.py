"""Build the original launcher, adding stdlib imports required by external app code."""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

PACKAGES = ('viewer', 'execution', 'gapengine', 'engine', 'scripts')

def hidden_stdlib(source):
    modules = set()
    for package in PACKAGES:
        for path in (source / package).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_bytes())):
                names = []
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module] + [node.module + '.' + item.name for item in node.names
                                            if item.name != '*']
                for name in names:
                    if name.split('.')[0] not in sys.stdlib_module_names:
                        continue
                    try:
                        if importlib.util.find_spec(name) is not None:
                            modules.add(name)
                    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
                        pass  # Imported attribute or a module unavailable on this OS.
    # No application package is frozen; PyInstaller follows these stdlib imports.
    return sorted(modules)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Windows build runner required')
    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError('Refusing to overwrite build output')
    output.mkdir(parents=True)
    modules = hidden_stdlib(source)
    commands = []
    for mode in ('WorldBloom-Studio',):
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile',
            '--windowed', '--name', mode, '--collect-all', 'webview', '--collect-all',
            'yaml', '--icon', str(source / 'viewer/worldbloom.ico'), '--distpath',
            str(output / 'dist'), '--workpath', str(output / 'build'),
            '--specpath', str(output / 'build')]
        for module in modules:
            command += ['--hidden-import', module]
        command += [str(source / 'viewer/app_desktop.py')]
        commands.append(command)
        subprocess.run(command, check=True, cwd=source)
    (output / 'build-commands.json').write_text(json.dumps(
        {'stdlib_hiddenimports': modules, 'commands': commands}, indent=2) + '\n')

if __name__ == '__main__':
    main()
