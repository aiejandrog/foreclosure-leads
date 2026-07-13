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
git add docs/index.html
git commit -m "phones: refresh skip-traced numbers"
if errorlevel 1 (echo no changes to push - done & pause & exit /b 0)
git push origin main || (timeout /t 6 /nobreak >nul & git push origin main)
echo ==== done - live site updates in ~1-2 min ====
pause
