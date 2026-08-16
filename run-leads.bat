@echo off
cd /d "%~dp0"
echo ==== run %date% %time% ==== >> leads-run.log
python foreclosure_leads.py >> leads-run.log 2>&1
if errorlevel 1 (echo SCRAPE FAILED - skipping commit/push, live site left intact >> leads-run.log & goto :done)
git add -A >> leads-run.log 2>&1
git commit -m "weekly lead refresh" >> leads-run.log 2>&1
rem  PULL BEFORE PUSH. Without this a local push is rejected non-fast-forward the moment
rem  GitHub Actions pushes anything (it publishes the balloon book independently), and the
rem  "retry" below is the SAME push 6s later, which fails identically. Measured 2026-08-16:
rem  4 commits stacked up and the live site sat frozen at 08-14 for two days while every
rem  local run reported success. -X theirs mirrors what .github/workflows/refresh.yml does.
git pull --rebase --autostash -X theirs origin main >> leads-run.log 2>&1
git push origin main >> leads-run.log 2>&1
:done
echo ==== done ==== >> leads-run.log
