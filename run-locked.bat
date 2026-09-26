@echo off
rem =====================================================================
rem  run-locked.bat <runner.bat> - run ONE publishing .bat under the
rem  CROSS-MACHINE runner lock (runner_lock.py). Point a scheduled task at
rem  this instead of the runner itself, e.g. for the nightly:
rem      run-locked.bat refresh-dealflow.bat
rem  If the other PC holds the lock, the runner never starts (rc=9). If the
rem  lease is lost mid-run (this PC slept past it, or the other PC broke it),
rem  the runner is killed (rc=10) so two machines never publish at once.
rem  Otherwise the runner's own exit code comes back unchanged.
rem  Lock lines (and the runner's console echo) go to runner-lock.log; each
rem  runner still writes its own log exactly as before.
rem  This file never builds, gates or pushes anything itself.
rem =====================================================================
setlocal
cd /d "%~dp0"
if "%~1"=="" goto :usage
if not exist "%~dp0%~1" goto :usage
echo ==== run-locked %~1 %date% %time% ====>> "%~dp0runner-lock.log"
python -u runner_lock.py run --runner "%~1" -- cmd /c "%~dp0%~1" >> "%~dp0runner-lock.log" 2>&1
endlocal & exit /b %errorlevel%

:usage
echo usage: run-locked.bat ^<runner.bat in this folder^>
endlocal & exit /b 2
