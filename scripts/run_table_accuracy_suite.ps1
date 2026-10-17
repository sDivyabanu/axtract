param(
    [Parameter(Mandatory=$true)][string]$Label,
    [string]$Revision = "",
    [string]$MainRepository = "C:/Users/sdivy/wdev/Projects/axtract"
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$appPython = Join-Path $MainRepository 'backend/.venv/Scripts/python.exe'
$scorerPython = Join-Path $MainRepository 'axtract-olmocr-data/python/cpython-3.12.15-windows-x86_64-none/python.exe'
$dataRoot = Join-Path $MainRepository 'benchmarks/olmOCR-bench/bench_data'
$scorerRoot = Join-Path $MainRepository 'axtract-olmocr-data/scorer'
$scorerPackages = Join-Path $MainRepository 'axtract-olmocr-data/scorer312/Lib/site-packages'
Push-Location $repoRoot
try {
    foreach ($splitName in @('original', 'development', 'heldout')) {
        $manifest = if ($splitName -eq 'original') { 'backend/data/table_accuracy/selection.json' } else { "backend/data/table_accuracy_v2/$splitName.json" }
        $candidate = "backend/data/table_accuracy_v2/$Label/$splitName"
        $extractArgs = @('scripts/table_accuracy_sample.py','extract','--data-root',$dataRoot,'--manifest',$manifest,'--output',$candidate)
        if ($Revision) { $extractArgs += @('--source-commit',$Revision) }
        & $appPython @extractArgs
        if ($LASTEXITCODE -ne 0) { throw "Extraction failed: $splitName" }
        & $scorerPython scripts/table_accuracy_sample.py score --data-root $dataRoot --manifest $manifest --output $candidate --scorer $scorerRoot --scorer-site-packages $scorerPackages
        if ($LASTEXITCODE -ne 0) { throw "Scoring failed: $splitName" }
    }
} finally { Pop-Location }
