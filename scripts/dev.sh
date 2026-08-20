#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Starting API at http://127.0.0.1:8000"
cd "$ROOT_DIR/services/api"
if [ ! -d .venv ]; then
  python -m venv .venv
fi
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
