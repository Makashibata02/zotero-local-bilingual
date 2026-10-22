"""Package one current release; do not duplicate engine or XPI aliases."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile
from repository_files import ROOT, source_files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', default='Makashibata02/zotero-local-bilingual')
    parser.add_argument('--reuse-xpi', action='store_true', help='Re-export source/checksums using an already verified XPI')
    args = parser.parse_args()
    version = json.loads((ROOT/'plugin/addon/manifest.json').read_text('utf8'))['version']
    release = ROOT/'dist'/f'release-{version}'
    release.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((release/'engine-manifest.json').read_text('utf8'))
    engines=manifest.get('engines', {manifest.get('platform'):manifest})
    for engine in engines.values():
        assert (release/engine['filename']).is_file()
    xpi = release/f'zotero-local-bilingual-{version}.xpi'
    if args.reuse_xpi:
        with zipfile.ZipFile(xpi) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read('manifest.json'))['version'] == version
            assert json.loads(archive.read('content/engine-manifest.json')) == manifest
    else:
        if not (ROOT/'plugin/build/manifest.json').is_file():
            raise FileNotFoundError('Build the frontend before packaging, or use --reuse-xpi explicitly')
        with zipfile.ZipFile(xpi, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((ROOT/'plugin/build').rglob('*')):
                if path.is_file() and path.suffix != '.map':
                    archive.write(path, path.relative_to(ROOT/'plugin/build').as_posix())
    with zipfile.ZipFile(release/f'local-bilingual-{version}-source.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            archive.write(path, path.relative_to(ROOT).as_posix())
    update = {'addons': {'local-bilingual@local.invalid': {'updates': []}}}
    if args.repository:
        update['addons']['local-bilingual@local.invalid']['updates'].append({
            'version':version,
            'update_link':f'https://github.com/{args.repository}/releases/download/v{version}/{xpi.name}',
            'applications':{'zotero':{'strict_min_version':'6.999','strict_max_version':'10.0.*'}},
        })
    (release/'updates.json').write_text(json.dumps(update,indent=2)+'\n','utf8')
    rows=[]
    for path in sorted(release.iterdir()):
        if path.is_file() and path.name!='SHA256SUMS':
            with path.open('rb') as stream:
                rows.append(hashlib.file_digest(stream,'sha256').hexdigest()+'  '+path.name)
    (release/'SHA256SUMS').write_text('\n'.join(rows)+'\n','utf8')
    print(str(release))


if __name__=='__main__':
    main()
