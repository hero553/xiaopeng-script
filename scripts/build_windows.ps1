python -m pip install --upgrade pip
python -m pip install -e . pyinstaller
python -m PyInstaller `
  --name xiaopeng-monitor `
  --onefile `
  --collect-all playwright `
  scripts/pyinstaller_entry.py

python -c "from pathlib import Path; from xiaopeng_script.config import write_default_config; write_default_config(Path(r'dist/config.json'), overwrite=True)"
