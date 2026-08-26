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
REM PRICE THE MEDIUM ROWS FIRST — lp_upgrade.py adjudicates them against the PA record (natural
REM person + homestead = knockable), and that needs value/dor/paOwners on the row. The 05:30
REM refresh REWRITES lp_addresses.json and runs lp_values.py WITHOUT --all, which prices only
REM high-confidence rows — so every morning the county-verified pool silently came back EMPTY
REM (27 doors -> 0, measured 2026-08-26; a third of the route inventory vanishing with no error).
REM --all prices medium/low too. Free county endpoint, no key, and folio-cached, so after the
REM first pass this is a handful of new folios a day. A failure here must not stop the routes:
REM the generator still builds from the auction + fresh-LP pools.
python -u lp_values.py --all >> "%~dp0daily-routes-run.log" 2>&1
python -u bsg_daily_routes.py >> "%~dp0daily-routes-run.log" 2>&1
if errorlevel 1 (
  echo [%date% %time%] ROUTES FAILED - run "python bsg_daily_routes.py" by hand to see why >> "%STATUS%"
  exit /b 1
)
echo [%date% %time%] done - BSG_Daily_Routes PDF is in %USERPROFILE%\DEALFLOW >> "%STATUS%"
endlocal
