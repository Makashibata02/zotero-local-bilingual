param(
    [switch]$SkipWarmup
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot
try {
    uv python install 3.12
    uv sync --python 3.12 --extra dev
    uv pip install --python .venv\Scripts\python.exe --reinstall --no-deps -e .
    if (-not $SkipWarmup) {
        uv run babeldoc --warmup
    }
    Write-Host '本地服务环境已安装。下一步运行 .\doctor.ps1。' -ForegroundColor Green
}
finally {
    Pop-Location
}

