@echo off
REM One-time: start send_server.py now + every Windows logon.
REM Mirrors install_diligence_autostart.bat (Startup folder first; Task Scheduler optional).
setlocal EnableExtensions
cd /d "%~dp0"
set "REPO=%CD%"
set "VBS=%REPO%\run_send_server_silent.vbs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTVBS=%STARTUP%\DealflowSendServer.vbs"
set "TASK=DealflowSendServer"

echo.
echo === Dealflow Send Server - one-time install ===
echo Repo: %REPO%
echo.

if not exist "%REPO%\send_server.py" (
  echo ERROR: send_server.py missing.
  pause
  exit /b 1
)
if not exist "%VBS%" (
  echo ERROR: run_send_server_silent.vbs missing.
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

REM 1) Startup folder - install the silent launcher with the REPO PATH BAKED IN.
REM    DO NOT go back to a plain `copy` here. The VBS used to resolve its repo from its own
REM    location; once copied into Startup that resolved to the Startup folder, so it looked for
REM    <Startup>\send_server.py, failed, and quit silently at EVERY logon with no window and no
REM    log. Autostart was dead from install until 2026-08-07 and looked identical to working.
REM    Python does the rewrite because batch string-replacement is not worth the quoting risk.
if not exist "%STARTUP%" mkdir "%STARTUP%" 2>nul
python -c "import io,os,sys; p=r'%VBS%'; d=r'%STARTVBS%'; s=io.open(p,encoding='utf-8',errors='replace').read(); ok='REPO_PATH = \"\"' in s; s=s.replace('REPO_PATH = \"\"', 'REPO_PATH = \"%REPO%\"',1); io.open(d,'w',encoding='utf-8').write(s); sys.exit(0 if ok else 3)"
if errorlevel 3 (
  echo ERROR: %VBS% is missing the REPO_PATH placeholder - cannot bake the path in.
  pause
  exit /b 1
)
if exist "%STARTVBS%" (
  echo OK: Autostart -^> %STARTVBS%
  echo     repo baked in: %REPO%
) else (
  echo ERROR: Could not write Startup folder:
  echo   %STARTUP%
  pause
  exit /b 1
)

REM 2) Scheduled tasks. The Startup folder only fires at LOGON, so it cannot help when the machine
REM    is already logged in, when the bridge dies mid-day, or when someone logs in after 8am - and
REM    the morning worker refuses to auto-run without the bridge. The 07:45 daily run guarantees it
REM    is up before the 8am auto-run. Repeat firing is safe: send_server.py probes /health first and
REM    exits if a healthy bridge already owns the port (Windows would otherwise happily let a second
REM    process bind the same port and split the daily send cap).
schtasks /Delete /TN "%TASK%" /F >nul 2>&1
schtasks /Create /TN "%TASK%" /TR "wscript.exe \"%STARTVBS%\"" /SC ONLOGON /RL LIMITED /F >nul 2>&1
if not errorlevel 1 (
  echo OK: Scheduled task %TASK% ^(at logon^)
) else (
  echo NOTE: %TASK% ^(ONLOGON^) skipped - needs elevation. Startup folder already covers logon.
)
schtasks /Delete /TN "%TASK%Daily" /F >nul 2>&1
schtasks /Create /TN "%TASK%Daily" /TR "wscript.exe \"%STARTVBS%\"" /SC DAILY /ST 07:45 /RL LIMITED /F >nul 2>&1
if not errorlevel 1 (
  echo OK: Scheduled task %TASK%Daily ^(07:45, before the 8am worker^)
) else (
  echo WARNING: could not create the 07:45 task - bridge may be down at 8am after a mid-day crash.
)

REM 3) Start NOW
echo.
echo Starting send_server on 127.0.0.1:8823 ...
wscript //B "%VBS%"
ping -n 5 127.0.0.1 >nul

REM 4) Health
echo.
echo Checking http://127.0.0.1:8823/health ...
python -c "import urllib.request,json,sys; j=json.load(urllib.request.urlopen('http://127.0.0.1:8823/health',timeout=8)); print('HEALTH OK:', j); sys.exit(0 if j.get('ok') else 1)"
if errorlevel 1 goto :fail
echo.
echo Done. You can close this window - send_server stays in the background.
echo Tracker Email button talks to http://127.0.0.1:8823
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo HEALTH FAILED. Last log:
echo -----
python -c "import os; p='send_server.log'; print(open(p,encoding='utf-8',errors='replace').read()[-2000:] if os.path.exists(p) else '(no log)')"
echo -----
echo Try: python send_server.py
pause
endlocal
exit /b 1
