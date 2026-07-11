@echo off
cd /d "%~dp0"
echo ==== run %date% %time% ==== >> leads-run.log
python foreclosure_leads.py >> leads-run.log 2>&1
if errorlevel 1 (echo SCRAPE FAILED - skipping commit/push, live site left intact >> leads-run.log & goto :done)
git add -A >> leads-run.log 2>&1
git commit -m "weekly lead refresh" >> leads-run.log 2>&1
git push origin main >> leads-run.log 2>&1
:done
echo ==== done ==== >> leads-run.log
