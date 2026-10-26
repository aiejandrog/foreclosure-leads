@echo off
rem  DealFlow refresh progress - the live screen for refresh-dealflow.bat (refresh_progress.py).
rem  Double-click any time: it shows the running refresh step by step, or the last one if none is
rem  running, and keeps watching for the next. Read-only; closing it never affects a run.
cd /d "%~dp0"
title DealFlow refresh progress
python -u refresh_progress.py watch
