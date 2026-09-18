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

rem  REPO GUARD FIRST. A publish job is the most destructive command in this project and
rem  until 2026-09-17 none of them checked what they were about to push. See repo_guard.bat.
call repo_guard.bat "%~dp0" "%LOG%"
if errorlevel 1 exit /b 1
set "STATUS=%USERPROFILE%\DEALFLOW\DEALFLOW-PHONES-STATUS.txt"
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
rem 3-DAY lane first (three_day.py): phones for sales within 3 business days, then the morning list.
python three_day.py --trace --max-spend 2 >> "%LOG%" 2>&1

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

rem 3b) THE GATES. This job published with none of them until 2026-09-17, and it is the job that
rem     runs most reliably -- so it was the easiest way to put a bad board on the live site.
rem
rem     Measured: on 09-15 at 19:11 the reply bake published a 709-phone board over the live
rem     1,148-phone one, and ONE MINUTE LATER this job published 714 over the same board. Both
rem     drops clear publish_guard's phones rule (709 and 714 are both under 1,148 x 0.85, and the
rem     ~435 lost numbers are far over its floor of 25), so the guard would have blocked both --
rem     neither job was asking it. Gating only the reply bake leaves this one-minute-later path
rem     open, which is the whole reason this block is here and not just there.
rem
rem     The healthcheck check matters more than it looks. refresh-dealflow.bat treats exit 2
rem     (lost 362 bankruptcy-stay flags / 2+ upstream sources down) as a HARD publish block and
rem     stops. But it leaves the rebuilt board sitting on disk, and this job rebuilds from the
rem     same leads_final.json 30 minutes later -- so an ungated publish here re-publishes the
rem     exact board the compliance gate just refused. A gate one job can walk around is not a gate.
python -u healthcheck.py >> "%LOG%" 2>&1
if errorlevel 2 (
  echo [%STAMP%] BLOCKED - healthcheck COMPLIANCE fail. Board NOT published; live site left on its last good build. See phones-run.log.> "%STATUS%"
  echo GATE: healthcheck COMPLIANCE fail - publish skipped. >> "%LOG%"
  exit /b 2
)
rem  exit 1 is the coverage floor only: advisory, publish_guard decides -- same as refresh-dealflow.bat.
python -u publish_guard.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%STAMP%] BLOCKED - publish_guard refused a board poorer than the live one. Live site unchanged. See phones-run.log.> "%STATUS%"
  echo GATE: publish_guard BLOCKED the build - publish skipped. >> "%LOG%"
  exit /b 2
)

rem 4) publish. ONLY the encrypted board + public Sunbiz officers -- NEVER `git add -A`
rem    (skiptrace_results.json / leads_final.json are gitignored PII and must stay off the public repo).
rem    llc_officers.json is gitignored too, so this add reports it and stages the rest; harmless,
rem    but it means the officers file never travels between machines -- each one builds its own.
git add docs/index.html docs/call llc_officers.json
git commit -m "phones: nightly refresh (%PHONESNOTE%)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%STAMP%] OK - board already current, nothing to push. %PHONESNOTE%.> "%STATUS%"
  echo no changes to commit >> "%LOG%"
  exit /b 0
)
rem  PULL BEFORE PUSH. Without this a local push is rejected non-fast-forward the moment
rem  GitHub Actions pushes anything (it publishes the balloon book independently), and the
rem  "retry" below is the SAME push 6s later, which fails identically. Measured 2026-08-16:
rem  4 commits stacked up and the live site sat frozen at 08-14 for two days while every
rem  local run reported success. -X theirs mirrors what .github/workflows/refresh.yml does.
git pull --rebase --autostash -X theirs origin main >> "%LOG%" 2>&1
git push origin main >> "%LOG%" 2>&1 || (timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1)
rem  THE LIVE SITE IS A SEPARATE PUBLIC REPO (2026-09-17). This repo is private now, so the
rem  lead data and history are no longer world-readable; docs/ still commits here (it is
rem  publish_guard's baseline) and publish_site.py mirrors the pages Pages actually serves.
python -u publish_site.py >> "%LOG%" 2>&1

rem The status file must state the phones outcome honestly. It previously always said "phones
rem refreshed", which would now be a lie on any degraded run.
rem
rem It also always said "published", which was a lie for three days straight (09-14 to 09-17):
rem the push was failing, the commits stacked up locally, and this line still wrote "OK - board
rem rebuilt + published. Live in ~1-2 min." every morning. Neither push's exit code was ever
rem read. publish_verify.bat asks the remote whether HEAD actually landed and writes the status
rem file from the answer, so this can no longer claim a publish that did not happen.
call publish_verify.bat "%LOG%" "%STATUS%" "%PHONESNOTE%"
if errorlevel 1 (
  echo ==== done - NOT PUBLISHED %date% %time% ==== >> "%LOG%"
  exit /b 1
)
echo ==== done %date% %time% ==== >> "%LOG%"
exit /b 0
