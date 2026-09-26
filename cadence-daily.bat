@echo off
setlocal
rem DEALFLOW email cadence (UNATTENDED). The scheduled task "DealFlow Cadence" runs THIS file.
rem
rem cadence-run.bat is the double-click twin and ends in `pause`. A scheduled task cannot answer a
rem pause -- it would sit at the prompt until ExecutionTimeLimit killed it, with the run already
rem half-done and nothing in a log to say so. Never point the task at that file.
rem
rem This job SENDS REAL EMAIL TO HOMEOWNERS. It publishes nothing, so there are no publish gates
rem here; the gates it does have are the two below plus everything cadence.py enforces itself
rem (opt-out ledger re-read per run, bounced_emails.json hard-suppression, and the shared per-alias
rem warm-up caps metered off mail_sent.json).
cd /d "%~dp0"

rem  1) REPO GUARD. Cadence reads cadence_queue.json, optouts.json and mail_sent.json from the
rem     folder it runs in, and every one of those is gitignored -- so a run from the WRONG checkout
rem     does not fail, it quietly mails an old queue against a stale opt-out ledger. That is the
rem     2026-09-17 wrong-folder failure with outreach on the end of it instead of a publish.
rem     LOG comes first because repo_guard.bat writes its refusal there.
if not exist "%USERPROFILE%\DEALFLOW" mkdir "%USERPROFILE%\DEALFLOW"
rem     The log carries homeowner email addresses, so it lives in ~\DEALFLOW -- outside the repo
rem     and outside OneDrive -- for the same reason paths.py exists. Never move it under %~dp0.
set "LOG=%USERPROFILE%\DEALFLOW\cadence-run.log"
set "STATUS=%USERPROFILE%\DEALFLOW\DEALFLOW-CADENCE-STATUS.txt"
set "STAMP=%date% %time%"
echo ==== cadence-daily %STAMP% ==== >> "%LOG%"

call repo_guard.bat "%~dp0" "%LOG%"
if errorlevel 1 (
  echo [%STAMP%] BLOCKED - repo guard refused this folder. Nothing sent.> "%STATUS%"
  exit /b 1
)

rem  2) THE HOUR WINDOW. Alejandro's standing outreach rule is 8am to 8pm, and the task is set
rem     StartWhenAvailable=true so a run missed while the laptop slept is caught up rather than
rem     lost. Those two together are what needs the guard: without it, a machine that wakes at
rem     22:40 sends a batch of homeowner follow-ups at 22:40. The window is checked HERE and not in
rem     cadence.py deliberately -- cadence.py is the CLAUDE.md-reserved suppression surface, and a
rem     scheduling decision does not belong inside the send engine anyway.
rem     Undefined HOUR fails CLOSED: if we cannot read the clock we do not mail anyone.
set "HOUR="
for /f %%h in ('python -c "import datetime;print(datetime.datetime.now().hour)" 2^>nul') do set "HOUR=%%h"
if not defined HOUR (
  echo [%STAMP%] SKIPPED - could not read the local hour. Nothing sent.> "%STATUS%"
  echo SKIPPED - could not read the local hour, no mail sent. >> "%LOG%"
  exit /b 0
)
if %HOUR% LSS 8 goto :outside
if %HOUR% GEQ 20 goto :outside
goto :send

:outside
echo [%STAMP%] SKIPPED - %HOUR%:00 is outside the 8am-8pm window. Steps stay due for tomorrow.> "%STATUS%"
echo SKIPPED - hour %HOUR% outside the 08-20 outreach window, no mail sent. >> "%LOG%"
echo ==== done - skipped %date% %time% ==== >> "%LOG%"
exit /b 0

:send
rem  07:15 OPT-OUT SYNC GATE (2026-09-26). cadence.py mails homeowners and has no ledger-freshness
rem  check of its own. sync_gate.py exits 3 unless TODAY's opt-out sync (run-optout-sync.bat) finished
rem  with every step OK - the same rule send_server /send enforces. Held = nothing sent, steps stay due.
python -u sync_gate.py >> "%LOG%" 2>&1
if errorlevel 1 goto :held
rem  -u so the log is written as the run goes, not flushed at exit. A cadence run that dies halfway
rem  has already mailed people, and the log is the only record of who.
python -u cadence.py >> "%LOG%" 2>&1
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo [%STAMP%] FAILED - cadence.py exit %RC%. See cadence-run.log.> "%STATUS%"
  echo ==== done - FAILED exit %RC% %date% %time% ==== >> "%LOG%"
  exit /b %RC%
)
echo [%STAMP%] OK - cadence ran. See cadence-run.log for the per-step detail.> "%STATUS%"
echo ==== done %date% %time% ==== >> "%LOG%"
exit /b 0

:held
echo [%STAMP%] HELD - today's 07:15 opt-out sync has not finished OK. Nothing sent; steps stay due. Run run-optout-sync.bat, then this.> "%STATUS%"
echo HELD - opt-out sync not confirmed today, no mail sent. >> "%LOG%"
echo ==== done - held %date% %time% ==== >> "%LOG%"
exit /b 3
