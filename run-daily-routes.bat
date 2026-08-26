@echo off
REM Daily door plan — builds BSG_Daily_Routes_YYYY-MM-DD.pdf for Alejandro + Carlos.
REM Scheduled as "DealFlow Daily Routes", 06:30 Mon-Sat: after the 05:30 refresh has landed
REM geocodes + rebuilt the board and the 06:00 phones pass (PT20M) has finished, so the plan is
REM built from the freshest data of the morning and is waiting in ~\DEALFLOW before anyone wakes.
REM No pause anywhere — a scheduled task can't answer one. NO git anything: the output is
REM homeowner PII on a public repo; it stays local (BSG_* is gitignored).
setlocal
cd /d "%~dp0"
set "STATUS=%USERPROFILE%\OneDrive\Desktop\DEALFLOW-ROUTES-STATUS.txt"
echo [%date% %time%] daily routes starting > "%STATUS%"
python -u bsg_daily_routes.py >> "%~dp0daily-routes-run.log" 2>&1
if errorlevel 1 (
  echo [%date% %time%] ROUTES FAILED - run "python bsg_daily_routes.py" by hand to see why >> "%STATUS%"
  exit /b 1
)
echo [%date% %time%] done - BSG_Daily_Routes PDF is in %USERPROFILE%\DEALFLOW >> "%STATUS%"
endlocal
