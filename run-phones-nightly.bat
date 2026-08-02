@echo off
setlocal
rem DEALFLOW nightly phones job (lean, UNATTENDED). No `pause` anywhere -- a scheduled task can't answer one.
rem Flow: llc_officers (free Sunbiz humans) -> skiptrace (hardened, fails loud) -> rebuild -> commit -> push.
rem If skip-trace ABORTS it stops BEFORE the rebuild, so a good board is never overwritten with a phone-poor one.
rem   skiptrace exit codes: 0 ok | 2 key/balance dead | 3 provider down | 4 over --max-spend.
rem Night one clears the backlog (~$40); every night after only pays for NEW leads (cache dedupes the rest).
cd /d "%~dp0"
set "LOG=%~dp0phones-run.log"
set "STATUS=%USERPROFILE%\OneDrive\Desktop\DEALFLOW-PHONES-STATUS.txt"
set "STAMP=%date% %time%"

echo ==== phones-nightly %STAMP% ==== >> "%LOG%"

rem 1) free Sunbiz officer names so LLC-owned leads have a human to trace (skiptrace reads llc_officers.json)
python llc_officers.py --all >> "%LOG%" 2>&1

rem 2) the hardened trace. The REAL ceiling is the shared daily budget in bd_budget.py (one wallet,
rem    every script, every scheduler) -- see `python bd_budget.py` to view or `--cap N` to change.
rem    --max-spend is a second belt on top of it, scoped to this one run.
python skiptrace.py --all --max-spend 5 >> "%LOG%" 2>&1
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo [%STAMP%] PHONES BLOCKED - skiptrace exit %RC% [2=key/balance 3=provider-down 4=over-budget]. Nothing rebuilt or pushed. See phones-run.log.> "%STATUS%"
  echo PHONES BLOCKED - skiptrace exit %RC%, nothing rebuilt/pushed >> "%LOG%"
  exit /b %RC%
)

rem 3) rebuild the ENCRYPTED board with the fresh numbers baked in
python -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%STAMP%] REBUILD FAILED - nothing pushed. See phones-run.log.> "%STATUS%"
  echo REBUILD FAILED >> "%LOG%"
  exit /b 1
)

rem 4) publish. ONLY the encrypted board + public Sunbiz officers -- NEVER `git add -A`
rem    (skiptrace_results.json / leads_final.json are gitignored PII and must stay off the public repo).
git add docs/index.html llc_officers.json
git commit -m "phones: nightly skip-trace refresh" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%STAMP%] OK - no new numbers since last run, nothing to push.> "%STATUS%"
  echo no changes to commit >> "%LOG%"
  exit /b 0
)
git push origin main >> "%LOG%" 2>&1 || (timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1)

echo [%STAMP%] OK - phones refreshed + published. Live in ~1-2 min.> "%STATUS%"
echo ==== done %date% %time% ==== >> "%LOG%"
exit /b 0
