@echo off
setlocal
title YT Hole - local
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
set YTHOLE_HOST=0.0.0.0
set YTHOLE_PORT=5000
set YTHOLE_DAILY_DOWNLOAD_LIMIT=10
.venv\Scripts\python.exe -m backend
pause
