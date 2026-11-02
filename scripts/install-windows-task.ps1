[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$TaskName = "EPD Dashboard Sync",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Uninstall) {
    if ($null -ne $existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Write-Output "uninstalled: $TaskName"
    exit 0
}

$runner = Join-Path $RepoRoot "scripts\windows-sync.ps1"
$wrapper = Join-Path $RepoRoot "scripts\windows-sync-hidden.vbs"
$config = Join-Path $RepoRoot "config.toml"
$state = Join-Path (Join-Path $env:LOCALAPPDATA "epd-dashboard-sync") "state.json"
if (-not (Test-Path -LiteralPath $runner)) { throw "runner not found: $runner" }
if (-not (Test-Path -LiteralPath $wrapper)) { throw "wrapper not found: $wrapper" }
if (-not (Test-Path -LiteralPath $config)) { throw "config.toml not found: $config" }
if (-not (Test-Path -LiteralPath $state)) { throw "run one successful manual sync before installing the task" }
$stateData = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
if ($stateData.lastResult -ne "success") { throw "run one successful manual sync before installing the task" }

$wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
$arguments = "//B //NoLogo `"$wrapper`" `"$runner`" `"$RepoRoot`""
$action = New-ScheduledTaskAction -Execute $wscript -Argument $arguments -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Refresh the sanitized Codex/GitHub EPD dashboard every 30 minutes while this user is logged in." -Force | Out-Null
Write-Output "installed: $TaskName"
