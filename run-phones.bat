@echo off
rem One-click phone refresh via BatchData (licensed, ~$0.20/lead). Traces Tier-A owners ->
rem rebuilds the tracker with phones baked in -> pushes to the live site.
rem PREREQ: batchdata.key present (gitignored) AND the BatchData account has a funded balance.
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
