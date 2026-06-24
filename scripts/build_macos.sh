#!/usr/bin/env bash
set -euo pipefail

python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller \
  --name xiaopeng-monitor \
  --onefile \
  --collect-all playwright \
  scripts/pyinstaller_entry.py

mkdir -p dist
python3 - <<'PY'
from pathlib import Path
from xiaopeng_script.config import write_default_config

write_default_config(Path("dist/config.json"), overwrite=True)
PY
