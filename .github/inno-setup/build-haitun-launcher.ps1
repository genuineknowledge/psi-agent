[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')),
    [string]$InnoSetupDir = (Join-Path $RepoRoot '.github\inno-setup'),
    [string]$WorkspaceDir = (Join-Path $RepoRoot 'agents\feishu')
)

$issPath = Join-Path $InnoSetupDir 'haitun.iss'
$issText = Get-Content -Raw -Encoding UTF8 $issPath
$match = [regex]::Match($issText, '#define\s+MyAppVersion\s+"([^"]+)"')
if (-not $match.Success) {
    throw "Could not parse MyAppVersion from $issPath"
}

$version = $match.Groups[1].Value
$msysMatch = [regex]::Match($issText, '#define\s+MyMsysVersion\s+"([^"]+)"')
if (-not $msysMatch.Success) {
    throw "Could not parse MyMsysVersion from $issPath"
}
$msysVersion = $msysMatch.Groups[1].Value
$baseUrl = [Environment]::GetEnvironmentVariable('HAITUN_DOWNLOAD_BASE_URL')
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    $baseUrl = ''
    Write-Warning 'HAITUN_DOWNLOAD_BASE_URL is not set; the built launcher will skip update checks.'
} else {
    $baseUrl = $baseUrl.TrimEnd('/')
}

$intervalHours = [Environment]::GetEnvironmentVariable('HAITUN_UPDATE_INTERVAL_HOURS')
if ([string]::IsNullOrWhiteSpace($intervalHours) -or
    $intervalHours -notmatch '^\d+$' -or
    [int]$intervalHours -le 0) {
    $intervalHours = '24'
}

if (-not (Test-Path $WorkspaceDir)) {
    New-Item -ItemType Directory -Path $WorkspaceDir -Force | Out-Null
}

$confPath = Join-Path $WorkspaceDir 'haitun-update.conf'
$versionPath = Join-Path $WorkspaceDir 'haitun-version.txt'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($confPath, @(
    "HAITUN_UPDATE_BASE_URL=$baseUrl",
    "HAITUN_UPDATE_INTERVAL_HOURS=$intervalHours"
), $utf8NoBom)
[System.IO.File]::WriteAllText($versionPath, $version + "`r`n", $utf8NoBom)

$msysVersionPath = Join-Path $WorkspaceDir 'msys64\msys-version.txt'
if (-not (Test-Path -LiteralPath (Split-Path -Parent $msysVersionPath))) {
    throw "msys64 directory not found; cannot write $msysVersionPath"
}
[System.IO.File]::WriteAllText($msysVersionPath, $msysVersion + "`r`n", $utf8NoBom)
Write-Host "Wrote $msysVersionPath (msys=$msysVersion)"

Write-Host "Wrote $confPath and $versionPath (version=$version)"

Push-Location $InnoSetupDir
try {
    rc /nologo 'haitun.rc'
    if ($LASTEXITCODE -ne 0) {
        throw "rc failed with exit code $LASTEXITCODE"
    }
    cl /nologo /O2 /utf-8 'haitun.c' 'haitun.res' /Fe:'haitun.exe' /link /SUBSYSTEM:WINDOWS user32.lib shell32.lib wininet.lib urlmon.lib ole32.lib gdi32.lib
    if ($LASTEXITCODE -ne 0) {
        throw "cl failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
