#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python3 -m pip install -e .
PYTHONPATH=src python3 -m xiaopeng_script.cli --wait-login -v "$@"
