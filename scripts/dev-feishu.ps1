# Canonical local Feishu + Gateway bring-up for haitun-workspace.
# Do NOT invent ad-hoc POST /ais bodies — this script is the only allowed model id.
#
# Usage (from repo root, credentials already in env or .env loaded by your shell):
#   powershell -File scripts/dev-feishu.ps1
# Optional:
#   $env:GATEWAY_LISTEN = 'http://127.0.0.1:8765'
#   $env:PSI_FEISHU_APP_ID / $env:PSI_FEISHU_APP_SECRET must be set for both processes.

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Agent = Join-Path $RepoRoot 'agents\feishu'
$Listen = if ($env:GATEWAY_LISTEN) { $env:GATEWAY_LISTEN } else { 'http://127.0.0.1:8765' }
$AiId = 'feishu-default'
# Company proxy allowlist: deepseek-v4-flash | deepseek-v4-pro only.
# Never use deepseek-v4-flash-free (retired; upstream 400).
$Model = 'deepseek-v4-flash'
# 云端转发器。旧地址 misakamikoto.genuineknowledge.cn 是个人服务器上的转发,
# 即将停用 —— 本行是它在本仓的最后一处依赖。
$BaseUrl = 'https://account.genuineknowledge.cn/llm/v1'
# 哨兵值,不是真 key。Gateway 在拉起 AI 子进程时换成当前登录 token
# (见 gateway/desktop/_free_model.py) —— ** 所以跑这个脚本前要先在客户端登录 **,
# 否则上游回 401。改这个字面量要同时改 _free_model.py 与两份 SPA。
$ApiKey = 'haitun-default'

if (-not $env:PSI_FEISHU_APP_ID -or -not $env:PSI_FEISHU_APP_SECRET) {
    Write-Error 'Set PSI_FEISHU_APP_ID and PSI_FEISHU_APP_SECRET before starting (Gateway + Channel both need them).'
}

# Same Gateway serves SPA + Feishu + OAuth relay. Feishu console redirect must be:
#   {PSI_OAUTH_CALLBACK_BASE}/oauth/callback
if (-not $env:PSI_OAUTH_CALLBACK_BASE) {
    $env:PSI_OAUTH_CALLBACK_BASE = $Listen
}

Set-Location $RepoRoot

Write-Host "Starting Gateway on $Listen (agent=$Agent, feishu-ai-id=$AiId, oauth=$($env:PSI_OAUTH_CALLBACK_BASE))..."
# --gateway is required (no default): which HTTP surfaces to mount is the caller's
# call, and mounting one too few fails silently (some frontend just 404s). This
# script serves SPA + Feishu + OAuth from one Gateway, so it needs BOTH surfaces.
$gwArgs = @(
    'run', 'psi-agent', 'gateway',
    '--gateway', 'desktop', 'feishu',
    '--listen', $Listen,
    '--browser',
    '--feishu-ai-id', $AiId,
    '--feishu-workspace-root', $Agent,
    '--default-agent', $Agent,
    '--verbose'
)
$gw = Start-Process -FilePath 'uv' -ArgumentList $gwArgs -PassThru -NoNewWindow `
    -WorkingDirectory $RepoRoot

$deadline = (Get-Date).AddSeconds(45)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod -Uri "$Listen/ais" -Method GET -TimeoutSec 2
        $ready = $true
        break
    } catch {
        Start-Sleep -Milliseconds 400
    }
}
if (-not $ready) {
    Write-Error "Gateway did not become ready at $Listen"
}

$ais = Invoke-RestMethod -Uri "$Listen/ais" -Method GET
$existing = @($ais | Where-Object { $_.id -eq $AiId })
if ($existing.Count -gt 0 -and $existing[0].model -ne $Model) {
    Write-Host "Replacing $AiId model $($existing[0].model) -> $Model"
    Invoke-RestMethod -Uri "$Listen/ais/$AiId" -Method DELETE | Out-Null
    $existing = @()
}
if ($existing.Count -eq 0) {
    Write-Host "POST /ais id=$AiId model=$Model"
    $body = @{
        id       = $AiId
        provider = 'openai'
        model    = $Model
        api_key  = $ApiKey
        base_url = $BaseUrl
    } | ConvertTo-Json
    Invoke-RestMethod -Uri "$Listen/ais" -Method POST -Body $body -ContentType 'application/json' | Out-Null
} else {
    Write-Host "$AiId already OK (model=$($existing[0].model))"
}

# --session-socket is required by CLI but only used as fallback when --gateway-url routes fail.
$FallbackSocket = '\\.\pipe\psi\channels\fallback'
Write-Host "Starting Feishu channel (--gateway-url $Listen)..."
$chArgs = @(
    'run', 'psi-agent', 'channel', 'feishu',
    '--session-socket', $FallbackSocket,
    '--gateway-url', $Listen,
    '--agent', $Agent,
    '--require-mention',
    '--verbose'
)
$ch = Start-Process -FilePath 'uv' -ArgumentList $chArgs -PassThru -NoNewWindow `
    -WorkingDirectory $RepoRoot

Write-Host "Gateway pid=$($gw.Id)  Channel pid=$($ch.Id)"
Write-Host "Stop with: Stop-Process -Id $($gw.Id),$($ch.Id)"
Write-Host "Both processes stay attached to this console; Ctrl+C does not kill them — use Stop-Process."
