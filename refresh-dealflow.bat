@echo off
setlocal
rem =====================================================================
rem  DEALFLOW - one-shot refresh.  Double-click it, or let Task Scheduler
rem  run it. Does the WHOLE chain: pull new auctions -> skip-trace phones
rem  (if a key exists) -> rebuild the site -> push.  Then on the website
rem  you just press refresh (F5) and see the newest leads.
rem
rem  Fail-safes: a thin/blocked scrape never overwrites the live site; a
rem  phone failure never blocks the leads; only pushes when data changed.
rem =====================================================================
cd /d "%~dp0"
set "LOG=leads-run.log"
echo.>> "%LOG%"
echo ==================== REFRESH %date% %time% ====================>> "%LOG%"

echo [1/4] Pulling new auction leads (scrape + enrich)...
python foreclosure_leads.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! scrape failed or too few leads - live site left intact, nothing pushed.>> "%LOG%"
  echo     SCRAPE FAILED - live site unchanged. See leads-run.log.
  goto :end
)

echo [2/5] Generating direct court-case + records links (new owners only; capped so publish is never starved)...
python gen_cases_qs.py --limit 40 >> "%LOG%" 2>&1
python gen_records_qs.py --limit 40 >> "%LOG%" 2>&1

echo [3/5] Skip-tracing owner phones...
if exist tracerfy.key goto :phones
if exist batchdata.key goto :phones
echo     no phone key present - skipping phones ^(leads still publish^).>> "%LOG%"
echo     (no phone key - leads only)
goto :rebuild

:phones
python skiptrace.py >> "%LOG%" 2>&1

:rebuild
echo [4/5] Rebuilding the site (cases + phones baked in)...
python -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))" >> "%LOG%" 2>&1

:publish
echo [5/5] Publishing to the live site...
git add docs/index.html >> "%LOG%" 2>&1
git commit -m "refresh: auto lead + phone update" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     nothing changed - site already current.>> "%LOG%"
  echo     Already current - nothing to push.
  goto :end
)
git push origin main >> "%LOG%" 2>&1
if errorlevel 1 (
  timeout /t 6 /nobreak >nul
  git push origin main >> "%LOG%" 2>&1
)
echo     Pushed - live site updates in ~1-2 min.>> "%LOG%"
echo     DONE - pushed. Refresh the site in ~1-2 min.

:end
echo ==================== done %date% %time% ====================>> "%LOG%"
endlocal
