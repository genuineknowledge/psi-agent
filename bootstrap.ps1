# psi-agent one-line bootstrap (Windows PowerShell).
#
# Usage, from a cloned checkout:
#   ./bootstrap.ps1
#
# This installs uv if it is missing, syncs dependencies, then launches the
# interactive setup wizard. Installing uv downloads and runs the official
# installer from https://astral.sh/uv/install.ps1 -- review it if you prefer.

$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

function Test-Uv {
    return [bool](Get-Command uv -ErrorAction SilentlyContinue)
}

if (-not (Test-Uv)) {
    Write-Host "uv not found. Installing uv from https://astral.sh/uv/install.ps1 ..."
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    # The installer adds uv under %USERPROFILE%\.local\bin; use it this session.
    $localBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $localBin) {
        $env:Path = "$localBin;$env:Path"
    }
}

if (-not (Test-Uv)) {
    Write-Error "uv is still not on PATH. Open a new terminal and re-run ./bootstrap.ps1"
    exit 1
}

Write-Host "Syncing dependencies with uv..."
uv sync

Write-Host "Launching the psi-agent setup wizard..."
uv run psi-agent setup
