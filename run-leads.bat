@echo off
cd /d "%~dp0"
echo ==== run %date% %time% ==== >> leads-run.log
rem  PULL THE CODE BEFORE BUILDING WITH IT (2026-09-10). Same fix, same reason as
rem  refresh-dealflow.bat: the only pull here was the one before the push at the bottom, so a run
rem  built yesterday's code, committed the result, and then rebased the new commits into history -
rem  leaving the repo looking current while the published pages were stale, with no error anywhere.
rem  --ff-only, never `--autostash -X theirs`: that is the combination whose stash reapply wrote
rem  conflict markers into docs/index.html on 2026-08-19. Non-fatal - if it cannot fast-forward,
rem  build with the code on disk.
git pull --ff-only origin main >> leads-run.log 2>&1
if errorlevel 1 (echo note: code pull skipped - building with the code already on disk >> leads-run.log)
python foreclosure_leads.py >> leads-run.log 2>&1
if errorlevel 1 (echo SCRAPE FAILED - skipping commit/push, live site left intact >> leads-run.log & goto :done)
rem  2026-08-19 stress-test CRITICAL: this was `git add -A`, which stages EVERYTHING not gitignored -
rem  and the client deliverables at repo root (BSG_Call_List = 305 phones, Acosta_Position_Report,
rem  workups) were NOT gitignored, so one run of this bat would have published families' phone
rem  numbers to a PUBLIC repo. Now: rebuild, gate, and add ONLY the two built site paths - never -A.
rem  Same contract as refresh-dealflow.bat.
python -c "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))" >> leads-run.log 2>&1
python publish_guard.py >> leads-run.log 2>&1
if errorlevel 1 (echo PUBLISH GUARD BLOCKED - live site left intact >> leads-run.log & goto :done)
git add docs/index.html docs/call >> leads-run.log 2>&1
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
