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

    THE NINTH TASK (fixed 2026-09-18). For three weeks this script managed eight tasks and
    `DealFlow Cadence` — the 09:00 job that emails homeowners — was registered by hand, outside it.
    `-DisableLocal` caught it anyway, because that path enumerates live tasks by name match rather
    than working from a list. `-Enable` did not, because that path only walked `tasks/*.xml`. So
    disarming was complete and arming was not: every handoff left outreach off and nothing said so.

    It could not simply be exported into `tasks/`, because that directory is gitignored — a Windows
    task export embeds the exporting machine's principal SID and user paths, so those travel in the
    transfer bundle, not in git. `task-templates/` is the answer: SID-free XML with `__REPO__`,
    `__PROFILE__` and `__USER__` placeholders, tracked, substituted here at install time. An export
    in `tasks/` with the same task name still wins — it carries hardening won on a live box.

    -Only <pattern> scopes every mode to the tasks whose name matches, which is how you add or arm
    ONE task on a machine whose other tasks are already live and must not be re-registered:

        .\install-tasks.ps1 -Only Cadence            # register it, disabled
        .\install-tasks.ps1 -Only Cadence -Enable    # and arm it

    Usage:
        .\install-tasks.ps1 [-RepoPath <path>] [-Only <pattern>] [-Enable] [-DisableLocal] [-WhatIf]
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepoPath,
    [string]$Only,
    [switch]$Enable,
    [switch]$DisableLocal
)

$ErrorActionPreference = 'Stop'

# -Only matching ignores case, spaces, hyphens and underscores (2026-09-26). The template docs said
# `-Only OptoutSync` while the task is named `DealFlow Opt-out Sync`, so the documented command
# threw "matched no task". A plain -like on the raw name is still tried first, so every pattern that
# worked before still matches exactly what it matched before.
function Get-TaskKey([string]$s) { return ($s -replace '[\s_-]', '').ToLowerInvariant() }
function Test-OnlyMatch([string]$name, [string]$pattern) {
    if (-not $pattern) { return $true }
    return ($name -like "*$pattern*") -or ((Get-TaskKey $name) -like "*$(Get-TaskKey $pattern)*")
}

$OLD_REPO = 'C:\Users\olqbb\projects\foreclosure-leads'
$OLD_PROFILE = 'C:\Users\olqbb'

# ---- mode: turn OFF the tasks on THIS machine (run on the laptop when handing the crown over) ----
if ($DisableLocal) {
    $live = Get-ScheduledTask | Where-Object { $_.TaskName -match '^(DEALFLOW|DealFlow)' }
    # Enumerating live tasks rather than reading the XML list is deliberate and is why this path
    # already caught `DealFlow Cadence` while -Enable did not. Keep it that way: a task registered
    # by hand on some future morning is still outreach, and this is what stands it down.
    if ($Only) { $live = $live | Where-Object { Test-OnlyMatch $_.TaskName $Only } }
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
                 'run-analyst-weekly.bat', 'sheets_crm.py', 'send_server.py',
                 'cadence-daily.bat', 'cadence.py')) {
    if (-not (Test-Path (Join-Path $RepoPath $p))) { $warn += "missing in repo: $p" }
}
if (-not (Test-Path 'C:\Program Files\Python311\pythonw.exe')) {
    $warn += 'Python 3.11 not at C:\Program Files\Python311 (the Sheets CRM task calls that exact path)'
}
if (-not (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe')) {
    $warn += 'Chrome not at the default path (the Morning Worker task opens the board with it)'
}
# bsg_gmail.key is listed since 2026-09-18: cadence.py and send_server.py both read it FIRST and
# fall back to gmail.key. Missing, outreach still sends — as the login instead of the lane alias,
# which is the split the 09-07 lane wiring exists to prevent. Absent is a warning, not a failure.
foreach ($k in @('gmail.key', 'bsg_gmail.key', 'optouts.json', 'mail_sent.json')) {
    if (-not (Test-Path (Join-Path $RepoPath $k))) {
        $warn += "MISSING $k — unzip the transfer bundle BEFORE arming outreach tasks"
    }
}

# ---- register ----
# Two sources, in priority order.
#   tasks/          gitignored Windows exports, UTF-16 — they carry hardening won on a live box.
#   task-templates/ tracked, UTF-8, SID-free, placeholder paths — the only kind that can live in git.
# An export wins over a template of the same task name; a template fills what no export covers.
# Before 2026-09-18 only the first existed, which is why `DealFlow Cadence` could never be armed by
# this script: there was nowhere in the repo to keep a task definition.
$xmlDir = Join-Path $PSScriptRoot 'tasks'
$tplDir = Join-Path $PSScriptRoot 'task-templates'
$srcs = @()
foreach ($f in @(Get-ChildItem -Path $xmlDir -Filter *.xml -ErrorAction SilentlyContinue)) {
    $srcs += [pscustomobject]@{ File = $f; Raw = (Get-Content -Path $f.FullName -Raw -Encoding Unicode); Kind = 'export' }
}
foreach ($f in @(Get-ChildItem -Path $tplDir -Filter *.xml -ErrorAction SilentlyContinue)) {
    $srcs += [pscustomobject]@{ File = $f; Raw = (Get-Content -Path $f.FullName -Raw -Encoding UTF8); Kind = 'template' }
}
if (-not $srcs) { throw "No task XMLs in '$xmlDir' and no templates in '$tplDir'." }
if (-not @($srcs | Where-Object { $_.Kind -eq 'export' })) {
    # Before task-templates/ existed this case was a throw, and a throw is what made it obvious.
    # Now it installs cleanly and under-installs silently, which on a machine being set up from
    # scratch means one task where eight were expected. Say so.
    $warn += "no exports in '$xmlDir' — only the tracked template(s) will be installed. The other tasks travel in the transfer bundle; unzip it here to get the full set."
}

# resolve each source's real task name now, so the export-beats-template rule can be applied by name
foreach ($s in $srcs) {
    $s | Add-Member -NotePropertyName Name -NotePropertyValue $(
        if ($s.Raw -match '<URI>\\?([^<]+)</URI>') { $matches[1] } else { $s.File.BaseName -replace '_', ' ' })
}
$exported = @($srcs | Where-Object { $_.Kind -eq 'export' } | ForEach-Object { $_.Name })
$srcs = @($srcs | Where-Object { $_.Kind -eq 'export' -or $exported -notcontains $_.Name })

if ($Only) {
    $all = @($srcs | ForEach-Object { $_.Name })
    $srcs = @($srcs | Where-Object { Test-OnlyMatch $_.Name $Only })
    if (-not $srcs) { throw "-Only '$Only' matched no task. Available: $($all -join ', ')" }
    Write-Host ("scope  : -Only '{0}' — {1} of {2} task(s); every other task on this machine is left exactly as it is`n" -f $Only, $srcs.Count, $all.Count)
}

$done = 0
$failed = @()
foreach ($src in $srcs) {
    $f = $src.File
    $name = $src.Name
    $raw = $src.Raw

    # Templates carry placeholders; exports carry the laptop's literal paths. Doing both
    # substitutions on both kinds is harmless and means a template can be promoted to an export
    # (or the reverse) without touching this loop.
    $body = $raw.Replace($OLD_REPO, $RepoPath).Replace($OLD_PROFILE, $ProfilePath)
    $body = $body.Replace('__REPO__', $RepoPath).Replace('__PROFILE__', $ProfilePath).Replace('__USER__', $Me)
    if ($body -match '__(REPO|PROFILE|USER)__') {
        throw "Unsubstituted placeholder left in '$($f.Name)'. Refusing to register a task with a literal __PLACEHOLDER__ path."
    }
    # <Author> is cosmetic registration metadata (Windows ignores it; -User below sets the real
    # principal) — but leaving the laptop's name on a desktop task makes an audit read wrong later.
    $body = $body -replace '<Author>[^<]*</Author>', "<Author>$Me</Author>"
    # DROP THE XML DECLARATION (2026-09-26). -Xml hands Task Scheduler a .NET string, i.e. UTF-16.
    # A template says encoding="UTF-8", and Task Scheduler refuses to switch encodings mid-string:
    #     The task XML is malformed. (1,40)::ERROR: unable to switch the encoding
    # Every template failed this way. Exports declare UTF-16 and were fine, but with no declaration
    # at all both kinds parse.
    $body = $body -replace '^[\uFEFF\s]*<\?xml[^>]*\?>\s*', ''

    if ($PSCmdlet.ShouldProcess($name, 'Register')) {
        # The ScheduledTasks cmdlets are CDXML: they do NOT inherit this script's
        # $ErrorActionPreference, so a failed Register used to print an error and carry on, and the
        # script went on to say ENABLED / installed / ARMED about a task that did not exist.
        # -ErrorAction Stop on each call, and nothing is reported as done until all of it succeeded.
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
            # -User rewrites the principal, so the laptop's SID never follows the task across machines
            Register-ScheduledTask -TaskName $name -Xml $body -User $Me -Force -ErrorAction Stop | Out-Null
            if ($Enable) { Enable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null }
            else { Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null }
            $state = (Get-ScheduledTask -TaskName $name -ErrorAction Stop).State
            if ($Enable -and "$state" -eq 'Disabled') { throw "registered, but its state is still Disabled" }
            if (-not $Enable -and "$state" -ne 'Disabled') { throw "registered, but its state is $state, not Disabled" }
        } catch {
            $failed += $name
            Write-Host ("  {0,-28} {1,-22} [{2}]  {3}" -f $name, 'FAILED', $src.Kind, $_.Exception.Message) -ForegroundColor Red
            continue
        }
        Write-Host ("  {0,-28} {1,-22} [{2}]" -f $name, $(if ($Enable) { 'ENABLED' } else { 'registered (disabled)' }), $src.Kind)
        $done++
    }
}

if ($failed) {
    Write-Host "`n$done task(s) installed, $($failed.Count) FAILED: $($failed -join ', ')" -ForegroundColor Red
    Write-Host "The rest of this run (prerequisite warnings, the ARMED notice) was skipped. Fix the error above and re-run; the failed task(s) may now be" -ForegroundColor Red
    Write-Host "MISSING from this machine (the old copy is unregistered before the new one is registered)." -ForegroundColor Red
    exit 1
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
    Write-Host "`nOne task is NOT in this set on a machine where it was hand-registered before"
    Write-Host "2026-09-18 — audit what is actually live by enumerating, never from this list:"
    Write-Host "    pwsh -c \"Get-ScheduledTask | ? TaskName -like '*ealFlow*' | ft TaskName,State\""
}
