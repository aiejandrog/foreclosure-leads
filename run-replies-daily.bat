@echo off
REM Daily inbox check. Exists because replies.py once went 3 days unchecked while 40 cold
REM emails were in flight — a reply is the warmest signal the system produces and it must
REM never sit unseen again. Registered as "DealFlow Replies" (daily 7:00 AM).
setlocal
cd /d "%~dp0"
python replies.py > "%TEMP%\dealflow_replies_last.txt" 2>&1
endlocal
