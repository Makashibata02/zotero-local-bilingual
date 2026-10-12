"""Explicit source upload/export allowlist; never export user state."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = (
    'README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY.md', 'CHANGELOG.md',
    'CONTRIBUTING.md', 'SECURITY.md', '.gitignore', '.gitattributes', '.editorconfig',
    'pyproject.toml', 'uv.lock', 'runtime-requirements.txt',
    'build-plugin.ps1', 'doctor.ps1', 'setup.ps1', 'start-service.ps1',
    'service/src', 'service/tests', 'plugin/src', 'plugin/addon', 'plugin/tests',
    'plugin/package.json', 'plugin/pnpm-lock.yaml', 'plugin/pnpm-workspace.yaml',
    'plugin/tsconfig.json', 'plugin/build.mjs', 'scripts', '.github', 'docs',
)
DENIED_SUFFIXES = {'.pyc','.log','.db','.sqlite','.sqlite3','.pdf','.xpi','.zip','.pem','.key'}


def source_files():
    for name in ROOTS:
        location = ROOT / name
        if not location.exists():
            raise FileNotFoundError(name)
        candidates = sorted(location.rglob('*')) if location.is_dir() else [location]
        for path in candidates:
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if not path.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError('Source path escapes repository: ' + str(path))
            if path.suffix in DENIED_SUFFIXES or path.name.startswith('.env') or path.name in {
                'service-token.txt','profiles.json','managed-service.json'}:
                raise ValueError('Private/generated file in upload roots: ' + str(path))
            yield path


if __name__ == '__main__':
    for path in source_files():
        print(path.relative_to(ROOT).as_posix())
