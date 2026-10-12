param(
    [switch]$ShowToken,
    [int]$Port = 17865
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot
try {
    Write-Host "Project: $ProjectRoot"
    uv --version
    uv run python --version
    uv run python -c "import babeldoc, pymupdf, zotero_local_bilingual; print('BabelDOC 0.6.4'); print('PyMuPDF', pymupdf.version[0]); print('Service', zotero_local_bilingual.__version__)"
    uv run python -c "from babeldoc.docvision.doclayout import DocLayoutModel; m=DocLayoutModel.load_onnx(); print('Layout model: OK', type(m).__name__)"
    if ($ShowToken) {
        Write-Host 'Service token:' -ForegroundColor Yellow
        uv run zotero-local-bilingual --show-token
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/health" -TimeoutSec 3
        Write-Host "Running service: $($health.engine) $($health.engine_version)" -ForegroundColor Green
    }
    catch {
        Write-Host 'Service is not running. Start it with .\start-service.ps1.' -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}

