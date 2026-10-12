$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location (Join-Path $ProjectRoot 'plugin')
try {
    corepack pnpm install --frozen-lockfile
    corepack pnpm run check
    corepack pnpm test
    corepack pnpm run build
} finally { Pop-Location }
Push-Location $ProjectRoot
try { uv run python scripts/package_release.py --repository Makashibata02/zotero-local-bilingual }
finally { Pop-Location }
