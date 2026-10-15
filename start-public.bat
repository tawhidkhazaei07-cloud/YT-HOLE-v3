@echo off
setlocal
title YT Hole - public Cloudflare Tunnel
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -m venv .venv
  if errorlevel 1 (
    echo Python was not found. Install Python 3.10+ and try again.
    pause
    exit /b 1
  )
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)

if not exist "cloudflared.exe" (
  echo Download cloudflared.exe from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  echo Put it next to this file, then run start-public.bat again.
  pause
  exit /b 1
)

set YTHOLE_ENV=development
set YTHOLE_HOST=127.0.0.1
set YTHOLE_PORT=5000
set YTHOLE_TRUST_PROXY=true
set YTHOLE_SERVE_FRONTEND=true
set YTHOLE_DAILY_DOWNLOAD_LIMIT=10

start "YT Hole backend" cmd /k "cd /d ""%~dp0"" && set YTHOLE_ENV=development && set YTHOLE_HOST=127.0.0.1 && set YTHOLE_PORT=5000 && set YTHOLE_TRUST_PROXY=true && .venv\Scripts\python.exe -m backend"
timeout /t 3 /nobreak >nul
echo.
echo Starting public HTTPS tunnel. Copy the https://...trycloudflare.com URL from the next window.
start "YT Hole public link" cmd /k "cd /d ""%~dp0"" && cloudflared.exe tunnel --url http://127.0.0.1:5000"
echo.
echo Keep both black windows open. Closing either one stops the public site.
pause
