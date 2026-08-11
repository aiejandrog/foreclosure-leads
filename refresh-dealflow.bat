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
python -u foreclosure_leads.py >> "%LOG%" 2>&1
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
python -u county_leads.py --county broward >> "%LOG%" 2>&1
if errorlevel 1 echo     ^!^! Broward scrape thin/failed - kept last good Broward file.>> "%LOG%"
python -u county_leads.py --county "palm beach" >> "%LOG%" 2>&1
if errorlevel 1 echo     ^!^! Palm Beach scrape thin/failed - kept last good Palm Beach file.>> "%LOG%"

echo [2/5] Generating direct court-case + records links (new owners only; capped so publish is never starved)...
python -u gen_cases_qs.py --limit 40 >> "%LOG%" 2>&1
python -u gen_records_qs.py --limit 40 >> "%LOG%" 2>&1

echo [2c/5] Deep per-parcel tax links for Broward leads (county-taxes.net account URLs, new only)...
python -u gen_tax_links.py --limit 60 >> "%LOG%" 2>&1

echo [2d/5] Radius comps for Broward + Palm Beach leads (cadastral recent sales, new only)...
python -u comps.py --limit 80 >> "%LOG%" 2>&1

echo [2b/5] Pulling recorded mortgage chains -> surviving 2nd mortgages (2Captcha solves the Turnstile wall)...
rem  Miami-Dade Official Records sits behind Cloudflare Turnstile. captcha_solver.py -> 2Captcha mints a
rem  valid token (~$0.003/solve) so records_liens.py reads the chain with plain requests, no browser.
rem  --all SKIPS already-traced cases (line 361), so each run only spends on genuinely NEW leads; --limit
rem  60 caps a single run at ~$0.18 so a bad day can never run the 2Captcha balance away.
python -u records_liens.py --all --limit 60 >> "%LOG%" 2>&1
rem  Broward records are captcha-free (AcclaimWeb, curl session) - pull the chain for new Broward leads.
if exist broward_leads.json python -u broward_liens.py --all >> "%LOG%" 2>&1
rem  Palm Beach chains via the county's OWN Landmark portal (2Captcha v2 - slow but first-party).
rem  Replaces the BatchData lien feed (BATCHDATA-EXIT, 2026-08-11): old bd chains keep merging from
rem  the committed batchdata_liens.json cache; only NEW purchases stopped. --limit 6 bounds a run
rem  to ~6-18 min of captcha solving so a bad portal day can't hang the nightly.
if exist captcha.key if exist palmbeach_leads.json python -u palmbeach_liens.py --all --limit 6 >> "%LOG%" 2>&1

echo [2c/5] Fresh LIS PENDENS front-of-funnel (name-sweep top plaintiffs, ISO dates -> lp_leads.json)...
rem  The docket-wide blank-name sweep is walled, but NAME searches aren't: sweep the ~34 lenders who
rem  file most foreclosures over a rolling window, keep the LIS PENDENS, dedupe -> the owner the DAY
rem  their case is filed. lp_leads.py shapes them into st='LP' board leads (the Fresh-filings lane).
if exist captcha.key python -u lis_pendens.py --days 30 >> "%LOG%" 2>&1
if exist lis_pendens.json python -u lp_leads.py >> "%LOG%" 2>&1

echo [2d/5] Geocoding new leads (keyless US Census) -> lat/lng for the origin-anchored door route...
python -u geo_enrich.py >> "%LOG%" 2>&1

echo [3/5] Humans behind LLC owners (Sunbiz officers + agent; FREE) - MUST run before skip-trace...
rem  Resolve the person behind every LLC FIRST, so the skip-trace step below can trace that officer
rem  (skiptrace.py reads llc_officers.json). Free Sunbiz curl - always runs, even with no phone key,
rem  so a company-owned lead still ships with a human name + People/CyberBG links.
python -u llc_officers.py --limit 60 >> "%LOG%" 2>&1

echo [3b/5] Skip-tracing owner + LLC-officer phones (ALL tiers, capped so a run can't overspend)...
if exist tracerfy.key goto :phones
if exist batchdata.key goto :phones
echo     no phone key present - skipping phones ^(names + People links still publish^).>> "%LOG%"
echo     (no phone key - names/People links only)
goto :rebuild

:phones
rem  --all = every human owner + (via the code) every resolved LLC officer, not just Tier A.
rem  --limit 120 caps a single run's spend; already-cached leads are skipped so it stays incremental.
python -u skiptrace.py --all --limit 6 >> "%LOG%" 2>&1

rem [3a-2] Whitepages Pro API - one paid /v2/property call returns EVERY owner + resident + all
rem typed phones (mobile/landline unmasked) + emails per lead. ~$0.10/call est.; --limit 30 caps
rem a single run so a trial key can't blow up. Cached in whitepages_lookup.json (gitignored); skips
rem already-cached leads. Also flags absentee-owner scenarios (Velima's Jacob = Lewisville TX).
if exist whitepages.key python -u whitepages_lookup.py --all --limit 30 >> "%LOG%" 2>&1

:rebuild
echo [3b/5] Property photos (Zillow listings all tiers + Street View when keyed + satellite aerials -^> docs/img)...
python -u property_photos.py --zillow >> "%LOG%" 2>&1

echo [3c/5] Property types (BCPA + PBCPAO use codes, cached per folio)...
python -u property_types.py >> "%LOG%" 2>&1

echo [3d/5] Zillow listing status (LISTED/PENDING/SOLD/RENTAL/OFF-MKT + Zestimate, 7-day cache)...
python -u listing_status.py --limit 120 >> "%LOG%" 2>&1

echo [3d2/5] Redfin Estimate (advisory AVM cross-check, 21-day cache, headless browser)...
python -u redfin_value.py --limit 100 >> "%LOG%" 2>&1

echo [3e/5] Sale-history survival counts (MD docket, 7-day cache - the STALLER signal)...
python -u sale_history.py --limit 150 >> "%LOG%" 2>&1

rem  [moved up to [3/5]] llc_officers now runs BEFORE skip-trace so officer phones can be pulled.

rem  Harvest hard bounces BEFORE the rebuild so dead addresses are excluded at bake time.
rem  This used to be a manual step a human had to remember after every send day; forgetting it
rem  is how the account ran a 24-33% bounce rate for a week (provider tolerance ~2%) — the
rem  bounce ledger only protects the NEXT send if it is refreshed before the queue bakes.
echo [3h/5] Harvesting hard bounces from the inbox...
python -u bounces.py >> "%LOG%" 2>&1

rem  Verify addresses BEFORE they can enter a queue (Cuban re-judge 2026-08-09: reacting at the
rem  5% banner still eats avoidable reputation damage on every fresh scrape). Free layers only
rem  unless a provider key file exists — suppression ledger, syntax, learned dead domains.
echo [3i/5] Verifying fresh addresses before they enter the sendable pool...
python -u verify_emails.py >> "%LOG%" 2>&1

echo [4/5] Rebuilding the site (cases + phones + photos baked in)...
python -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))" >> "%LOG%" 2>&1

echo [4b/5] Refreshing the Deals-on-the-Clock auction forecast (reads the freshly-built board)...
python -u auction_forecast.py >> "%LOG%" 2>&1

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
python -u healthcheck.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! HEALTH: a source is DOWN or the data looks wrong - see leads-run.log.
) else (
  echo     health OK.
)
rem  Self-report: writes DEALFLOW-STATUS.txt to the Desktop + a tray notification, so an unattended
rem  7 AM run tells you whether it worked without needing anyone watching.
echo [report] Writing run status to Desktop + notification...
python -u run_report.py >> "%LOG%" 2>&1
echo ==================== done %date% %time% ====================>> "%LOG%"
endlocal
