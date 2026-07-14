@echo off
setlocal
cd /d "%~dp0"
title DEALFLOW Access Codes
:menu
cls
echo ==============================================
echo     DEALFLOW  -  ACCESS CODES
echo ==============================================
echo     1.  Create a new access code  (give someone access)
echo     2.  See who has access
echo     3.  Revoke someone's access
echo     4.  Quit
echo ==============================================
set "choice="
set /p choice="Pick 1-4 and press Enter: "
if "%choice%"=="1" goto create
if "%choice%"=="2" goto list
if "%choice%"=="3" goto revoke
if "%choice%"=="4" exit /b
goto menu

:create
echo.
python access_codes.py create
echo.
pause
goto menu

:list
echo.
python access_codes.py list
echo.
pause
goto menu

:revoke
echo.
python access_codes.py revoke
echo.
pause
goto menu
