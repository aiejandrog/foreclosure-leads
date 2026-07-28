# Prefer Register-ScheduledTask (WorkingDirectory). If PowerShell is broken, bat falls back to schtasks.
$ErrorActionPreference = 'Stop'
$TaskName = 'DealflowDiligence'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $RepoRoot 'diligence_server.py'

function Resolve-PythonW {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return $cmd.Source }
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py -and $py.Source) {
        $sibling = Join-Path (Split-Path -Parent $py.Source) 'pythonw.exe'
        if (Test-Path $sibling) { return $sibling }
    }
    throw 'pythonw.exe not found. Install Python and ensure pythonw is on PATH.'
}

if (-not (Test-Path $ScriptPath)) {
    throw "Missing diligence_server.py at $ScriptPath"
}

$PythonW = Resolve-PythonW
Write-Host "pythonw: $PythonW"
Write-Host "script:  $ScriptPath"
Write-Host "workdir: $RepoRoot"

$action = New-ScheduledTaskAction `
    -Execute $PythonW `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName (At logon)"

# Start immediately so Run Diligence works without reboot
Start-Process -FilePath $PythonW -ArgumentList "`"$ScriptPath`"" -WorkingDirectory $RepoRoot -WindowStyle Hidden
Write-Host 'Started diligence server now (pythonw, no window).'

Start-Sleep -Seconds 2
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 5
    Write-Host "Health OK: $($r.Content)"
} catch {
    Write-Host 'Health check not ready yet — check diligence_server.log if Run Diligence still fails.'
}

Write-Host ''
Write-Host 'Done. Run Diligence on the Call Sheet needs no open window.'
Write-Host 'Uninstall: uninstall_diligence_autostart.bat'
