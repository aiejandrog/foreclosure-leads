@echo off
REM One-time: start Diligence API now + every Windows logon.
REM No PowerShell required (PS is broken on some machines here).
setlocal EnableExtensions
cd /d "%~dp0"
set "REPO=%CD%"
set "VBS=%REPO%\run_diligence_silent.vbs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTVBS=%STARTUP%\DealflowDiligence.vbs"
set "TASK=DealflowDiligence"

echo.
echo === Dealflow Diligence — one-time install ===
echo Repo: %REPO%
echo.

if not exist "%REPO%\diligence_server.py" (
  echo ERROR: diligence_server.py missing.
  pause
  exit /b 1
)
if not exist "%VBS%" (
  echo ERROR: run_diligence_silent.vbs missing.
  pause
  exit /b 1
)

if exist "C:\Program Files\Python311\pythonw.exe" (
  echo Found pythonw: C:\Program Files\Python311\pythonw.exe
) else (
  where pythonw >nul 2>&1
  if errorlevel 1 (
    echo ERROR: pythonw.exe not found. Install Python and add it to PATH.
    pause
    exit /b 1
  )
  echo Found pythonw on PATH.
)

REM 1) Startup folder — copy silent launcher (survives reboot, no admin)
if not exist "%STARTUP%" mkdir "%STARTUP%" 2>nul
copy /Y "%VBS%" "%STARTVBS%" >nul
if exist "%STARTVBS%" (
  echo OK: Autostart → %STARTVBS%
) else (
  echo ERROR: Could not write Startup folder:
  echo   %STARTUP%
  pause
  exit /b 1
)

REM 2) Optional Task Scheduler (ignore if Access denied)
schtasks /Delete /TN "%TASK%" /F >nul 2>&1
schtasks /Create /TN "%TASK%" /TR "wscript.exe \"%VBS%\"" /SC ONLOGON /RL LIMITED /F >nul 2>&1
if not errorlevel 1 (
  echo OK: Scheduled task %TASK%
) else (
  echo NOTE: Task Scheduler skipped — Startup copy is enough.
)

REM 3) Start NOW
echo.
echo Starting Diligence API now...
wscript //B "%VBS%"
ping -n 5 127.0.0.1 >nul

REM 4) Health (python only — no PowerShell)
echo.
echo Checking http://127.0.0.1:8765/health ...
python -c "import urllib.request,json,sys; j=json.load(urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=8)); print('HEALTH OK:', j); sys.exit(0 if j.get('ok') else 1)"
if errorlevel 1 goto :fail
echo.
echo Done. You can close this window — Diligence stays in the background.
echo Open Dealflow → Call Sheet → Diligence auto-runs.
echo Uninstall: uninstall_diligence_autostart.bat
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo HEALTH FAILED. Last log:
echo -----
python -c "import os; p='diligence_server.log'; print(open(p,encoding='utf-8',errors='replace').read()[-2000:] if os.path.exists(p) else '(no log)')"
echo -----
echo Try debug: start_diligence.bat
pause
endlocal
exit /b 1
