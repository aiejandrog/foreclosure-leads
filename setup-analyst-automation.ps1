# Registers the standing business-analyst automation. Mirrors setup-phones-automation.ps1.
#   - "DealFlow Weekly Analyst": Sundays 7:30 AM -> run-analyst-weekly.bat
#       (fresh replies.py pull, then analyst.py -> Desktop\DealFlow-Scorecard\YYYY-MM-DD_scorecard.html)
#   - "DealFlow Replies": daily 7:00 AM -> run-replies-daily.bat
#       (the inbox must never go days unchecked again while cold email is in flight)
# Re-runnable (idempotent). Run:  pwsh -ExecutionPolicy Bypass -File setup-analyst-automation.ps1
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\olqbb\projects\foreclosure-leads'

function Register-DealflowTask($name, $bat, $trigger, $desc, $mins) {
  if (-not (Test-Path $bat)) { throw "missing $bat" }
  $existing = Get-ScheduledTask | Where-Object { $_.TaskName -eq $name }
  if ($existing) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; Write-Host "removed old task: $name" }
  $action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $repo
  try {
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
                  -ExecutionTimeLimit (New-TimeSpan -Minutes $mins) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description $desc -Force | Out-Null
  } catch {
    Write-Host '  (WakeToRun needs admin - registering without wake; StartWhenAvailable still catches up)'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                  -ExecutionTimeLimit (New-TimeSpan -Minutes $mins) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description $desc -Force | Out-Null
  }
  Write-Host "registered: '$name'"
}

Register-DealflowTask 'DealFlow Weekly Analyst' (Join-Path $repo 'run-analyst-weekly.bat') `
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '7:30AM') `
  'Weekly DealFlow business scorecard: funnel truth, reply-SLA, compliance flags, pace to $10k.' 15

Register-DealflowTask 'DealFlow Replies' (Join-Path $repo 'run-replies-daily.bat') `
  (New-ScheduledTaskTrigger -Daily -At '7:00AM') `
  'Daily IMAP reply check so an owner writing back is never unseen for days.' 10

Write-Host 'DONE. Weekly scorecard Sundays 7:30 AM; replies checked daily 7:00 AM.'
