$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & .\.venv\Scripts\python.exe scripts/dev.py --stop
    if ($LASTEXITCODE -ne 0) { throw 'The local services could not be stopped cleanly.' }
} finally { Pop-Location }

