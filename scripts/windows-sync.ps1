[CmdletBinding()]
param(
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$Python = Join-Path $RepoRoot ".venv\Scripts\epd-dashboard.exe"
$Config = Join-Path $RepoRoot "config.toml"
$StateDir = Join-Path $RepoRoot "state"
$Log = Join-Path $StateDir "windows-sync.log"
$SyncMutex = [Threading.Mutex]::new($false, "Global\EPD-Dashboard-Sync")
$SyncMutexAcquired = $false
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

try {
    $SyncMutexAcquired = $SyncMutex.WaitOne(0)
    if (-not $SyncMutexAcquired) {
        Add-Content -LiteralPath $Log -Value ("{0} skipped: another sync is already running" -f (Get-Date -Format o))
        exit 0
    }

    if (-not (Test-Path -LiteralPath $Python)) { throw "epd-dashboard executable not found: $Python" }
    if (-not (Test-Path -LiteralPath $Config)) { throw "config.toml not found: $Config" }
    Set-Location -LiteralPath $RepoRoot

function Invoke-DashboardCommand {
    param([string[]]$Arguments, [switch]$AllowFailure)

    $output = & $Python @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $summary = (($output | ForEach-Object { $_.ToString().Trim() }) -join " ")
    Add-Content -LiteralPath $Log -Value ("{0} exit={1} {2}" -f (Get-Date -Format o), $exitCode, $summary)
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "epd-dashboard command failed with exit code $exitCode"
    }
}

    # Refresh local account usage and rate windows; keep the NAS feed as fallback.
    Invoke-DashboardCommand @("codex-snapshot", "--config", $Config) -AllowFailure
    Invoke-DashboardCommand @("sync-once", "--config", $Config, "--only", "codex", "--combined")
}
catch {
    Add-Content -LiteralPath $Log -Value ("{0} error: {1}" -f (Get-Date -Format o), $_.Exception.Message)
    exit 1
}
finally {
    if ($SyncMutexAcquired) {
        $SyncMutex.ReleaseMutex()
    }
    $SyncMutex.Dispose()
}
