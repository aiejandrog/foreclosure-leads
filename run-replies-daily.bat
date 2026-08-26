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
git add docs/index.html docs/call >> "%LOG%" 2>&1
git commit -m "replies: morning scan baked into board (auto)" >> "%LOG%" 2>&1
if not errorlevel 1 (
  git pull --rebase --autostash -X theirs origin main >> "%LOG%" 2>&1
  git push origin main >> "%LOG%" 2>&1
  if errorlevel 1 ( timeout /t 6 /nobreak >nul & git push origin main >> "%LOG%" 2>&1 )
)
:end
endlocal
