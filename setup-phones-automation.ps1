# Registers DEALFLOW's lean nightly PHONES job as a scheduled task.
#   - "DEALFLOW Phones", daily 6:00 AM (3h ahead of the 9am full refresh, so numbers are already
#     cached by the time the heavy pipeline runs -> its own skiptrace spends nothing).
#   - 20-min kill-switch: a lean job can't time out the way the 120-min full refresh did (2026-07-18).
#   - Catches up if the PC was asleep; wakes to run when allowed.
#   - Drops a "Refresh Phones" icon on the Desktop for a manual one-press.
# This is SEPARATE from "DEALFLOW Refresh" (the full pipeline) on purpose -- it must not inherit that
# job's timeout risk. Re-runnable (idempotent). Run:  pwsh -ExecutionPolicy Bypass -File setup-phones-automation.ps1
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\olqbb\projects\foreclosure-leads'
$bat  = Join-Path $repo 'run-phones-nightly.bat'
$name = 'DEALFLOW Phones'

if (-not (Test-Path $bat)) { throw "missing $bat" }

# Replace only a prior copy of THIS task (leave 'DEALFLOW Refresh' and everything else alone).
$existing = Get-ScheduledTask | Where-Object { $_.TaskName -eq $name }
if ($existing) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
  Write-Host "removed old task: $name"
}

$action  = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At '6:00AM'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew `
              -RunOnlyIfNetworkAvailable
try {
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Nightly skip-trace of new DEALFLOW leads: pull phones, rebuild and publish the board.' -Force | Out-Null
} catch {
  # WakeToRun can require elevation; fall back to a still-reliable catch-up task.
  Write-Host '  (WakeToRun needs admin - registering without wake; StartWhenAvailable still catches up)'
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Nightly skip-trace of new DEALFLOW leads: pull phones, rebuild and publish the board.' -Force | Out-Null
}
Write-Host "registered: '$name' (daily 6:00 AM, 20-min kill-switch, catches up if missed)"

$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Refresh Phones.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = $bat
$sc.WorkingDirectory = $repo
$sc.IconLocation     = 'shell32.dll,264'
$sc.Description       = 'Skip-trace new leads for phones and publish DEALFLOW now.'
$sc.Save()
Write-Host "desktop shortcut: $lnk"
Write-Host 'DONE. Phones refresh nightly at 6 AM. Requires the laptop to be on (it catches up if it was asleep).'
