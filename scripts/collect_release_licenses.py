"""Collect exact installed notices. Missing required notices stop packaging."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import hashlib

RUNTIME_PACKAGES = {'pywebview', 'pyyaml', 'pythonnet', 'clr-loader', 'cffi',
                    'pycparser', 'bottle', 'proxy-tools', 'typing-extensions', 'pyinstaller'}

def canonical(name):
    return name.lower().replace('_', '-')

def collect(destination):
    destination.mkdir(parents=True, exist_ok=False)
    inventory = []
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise ValueError('CPython LICENSE.txt is missing')
    target = destination / 'CPython' / 'LICENSE.txt'
    target.parent.mkdir()
    shutil.copyfile(python_license, target)
    inventory.append({'package': 'CPython', 'version': sys.version.split()[0],
                      'file': 'CPython/LICENSE.txt',
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    vendor = Path(__file__).resolve().parents[1] / 'release/licenses/Microsoft.Web.WebView2.LICENSE.md'
    data = vendor.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if blob != '5dd43f57f273797aea6904ae8000c870e424b35a':
        raise ValueError('WebView2 vendor notice differs from reviewed upstream bytes')
    target = destination / 'Microsoft.Web.WebView2.LICENSE.md'
    target.write_bytes(data)
    inventory.append({'package': 'Microsoft.Web.WebView2', 'version': 'pywebview-6.2.1',
                      'file': target.name, 'sha256': hashlib.sha256(data).hexdigest()})
    installed = {canonical(dist.metadata['Name']): dist
                 for dist in importlib.metadata.distributions()}
    for name in ('pywebview', 'pyyaml', 'pythonnet', 'pyinstaller'):
        if name not in installed:
            raise ValueError('Required Windows dependency missing: ' + name)
    for name, dist in sorted(installed.items()):
        paths = []
        for file in dist.files or ():
            parts = Path(str(file)).parts
            basename = Path(str(file)).name.lower()
            if (any(part.endswith('.dist-info') or part.endswith('.egg-info') for part in parts)
                    and basename.startswith(('license', 'copying', 'notice', 'copyright'))
                    and not basename.endswith(('.py', '.pyc'))):
                paths.append(file)
        if name in RUNTIME_PACKAGES and not paths:
            raise ValueError('Required third-party notice missing: ' + name)
        for index, file in enumerate(paths):
            source = Path(dist.locate_file(file))
            if not source.is_file() or source.is_symlink():
                raise ValueError('Third-party notice is missing or linked: ' + name)
            relative = f'{name}/{dist.version}/{index}-{source.name}'
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            inventory.append({'package': name, 'version': dist.version, 'file': relative,
                              'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    (destination / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    return inventory


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'third_party_license_inventory': collect(args.output)}, indent=2))
