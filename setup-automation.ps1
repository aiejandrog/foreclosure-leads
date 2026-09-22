param([switch]$Force)
# =====================================================================
#  SUPERSEDED 2026-09-21 - this script re-creates three faults this
#  project has already paid for. It refuses to run without -Force.
#
#  CLAUDE.md: "Register/enable/disable only via
#  pwsh .\desktop-setup\install-tasks.ps1". That is the path that
#  substitutes paths per machine, registers every task DISABLED, and
#  reads the hardened definitions in desktop-setup/tasks/ and
#  desktop-setup/task-templates/. Use it:
#
#      pwsh .\desktop-setup\install-tasks.ps1 -Only Refresh
#      pwsh .\desktop-setup\install-tasks.ps1 -Only Refresh -Enable
#
#  WHAT THIS FILE WOULD DO IF YOU RAN IT TODAY:
#    1. Unregister-ScheduledTask on EVERY task whose action mentions
#       refresh-dealflow - which is the live, hardened 05:30 export. It
#       is gitignored, so on a machine without the transfer bundle it is
#       not recoverable after that.
#    2. Re-register it at 9:00 AM, not 05:30.
#    3. New-ScheduledTaskSettingsSet with -ExecutionTimeLimit 120 min.
#       install-tasks.ps1's header: "PT2H silently killed rebuild/publish
#       for days". The measured chain on 2026-09-20 took 3h08m.
#    4. No -IdleSettings, so StopOnIdleEnd defaults back to true - the
#       2026-08-31 root cause written up at the :end label of
#       refresh-dealflow.bat: the run is terminated the moment somebody
#       sits down at the machine, exit 255, eleven consecutive dead days.
#    5. Point all of it at C:\Users\olqbb\projects\foreclosure-leads,
#       hardcoded below, which is not where this checkout lives.
#
#  It is kept rather than deleted because it is the record of how the
#  original task was built, and its Desktop-shortcut half still works.
# =====================================================================
if (-not $Force) {
    Write-Host "setup-automation.ps1 is superseded - see the header of this file." -ForegroundColor Yellow
    Write-Host "It would DELETE the hardened 05:30 task and re-register it at 9:00 AM with a 2-hour"
    Write-Host "kill and StopOnIdleEnd back on. Use instead:"
    Write-Host "    pwsh .\desktop-setup\install-tasks.ps1 -Only Refresh -Enable"
    Write-Host "Re-run with -Force only if you have read the header and mean it."
    exit 1
}

# Sets up DEALFLOW's hands-off refresh:
#   1) replaces any old/broken scheduled task with a reliable "DEALFLOW Refresh" (daily, catches up
#      if the PC was asleep, wakes to run, 30-min kill-switch so a hung scrape can never stall forever)
#   2) drops a "Refresh DEALFLOW" icon on the Desktop for a manual one-press.
# Re-runnable (idempotent). Run:  pwsh -ExecutionPolicy Bypass -File setup-automation.ps1
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\olqbb\projects\foreclosure-leads'
$bat  = Join-Path $repo 'refresh-dealflow.bat'
$name = 'DEALFLOW Refresh'

Write-Host '=== existing tasks that reference this project ==='
$old = Get-ScheduledTask | Where-Object {
  $_.Actions | Where-Object {
    ($_.Execute  -match 'run-leads|refresh-dealflow|foreclosure') -or
    ($_.Arguments -match 'run-leads|refresh-dealflow|foreclosure')
  }
}
foreach ($t in $old) {
  Write-Host (" - {0}  [{1}]" -f $t.TaskName, $t.State)
  if ($t.TaskName -ne $name) {
    Unregister-ScheduledTask -TaskName $t.TaskName -Confirm:$false
    Write-Host ("   removed old task: {0}" -f $t.TaskName)
  }
}

$action   = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -Daily -At '9:00AM'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 120) -MultipleInstances IgnoreNew `
              -RunOnlyIfNetworkAvailable
try {
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Pull new Miami-Dade auction leads + skip-trace phones, rebuild and publish DEALFLOW.' -Force | Out-Null
} catch {
  # WakeToRun can require elevation; fall back to a still-reliable catch-up task
  Write-Host "  (WakeToRun needs admin - registering without wake; StartWhenAvailable still catches up)"
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 120) -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Pull new Miami-Dade auction leads + skip-trace phones, rebuild and publish DEALFLOW.' -Force | Out-Null
}
Write-Host "registered: '$name' (daily 9:00 AM, catches up if missed, 30-min kill-switch)"

$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Refresh DEALFLOW.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $bat
$sc.WorkingDirectory = $repo
$sc.IconLocation     = 'shell32.dll,238'
$sc.Description       = 'Refresh DEALFLOW now: pull new leads + phones and publish.'
$sc.Save()
Write-Host "desktop shortcut: $lnk"
Write-Host 'DONE.'
