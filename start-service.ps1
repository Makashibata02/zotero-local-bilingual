param(
    [int]$Port = 17865
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot
try {
    uv run zotero-local-bilingual --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}

