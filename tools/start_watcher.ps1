# Start the league bot's live watcher DETACHED from whatever shell launched it.
#
#     powershell -NoProfile -File tools\start_watcher.ps1
#     powershell -NoProfile -File tools\start_watcher.ps1 -Stop
#     powershell -NoProfile -File tools\start_watcher.ps1 -Status
#
# WHY THIS EXISTS
# ---------------
# `python tools/discord_league.py --watch` run from an agent shell, a
# terminal tab, or a background job dies when that parent goes away --
# twice in a row it was killed mid-backoff after a Discord 503, leaving a
# log that just stops. A dead watcher is SILENT: people type commands into
# #dota-league-2026 and get nothing back, and nobody finds out until
# somebody complains. That is the exact failure the watch loop was written
# to prevent, defeated by how it was launched.
#
# Start-Process gives it its own process, not a child of the caller's
# shell, so closing the terminal or ending an agent turn leaves it running.
# It still dies on a reboot or logoff -- for that, use -Install to register
# a logon Scheduled Task.

param(
    [switch]$Stop,
    [switch]$Status,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
$Repo   = Split-Path -Parent $PSScriptRoot
$Python = 'C:\Python314\python.exe'
$Log    = Join-Path $env:TEMP 'league_watch.log'
$Err    = Join-Path $env:TEMP 'league_watch.err'
$Script = 'tools/discord_league.py'

function Get-Watcher {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*discord_league*--watch*' }
}

if ($Status) {
    $w = Get-Watcher
    if ($w) {
        foreach ($p in $w) { Write-Output ("RUNNING  pid {0}  since {1}" -f $p.ProcessId, $p.CreationDate) }
        if (Test-Path $Log) { Write-Output "--- last lines ---"; Get-Content $Log -Tail 5 -Encoding UTF8 }
    } else {
        Write-Output "NOT RUNNING"
        if (Test-Path $Err) {
            $tail = Get-Content $Err -Tail 5 -Encoding UTF8
            if ($tail) { Write-Output "--- last stderr ---"; $tail }
        }
    }
    exit 0
}

if ($Stop) {
    $w = Get-Watcher
    if (-not $w) { Write-Output "nothing to stop"; exit 0 }
    foreach ($p in $w) { Stop-Process -Id $p.ProcessId -Force; Write-Output ("stopped pid " + $p.ProcessId) }
    exit 0
}

if ($Install) {
    # Survive a reboot too. Runs at logon, and the task itself restarts the
    # process if it exits, so a crash does not need a human either.
    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
                 -Argument ("-NoProfile -WindowStyle Hidden -File `"{0}`"" -f $PSCommandPath) `
                 -WorkingDirectory $Repo
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName 'DotaLeagueWatcher' -Action $action `
        -Trigger $trigger -Settings $set -Force | Out-Null
    Write-Output "installed scheduled task 'DotaLeagueWatcher' (runs at logon)"
    exit 0
}

# Only ever one. Two watchers answer every command twice, and both advance
# the same watermark, so the duplicate replies look like a bot gone mad.
$existing = Get-Watcher
if ($existing) {
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force }
    Start-Sleep -Milliseconds 400
}

$proc = Start-Process -FilePath $Python `
    -ArgumentList '-X', 'utf8', $Script, '--watch' `
    -WorkingDirectory $Repo `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Log `
    -RedirectStandardError $Err `
    -PassThru

Start-Sleep -Seconds 4
if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
    Write-Output ("started pid {0}" -f $proc.Id)
    Write-Output ("log: {0}" -f $Log)
    if (Test-Path $Log) { Get-Content $Log -Tail 3 -Encoding UTF8 }
} else {
    Write-Output "FAILED to stay up. stderr:"
    if (Test-Path $Err) { Get-Content $Err -Tail 20 -Encoding UTF8 }
    exit 1
}
