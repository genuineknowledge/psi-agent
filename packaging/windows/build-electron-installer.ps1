[CmdletBinding()]
param(
    [string]$WorkspacePath = "examples/haitun-workspace",
    [switch]$BundleMsys2,
    [string]$Msys2Root = "",
    [switch]$SkipSpaInstall,
    [switch]$SkipPythonSync,
    [switch]$SkipPyInstallerInstall,
    [switch]$SkipDesktopInstall,
    [switch]$UseMirror
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return [System.IO.Path]::GetFullPath((Join-Path $scriptDir "..\.."))
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

$repoRoot = Get-RepoRoot
$desktopRoot = Join-Path $repoRoot "src\psi_agent\gateway\electron"
$prepareScript = Join-Path $repoRoot "packaging\windows\prepare-electron-resources.ps1"
$builderCmd = Join-Path $desktopRoot "node_modules\.bin\electron-builder.cmd"
$distDir = Join-Path $desktopRoot "dist"

Assert-Command -Name "powershell"
Assert-Command -Name "npm.cmd"

if (-not (Test-Path -LiteralPath $prepareScript)) {
    throw "Prepare script was not found: $prepareScript"
}
if (-not (Test-Path -LiteralPath $desktopRoot)) {
    throw "Desktop project directory was not found: $desktopRoot"
}

if ($UseMirror) {
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
}

if (-not $SkipDesktopInstall) {
    Write-Host "[1/3] Installing Electron dependencies..."
    Push-Location $desktopRoot
    try {
        & npm.cmd install
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[1/3] Skipping Electron dependency install."
}

$prepareArgs = @(
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $prepareScript,
    "-WorkspacePath",
    $WorkspacePath
)
if ($BundleMsys2) {
    $prepareArgs += "-BundleMsys2"
    if ($Msys2Root) {
        $prepareArgs += @("-Msys2Root", $Msys2Root)
    }
}
if ($SkipSpaInstall) {
    $prepareArgs += "-SkipSpaInstall"
}
if ($SkipPythonSync) {
    $prepareArgs += "-SkipPythonSync"
}
if ($SkipPyInstallerInstall) {
    $prepareArgs += "-SkipPyInstallerInstall"
}

Write-Host "[2/3] Preparing backend/frontend resources..."
Push-Location $repoRoot
try {
    & powershell @prepareArgs
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $builderCmd)) {
    throw "Electron Builder command was not found: $builderCmd"
}

Write-Host "[3/3] Building NSIS installer..."
Push-Location $desktopRoot
try {
    & $builderCmd "--win" "nsis"
} finally {
    Pop-Location
}

$installer = Get-ChildItem -LiteralPath $distDir -Filter "*.exe" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike "*.blockmap" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

Write-Host ""
if ($installer) {
    Write-Host "Electron installer created:"
    Write-Host $installer.FullName
} else {
    Write-Host "Electron build completed, but no installer .exe was found under:"
    Write-Host $distDir
}
