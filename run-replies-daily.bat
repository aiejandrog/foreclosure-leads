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
rem  MIRRORFAIL carries the site-mirror outcome to this file's exit code at :end.
set "MIRRORFAIL=0"
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

REM  PUBLISH LOCK, and this file is the reason the lock exists. Five .bat files rebuild docs/ and
REM  push it, and until 2026-09-22 the only thing keeping two of them apart was the clock on their
REM  triggers. On 2026-09-15 at 19:11 this file published 709 phones over a live 1,148 and
REM  run-phones-nightly.bat published 714 over the same board a minute later; the poorer build became
REM  origin/main, which moved the baseline every later publish_guard compared against. Refresh runs
REM  05:30 and has measured 2h11m, 2h49m, 3h08m and ~4h on different days, so "it will be done by
REM  08:30" is an assumption, not a mechanism. This is the mechanism.
REM
REM  IT SITS HERE AND NOT AT THE TOP OF THE FILE, and that placement is the whole design. Everything
REM  above this line - the inbox scan and optout_sync.py carrying detected STOPs into optouts.json -
REM  is the time-critical work this file exists for and it contends with nothing: it reads a mailbox
REM  and appends to a ledger that is add-only. Only the REBUILD AND PUSH below races another runner.
REM  A guard at the top would have made a locked morning a morning with no reply scan and no opt-out
REM  sync at all, which is a worse failure than the race it prevents - the STOP has to reach the
REM  ledger before anything sends at 09:00.
REM
REM  So a held lock is a DEGRADED run, not a dead one: replies are scanned, opt-outs are synced, and
REM  only the board publish is skipped - which is what a blocked gate already does here, and what
REM  CLAUDE.md names as correct. rc=9 says so. It jumps to :nolock, NOT :end, because :end releases
REM  the lock and this run never held it.
python -u publish_lock.py acquire run-replies-daily.bat >> "%LOG%" 2>&1
if errorlevel 1 (
  echo     ^!^! PUBLISH LOCK not obtained - reason is in the lines above. Board NOT rebuilt or pushed. >> "%LOG%"
  echo     ^!^! Replies were scanned and opt-outs synced above, so nothing warm was lost. >> "%LOG%"
  echo     ^!^! PUBLISH LOCK not obtained - replies saved, publish skipped.
  goto :nolock
)

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
  rem  %SystemRoot% path on timeout.exe, not a bare `timeout`: under a git-bash PATH the bare
  rem  name resolves to GNU coreutils timeout, which rejects /t and drops the retry backoff
  rem  entirely. Same root cause as the `find` note in repo_guard.bat.
  git push origin main >> "%LOG%" 2>&1
  if errorlevel 1 ( "%SystemRoot%\System32\timeout.exe" /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1 )
  rem  mirror the rebuilt board to the PUBLIC site repo (see publish_site.py)
  python -u publish_site.py >> "%LOG%" 2>&1
  rem  READ ITS EXIT CODE (2026-09-18). publish_site.py exits 1 when it cannot find the site clone,
  rem  and every caller discarded that - so between the 09-17 repo split and 09-18 the live site sat
  rem  on one build while three newer boards were published to this repo and every run said success.
  if errorlevel 1 (
    echo     ^!^! MIRROR DID NOT PUBLISH - board committed here, LIVE SITE UNCHANGED.>> "%LOG%"
    echo     ^!^! The live board is the dealflow-board repo, not this one. See publish_site above.>> "%LOG%"
    echo     ^!^! MIRROR DID NOT PUBLISH - the live site is unchanged. See the run log.
    set "MIRRORFAIL=1"
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
rem  RELEASE THE PUBLISH LOCK. Every path out of this file below the acquire reaches :end, which is
rem  what makes one release enough - a lock released on the happy path only wedges the machine on
rem  the first rebuild failure. It always exits 0 and only removes a lock this runner owns, so it
rem  can neither change the code below nor drop another runner's lock.
python -u publish_lock.py release run-replies-daily.bat >> "%LOG%" 2>&1
rem  ...and carry it out. This file ended at `endlocal` with no exit code, so a run whose mirror
rem  never published - and whose live site therefore did not move - returned 0 like any other.
rem  rc=5 = the reply was saved, the board was gated and pushed here, the live site is unchanged.
rem  `endlocal & exit /b` on one line: both halves are parsed before endlocal discards the variable.
if "%MIRRORFAIL%"=="1" (endlocal & exit /b 5)
endlocal & exit /b 0

rem  BELOW THE FINAL EXIT ON PURPOSE - control must not fall into it. rc=9 = the publish lock was not
rem  obtained, held or unusable, so the board was not rebuilt or pushed. NO RELEASE here: the lock is the
rem  other runner's and dropping it would be worse than the race it was stopping. The scan and the
rem  opt-out sync above this ran normally, which is why this is a degraded morning and not a failed
rem  one - but it is still not rc=0, because the live board did not move.
:nolock
echo ==== done - DEGRADED, publish lock held, board not published %date% %time% ==== >> "%LOG%"
endlocal & exit /b 9
