@echo off
rem =====================================================================
rem  DealFlow Opt-out Sync - daily 07:15, before the 08:00 Morning Worker.
rem  Runs morning_sync.py: replies.py (inbox scan) -> optout_sync.py (the
rem  one ledger writer, ledger_add) -> ledger_sync.py (add-only union with
rem  the other machine). No rebuild, no publish, no push to this repo.
rem  It records the result in sync_status.json; unless TODAY's run finished
rem  OK, send_server /send and cadence-daily.bat HOLD every send
rem  (sync_gate.py). Re-run this by hand after fixing a failure; the hold
rem  clears as soon as a run finishes clean.
rem =====================================================================
setlocal
cd /d "%~dp0"
set "LOG=%~dp0optout-sync-run.log"
echo ==== optout-sync %date% %time% ====>> "%LOG%"
call repo_guard.bat "%~dp0" "%LOG%"
if errorlevel 1 exit /b 1
python -u morning_sync.py >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo ==== optout-sync ENDED rc=%RC% %date% %time% ====>> "%LOG%"
endlocal & exit /b %RC%
