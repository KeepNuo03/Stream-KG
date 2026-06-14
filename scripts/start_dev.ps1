# Start all dev services (run each in separate terminal for logs, or use this orchestrator)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

function Resolve-UvPath {
    $candidates = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd -and $uvCmd.Source) {
        return $uvCmd.Source
    }
    throw "uv executable not found. Please install uv or add it to PATH."
}

$UvExe = Resolve-UvPath
Write-Host "Using uv at: $UvExe"

Write-Host "Ensure Qdrant is running: docker compose up -d qdrant"
Write-Host "Terminal 1: & `"$UvExe`" run python -m stream_kg.models.embedding_server"
Write-Host "Terminal 2: & `"$UvExe`" run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000"
Write-Host "Terminal 3: cd frontend && pnpm dev"
