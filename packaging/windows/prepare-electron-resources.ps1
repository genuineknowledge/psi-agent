[CmdletBinding()]
param(
    [string]$WorkspacePath = "examples/haitun-workspace",
    [string]$BackendExecutablePath = "",
    [switch]$BundleMsys2,
    [string]$Msys2Root = "",
    [switch]$SkipSpaInstall,
    [switch]$SkipPythonSync,
    [switch]$SkipPyInstallerInstall
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

$repoRoot = Get-RepoRoot
$workspaceSource = Resolve-AbsolutePath -Path $WorkspacePath -BasePath $repoRoot
$backendSource = if ($BackendExecutablePath) {
    Resolve-AbsolutePath -Path $BackendExecutablePath -BasePath $repoRoot
} else {
    ""
}
$spaDir = Join-Path $repoRoot "src\psi_agent\gateway\spa"
$desktopRoot = Join-Path $repoRoot "src\psi_agent\gateway\electron"
$resourcesRoot = Join-Path $desktopRoot ".build\resources"
$backendDir = Join-Path $resourcesRoot "backend"
$workspaceTemplateDir = Join-Path $resourcesRoot "workspace-template"
$msysDir = Join-Path $resourcesRoot "msys64"

Assert-Command -Name "npm"

if (-not (Test-Path -LiteralPath $workspaceSource)) {
    throw "Workspace path does not exist: $workspaceSource"
}
if ($backendSource -and -not (Test-Path -LiteralPath $backendSource)) {
    throw "Backend executable path does not exist: $backendSource"
}

Write-Host "[1/4] Building SPA dist..."
Push-Location $spaDir
try {
    if (-not $SkipSpaInstall) {
        & npm ci
    }
    & npm run build
} finally {
    Pop-Location
}

Write-Host "[2/4] Preparing backend executable..."
Push-Location $repoRoot
try {
    New-RebuildDirectory -Path $resourcesRoot -RepoRoot $repoRoot
    New-RebuildDirectory -Path $backendDir -RepoRoot $repoRoot

    if ($backendSource) {
        Copy-Item -LiteralPath $backendSource -Destination (Join-Path $backendDir "psi-agent.exe")
    } else {
        Assert-Command -Name "uv"
        if (-not $SkipPythonSync) {
            & uv sync
        }
        if (-not $SkipPyInstallerInstall) {
            & uv pip install pyinstaller "mcp[cli,rich,ws]" "any-llm-sdk[all]"
        }

        $pyinstallerArgs = @(
            "run",
            "pyinstaller",
            "--onefile",
            "--name",
            "psi-agent",
            "--distpath",
            $backendDir,
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
    }
} finally {
    Pop-Location
}

$backendExe = Join-Path $backendDir "psi-agent.exe"
if (-not (Test-Path -LiteralPath $backendExe)) {
    throw "Expected backend executable was not produced: $backendExe"
}

Write-Host "[3/4] Staging workspace template..."
New-RebuildDirectory -Path $workspaceTemplateDir -RepoRoot $repoRoot
Invoke-RobocopyDirectory `
    -Source $workspaceSource `
    -Destination $workspaceTemplateDir `
    -ExcludedDirectories @("__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".venv", "node_modules", "msys64") `
    -ExcludedFiles @("*.pyc", "*.pyo")

$historiesDir = Join-Path $workspaceTemplateDir "histories"
if (-not (Test-Path -LiteralPath $historiesDir)) {
    New-Item -ItemType Directory -Path $historiesDir -Force | Out-Null
}
Get-ChildItem -LiteralPath $historiesDir -Filter "*.jsonl" -File -ErrorAction SilentlyContinue | Remove-Item -Force

Write-Host "[4/4] Preparing optional bundled runtimes..."
New-RebuildDirectory -Path $msysDir -RepoRoot $repoRoot
if ($BundleMsys2) {
    $msys2BundleRoot = Resolve-Msys2BundleRoot -ExplicitRoot $Msys2Root
    Assert-Msys2Payload -Root $msys2BundleRoot
    Invoke-RobocopyDirectory -Source $msys2BundleRoot -Destination $msysDir
    $pacmanCacheDir = Join-Path $msysDir "var\cache\pacman\pkg"
    if (Test-Path -LiteralPath $pacmanCacheDir) {
        Remove-RebuildTarget -Path $pacmanCacheDir -RepoRoot $repoRoot
    }
} else {
    Write-Host "MSYS2 bundle skipped."
}

Write-Host ""
Write-Host "Electron resources are ready under:"
Write-Host $resourcesRoot
