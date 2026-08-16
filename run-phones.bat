@echo off
rem One-click phone refresh (licensed skip-trace). Traces Tier-A owners -> rebuilds the tracker
rem with phones baked in -> pushes to the live site.
rem PREREQ: a provider key present (gitignored) - tracerfy.key (no minimum) or batchdata.key.
rem   skiptrace.py auto-detects whichever key exists (tracerfy preferred).
rem To change how many you spend on: add  --limit N  or  --tier B  after skiptrace.py below.
cd /d "%~dp0"
echo ==== phones run %date% %time% ====
python skiptrace.py
if errorlevel 1 (echo TRACE FAILED - nothing rebuilt or pushed & pause & exit /b 1)
python -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))"
if errorlevel 1 (echo REBUILD FAILED - nothing pushed & pause & exit /b 1)
git add docs/index.html docs/call
git commit -m "phones: refresh skip-traced numbers"
if errorlevel 1 (echo no changes to push - done & pause & exit /b 0)
rem  PULL BEFORE PUSH. Without this a local push is rejected non-fast-forward the moment
rem  GitHub Actions pushes anything (it publishes the balloon book independently), and the
rem  "retry" below is the SAME push 6s later, which fails identically. Measured 2026-08-16:
rem  4 commits stacked up and the live site sat frozen at 08-14 for two days while every
rem  local run reported success. -X theirs mirrors what .github/workflows/refresh.yml does.
git pull --rebase --autostash -X theirs origin main
git push origin main || (timeout /t 6 /nobreak >nul & git push origin main)
echo ==== done - live site updates in ~1-2 min ====
pause
