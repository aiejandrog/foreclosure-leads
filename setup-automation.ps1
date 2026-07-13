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
              -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
              -RunOnlyIfNetworkAvailable
try {
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Pull new Miami-Dade auction leads + skip-trace phones, rebuild and publish DEALFLOW.' -Force | Out-Null
} catch {
  # WakeToRun can require elevation; fall back to a still-reliable catch-up task
  Write-Host "  (WakeToRun needs admin - registering without wake; StartWhenAvailable still catches up)"
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable
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
