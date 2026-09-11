<#
.SYNOPSIS
  Signs files with AzureSignTool, serialized and with retry on TSA throttling.

.DESCRIPTION
  The GlobalSign RFC3161 timestamp service rate-limits us. AzureSignTool
  surfaces that as `Signing failed with error 801901AD` — and that code is not
  opaque: 0x801901AD has facility 0x19 (FACILITY_HTTP) and low word 0x1AD =
  **429 Too Many Requests**. Every signing failure we have seen on main decodes
  to exactly that.

  Two things made it fatal:

  1. AzureSignTool signs the input files *concurrently* by default, so N files
     meant N simultaneous requests to the TSA. The installer step passes 3 and
     reliably lost 1 of them ("Successful operations: 2 / Failed operations: 1").
     Which file lost was random — it moved between runs, which is why the first
     diagnosis of "one specific file is broken" was wrong.
  2. There was no retry, so a single 429 threw away a ~21 min build and blocked
     the release. 1.0.15 sat unpublished because of it.

  So this script does both: `--max-degree-of-parallelism 1` to stop competing
  with ourselves, and a retry with growing backoff for the throttling that
  remains. `--skip-signed` makes the retry idempotent — files that already got
  a signature in an earlier attempt are not signed twice (which would otherwise
  either fail or stack a second signature).

  Serializing costs wall time (each file waits for the previous TSA round trip)
  but the whole step was ~10 s of the ~21 min job, so it does not matter here.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SignTool,
    [Parameter(Mandatory = $true)][string]$VaultUrl,
    [Parameter(Mandatory = $true)][string]$CertificateName,
    [Parameter(Mandatory = $true)][string]$TsaUrl,
    [Parameter(Mandatory = $true)][string]$Description,
    [Parameter(Mandatory = $true)][string[]]$Files,
    # 5 attempts with 15 s growth tolerates roughly two and a half minutes of
    # throttling. Chosen over a tighter loop because a 429 means the *service*
    # wants us to slow down; retrying hard would earn more 429s.
    [int]$MaxAttempts = 5,
    [int]$BackoffSeconds = 15
)

$ErrorActionPreference = 'Stop'

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    Write-Output "AzureSignTool attempt $attempt/$MaxAttempts for: $($Files -join ', ')"

    # --skip-signed is what makes a retry safe: on attempt 2+ the files that
    # already succeeded are left alone.
    & $SignTool sign `
        --azure-key-vault-url $VaultUrl `
        --azure-key-vault-managed-identity `
        --azure-key-vault-certificate $CertificateName `
        --description $Description `
        --timestamp-rfc3161 $TsaUrl `
        --timestamp-digest sha256 `
        --file-digest sha256 `
        --max-degree-of-parallelism 1 `
        --skip-signed `
        --verbose `
        $Files
    $code = $LASTEXITCODE

    if ($code -eq 0) {
        Write-Output "AzureSignTool succeeded on attempt $attempt."
        exit 0
    }

    Write-Warning "AzureSignTool exited with $code on attempt $attempt."

    if ($attempt -eq $MaxAttempts) {
        throw "AzureSignTool failed for $Description after $MaxAttempts attempts (last exit code $code)."
    }

    $delay = $BackoffSeconds * $attempt
    Write-Output "Retrying in ${delay}s (TSA throttling surfaces as 801901AD = HTTP 429)."
    Start-Sleep -Seconds $delay
}
