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

echo [1d/5] Resolving parcel-less auction stubs (defendant name -^> appraiser roll, corroborated)...
rem  'parcel not linked' rows are auction items that publish NOTHING (no address, no folio). The
rem  court knows the defendant and the appraiser knows what they own; stub_resolve ties the two,
rem  caches case-^>folio in stub_folios.json, and the scrape hooks keep it resolved every night.
rem  --limit caps Broward clerk lookups (2Captcha, ~$0.003/case) per run.
python -u stub_resolve.py --sweep --limit 25 >> "%LOG%" 2>&1

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
rem  --retries 80: a cached conf='none' is a FAILURE, not a result. MD cached failures forever,
rem  which froze 259 of 370 leads at "equity unverified" while the tracer reported nothing to do.
rem  stub_resolve (step 1d) backfills the folios these traces need, so yesterday's failure often
rem  succeeds today — retry a real batch every night, not never. Camoufox mints are free.
rem  2026-08-27: this now also covers the LIS PENDENS board. records_liens read only leads_final.json
rem  (370 AUCTION rows, which already have a judgment) and never touched lp_leads.json (1,007 rows
rem  that by definition do NOT). So every fresh filing sat at "debt unknown" forever, and a closer
rem  reading value-with-no-debt reads it as equity - the exact state that put an underwater owner on
rem  a live call. 284 Miami-Dade LP leads folded in; --all picks them up here, 120/night.
python -u records_liens.py --all --limit 120 --retries 80 >> "%LOG%" 2>&1
rem  Broward records are captcha-free (AcclaimWeb, curl session) - pull the chain for new Broward leads.
if exist broward_leads.json python -u broward_liens.py --all >> "%LOG%" 2>&1
rem  Palm Beach chains via the county's OWN Landmark portal (2Captcha v2 - slow but first-party).
rem  Replaces the BatchData lien feed (BATCHDATA-EXIT, 2026-08-11): old bd chains keep merging from
rem  the committed batchdata_liens.json cache; only NEW purchases stopped.
rem  --limit 6 -> 60 with --workers 6 --deadline 720 (2026-08-26). The old cap was never about
rem  money (~$0.036 a night); it bounded WALL CLOCK. But --limit caps LEADS, and a lead costs one
rem  or two 2Captcha solves at 60-180s each, so the same --limit is a 6-minute run on a good night
rem  and a 25-minute one on a bad one. It had to be sized for the worst case, which starved every
rem  good night - six leads against a 190-lead backlog is 32 nights.
rem  --deadline bounds the clock directly (12 min, well inside the old worst case), so --limit can
rem  be set to what the BACKLOG needs and the clock decides where the run stops. --workers 6 keeps
rem  6 solves in flight; note this does NOT raise the request rate at the county - the searches are
rem  still strictly serial, only the 2Captcha waiting overlaps. Nothing is lost at the cutoff:
rem  --all skips whatever is already in palmbeach_liens.json, so the next run resumes where this
rem  one stopped. Worst case spend is ~$0.36 on a night that traces the full 60.
rem  THE CLOUD CANNOT DO THIS YET: refresh.yml's PB step skipped in 0s on run #45 because there is
rem  no CAPTCHA_KEY repo secret. Until that secret exists, this laptop line is the ONLY thing
rem  moving Palm Beach coverage.
if exist captcha.key if exist palmbeach_leads.json python -u palmbeach_liens.py --all --limit 60 --workers 6 --deadline 720 >> "%LOG%" 2>&1

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
rem  RAISED 2026-08-27: --max 80 capped to "the 80 SOONEST", so the nightly re-scanned the same
rem  80 leads every night and the other 412 in the window were NEVER checked. The consequence was
rem  not cosmetic: the diligence gate HELD 288 leads out of the call queue for "never had a live
rem  ownership check" — leads with real equity and a phone, held by an unrun check rather than a
rem  defect. The scan is free (county appraiser pages) and runs ~2.5s/lead, so cover the window.
python -u ownership_scan.py --days 45 --max 500 --budget 1500 >> "%LOG%" 2>&1

rem  [moved up to [3/5]] llc_officers now runs BEFORE skip-trace so officer phones can be pulled.

rem  Harvest hard bounces BEFORE the rebuild so dead addresses are excluded at bake time.
rem  This used to be a manual step a human had to remember after every send day; forgetting it
rem  is how the account ran a 24-33% bounce rate for a week (provider tolerance ~2%) — the
rem  bounce ledger only protects the NEXT send if it is refreshed before the queue bakes.
echo [3h/5] Harvesting hard bounces from the inbox...
python -u bounces.py >> "%LOG%" 2>&1

rem  Verify addresses BEFORE they can enter a queue (Cuban re-judge 2026-08-09: reacting at the
rem  5% banner still eats avoidable reputation damage on every fresh scrape). Free layers only
rem  catch syntax/role/learned-dead-domain -- they cannot settle yahoo.com/aol.com (47%/49% dead
rem  in this ledger), which is most of the 32%-first-contact-bounce problem analyst.py measured
rem  2026-08-23. --api spends the already-loaded zerobounce.key at ~$0.008/address; --limit 150
rem  caps it near $1.20/night (worst case) while clearing the ~3,900-address backlog in a few weeks.
rem  provider_check() degrades to free-tier-only on any key/credit/network failure -- see its
rem  docstring -- so a dead key never breaks this step, it just stops buying anything.
echo [3i/5] Verifying fresh addresses before they enter the sendable pool...
python -u verify_emails.py --api --limit 150 >> "%LOG%" 2>&1

rem  ENTITY GATE. Re-verify the company against Sunbiz BEFORE the board is built, so a filing
rem  that has just indexed starts printing its " LLC" on this run instead of the next one --
rem  and so a name that stops being ACTIVE stops being claimed. Exit 1 = "not verified",
rem  which is the guard working, NOT a pipeline failure: never gate the build on it.
echo [3j/5] Verifying the company entity against Sunbiz...
python -u entity_check.py --quiet >> "%LOG%" 2>&1

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

echo [4d/5] Morning worker standup (today's meeting agenda + workable lanes)...
rem  ADDED 2026-08-27: morning_planner.py was called by NOTHING — not this bat, not refresh.yml,
rem  not a scheduled task. analyst.py imports it as a LIBRARY, which is why a grep looked like it
rem  ran. So the morning standup only existed when a human remembered to type the command, and
rem  the one surface built to answer "what do I do today" was the one surface that never appeared
rem  on its own. Same defect class as ownership_scan (called by nothing until 08-19). It reads the
rem  freshly-rebuilt leads above and writes the agenda to the Desktop, so it must run AFTER [4/5].
python -u morning_planner.py >> "%LOG%" 2>&1
if errorlevel 1 echo     ^!^! morning worker failed - see leads-run.log ^(board unaffected^).>> "%LOG%"

echo [4d/5] Team CRM (Desktop CSV always; Google Sheets when sheets_crm_webhook.url exists)...
rem  Reads the Desktop twin's RAW payload, so it MUST run after the [4/5] rebuild above.
python -u sheets_crm.py >> "%LOG%" 2>&1

echo [4e/5] Contact trust — do the traced phones/emails belong to the OWNER?
rem  WHY THIS RUNS EVERY NIGHT (2026-08-23). Twice in one week a stranger was one dial away from
rem  being told his home was in foreclosure. 1400 Saint Charles Pl "#107" does not exist on the
rem  county roll (that building is lettered L1-L8; the real unit is #L7), so the address-keyed
rem  trace returned JOHN CARDENAS of #201 onto a card owned by the ESTATE OF BARBARA COONEY. And
rem  1343 Ponce De Leon carried wamlong@gmail.com, an address that has never existed. Skip-trace
rem  data drifts every night, so this is a nightly check, not a one-time cleanup.
rem
rem  Reads the twin (merged owner + phones) so it MUST follow the [4/5] rebuild, same as sheets_crm.
rem  It stamps contact_trust into skiptrace_results.json, which the NEXT build reads — so a lead
rem  flagged tonight shows its warning on tomorrow's board. That one-day lag is deliberate: the
rem  alternative is re-running the whole build, and nothing here is urgent enough to pay for that.
rem
rem  NON-FATAL BY DESIGN. This is an advisory flag. It must never be the reason a publish does not
rem  happen — the board being live matters more than the annotation being current, and `|| true`
rem  semantics here mirror the auction/CRM steps above.
python -u contact_trust.py --write >> "%LOG%" 2>&1
if errorlevel 1 echo    (contact_trust exited non-zero - advisory only, continuing) >> "%LOG%" 2>&1

:publish
rem  GATE BEFORE PUBLISH (2026-08-19). healthcheck used to run only at :end - AFTER the push - so a
rem  FAIL could never stop a bad board from going live from this machine; and publish_guard (the
rem  corrupt-page + enrichment-regression check that stopped the conflict-marker outage in CI) was
rem  never in this bat at all. Same gates as the cloud workflow now: either one failing skips the
rem  push, the board stays on its last good build, and the run still writes its report.
echo [gate] healthcheck + publish guard before anything goes live...
rem  TIERED GATE (2026-08-20). healthcheck exit 2 = COMPLIANCE/systemic fail (lost §362 stay flags,
rem  or >=2 upstream sources down) -> HARD block. exit 1 = coverage-floor fail only (value/lien %) ->
rem  ADVISORY: a fresh-filing-heavy day dips below the value floor because new MD leads have no folio
rem  to price, and blocking a build publish_guard already proved is RICHER than live just leaves the
rem  board stale. `if errorlevel 2` matches exit>=2, so it must be tested BEFORE `if errorlevel 1`.
python -u healthcheck.py >> "%LOG%" 2>&1
if errorlevel 2 (
  echo     ^!^! GATE: healthcheck COMPLIANCE fail (^&sect;362 stays / sources down) - publish SKIPPED.>> "%LOG%"
  echo     ^!^! GATE: healthcheck COMPLIANCE fail - publish SKIPPED. See leads-run.log.
  goto :end
)
if errorlevel 1 (
  echo     ^!^! GATE: healthcheck coverage below floor - ADVISORY, publish_guard decides.>> "%LOG%"
)
python -u publish_guard.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! GATE: publish_guard BLOCKED the build (regression or corruption) - publish SKIPPED.>> "%LOG%"
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

rem  STANDING BUY-BOXES. Jose asked for "Miami Gardens, 4+ bed / 2+ bath, for my son" and that got
rem  answered ONCE, by hand, as a dated HTML sheet. Two more matching cases were filed inside the
rem  next week and were on nobody's radar, because a hand-built sheet is a photograph, not a filter.
rem  This re-evaluates the criteria every night against the whole board, so a new 4-bedroom in
rem  Miami Gardens surfaces by itself. Edit BOXES in buybox.py to add a box; nothing else changes.
rem
rem  That last sentence was FALSE until 2026-08-31: this line named `--box mg4` explicitly, so a
rem  second box was defined-but-never-scanned, and it failed silently because a box that matches
rem  nothing looks exactly like a box that never ran. No argument now = every box in BOXES, each
rem  written to its own buybox_<key>.json for the morning digest.
echo [buybox] Re-scanning standing acquisition criteria...
python -u buybox.py >> "%LOG%" 2>&1

:end
rem  ROOT CAUSE FOUND 2026-08-31, and it was never a time limit. The task carried
rem  IdleSettings.StopOnIdleEnd = True with RestartOnIdle = False, which tells Task Scheduler to
rem  TERMINATE the task the moment the machine stops being idle — i.e. the moment Alejandro sits
rem  down at it. Hence exit code 255 (terminated), at a time of day that tracked when he woke up
rem  rather than any elapsed duration. The date correlation is exact: done markers land every day
rem  through 08/20 and then stop for ELEVEN CONSECUTIVE DAYS — he was fired 08/19, so before that
rem  he was out driving in the mornings and the box stayed idle to completion. Raising
rem  ExecutionTimeLimit to PT6H never helped because the run was never hitting it (it dies ~2h30m
rem  into a 6h budget). StopOnIdleEnd is now False on this task and on every other DEALFLOW task,
rem  all of which carried the same setting and survived only by being short.
rem
rem  REPORT FIRST, HEALTH SECOND (2026-08-27). Measured on this log: the refresh has STARTED on
rem  time every single morning (5:30:02, seven days straight) and had not written a `done` marker
rem  since 08/20 — it reaches the healthcheck ~1h50m in, then the process ended before the two
rem  steps below it ever ran. Keep this ordering anyway: it is the belt to the idle fix's braces. Consequence: DEALFLOW-STATUS.txt on the Desktop, the ONLY unattended
rem  signal that the night worked, sat frozen at 08-20 for a week while the board published fine
rem  every day. run_report.py itself is healthy (runs clean by hand in seconds), so it was never
rem  the failure — it was just last in line behind the slowest step.
rem  The real work (scrape -> enrich -> build -> publish) is all ABOVE this label and completes,
rem  so moving the report ahead of the healthcheck costs nothing and guarantees the morning signal
rem  survives a truncated tail. The `health:` line it prints comes from the PREVIOUS run's
rem  health.json — one run stale, which is worth far more than no report at all.
echo [report] Writing run status to Desktop + notification...
python -u run_report.py >> "%LOG%" 2>&1

rem  DIGEST SITS HERE FOR THE SAME REASON run_report.py DOES — see the :end note above. It is a
rem  MORNING SIGNAL, so putting it last would put it exactly where the 2h scheduler kill lands and
rem  it would be missing on precisely the mornings something went wrong. Everything it reads
rem  (board, buy-boxes, ledgers, skiptrace) is produced above this line.
rem  It answers a different question from its neighbours and does not replace any of them:
rem    run_report.py      did THIS RUN finish
rem    morning_planner.py what do I knock today
rem    healthcheck.py     is the infrastructure sane
rem    morning_digest.py  what CHANGED since yesterday, across every subsystem
rem  Never raises and always exits 0 by construction, so it cannot mask the health gate below.
echo [digest] Building the morning digest...
python -u morning_digest.py >> "%LOG%" 2>&1

echo ==================== done %date% %time% ====================>> "%LOG%"

echo [health] Checking shipped data + upstream sources...
python -u healthcheck.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! HEALTH: a source is DOWN or the data looks wrong - see leads-run.log.
) else (
  echo     health OK.
)
rem  (report + done marker moved ABOVE the healthcheck — see the note at :end)
echo     health check complete - see leads-run.log.
endlocal
