@echo off
rem kimi: email cadence engine — sends due steps, auto-cancels on reply, writes opt-outs.
cd /d "%~dp0"
python cadence.py %*
pause
