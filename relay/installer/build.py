"""Build an offline x64 Windows installer; no deployment secrets included."""
import argparse
import hashlib
import importlib.metadata as md
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
PYTHON_SHA = '59ff76e16e6597de47474fb22be69e7191a89116910d728ab735079b078e52db'
WINSW_SHA = '923111c7142b3dc783a3c722b19b8a21bcb78222d7a136ac33f0ca8a29f4cb66'


def checked(path, digest):
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Unexpected upstream archive hash: ' + path.name)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tools', type=Path, default=ROOT / 'test-output/relay-installer-tools')
    p.add_argument('--winsw', type=Path, required=True)
    a = p.parse_args()
    archive = a.tools / 'python-3.13.3-embed-amd64.zip'
    checked(archive, PYTHON_SHA)
    checked(a.winsw, WINSW_SHA)
    payload = a.tools / 'payload'
    # Build directory only; never delete deployment or runtime data.
    if payload.exists():
        if payload.resolve().parent != a.tools.resolve(): raise ValueError('Unsafe build path')
        shutil.rmtree(payload)
    (payload / 'python').mkdir(parents=True)
    with zipfile.ZipFile(archive) as z: z.extractall(payload / 'python')
    (payload / 'python/python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../app\nimport site\n', encoding='ascii')
    dependencies = set()
    def collect(name):
        d = md.distribution(name)
        canonical = d.metadata['Name'].lower().replace('_', '-')
        if canonical in dependencies: return
        dependencies.add(canonical)
        for requirement in d.requires or []:
            req = Requirement(requirement)
            if not req.marker or req.marker.evaluate({'extra': ''}): collect(req.name)
        for f in d.files or []:
            if '..' in f.parts or f.suffix == '.pyc': continue
            source = d.locate_file(f)
            if source.is_file():
                dest = payload / 'python/Lib/site-packages' / f
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
    for line in (ROOT / 'relay/requirements.txt').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            req = Requirement(line)
            if md.version(req.name) not in req.specifier: raise ValueError('Build dependency version mismatch')
            collect(req.name)
    for part in ('relay', 'shared'):
        shutil.copytree(ROOT / part, payload / 'app' / part,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.env', 'dist', 'installer'))
    (payload / 'app/installer').mkdir(parents=True)
    shutil.copy2(ROOT / 'relay/installer/bootstrap.py', payload / 'app/installer/bootstrap.py')
    (payload / 'service').mkdir()
    shutil.copy2(a.winsw, payload / 'service/WinSW.NET4.exe')
    (payload / 'DEPENDENCIES.txt').write_text('\n'.join(sorted(n + '==' + md.version(n) for n in dependencies)) + '\n', encoding='utf-8')
    subprocess.run([str(payload / 'python/python.exe'), '-c', 'import relay.app,shared.auth,ssl,sqlite3;print("Bundled runtime OK")'], check=True)
    out = ROOT / 'relay/dist'
    out.mkdir(exist_ok=True)
    subprocess.run([str(a.tools / 'inno/ISCC.exe'), '/DPayload=' + str(payload), '/DOutput=' + str(out),
        str(ROOT / 'relay/installer/setup.iss')], check=True)
    exe = out / 'AP-ESOP-Relay-1.0.0-Setup.exe'
    exe.with_suffix('.exe.sha256').write_text(hashlib.sha256(exe.read_bytes()).hexdigest() + '  ' + exe.name + '\n')
    print('Built', exe, exe.stat().st_size)


if __name__ == '__main__': main()
