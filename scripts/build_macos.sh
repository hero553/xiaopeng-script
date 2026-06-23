#!/usr/bin/env bash
set -euo pipefail

python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller \
  --name xiaopeng-monitor \
  --onefile \
  --collect-all playwright \
  scripts/pyinstaller_entry.py
