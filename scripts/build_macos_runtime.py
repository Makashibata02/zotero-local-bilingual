"""Build Mac engine candidates using locked wheels and verified shared assets.

Can cross-assemble on Windows. A cross build is explicitly NOT native-verified;
run acceptance_runtime_handoff.py on the target Mac before public release.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform as host_platform
import shutil
import stat
import subprocess
import sys
import zipfile
from sanitize_runtime import sanitize_examples

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT/'plugin/addon/manifest.json').read_text('utf8'))['version']
TARGETS = {'macos-arm64': ('aarch64', 0x100000c), 'macos-x64': ('x86_64', 0x1000007)}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_assets(archive_path, stage):
    archive_path = archive_path.resolve()
    manifest = json.loads((archive_path.parent/'engine-manifest.json').read_text('utf8'))
    candidates = manifest.get('engines', {'single': manifest}).values()
    expected = next((m for m in candidates if m['filename'] == archive_path.name), None)
    if not expected or digest(archive_path) != expected['sha256'] or archive_path.stat().st_size != expected['size']:
        raise ValueError('Asset source archive does not match its release manifest')
    with zipfile.ZipFile(archive_path) as archive:
        rows = json.loads(archive.read('assets/resources.json'))['files']
        for row in rows:
            target = stage/'assets'/row['path']
            if not target.resolve().is_relative_to((stage/'assets').resolve()):
                raise ValueError('Unsafe asset path')
            payload = archive.read('assets/'+row['path'])
            if len(payload) != row['size'] or hashlib.sha256(payload).hexdigest() != row['sha256']:
                raise ValueError('Asset hash mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (stage/'assets/resources.json').write_bytes(archive.read('assets/resources.json'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=TARGETS, required=True)
    parser.add_argument('--python-home', type=Path, required=True, help='uv-managed portable CPython 3.12.14 for this target')
    parser.add_argument('--assets-from', type=Path, required=True, help='An existing hash-verified engine ZIP; shares only immutable assets')
    parser.add_argument('--uv', default='uv')
    parser.add_argument('--repository', default='Makashibata02/zotero-local-bilingual')
    args = parser.parse_args()
    if not __import__('re').fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        raise ValueError('Invalid repository')
    arch, cpu = TARGETS[args.platform]
    python_home = args.python_home.resolve()
    interpreter = python_home/'bin/python3.12'
    with interpreter.open('rb') as stream:
        header = stream.read(8)
    if header[:4] != b'\xcf\xfa\xed\xfe' or int.from_bytes(header[4:8], 'little') != cpu:
        raise ValueError('Portable Python is not a Mach-O interpreter for the requested architecture')
    stage = ROOT/'output/runtime-build'/f'{VERSION}-{args.platform}'
    if stage.exists():
        raise FileExistsError('Build staging already exists; inspect it before removing or replacing it: '+str(stage))
    stage.mkdir(parents=True)
    # Flatten symlinks to regular files: the installer never creates archive links.
    for path in python_home.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(python_home):
            raise ValueError('Portable Python contains an external symlink')
    shutil.copytree(python_home, stage/'python', symlinks=False,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    site = stage/'python/lib/python3.12/site-packages'
    # Discard PBS installer packages; install only the locked production set.
    if site.exists():
        assert site.resolve().is_relative_to(stage.resolve())
        shutil.rmtree(site)
    env = {**os.environ, 'MACOSX_DEPLOYMENT_TARGET': '14.0', 'UV_LINK_MODE': 'copy'}
    subprocess.run([args.uv, 'pip', 'sync', str(ROOT/'runtime-requirements.txt'),
        '--target', str(site), '--python-version', '3.12', '--python-platform', f'{arch}-apple-darwin',
        '--only-binary', ':all:', '--require-hashes', '--no-compile-bytecode'], env=env, check=True)
    shutil.copytree(ROOT/'service/src/zotero_local_bilingual', site/'zotero_local_bilingual',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    sanitize_examples(site)
    copy_assets(args.assets_from, stage)
    licenses = stage/'licenses'; licenses.mkdir()
    for name in ('LICENSE', 'THIRD_PARTY.md', 'uv.lock', 'runtime-requirements.txt'):
        shutil.copy2(ROOT/name, licenses/name)
    shutil.copy2(ROOT/'docs/font-license-inventory.json', licenses/'font-license-inventory.json')
    dependencies = sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions(path=[str(site)]))
    # Console scripts embed build paths and are not used by the plugin.
    for directory in (site/'bin', site/'Scripts'):
        if directory.exists():
            assert directory.resolve().is_relative_to(stage.resolve()); shutil.rmtree(directory)
    for path in (stage/'python/bin').iterdir():
        if path.name != 'python3.12':
            assert path.resolve().is_relative_to(stage.resolve()); path.unlink()
    for path in sorted(site.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.name in {'tests', 'test', '__pycache__'}:
            assert path.resolve().is_relative_to(stage.resolve()); shutil.rmtree(path)
    for path in stage.rglob('*.pyc'):
        path.unlink()
    runtime = {'version': VERSION, 'protocol_version': 2, 'platform': args.platform,
               'python': '3.12.14', 'babeldoc': '0.6.4', 'minimum_os': '14.0',
               'native_verified': False, 'dependencies': dependencies}
    # Native builds must load resources and exercise an isolated service before claiming validation.
    if sys.platform == 'darwin' and host_platform.machine() == ('arm64' if arch == 'aarch64' else arch):
        (stage/'python/bin/python3.12').chmod(0o755)
        (stage/'runtime.json').write_text(json.dumps(runtime, indent=2), 'utf8')
        subprocess.run([sys.executable, str(ROOT/'scripts/acceptance_runtime_handoff.py'), '--runtime', str(stage)], check=True)
        runtime['native_verified'] = True
    runtime['installed_size'] = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file() and p.name != 'runtime.json')
    (stage/'runtime.json').write_text(json.dumps(runtime, indent=2), 'utf8')
    from verify_macos_binaries import audit
    audit(stage, args.platform)  # Refuse missing architecture or non-portable load dependencies.
    release = ROOT/'dist'/f'release-{VERSION}'; release.mkdir(parents=True, exist_ok=True)
    filename = f'zlb-engine-{VERSION}-{args.platform}.zip'; target = release/filename
    print('Compressing '+args.platform, flush=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(stage.rglob('*')):
            if not path.is_file(): continue
            relative = path.relative_to(stage).as_posix()
            info = zipfile.ZipInfo(relative); info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | (0o755 if relative == 'python/bin/python3.12' else 0o644)) << 16
            with path.open('rb') as src, archive.open(info, 'w', force_zip64=True) as dest:
                shutil.copyfileobj(src, dest)
    manifest = {'version': VERSION, 'protocol_version': 2, 'platform': args.platform, 'filename': filename,
        'minimum_os': '14.0', 'native_verified': runtime['native_verified'],
        'url': f'https://github.com/{args.repository}/releases/download/v{VERSION}/{filename}',
        'size': target.stat().st_size, 'installed_size': runtime['installed_size'], 'sha256': digest(target)}
    (release/f'engine-manifest-{args.platform}.json').write_text(json.dumps(manifest, indent=2)+'\n', 'utf8')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
