@echo off
cd /d "%~dp0"
echo A browser will open on the RealForeclose login page.
echo Log in yourself, then CLOSE the browser window when done.
python login-setup.py
pause
