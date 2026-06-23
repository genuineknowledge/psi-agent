# psi-agent remote installer (Windows PowerShell).
#
# One-line install:
#   irm https://raw.githubusercontent.com/genuineknowledge/psi-agent/feat/web-channel-setup-flow/install.ps1 | iex
#
# What it does:
#   1. installs uv if missing (from https://astral.sh/uv/install.ps1)
#   2. clones psi-agent into %USERPROFILE%\.psi-agent\psi-agent (pulls if present)
#   3. runs `uv sync` to install dependencies
#   4. launches the interactive `psi-agent setup` wizard
#
# Environment overrides:
#   PSI_AGENT_REPO     git URL to clone (default: GitHub)
#   PSI_AGENT_BRANCH   branch to check out (default: main)
#   PSI_AGENT_HOME     install root (default: ~\.psi-agent)
#   PSI_AGENT_SKIP_SETUP=1   clone + sync only, skip the wizard

$ErrorActionPreference = "Stop"

$repo = if ($env:PSI_AGENT_REPO) { $env:PSI_AGENT_REPO } else { "https://github.com/genuineknowledge/psi-agent.git" }
$branch = if ($env:PSI_AGENT_BRANCH) { $env:PSI_AGENT_BRANCH } else { "feat/web-channel-setup-flow" }
$homeDir = if ($env:PSI_AGENT_HOME) { $env:PSI_AGENT_HOME } else { Join-Path $env:USERPROFILE ".psi-agent" }
$srcDir = Join-Path $homeDir "psi-agent"

function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Cmd "git")) {
    Write-Error "git is required but not found. Install Git for Windows, then re-run the installer."
    exit 1
}

if (-not (Test-Cmd "uv")) {
    Write-Host "Installing uv from https://astral.sh/uv/install.ps1 ..."
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $localBin = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path $localBin) {
        $env:Path = "$localBin;$env:Path"
    }
}

if (-not (Test-Cmd "uv")) {
    Write-Error "uv installed but not on PATH. Open a new terminal and re-run the installer."
    exit 1
}

if (Test-Path (Join-Path $srcDir ".git")) {
    Write-Host "Updating existing checkout at $srcDir ..."
    git -C $srcDir fetch --depth 1 origin $branch
    git -C $srcDir checkout $branch
    git -C $srcDir reset --hard "origin/$branch"
}
else {
    Write-Host "Cloning $repo into $srcDir ..."
    New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
    git clone --depth 1 --branch $branch $repo $srcDir
}

Set-Location -Path $srcDir
Write-Host "Syncing dependencies with uv..."
uv sync

if ($env:PSI_AGENT_SKIP_SETUP -eq "1") {
    Write-Host ""
    Write-Host "Installed at $srcDir. Run the wizard later with:"
    Write-Host "  cd $srcDir; uv run psi-agent setup"
    exit 0
}

Write-Host "Launching the psi-agent setup wizard..."
uv run psi-agent setup
