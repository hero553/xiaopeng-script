python -m pip install --upgrade pip
python -m pip install -e . pyinstaller
python -m PyInstaller `
  --name xiaopeng-monitor `
  --onefile `
  --collect-all playwright `
  scripts/pyinstaller_entry.py
