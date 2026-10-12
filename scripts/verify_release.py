"""Verify the local release and its bundled source/resources without running translation."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    requirements = (ROOT / 'runtime-requirements.txt').read_text('utf8')
    assert not re.search(r"--output-file\s+[A-Za-z]:[\\/]", requirements), "Dependency header contains a local path"
    manifest = json.loads((ROOT / 'plugin/addon/content/engine-manifest.json').read_text('utf8'))
    version = manifest['version']
    release = ROOT / 'dist' / f'release-{version}'
    engine = release / manifest['filename']
    assert engine.stat().st_size == manifest['size']
    assert digest(engine) == manifest['sha256']
    with zipfile.ZipFile(engine) as archive:
        assert archive.testzip() is None
        runtime = json.loads(archive.read('runtime.json'))
        assert runtime['version'] == version
        assert runtime['protocol_version'] == manifest['protocol_version']
        resources = json.loads(archive.read('assets/resources.json'))['files']
        for row in resources:
            data = archive.read('assets/' + row['path'])
            assert len(data) == row['size']
            assert hashlib.sha256(data).hexdigest() == row['sha256']
        for source in (ROOT / 'service/src/zotero_local_bilingual').rglob('*.py'):
            relative = source.relative_to(ROOT / 'service/src').as_posix()
            assert archive.read('python/Lib/site-packages/' + relative) == source.read_bytes()
        assert archive.read('licenses/uv.lock') == (ROOT / 'uv.lock').read_bytes()
        assert archive.read('licenses/runtime-requirements.txt') == (ROOT / 'runtime-requirements.txt').read_bytes()
        assert not any(n.endswith(('.pyc', '.db', '.db-wal', '.db-shm')) for n in archive.namelist())
    xpi = release / f'zotero-local-bilingual-{version}.xpi'
    with zipfile.ZipFile(xpi) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read('manifest.json'))['version'] == version
        assert json.loads(archive.read('content/engine-manifest.json')) == manifest
        assert not any(n.endswith('.map') for n in archive.namelist())
    source_zip = release / f'local-bilingual-{version}-source.zip'
    with zipfile.ZipFile(source_zip) as archive:
        assert archive.testzip() is None
        assert not any(n.startswith(('output/', '.venv/', 'dist/')) or
                       '/node_modules/' in n or n.endswith(('.db', '.pyc', '.map'))
                       for n in archive.namelist())
    for row in (release / 'SHA256SUMS').read_text('utf8').splitlines():
        expected, name = row.split('  ', 1)
        assert digest(release / name) == expected, name
    result = {'ok': True, 'version': version, 'engine_size': manifest['size'],
              'engine_sha256': manifest['sha256'], 'resources_verified': len(resources),
              'xpi_size': xpi.stat().st_size, 'xpi_sha256': digest(xpi),
              'source_and_archive_integrity': True}
    output = ROOT / f'output/acceptance-{version.replace(".","")}/release-verification.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), 'utf8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
