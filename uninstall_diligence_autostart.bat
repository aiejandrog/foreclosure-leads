@echo off
setlocal
cd /d "%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TASK=DealflowDiligence"

echo Removing Dealflow Diligence autostart...
del /F /Q "%STARTUP%\DealflowDiligence.lnk" 2>nul
del /F /Q "%STARTUP%\DealflowDiligence.vbs" 2>nul
schtasks /Delete /TN "%TASK%" /F >nul 2>&1

echo Stopping diligence_server...
python -c "import os,signal,subprocess,sys
try:
  out=subprocess.check_output('wmic process where \"name=\'pythonw.exe\'\" get ProcessId,CommandLine /FORMAT:CSV',shell=True,text=True,errors='replace')
except Exception:
  out=''
for line in out.splitlines():
  if 'diligence_server' in line.lower():
    parts=[p.strip() for p in line.split(',') if p.strip()]
    if parts and parts[-1].isdigit():
      try: os.kill(int(parts[-1]), signal.SIGTERM)
      except Exception: pass
print('stopped')" 2>nul

echo Done.
pause
endlocal
