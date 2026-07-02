[CmdletBinding()]
param(
    [string]$WorkspacePath = "examples/haitun-workspace",
    [string]$OutputRoot = "build/windows",
    [string]$AppVersion = "",
    [switch]$BundleMsys2,
    [string]$Msys2Root = "",
    [switch]$SkipSpaInstall,
    [switch]$SkipPythonSync,
    [switch]$SkipPyInstallerInstall,
    [switch]$NoInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return [System.IO.Path]::GetFullPath((Join-Path $scriptDir "..\.."))
}

function Resolve-AbsolutePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$BasePath
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Assert-WithinRepo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullRoot = [System.IO.Path]::GetFullPath($RepoRoot)
    if (-not $fullPath.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate on path outside the repo: $fullPath"
    }
}

function Remove-RebuildTarget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    Assert-WithinRepo -Path $Path -RepoRoot $RepoRoot
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -Recurse -Force -LiteralPath $Path
    }
}

function New-RebuildDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    Remove-RebuildTarget -Path $Path -RepoRoot $RepoRoot
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Invoke-RobocopyDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination,
        [string[]]$ExcludedDirectories = @(),
        [string[]]$ExcludedFiles = @()
    )

    $args = @(
        $Source,
        $Destination,
        "/E",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/R:1",
        "/W:1"
    )
    if ($ExcludedDirectories.Count -gt 0) {
        $args += "/XD"
        $args += $ExcludedDirectories
    }
    if ($ExcludedFiles.Count -gt 0) {
        $args += "/XF"
        $args += $ExcludedFiles
    }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

function Resolve-Msys2BundleRoot {
    param([string]$ExplicitRoot)

    $candidates = @()
    if ($ExplicitRoot) {
        $candidates += $ExplicitRoot
    }
    $candidates += @("C:\msys64", "D:\msys64")
    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        $root = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Path -LiteralPath (Join-Path $root "usr\bin\bash.exe")) {
            return $root
        }
    }
    throw "MSYS2 root was not found. Pass -Msys2Root with a valid MSYS2 installation."
}

function Assert-Msys2Payload {
    param([Parameter(Mandatory = $true)][string]$Root)

    $required = @(
        "usr\bin\bash.exe",
        "usr\bin\git.exe",
        "ucrt64\bin\node.exe",
        "ucrt64\bin\npm.cmd",
        "ucrt64\bin\uv.exe"
    )
    foreach ($relativePath in $required) {
        $fullPath = Join-Path $Root $relativePath
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "Bundled MSYS2 is missing required file: $fullPath"
        }
    }
}

function Resolve-InnoSetupCompiler {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles} "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    throw "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup or rerun with -NoInstaller."
}

function Get-ResolvedAppVersion {
    param(
        [string]$ExplicitVersion,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    if ($ExplicitVersion) {
        return $ExplicitVersion
    }
    try {
        Push-Location $RepoRoot
        $version = (& uv run python -c "import importlib.metadata; print(importlib.metadata.version('psi-agent'))").Trim()
        Pop-Location
        if ($version) {
            return $version
        }
    } catch {
        if ((Get-Location).Path -eq $RepoRoot) {
            Pop-Location
        }
    }
    return (Get-Date -Format "yyyy.MM.dd")
}

$repoRoot = Get-RepoRoot
$workspaceSource = Resolve-AbsolutePath -Path $WorkspacePath -BasePath $repoRoot
$outputRoot = Resolve-AbsolutePath -Path $OutputRoot -BasePath $repoRoot
$spaDir = Join-Path $repoRoot "src\psi_agent\gateway\spa"
$backendDistDir = Join-Path $outputRoot "pyinstaller-dist"
$stageDir = Join-Path $outputRoot "haitun-stage"
$portableDir = Join-Path $outputRoot "portable"
$portableRoot = Join-Path $portableDir "Haitun Agent"
$portableZip = Join-Path $outputRoot "Haitun-Agent-portable.zip"
$installerDir = Join-Path $outputRoot "installer"
$vbsTemplate = Join-Path $repoRoot "packaging\windows\haitun-agent.vbs"
$issPath = Join-Path $repoRoot "packaging\windows\haitun-installer.iss"
$iconPath = Join-Path $repoRoot ".github\inno-setup\haitun.ico"

Assert-Command -Name "uv"
Assert-Command -Name "npm"

if (-not (Test-Path -LiteralPath $workspaceSource)) {
    throw "Workspace path does not exist: $workspaceSource"
}
if (-not (Test-Path -LiteralPath $spaDir)) {
    throw "SPA directory does not exist: $spaDir"
}
if (-not (Test-Path -LiteralPath $vbsTemplate)) {
    throw "VBS launcher template not found: $vbsTemplate"
}
if (-not (Test-Path -LiteralPath $issPath)) {
    throw "Inno Setup script not found: $issPath"
}
if (-not (Test-Path -LiteralPath $iconPath)) {
    throw "App icon not found: $iconPath"
}

$appVersion = Get-ResolvedAppVersion -ExplicitVersion $AppVersion -RepoRoot $repoRoot

Write-Host "[1/6] Building SPA dist..."
Push-Location $spaDir
try {
    if (-not $SkipSpaInstall) {
        & npm ci
    }
    & npm run build
} finally {
    Pop-Location
}

Write-Host "[2/6] Building frozen backend with PyInstaller..."
Push-Location $repoRoot
try {
    if (-not $SkipPythonSync) {
        & uv sync
    }
    if (-not $SkipPyInstallerInstall) {
        & uv pip install pyinstaller "mcp[cli,rich,ws]" "any-llm-sdk[all]"
    }
    New-RebuildDirectory -Path $outputRoot -RepoRoot $repoRoot
    $pyinstallerArgs = @(
        "run",
        "pyinstaller",
        "--onefile",
        "--name",
        "psi-agent",
        "--distpath",
        $backendDistDir,
        "--add-data",
        "src\psi_agent\gateway\spa\dist;psi_agent\gateway\spa\dist",
        "--collect-submodules",
        "any_llm",
        "--collect-submodules",
        "mcp",
        "--collect-submodules",
        "pystray",
        "--collect-submodules",
        "serper_mcp_server",
        "src\psi_agent\cli.py"
    )
    & uv @pyinstallerArgs
} finally {
    Pop-Location
}

$backendExe = Join-Path $backendDistDir "psi-agent.exe"
if (-not (Test-Path -LiteralPath $backendExe)) {
    throw "Expected backend executable was not produced: $backendExe"
}

Write-Host "[3/6] Staging workspace bundle..."
New-RebuildDirectory -Path $stageDir -RepoRoot $repoRoot
Invoke-RobocopyDirectory `
    -Source $workspaceSource `
    -Destination $stageDir `
    -ExcludedDirectories @("__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".venv", "node_modules", "msys64") `
    -ExcludedFiles @("*.pyc", "*.pyo")
Copy-Item -LiteralPath $backendExe -Destination (Join-Path $stageDir "psi-agent.exe")
Copy-Item -LiteralPath $iconPath -Destination (Join-Path $stageDir "haitun.ico")
Copy-Item -LiteralPath $vbsTemplate -Destination (Join-Path $stageDir "haitun agent.vbs")

$historiesDir = Join-Path $stageDir "histories"
if (-not (Test-Path -LiteralPath $historiesDir)) {
    New-Item -ItemType Directory -Path $historiesDir -Force | Out-Null
}
Get-ChildItem -LiteralPath $historiesDir -Filter "*.jsonl" -File -ErrorAction SilentlyContinue | Remove-Item -Force

if ($BundleMsys2) {
    Write-Host "[4/6] Copying bundled MSYS2 runtime..."
    $msys2BundleRoot = Resolve-Msys2BundleRoot -ExplicitRoot $Msys2Root
    Assert-Msys2Payload -Root $msys2BundleRoot
    Invoke-RobocopyDirectory -Source $msys2BundleRoot -Destination (Join-Path $stageDir "msys64")
    $pacmanCacheDir = Join-Path $stageDir "msys64\var\cache\pacman\pkg"
    if (Test-Path -LiteralPath $pacmanCacheDir) {
        Remove-RebuildTarget -Path $pacmanCacheDir -RepoRoot $repoRoot
    }
} else {
    Write-Host "[4/6] Skipping MSYS2 bundle. bash/node/uv tools will rely on the target machine environment."
}

Write-Host "[5/6] Creating portable zip..."
New-RebuildDirectory -Path $portableDir -RepoRoot $repoRoot
Invoke-RobocopyDirectory -Source $stageDir -Destination $portableRoot
if (Test-Path -LiteralPath $portableZip) {
    Remove-RebuildTarget -Path $portableZip -RepoRoot $repoRoot
}
Compress-Archive -Path $portableRoot -DestinationPath $portableZip -Force

$installerPath = $null
if (-not $NoInstaller) {
    Write-Host "[6/6] Building Inno Setup installer..."
    $iscc = Resolve-InnoSetupCompiler
    New-RebuildDirectory -Path $installerDir -RepoRoot $repoRoot
    $isccArgs = @(
        "/Qp",
        "/DMyAppVersion=$appVersion",
        "/DAppStage=$stageDir",
        "/DAppOutputDir=$installerDir",
        $issPath
    )
    & $iscc @isccArgs
    $installerPath = Join-Path $installerDir "Haitun Agent Setup.exe"
    if (-not (Test-Path -LiteralPath $installerPath)) {
        throw "Expected installer was not produced: $installerPath"
    }
} else {
    Write-Host "[6/6] Installer step skipped."
}

Write-Host ""
Write-Host "Build completed."
Write-Host "Portable bundle : $portableRoot"
Write-Host "Portable zip    : $portableZip"
if ($installerPath) {
    Write-Host "Installer       : $installerPath"
}
