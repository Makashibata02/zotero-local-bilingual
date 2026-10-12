"""Redacted source/archive scan with hash-pinned non-secret classifications."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from repository_files import ROOT, source_files


def scan(tool, target, report, exceptions=False):
    report.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='zlb-scan-rules-') as temporary:
        ignore=Path(temporary)/'.gitleaksignore'
        rows=[]
        if exceptions:
            notes=json.loads((ROOT/'docs/dependency-scan-exceptions.json').read_text('utf8'))
            for row in notes['entries']:
                path=target/row['path']
                assert path.resolve().is_relative_to(target.resolve())
                with path.open('rb') as stream:
                    digest=hashlib.file_digest(stream,'sha256').hexdigest()
                if digest!=row['sha256']:
                    raise ValueError('Dependency classification changed: '+row['path'])
                rows.append(f"{row['path']}:{row['rule']}:{row['line']}")
        ignore.write_text('\n'.join(rows)+'\n','utf8')
        subprocess.run([str(tool),'dir','.', '--redact','--no-banner',
            '--gitleaks-ignore-path',str(ignore),'--report-format','json','--report-path',str(report)],
            cwd=target,check=True)
    return len(rows)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gitleaks',type=Path,required=True)
    parser.add_argument('--engine',action='store_true')
    args=parser.parse_args()
    tool=args.gitleaks.resolve()
    with tempfile.TemporaryDirectory(prefix='zlb-release-scan-') as temporary:
        target=Path(temporary)
        if args.engine:
            version=json.loads((ROOT/'plugin/addon/manifest.json').read_text('utf8'))['version']
            engine=ROOT/'dist'/f'release-{version}'/f'zlb-engine-{version}-windows-x64.zip'
            with zipfile.ZipFile(engine) as archive:
                for member in archive.infolist():
                    if not (target/member.filename).resolve().is_relative_to(target.resolve()):
                        raise ValueError('Archive path escapes root')
                archive.extractall(target)
            count=scan(tool,target,ROOT/'output/privacy/engine.json',exceptions=True)
        else:
            for source in source_files():
                destination=target/source.relative_to(ROOT)
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,destination)
            count=scan(tool,target,ROOT/'output/privacy/source.json')
    print(json.dumps({'ok':True,'kind':'engine' if args.engine else 'source',
        'unresolved_findings':0,'verified_nonsecret_classifications':count}))


if __name__=='__main__':
    main()
