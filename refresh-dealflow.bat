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
rem  GUARDED 2026-08-19: same corrupt-page + enrichment gate as the final publish. This early push
rem  is the exact block whose autostash pull can write conflict markers into docs/index.html (the
rem  08-19 outage class) - never push a build the guard refuses.
python -u publish_guard.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! GATE: publish_guard blocked the early publish - continuing enrichment unpublished.>> "%LOG%"
  goto :afterearly
)
git add docs/index.html docs/call >> "%LOG%" 2>&1
git commit -m "refresh: fresh leads" >> "%LOG%" 2>&1
if not errorlevel 1 (
  rem  PULL BEFORE PUSH. Without this the push is rejected non-fast-forward the moment GitHub
rem  Actions pushes anything (it publishes the balloon book on its own schedule), and the
rem  "retry" below is the SAME push 6s later, which fails identically. Measured 2026-08-16:
rem  4 commits stacked and the LIVE SITE SAT FROZEN AT 08-14 for two days while every local
rem  run still reported success. -X theirs mirrors .github/workflows/refresh.yml exactly.
git pull --rebase --autostash -X theirs origin main >> "%LOG%" 2>&1
git push origin main >> "%LOG%" 2>&1
  if errorlevel 1 ( timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1 )
  echo     fresh leads pushed - enrichment continues below.>> "%LOG%"
)
:afterearly

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
rem  ORDER FIX 2026-08-19: this bat used to run lp_leads.py FIRST and then
rem  lp_resolve/lp_values/lp_status - the exact inversion lp_refresh.py's docstring calls out:
rem  lp_resolve's whole-row merge strips everything lp_values wrote, so every LP lead shipped with
rem  value=0 / hs=False and equity ranking was silently dead for the freshest lane on the board.
rem  It also never ran lp_resolve2 or fl_lp/broward_resolve at all. lp_refresh.py IS the canonical
rem  chain (sweep -> resolve -> resolve2 -> broward_resolve -> values -> status -> leads -> phones),
rem  fail-fast, and stamps lp_meta.json so healthcheck can age it. One line replaces five.
if exist captcha.key python -u lp_refresh.py --days 30 >> "%LOG%" 2>&1

echo [2d/5] Geocoding new leads (keyless US Census) -> lat/lng for the origin-anchored door route...
python -u geo_enrich.py >> "%LOG%" 2>&1

echo [3/5] Humans behind LLC owners (Sunbiz officers + agent; FREE) - MUST run before skip-trace...
rem  Resolve the person behind every LLC FIRST, so the skip-trace step below can trace that officer
rem  (skiptrace.py reads llc_officers.json). Free Sunbiz curl - always runs, even with no phone key,
rem  so a company-owned lead still ships with a human name + People/CyberBG links.
python -u llc_officers.py --limit 60 >> "%LOG%" 2>&1

echo [3b/5] Skip-tracing owner + LLC-officer phones (ALL tiers, capped so a run can't overspend)...
if exist tracerfy.key goto :phones
rem BatchData RETIRED 2026-08-16: the provider was exited 08-11 (see BATCHDATA-EXIT.md) but this
rem gate still treated its key as a reason to enter the phones stage - meaning a Tracerfy key
rem problem would silently fail over to the $0.15/hit provider we left. Tracerfy key only now.
echo     no phone key present - skipping phones ^(names + People links still publish^).>> "%LOG%"
echo     (no phone key - names/People links only)
goto :rebuild

:phones
rem  --all = every human owner + (via the code) every resolved LLC officer, not just Tier A.
rem  --limit caps a single run's spend; already-cached leads are skipped so it stays incremental.
rem
rem  RAISED 6 -> 100 (2026-08-15). The 6 came from a $1/day emergency budget cap and then stayed,
rem  while the comment above it still claimed 120. Measured consequence: ~21 NEW cases land on the
rem  board every day, so tracing only 6 of them meant the DIALABLE pool fell further behind every
rem  single night no matter how many leads arrived - 263 of 696 board leads had no phone at all, and
rem  the in-repo estimate to clear that backlog was ~26 nights. That is a real reason the same names
rem  kept coming back: only 291 leads were ever workable and 82.5% of those had already been worked.
rem  skiptrace.py still enforces its own --max-spend and the shared bd_budget daily dollar cap, so
rem  this raises the throughput ceiling without removing the spend guard.
python -u skiptrace.py --all --limit 100 >> "%LOG%" 2>&1

rem [3a-2] Whitepages Pro - DISABLED 2026-08-16 by Alejandro's call. The plan quota has been
rem exhausted since 08-08 (nine straight nights of first-call 429s, zero results, last success
rem 08-07 09:51) while still consuming a slot of the shared daily budget. Real cost when live was
rem $0.22/call, not the $0.10 this comment used to claim. Cached results still bake into the board.
rem To RE-ENABLE after renewing the plan, restore this line:
rem   if exist whitepages.key python -u whitepages_lookup.py --all --limit 30 >> "%LOG%" 2>&1
rem Manual gap runs still work anytime: python whitepages_lookup.py --gap --limit N

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

echo [3f/5] Ownership flip gate (live appraiser owner vs defendant; budget-capped)...
rem  ADDED 2026-08-19: ownership_scan.py was called by NOTHING - not this bat, not refresh.yml -
rem  so the title-flip truth on the board was only as fresh as the last time a human remembered
rem  to run it (it had been stamped 08-14 for five days). A lead whose title already transferred
rem  is a WRONG-PERSON conversation waiting to happen. Free, and --budget bounds it to 3 minutes.
python -u ownership_scan.py --days 45 --max 80 --budget 180 >> "%LOG%" 2>&1

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

echo [4c/5] Archiving today's auction calendar (churn + cash-buyer history)...
rem  ADDED 2026-08-15. This step lives in refresh.yml but was never in this bat, and the cloud
rem  workflow stopped publishing on 07-27 - so auction_archive.json holds 722 records ALL stamped
rem  2026-08-04. It ran exactly once, ever. The consequence is that NOTHING in this repo tracks the
rem  same case across two points in time, so reschedule rate, drop-off rate and "is the board
rem  actually churning" were unanswerable - which is precisely the question that started this
rem  restructure. Each nightly append makes churn measurable from here on.
python -u auction_archive.py >> "%LOG%" 2>&1

echo [4d/5] Team CRM (Desktop CSV always; Google Sheets when sheets_crm_webhook.url exists)...
rem  Reads the Desktop twin's RAW payload, so it MUST run after the [4/5] rebuild above.
python -u sheets_crm.py >> "%LOG%" 2>&1

:publish
rem  GATE BEFORE PUBLISH (2026-08-19). healthcheck used to run only at :end - AFTER the push - so a
rem  FAIL could never stop a bad board from going live from this machine; and publish_guard (the
rem  corrupt-page + enrichment-regression check that stopped the conflict-marker outage in CI) was
rem  never in this bat at all. Same gates as the cloud workflow now: either one failing skips the
rem  push, the board stays on its last good build, and the run still writes its report.
echo [gate] healthcheck + publish guard before anything goes live...
python -u healthcheck.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! GATE: healthcheck FAILED - publish SKIPPED, board stays on last good build.>> "%LOG%"
  echo     ^!^! GATE: healthcheck FAILED - publish SKIPPED. See leads-run.log.
  goto :end
)
python -u publish_guard.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! GATE: publish_guard BLOCKED the build - publish SKIPPED.>> "%LOG%"
  echo     ^!^! GATE: publish_guard BLOCKED the build - publish SKIPPED. See leads-run.log.
  goto :end
)
echo [5/5] Publishing to the live site...
git add docs/index.html docs/call >> "%LOG%" 2>&1
git commit -m "refresh: auto lead + phone update" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     nothing changed - site already current.>> "%LOG%"
  echo     Already current - nothing to push.
  goto :end
)
rem  PULL BEFORE PUSH. Without this the push is rejected non-fast-forward the moment GitHub
rem  Actions pushes anything (it publishes the balloon book on its own schedule), and the
rem  "retry" below is the SAME push 6s later, which fails identically. Measured 2026-08-16:
rem  4 commits stacked and the LIVE SITE SAT FROZEN AT 08-14 for two days while every local
rem  run still reported success. -X theirs mirrors .github/workflows/refresh.yml exactly.
git pull --rebase --autostash -X theirs origin main >> "%LOG%" 2>&1
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
