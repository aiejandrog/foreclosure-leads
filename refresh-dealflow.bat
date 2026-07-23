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

rem  Publish the fresh leads NOW, before the slower cases/records/phones steps -- so even if a
rem  later step is slow or fails, the newest leads are already live on the site.
echo [1b/5] Publishing fresh leads immediately...
git add docs/index.html >> "%LOG%" 2>&1
git commit -m "refresh: fresh leads" >> "%LOG%" 2>&1
if not errorlevel 1 (
  git push origin main >> "%LOG%" 2>&1
  if errorlevel 1 ( timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1 )
  echo     fresh leads pushed - enrichment continues below.>> "%LOG%"
)

rem  Re-scrape the OTHER counties too (Miami-Dade was done above by foreclosure_leads.py). county_leads.py
rem  has its own thin-scrape guard, so a blocked county keeps its last good file. The fresh county leads
rem  land in the final rebuild (step 4) which re-merges every *_leads.json.
echo [1c/5] Refreshing Broward + Palm Beach auctions (statewide cadastral enrich)...
python county_leads.py --county broward >> "%LOG%" 2>&1
if errorlevel 1 echo     ^!^! Broward scrape thin/failed - kept last good Broward file.>> "%LOG%"
python county_leads.py --county "palm beach" >> "%LOG%" 2>&1
if errorlevel 1 echo     ^!^! Palm Beach scrape thin/failed - kept last good Palm Beach file.>> "%LOG%"

echo [2/5] Generating direct court-case + records links (new owners only; capped so publish is never starved)...
python gen_cases_qs.py --limit 40 >> "%LOG%" 2>&1
python gen_records_qs.py --limit 40 >> "%LOG%" 2>&1

echo [2c/5] Deep per-parcel tax links for Broward leads (county-taxes.net account URLs, new only)...
python gen_tax_links.py --limit 60 >> "%LOG%" 2>&1

echo [2d/5] Radius comps for Broward + Palm Beach leads (cadastral recent sales, new only)...
python comps.py --limit 80 >> "%LOG%" 2>&1

echo [2b/5] Pulling recorded mortgage chains -> surviving 2nd mortgages (2Captcha solves the Turnstile wall)...
rem  Miami-Dade Official Records sits behind Cloudflare Turnstile. captcha_solver.py -> 2Captcha mints a
rem  valid token (~$0.003/solve) so records_liens.py reads the chain with plain requests, no browser.
rem  --all SKIPS already-traced cases (line 361), so each run only spends on genuinely NEW leads; --limit
rem  60 caps a single run at ~$0.18 so a bad day can never run the 2Captcha balance away.
python records_liens.py --all --limit 60 >> "%LOG%" 2>&1
rem  Broward records are captcha-free (AcclaimWeb, curl session) - pull the chain for new Broward leads.
if exist broward_leads.json python broward_liens.py --all >> "%LOG%" 2>&1
rem  BatchData property API = the second lien feed + the ONLY automated path for Palm Beach (no captcha).
rem  Fails fast + skips itself when the balance is exhausted, so it's safe to leave wired.
if exist batchdata.key python batchdata_liens.py --all --limit 80 >> "%LOG%" 2>&1

echo [2c/5] Fresh LIS PENDENS front-of-funnel (name-sweep top plaintiffs, ISO dates -> lp_leads.json)...
rem  The docket-wide blank-name sweep is walled, but NAME searches aren't: sweep the ~34 lenders who
rem  file most foreclosures over a rolling window, keep the LIS PENDENS, dedupe -> the owner the DAY
rem  their case is filed. lp_leads.py shapes them into st='LP' board leads (the Fresh-filings lane).
if exist captcha.key python lis_pendens.py --days 30 >> "%LOG%" 2>&1
if exist lis_pendens.json python lp_leads.py >> "%LOG%" 2>&1

echo [3/5] Humans behind LLC owners (Sunbiz officers + agent; FREE) - MUST run before skip-trace...
rem  Resolve the person behind every LLC FIRST, so the skip-trace step below can trace that officer
rem  (skiptrace.py reads llc_officers.json). Free Sunbiz curl - always runs, even with no phone key,
rem  so a company-owned lead still ships with a human name + People/CyberBG links.
python llc_officers.py --limit 60 >> "%LOG%" 2>&1

echo [3b/5] Skip-tracing owner + LLC-officer phones (ALL tiers, capped so a run can't overspend)...
if exist tracerfy.key goto :phones
if exist batchdata.key goto :phones
echo     no phone key present - skipping phones ^(names + People links still publish^).>> "%LOG%"
echo     (no phone key - names/People links only)
goto :rebuild

:phones
rem  --all = every human owner + (via the code) every resolved LLC officer, not just Tier A.
rem  --limit 120 caps a single run's spend; already-cached leads are skipped so it stays incremental.
python skiptrace.py --all --limit 120 >> "%LOG%" 2>&1

:rebuild
echo [3b/5] Property photos (Zillow listings all tiers + Street View when keyed + satellite aerials -^> docs/img)...
python property_photos.py --zillow >> "%LOG%" 2>&1

echo [3c/5] Property types (BCPA + PBCPAO use codes, cached per folio)...
python property_types.py >> "%LOG%" 2>&1

echo [3d/5] Zillow listing status (LISTED/PENDING/SOLD/RENTAL/OFF-MKT, 7-day cache)...
python listing_status.py --limit 120 >> "%LOG%" 2>&1

echo [3e/5] Sale-history survival counts (MD docket, 7-day cache - the STALLER signal)...
python sale_history.py --limit 150 >> "%LOG%" 2>&1

rem  [moved up to [3/5]] llc_officers now runs BEFORE skip-trace so officer phones can be pulled.

echo [4/5] Rebuilding the site (cases + phones + photos baked in)...
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
echo [health] Checking shipped data + upstream sources...
python healthcheck.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! HEALTH: a source is DOWN or the data looks wrong - see leads-run.log.
) else (
  echo     health OK.
)
rem  Self-report: writes DEALFLOW-STATUS.txt to the Desktop + a tray notification, so an unattended
rem  7 AM run tells you whether it worked without needing anyone watching.
echo [report] Writing run status to Desktop + notification...
python run_report.py >> "%LOG%" 2>&1
echo ==================== done %date% %time% ====================>> "%LOG%"
endlocal
