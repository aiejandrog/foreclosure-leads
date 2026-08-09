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
python -u replies.py > "%TEMP%\dealflow_replies_last.txt" 2>&1

REM Bake the fresh replies into the board and publish. Rebuild-only (no scrape) — this is
REM the same command the memory file records for time-critical rebuilds, ~2 min total.
python -u -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))" >> "%TEMP%\dealflow_replies_last.txt" 2>&1
if errorlevel 1 (
  echo REBUILD FAILED - board not published, replies still on disk only >> "%TEMP%\dealflow_replies_last.txt"
  goto :end
)
git add docs/index.html >> "%TEMP%\dealflow_replies_last.txt" 2>&1
git commit -m "replies: morning scan baked into board (auto)" >> "%TEMP%\dealflow_replies_last.txt" 2>&1
if not errorlevel 1 (
  git push origin main >> "%TEMP%\dealflow_replies_last.txt" 2>&1
  if errorlevel 1 ( timeout /t 6 /nobreak >nul & git push origin main >> "%TEMP%\dealflow_replies_last.txt" 2>&1 )
)
:end
endlocal
