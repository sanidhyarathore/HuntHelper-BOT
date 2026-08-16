#!/bin/bash
# Mac: double-click this file to run the job radar.
# First time only: right-click -> Open, to get past the security warning.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Setting up for the first time…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi
./.venv/bin/python run.py cycle
echo
read -p "Done. Press Enter to close."
