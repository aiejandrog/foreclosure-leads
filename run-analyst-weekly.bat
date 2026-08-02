@echo off
REM Weekly business analyst: fresh inbox check, then the judge.
REM Registered as "DealFlow Weekly Analyst" (Sundays 7:30 AM) by setup-analyst-automation.ps1.
setlocal
cd /d "%~dp0"
echo [%date% %time%] weekly analyst starting > "%USERPROFILE%\OneDrive\Desktop\DEALFLOW-ANALYST-STATUS.txt"
python replies.py >> "%USERPROFILE%\OneDrive\Desktop\DEALFLOW-ANALYST-STATUS.txt" 2>&1
python analyst.py >> "%USERPROFILE%\OneDrive\Desktop\DEALFLOW-ANALYST-STATUS.txt" 2>&1
if errorlevel 1 (
  echo [%date% %time%] ANALYST FAILED - run "python analyst.py" by hand to see why >> "%USERPROFILE%\OneDrive\Desktop\DEALFLOW-ANALYST-STATUS.txt"
  exit /b 1
)
echo [%date% %time%] done >> "%USERPROFILE%\OneDrive\Desktop\DEALFLOW-ANALYST-STATUS.txt"
endlocal
