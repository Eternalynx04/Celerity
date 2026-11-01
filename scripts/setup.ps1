param(
    [string]$PythonPath = "python",
    [switch]$PortablePostgres
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $PythonPath -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required. Pass -PythonPath with its executable.' }
    }
    & .\.venv\Scripts\python.exe -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & .\.venv\Scripts\python.exe -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
    if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
    if ($PortablePostgres) {
        & .\.venv\Scripts\python.exe scripts/bootstrap_postgres.py
        if ($LASTEXITCODE -ne 0) { throw 'Project-local PostgreSQL setup failed.' }
    }
} finally { Pop-Location }

