#!/usr/bin/env sh
# Start YT Hole (macOS / Linux). Creates a virtual environment on first run.
#   ./run.sh                 # LAN mode: other devices on your Wi-Fi can connect
#   ./run.sh --check         # configuration report
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec python -m backend "$@"
