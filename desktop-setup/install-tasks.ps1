<#
    DealFlow — install the scheduled tasks on a second (desktop) PC.

    These XMLs were EXPORTED from the live laptop, so they carry the hardening that was won the hard
    way and must not be retyped from memory:
        ExecutionTimeLimit PT6H     (PT2H silently killed rebuild/publish for days)
        DisallowStartIfOnBatteries / StopIfGoingOnBatteries = false
        WakeToRun / StartWhenAvailable = true   (survives sleep + a missed wake)

    ⛔ THE ONE RULE: only ONE machine may run these. Both refresh tasks end in `git push`, and the
    call log / ledgers (worker_notes.json, optouts.json, mail_sent.json) are gitignored, so a second
    runner does not "share" state — it forks it, and whichever pushes last wins.

    That is why this script registers every task DISABLED by default. Nothing starts running just
    because you ran the installer. Arm them only after the laptop's copies are switched off:

        1. On the LAPTOP:   .\install-tasks.ps1 -DisableLocal
        2. On the DESKTOP:  .\install-tasks.ps1            (registers, disabled)
        3. On the DESKTOP:  .\install-tasks.ps1 -Enable    (arms them)

    Usage:
        .\install-tasks.ps1 [-RepoPath <path>] [-Enable] [-DisableLocal] [-WhatIf]
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepoPath,
    [switch]$Enable,
    [switch]$DisableLocal
)

$ErrorActionPreference = 'Stop'
$OLD_REPO = 'C:\Users\olqbb\projects\foreclosure-leads'
$OLD_PROFILE = 'C:\Users\olqbb'

# ---- mode: turn OFF the tasks on THIS machine (run on the laptop when handing the crown over) ----
if ($DisableLocal) {
    $live = Get-ScheduledTask | Where-Object { $_.TaskName -match '^(DEALFLOW|DealFlow)' }
    if (-not $live) { Write-Host "No DealFlow tasks on this machine — nothing to disable."; return }
    foreach ($t in $live) {
        if ($PSCmdlet.ShouldProcess($t.TaskName, 'Disable')) {
            Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-Null
            Write-Host ("  disabled  {0}" -f $t.TaskName)
        }
    }
    Write-Host "`nThis machine is no longer the runner. Arm the other one with -Enable."
    return
}

# ---- resolve the repo on THIS machine ----
if (-not $RepoPath) { $RepoPath = Split-Path -Parent $PSScriptRoot }
$RepoPath = (Resolve-Path $RepoPath).Path.TrimEnd('\')
if (-not (Test-Path (Join-Path $RepoPath 'refresh-dealflow.bat'))) {
    throw "refresh-dealflow.bat not found in '$RepoPath'. Pass -RepoPath <checkout> explicitly."
}
$ProfilePath = $env:USERPROFILE.TrimEnd('\')
$Me = "$env:USERDOMAIN\$env:USERNAME"

Write-Host "repo   : $RepoPath"
Write-Host "user   : $Me"
Write-Host "action : $(if ($Enable) { 'register + ENABLE' } else { 'register (left DISABLED)' })`n"

# ---- prerequisites: fail loudly now rather than at 5:30am ----
$warn = @()
foreach ($p in @('refresh-dealflow.bat', 'run-phones-nightly.bat', 'run-replies-daily.bat',
                 'run-analyst-weekly.bat', 'sheets_crm.py', 'send_server.py')) {
    if (-not (Test-Path (Join-Path $RepoPath $p))) { $warn += "missing in repo: $p" }
}
if (-not (Test-Path 'C:\Program Files\Python311\pythonw.exe')) {
    $warn += 'Python 3.11 not at C:\Program Files\Python311 (the Sheets CRM task calls that exact path)'
}
if (-not (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe')) {
    $warn += 'Chrome not at the default path (the Morning Worker task opens the board with it)'
}
foreach ($k in @('gmail.key', 'optouts.json', 'mail_sent.json')) {
    if (-not (Test-Path (Join-Path $RepoPath $k))) {
        $warn += "MISSING $k — unzip the transfer bundle BEFORE arming outreach tasks"
    }
}

# ---- register ----
$xmlDir = Join-Path $PSScriptRoot 'tasks'
$xmls = Get-ChildItem -Path $xmlDir -Filter *.xml -ErrorAction SilentlyContinue
if (-not $xmls) { throw "No task XMLs in '$xmlDir'." }

$done = 0
foreach ($f in $xmls) {
    # exported name is sanitized (spaces -> _); recover the real task name from the XML URI, else the file name
    $raw = Get-Content -Path $f.FullName -Raw -Encoding Unicode
    $name = if ($raw -match '<URI>\\?([^<]+)</URI>') { $matches[1] } else { $f.BaseName -replace '_', ' ' }

    $body = $raw.Replace($OLD_REPO, $RepoPath).Replace($OLD_PROFILE, $ProfilePath)
    # <Author> is cosmetic registration metadata (Windows ignores it; -User below sets the real
    # principal) — but leaving the laptop's name on a desktop task makes an audit read wrong later.
    $body = $body -replace '<Author>[^<]*</Author>', "<Author>$Me</Author>"

    if ($PSCmdlet.ShouldProcess($name, 'Register')) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        # -User rewrites the principal, so the laptop's SID never follows the task across machines
        Register-ScheduledTask -TaskName $name -Xml $body -User $Me -Force | Out-Null
        if ($Enable) { Enable-ScheduledTask -TaskName $name | Out-Null }
        else { Disable-ScheduledTask -TaskName $name | Out-Null }
        Write-Host ("  {0,-28} {1}" -f $name, $(if ($Enable) { 'ENABLED' } else { 'registered (disabled)' }))
        $done++
    }
}

Write-Host "`n$done task(s) installed."

# ---- the send-server piece lives OUTSIDE the repo (Startup folder), so it needs its own install ----
$vbs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\DealflowSendServer.vbs'
if (-not (Test-Path $vbs)) {
    Write-Host "`nNOTE: DealflowSendServer.vbs is not in this machine's Startup folder."
    Write-Host "      The DealflowSendServerDaily task launches it from there, so run:"
    Write-Host "          $RepoPath\install_send_server_autostart.bat"
}

if ($warn) {
    Write-Host "`n!! CHECK THESE BEFORE ARMING:" -ForegroundColor Yellow
    $warn | ForEach-Object { Write-Host "   - $_" -ForegroundColor Yellow }
}

if (-not $Enable) {
    Write-Host "`nEverything is registered but DISABLED — nothing will run yet. That is deliberate."
    Write-Host "Turn the laptop's copies off FIRST (.\install-tasks.ps1 -DisableLocal there), then"
    Write-Host "come back here and run:  .\install-tasks.ps1 -Enable"
} else {
    Write-Host "`nARMED. Confirm the laptop's tasks are disabled — two runners will fork the call log"
    Write-Host "and fight over the published board."
}
