@echo off
rem =====================================================================
rem  publish_verify.bat - did the commit we just made ACTUALLY land on
rem  origin/main?  Call this immediately after a push block.
rem
rem  WHY THIS EXISTS
rem  Every runner ends the same way: pull --rebase, push, retry once 6s
rem  later, then echo "published, live in ~1-2 min" with NOTHING checking
rem  whether either push returned 0. The exit code of the last push is
rem  simply discarded.
rem
rem  That has now cost two multi-day blackouts, a month apart, with the
rem  same signature both times:
rem    2026-08-16  4 commits stacked, live site frozen at 08-14 for two
rem                days "while every local run still reported success"
rem                (the comment above each push block says so).
rem    2026-09-14  6 commits stacked - 09-14/09-16/09-17 phones+replies -
rem                and did not reach origin until 09-17 16:42. Their
rem                author dates are the nightly trigger times; their
rem                COMMITTER dates are all 16:42 on the 17th, which is
rem                the rebase that finally landed them. For three days
rem                DEALFLOW-PHONES-STATUS.txt said "OK - board rebuilt +
rem                published. Live in ~1-2 min."
rem
rem  August's fix was to add the pull before the push - it addressed the
rem  CAUSE of that outage and left the REPORTING untouched, so the second
rem  outage was just as invisible as the first. This file is the missing
rem  half: the runner is no longer allowed to claim a publish it cannot
rem  prove.
rem
rem  WHAT IT CHECKS, and why it is not the push exit code
rem  A push can exit 0 and still not be what the site serves - another
rem  machine can push over it seconds later, and a rebase can drop the
rem  commit entirely. The only honest question is "is HEAD an ancestor of
rem  origin/main RIGHT NOW", so that is what this asks the remote.
rem
rem  Usage:  call publish_verify.bat "<logfile>" "<statusfile>|-" "<label>"
rem  Exit:   0 = confirmed on origin/main   1 = NOT published
rem =====================================================================
setlocal
set "VLOG=%~1"
set "VSTATUS=%~2"
set "VLABEL=%~3"
if "%VLOG%"=="" set "VLOG=publish-verify.log"

git fetch origin main --quiet >> "%VLOG%" 2>&1
if errorlevel 1 goto :vfail
git merge-base --is-ancestor HEAD FETCH_HEAD >> "%VLOG%" 2>&1
if errorlevel 1 goto :vfail

rem  the trailing period is load-bearing: a label ending in a digit followed directly by
rem  ">" is parsed by cmd as a file-handle redirect, not as text. run-phones-nightly.bat
rem  passes "...skiptrace exit 3, see phones-run.log" through here.
rem  IT VERIFIES THIS REPO, NOT THE LIVE SITE (2026-09-18). Since the 09-17 split the live board is
rem  a SEPARATE public repo (dealflow-board), mirrored by publish_site.py. This file only asks
rem  whether HEAD reached origin/main HERE, so the old wording - "published to the live site",
rem  "live site updates in ~1-2 min" - was the very kind of unearned claim this file exists to
rem  remove, one repo further out. It was true until 09-17 and false every run after. The caller
rem  reports the mirror separately; this says only what it actually checked.
echo     publish CONFIRMED on origin/main (engine repo) - %VLABEL%.>> "%VLOG%"
if not "%VSTATUS%"=="-" echo [%date% %time%] OK - board pushed to the engine repo. Live site depends on the mirror step. %VLABEL%.> "%VSTATUS%"
echo     Pushed to the engine repo - the live site follows only if the mirror step above published.
endlocal & exit /b 0

:vfail
rem  Deliberately loud, and deliberately NOT self-healing. Something is
rem  wrong with the push (no network, credentials, a rebase left mid-flight,
rem  or another machine is fighting this one for main) and retrying it here
rem  would only hide that for another day.
echo.>> "%VLOG%"
echo     ^!^! PUBLISH DID NOT LAND. HEAD is not on origin/main. - %VLABEL%.>> "%VLOG%"
echo     ^!^! The board was rebuilt locally but the LIVE SITE IS UNCHANGED.>> "%VLOG%"
echo     ^!^! Commits are stacking up on this machine. Run `git log origin/main..HEAD`>> "%VLOG%"
echo     ^!^! to see how many, and `git status` to check for a rebase left in progress.>> "%VLOG%"
if not "%VSTATUS%"=="-" echo [%date% %time%] ^!^! NOT PUBLISHED - built locally, push did NOT land. LIVE SITE IS STALE. See the run log.> "%VSTATUS%"
echo     ^!^! NOT PUBLISHED - the live site is unchanged. See the run log.
endlocal & exit /b 1
