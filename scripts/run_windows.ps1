if (!(Test-Path ".venv")) {
  python -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install -e .
xiaopeng-monitor --wait-login @args
