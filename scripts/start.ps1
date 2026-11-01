param(
    [ValidateSet('healthy','n-plus-one','missing-index','redundant-shipping','all')]
    [string]$Scenario = 'healthy'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        throw 'Run scripts/setup.ps1 first.'
    }
    & .\.venv\Scripts\python.exe scripts/dev.py --scenario $Scenario
    if ($LASTEXITCODE -ne 0) { throw 'The local launcher exited with an error.' }
} finally { Pop-Location }

