# stream-kg environment setup (Windows)
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
Write-Host "==> Using uv at: $UvExe"

Write-Host "==> Creating data directories"
@("data/uploads", "data/parsed", "data/qdrant") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "==> Created .env from .env.example — please set LLM_API_KEY"
}

Write-Host "==> uv sync"
& $UvExe sync --all-extras

Write-Host "==> Docker: Qdrant"
docker compose up -d qdrant

Write-Host "Setup complete."
