@echo off
rem Start YT Hole (Windows). Creates a virtual environment on first run.
rem If phones on your Wi-Fi cannot connect, allow the port once (as Administrator):
rem   netsh advfirewall firewall add rule name="YT Hole" dir=in action=allow protocol=TCP localport=5000
cd /d "%~dp0"
if not exist .venv ( py -3 -m venv .venv || python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python -m backend %*
