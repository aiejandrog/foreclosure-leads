@echo off
setlocal
rem DEALFLOW nightly phones job (lean, UNATTENDED). No `pause` anywhere -- a scheduled task can't answer one.
rem Flow: llc_officers (free Sunbiz humans) -> skiptrace (hardened, fails loud) -> rebuild -> commit -> push.
rem If skip-trace fails the run DEGRADES, it does not stop: the board still rebuilds and publishes with
rem the phone data already cached. Only a REBUILD failure blocks the push. (Changed 2026-08-07 -- see
rem the note at the skiptrace step for why the old stop-everything behavior was wrong.)
rem   skiptrace exit codes: 0 ok | 2 key/balance dead | 3 provider down | 4 over --max-spend
rem                         | 5 shared daily budget spent mid-run (benign, rest resume tomorrow).
rem Night one clears the backlog (~$40); every night after only pays for NEW leads (cache dedupes the rest).
cd /d "%~dp0"
set "LOG=%~dp0phones-run.log"
set "STATUS=%USERPROFILE%\OneDrive\Desktop\DEALFLOW-PHONES-STATUS.txt"
set "STAMP=%date% %time%"

echo ==== phones-nightly %STAMP% ==== >> "%LOG%"

rem 1) free Sunbiz officer names so LLC-owned leads have a human to trace (skiptrace reads llc_officers.json)
rem    FIXED 2026-08-05: this called `--all`, a flag llc_officers.py has never accepted (its real args are
rem    --limit/--refresh/--case). It errored out silently every single night since this file was written --
rem    the .bat doesn't gate on its exit code, so the run just continued with STALE officer data. --limit 0
rem    is the script's actual "no cap" flag.
python llc_officers.py --limit 0 >> "%LOG%" 2>&1

rem 2) the hardened trace. The REAL ceiling is the shared daily budget in bd_budget.py (one wallet,
rem    every script, every scheduler) -- see `python bd_budget.py` to view or `--cap N` to change.
rem    --max-spend is a second belt on top of it, scoped to this one run.
rem    FIXED 2026-08-05: `--max-spend 1` with NO `--limit` means skiptrace prices the FULL eligible
rem    backlog and aborts entirely (exit 4, zero leads traced) the moment that total exceeds $1 -- it
rem    does not trace a partial, affordable slice. With a 159-lead backlog costing ~$23.85, that is
rem    every night forever: confirmed stuck for 2+ straight days (phones-run.log, Mon+Tue), zero
rem    progress, zero rebuild, zero push. `--limit 6` caps the ask itself to what $1/day actually buys
rem    ($0.15/lookup), so it makes real progress nightly instead of refusing outright. Backlog clears in
rem    ~26 nights at this rate -- raise --limit (and the bd_budget.py cap that gates it) if that's too slow.
python skiptrace.py --all --limit 6 --max-spend 1 >> "%LOG%" 2>&1
set "RC=%errorlevel%"

rem    FIXED 2026-08-07: a skiptrace failure used to `exit /b` here, so the board was never rebuilt
rem    or pushed. That froze the ENTIRE refresh on an unrelated problem: on 08/07 BatchData returned
rem    "403 Insufficient balance", skiptrace correctly stopped without spending a cent, and the board
rem    then went stale -- no new auction dates, no new county leads, nothing -- purely because ONE
rem    enrichment step could not run.
rem    The original guard ("never overwrite a good board with a phone-poor one") does not actually
rem    apply to exits 2/3/4: in every one of those cases skiptrace traced NOTHING and wrote NOTHING,
rem    so skiptrace_results.json is untouched and a rebuild reproduces the exact same phone data it
rem    had before, plus whatever fresh county/auction data arrived. Degrade, don't freeze.
rem    A rebuild failure (below) still blocks the push -- that guard is the one that matters.
rem    NOTE: no parentheses inside these set values -- an unescaped ) closes an if-block in batch.
set "PHONESNOTE=phones refreshed"
if not "%RC%"=="0" (
  echo PHONES DEGRADED - skiptrace exit %RC% [2=key/balance 3=provider-down 4=over-budget 5=daily-budget-spent]. No new phones this run; rebuilding + pushing anyway with existing data. >> "%LOG%"
  set "PHONESNOTE=phones NOT refreshed - skiptrace exit %RC%, see phones-run.log"
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
git commit -m "phones: nightly refresh (%PHONESNOTE%)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%STAMP%] OK - board already current, nothing to push. %PHONESNOTE%.> "%STATUS%"
  echo no changes to commit >> "%LOG%"
  exit /b 0
)
git push origin main >> "%LOG%" 2>&1 || (timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1)

rem The status file must state the phones outcome honestly. It previously always said "phones
rem refreshed", which would now be a lie on any degraded run.
echo [%STAMP%] OK - board rebuilt + published (%PHONESNOTE%). Live in ~1-2 min.> "%STATUS%"
echo ==== done %date% %time% ==== >> "%LOG%"
exit /b 0
