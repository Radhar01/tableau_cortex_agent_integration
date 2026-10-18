# Start the Cortex chat app (backend API + frontend UI) on http://localhost:8501
# Usage: .\start.ps1 [-Port 8501] [-Install]
param(
    [int]$Port = 8501,
    [switch]$Install
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment in venv\ ..."
    python -m venv venv
    $Install = $true
}
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

if ($Install) {
    & $python -m pip install -r backend\requirements.txt
}

if (-not (Test-Path ".env")) {
    Write-Warning ".env not found. Copy .env.example to .env and fill in your Snowflake values."
    exit 1
}

Write-Host "Starting on http://localhost:$Port  (Ctrl+C to stop)"
& $python -m uvicorn app:app --app-dir backend --host 0.0.0.0 --port $Port
