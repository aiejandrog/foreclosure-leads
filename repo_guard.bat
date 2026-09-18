@echo off
rem =====================================================================
rem  repo_guard.bat - refuse to run a publishing job from a checkout that
rem  is not the DEALFLOW repo, or that has lost its history.
rem
rem  WHY THIS EXISTS - 2026-09-17, and it cost the remote copy of the repo
rem  A publish was run on the desktop from a directory that was NOT the
rem  project checkout. It held the built site and little else, so its
rem  `git push origin main` replaced main on GitHub with a single commit
rem  containing `docs/` and a README - 318 source modules, every .bat, and
rem  the whole commit history gone from the remote in one push. Recovered
rem  only because a cloud session happened to be holding a clone from
rem  eighteen minutes earlier.
rem
rem  Every runner in this repo ends in `git push origin main`, and not one
rem  of them checked WHAT it was about to push. A publish job is the most
rem  destructive command in the project and it trusted its working
rem  directory completely.
rem
rem  WHAT IT CHECKS, and why each one
rem    1. The pipeline's own files are here. A built-site folder passes
rem       every git check there is - it is a perfectly valid repo. The
rem       only thing that distinguishes it is that the CODE is absent.
rem    2. This is a git work tree at all.
rem    3. The history is not a stub. A fresh `git init` in the wrong
rem       folder has 0-1 commits; the real repo has hundreds. This is the
rem       check that catches "first publish of the built DEALFLOW pages".
rem    4. origin is this project. A correct checkout pointed at someone
rem       else's remote is still a bad push.
rem
rem  It only ever refuses. It never fixes, resets or reclones - guessing
rem  what a wrong checkout "meant" is how a bad situation becomes worse.
rem
rem  Usage:  call repo_guard.bat "%~dp0" "<logfile>"
rem  Exit:   0 = this is the DEALFLOW repo, safe to proceed
rem          1 = it is not; the caller must NOT publish
rem =====================================================================
setlocal
set "RGDIR=%~1"
if "%RGDIR%"=="" set "RGDIR=%CD%"
if "%RGDIR:~-1%"=="\" set "RGDIR=%RGDIR:~0,-1%"
set "RGLOG=%~2"
if "%RGLOG%"=="" set "RGLOG=nul"

rem  1. the pipeline's own files. tracker_template.html is the design source, paths.py owns every
rem     output path, CLAUDE.md is the repo contract - a built-site folder has none of them.
for %%F in (foreclosure_leads.py tracker_template.html paths.py CLAUDE.md) do (
  if not exist "%RGDIR%\%%F" (
    echo     ^!^! REPO GUARD: %%F is missing from "%RGDIR%".>> "%RGLOG%"
    echo     ^!^! This is not the DEALFLOW repo. REFUSING to build or publish.>> "%RGLOG%"
    echo     ^!^! REPO GUARD: not the DEALFLOW repo ^(%%F missing^) - refusing to publish.
    endlocal & exit /b 1
  )
)

rem  2. it is a git work tree
git -C "%RGDIR%" rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo     ^!^! REPO GUARD: "%RGDIR%" is not a git work tree. REFUSING to publish.>> "%RGLOG%"
  echo     ^!^! REPO GUARD: not a git work tree - refusing to publish.
  endlocal & exit /b 1
)

rem  3. the history is real, not a stub. THIS is the check that would have stopped 09-17.
set "RGN=0"
for /f %%N in ('git -C "%RGDIR%" rev-list --count HEAD 2^>nul') do set "RGN=%%N"
if %RGN% LSS 20 (
  echo     ^!^! REPO GUARD: only %RGN% commit^(s^) of history here. A real checkout has hundreds.>> "%RGLOG%"
  echo     ^!^! A push from this directory would REPLACE the remote history. REFUSING.>> "%RGLOG%"
  echo     ^!^! REPO GUARD: history is a stub ^(%RGN% commits^) - refusing to publish.
  endlocal & exit /b 1
)

rem  4. origin is this project
set "RGURL="
for /f "delims=" %%U in ('git -C "%RGDIR%" config --get remote.origin.url 2^>nul') do set "RGURL=%%U"
echo %RGURL% | find /i "foreclosure-leads" >nul
if errorlevel 1 (
  echo     ^!^! REPO GUARD: origin is "%RGURL%", not the foreclosure-leads remote. REFUSING.>> "%RGLOG%"
  echo     ^!^! REPO GUARD: wrong origin remote - refusing to publish.
  endlocal & exit /b 1
)

echo     repo guard OK - DEALFLOW checkout, %RGN% commits, origin %RGURL%.>> "%RGLOG%"
endlocal & exit /b 0
