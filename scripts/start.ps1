param(
    [ValidateSet('healthy','n-plus-one','missing-index','redundant-shipping','all')]
    [string]$Scenario = 'healthy',
    [switch]$Jaeger
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        throw 'Run scripts/setup.ps1 first.'
    }
    $launchArgs = @('scripts/dev.py', '--scenario', $Scenario)
    if ($Jaeger) { $launchArgs += '--jaeger' }
    & .\.venv\Scripts\python.exe @launchArgs
    if ($LASTEXITCODE -ne 0) { throw 'The local launcher exited with an error.' }
} finally { Pop-Location }

