@echo off
REM Prefer install_diligence_autostart.bat once — then Run Diligence works with no window.
REM This bat is for manual debug (visible console).
cd /d "%~dp0"
echo Prefer install_diligence_autostart.bat once — then Run Diligence works with no window.
echo This window is DEBUG only (visible console). Close it to stop the debug server.
echo.
python diligence_server.py
pause
