@echo off
REM Daily inbox check. Exists because replies.py once went 3 days unchecked while 40 cold
REM emails were in flight — a reply is the warmest signal the system produces and it must
REM never sit unseen again. Registered as "DealFlow Replies" (daily 7:00 AM).
REM
REM 2026-08-09: the scan now REBUILDS AND PUBLISHES the board instead of printing the
REM rebuild command for a human to run. Before this, a 7:00 reply did not reach the live
REM site until the 9:00 refresh pushed at ~9:12 — an hour AFTER the 8:00 Morning Worker
REM auto-ran on YESTERDAY'S build. Speed-to-lead was 24-48h by scheduling accident.
REM The worker session that opens at 8:00 must contain the replies found at 7:00.
setlocal
cd /d "%~dp0"
REM Durable log in the repo (gitignored), matching phones-run.log / daily-routes-run.log. The old
REM %TEMP%\dealflow_replies_last.txt evaporated with Windows temp cleanup — verified 2026-08-26:
REM the 08-25 run exited 0 and its output file already no longer existed anywhere, so a bad morning
REM would have been undiagnosable by afternoon.
set "LOG=%~dp0replies-run.log"
echo ==== replies-daily %date% %time% ==== >> "%LOG%"

REM  REPO GUARD FIRST (2026-09-18). This was the LAST publish path with no guard. It is also the
REM  fastest route from a local build to the live site - replies.py runs, the board is rebuilt, and
REM  docs/index.html + docs/call are pushed, all inside a couple of minutes. On 2026-09-17 a publish
REM  from a directory that was NOT this checkout replaced origin/main with one docs-only commit and
REM  wiped 318 modules off the remote; the offending tree was a valid work tree with a clean index
REM  and the correct origin URL, so git itself had nothing to object to. repo_guard.bat checks the
REM  four things git cannot: the pipeline's own files are here, this is a work tree, the history is
REM  deeper than a stub, and origin is this project. It only ever refuses.
call repo_guard.bat "%~dp0" "%LOG%"
if errorlevel 1 exit /b 1
python -u replies.py >> "%LOG%" 2>&1

REM DETECTION IS NOT SUPPRESSION. replies.py only writes stop:true into replies.json; optout_sync.py
REM is what carries that into optouts.json, the ledger the board and the send path actually consult.
REM
REM Added here 2026-08-22. optout_sync.py existed since 08-13 but was invoked from exactly ONE place:
REM .github/workflows/refresh.yml. That run cannot persist anything — optouts.json is gitignored AND
REM absent from the workflow's actions/cache path list, so CI re-derives the ledger every morning into
REM a file that dies with the runner. No local runner called it at all. Net effect: optouts.json had
REM not changed since 2026-08-13, the day the script was written, and two STOPs sat detected-but-armed
REM (@lilmamabain1@aol.com and CACE-19-009401 — the same person, both keys, "Re: Regarding your
REM property at 2003 SW 86 AVE"). That is the exact gil_sosa pattern the script was written to end.
REM
REM Runs on whichever machine is armed, right after the scan that produces its input, and BEFORE the
REM rebuild below so a fresh opt-out reaches the board in the same pass. It only ever ADDS and
REM re-running is a no-op, so a failure here must not stop the publish.
python -u optout_sync.py >> "%LOG%" 2>&1

REM Bake the fresh replies into the board and publish. Rebuild-only (no scrape) — this is
REM the same command the memory file records for time-critical rebuilds, ~2 min total.
python -u -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo REBUILD FAILED - board not published, replies still on disk only >> "%LOG%"
  goto :end
)
REM ===================== GATE BEFORE PUBLISH (added 2026-09-17) =====================
REM This path published the live board with NO GATES AT ALL. refresh-dealflow.bat has run
REM healthcheck + publish_guard before its push since 2026-08-19; run-leads.bat runs
REM publish_guard. This one rebuilt docs/index.html and docs/call and pushed them straight to
REM the site, every morning at 7:00, with nothing between the build and the world.
REM
REM IT FIRED. Commit 2d6f36d, 2026-09-15 19:11, "replies: morning scan baked into board (auto)":
REM 2,266 leads / 709 phones published over a live board carrying 2,297 / 1,148. publish_guard's
REM phones rule is (ratio 0.85, floor 25) -- 709 is below 1,148 x 0.85 = 976 and the drop is 439,
REM so BOTH conditions were met and it would have blocked. It was never invoked. 439 dialable
REM numbers came off the live site and off both call pages, and the log said success.
REM
REM Same tiered gate as refresh-dealflow.bat, deliberately identical down to the ordering:
REM   healthcheck exit 2 = COMPLIANCE/systemic (lost 362 stay flags, >=2 sources down) -> HARD stop
REM   healthcheck exit 1 = coverage floor only -> ADVISORY, publish_guard decides
REM   publish_guard  exit 2 = corrupt page, or materially poorer than live -> HARD stop
REM `if errorlevel 2` matches exit>=2, so it MUST be tested before `if errorlevel 1`.
REM
REM A BLOCKED PUBLISH IS NOT A LOST REPLY. replies.py and optout_sync.py have already run above,
REM so the reply is on disk and the opt-out is in the ledger before this gate is reached. The only
REM thing skipped is overwriting a good live board with a worse one -- which CLAUDE.md names as
REM correct behaviour, not a bug to route around.
echo [gate] healthcheck + publish guard before anything goes live... >> "%LOG%"
python -u healthcheck.py >> "%LOG%" 2>&1
if errorlevel 2 (
  echo     !! GATE: healthcheck COMPLIANCE fail - publish SKIPPED, replies still saved. >> "%LOG%"
  goto :end
)
if errorlevel 1 (
  echo     !! GATE: healthcheck coverage below floor - ADVISORY, publish_guard decides. >> "%LOG%"
)
python -u publish_guard.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     !! GATE: publish_guard BLOCKED the build - publish SKIPPED, replies still saved. >> "%LOG%"
  goto :end
)
REM ==================================================================================

git add docs/index.html docs/call >> "%LOG%" 2>&1
git commit -m "replies: morning scan baked into board (auto)" >> "%LOG%" 2>&1
if not errorlevel 1 (
  git pull --rebase --autostash -X theirs origin main >> "%LOG%" 2>&1
  git push origin main >> "%LOG%" 2>&1
  if errorlevel 1 ( timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1 )
  rem  mirror the rebuilt board to the PUBLIC site repo (see publish_site.py)
  python -u publish_site.py >> "%LOG%" 2>&1
  rem  READ ITS EXIT CODE (2026-09-18). publish_site.py exits 1 when it cannot find the site clone,
  rem  and every caller discarded that - so between the 09-17 repo split and 09-18 the live site sat
  rem  on one build while three newer boards were published to this repo and every run said success.
  if errorlevel 1 (
    echo     ^!^! MIRROR DID NOT PUBLISH - board committed here, LIVE SITE UNCHANGED.>> "%LOG%"
    echo     ^!^! The live board is the dealflow-board repo, not this one. See publish_site above.>> "%LOG%"
    echo     ^!^! MIRROR DID NOT PUBLISH - the live site is unchanged. See the run log.
  )
  rem  ...and then ASK THE REMOTE whether that push landed, instead of ending the run silently.
  rem  The blind `timeout 6 & push again` above is the same push six seconds later: when the first
  rem  one failed for a reason six seconds does not fix, the second fails identically and nothing
  rem  here notices. That is how six commits - 09-14/09-16/09-17 phones and replies - sat unpushed
  rem  until 09-17 16:42 while every run reported a good morning. publish_verify.bat asks whether
  rem  HEAD is an ancestor of origin/main right now, which is the only honest form of the question.
  call publish_verify.bat "%LOG%" "-" "replies: morning scan baked into board"
)
:end
endlocal
