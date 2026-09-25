@echo off
rem One-click phone refresh (licensed skip-trace). Traces Tier-A owners -> rebuilds the tracker
rem with phones baked in -> pushes to the live site.
rem PREREQ: a provider key present (gitignored) - tracerfy.key (no minimum) or batchdata.key.
rem   skiptrace.py auto-detects whichever key exists (tracerfy preferred).
rem To change how many you spend on: add  --limit N  or  --tier B  after skiptrace.py below.
cd /d "%~dp0"
rem  REPO GUARD FIRST, and before the skip-trace spend. This was the only publish path with no
rem  `call repo_guard.bat` - the other four have had it since 2026-09-17, the day a publish run
rem  from a folder that was not the checkout replaced main on GitHub with one commit. Running it
rem  ahead of skiptrace.py also means a wrong-folder run costs nothing at the provider.
call repo_guard.bat "%~dp0" "phones-run.log"
if errorlevel 1 (echo REPO GUARD refused this checkout - nothing traced, built or pushed. & pause & exit /b 1)
echo ==== phones run %date% %time% ====
rem  PUBLISH LOCK, and ahead of the skip-trace spend for the same reason repo_guard is: a run that
rem  is going to refuse should not pay the provider first. This is the MANUAL twin of
rem  run-phones-nightly.bat and it is the likeliest of the five to be double-clicked while the
rem  nightly chain is still going - the 05:30 refresh runs 3h08m measured. rc=9 = another publishing
rem  runner holds the lock; it exits WITHOUT releasing, because the lock is not ours to drop.
rem  Unlike the four unattended runners these lines go to the CONSOLE, not a log, because this file
rem  is hand-run and the person is looking at the window. NEXIT funnels every exit below here to
rem  :end so the lock is released on failures too, and that is where the single `pause` now lives.
set "NEXIT=0"
python -u publish_lock.py acquire run-phones.bat
if errorlevel 1 (echo PUBLISH LOCK not obtained - reason is in the lines above. Nothing traced, built or pushed. & pause & exit /b 9)
python skiptrace.py
if errorlevel 1 (echo TRACE FAILED - nothing rebuilt or pushed & set "NEXIT=1" & goto :end)
python -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))"
if errorlevel 1 (echo REBUILD FAILED - nothing pushed & set "NEXIT=1" & goto :end)
rem  PUBLISH GATES. CLAUDE.md: every path that pushes docs/ runs healthcheck.py and
rem  publish_guard.py first. This one was the fifth path and had neither, while it does the
rem  most dangerous version of the publish: it rebuilds the board, then rebases onto main with
rem  -X theirs and pushes. That combination is how 09-15 put a 709-phone board over a live
rem  1,148 one from run-replies-daily.bat, and because the bad build became origin/main it also
rem  moved the baseline every later gate compared against. A blocked publish here is correct:
rem  the traced numbers stay in leads_final.json and the next gated run publishes them.
rem  errorlevel 2 is tested first because `if errorlevel N` matches exit^>=N.
python -u healthcheck.py
if errorlevel 2 (echo GATE: healthcheck COMPLIANCE fail - nothing published. Live site left on its last good build. & set "NEXIT=2" & goto :end)
rem  healthcheck exit 1 is the advisory coverage floor only - publish_guard decides, same as refresh-dealflow.bat.
python -u publish_guard.py
if errorlevel 1 (echo GATE: publish_guard refused a board poorer than the live one - nothing published. & set "NEXIT=2" & goto :end)
git add docs/index.html docs/call
git commit -m "phones: refresh skip-traced numbers"
if errorlevel 1 (echo no changes to push - done & goto :end)
rem  PULL BEFORE PUSH. Without this a local push is rejected non-fast-forward the moment
rem  GitHub Actions pushes anything (it publishes the balloon book independently), and the
rem  "retry" below is the SAME push 6s later, which fails identically. Measured 2026-08-16:
rem  4 commits stacked up and the live site sat frozen at 08-14 for two days while every
rem  local run reported success. -X theirs mirrors what .github/workflows/refresh.yml does.
git pull --rebase --autostash -X theirs origin main
rem  %SystemRoot% path on timeout.exe, not a bare `timeout`: under a git-bash PATH the bare
rem  name resolves to GNU coreutils timeout, which rejects /t and drops the retry backoff
rem  entirely. Same root cause as the `find` note in repo_guard.bat.
git push origin main || ("%SystemRoot%\System32\timeout.exe" /t 6 /nobreak >nul & git push origin main)
rem  publish the built pages to the PUBLIC site repo (see publish_site.py)
python -u publish_site.py
echo ==== done - live site updates in ~1-2 min ====

:end
rem  RELEASE THE PUBLISH LOCK, the only place it happens - every exit below the acquire funnels here.
rem  It always exits 0 and only removes a lock this runner owns.
python -u publish_lock.py release run-phones.bat
pause
exit /b %NEXIT%
