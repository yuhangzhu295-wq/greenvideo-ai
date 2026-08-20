#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/services/api"
LOG_FILE="$ROOT_DIR/FIX_LOG.md"
API_PORT="${GREENVIDEO_VERIFY_PORT:-8000}"
BASE_URL="http://127.0.0.1:${API_PORT}"
export CI=true

if command -v python3 >/dev/null 2>&1; then
  SYSTEM_PYTHON=(python3)
elif command -v python >/dev/null 2>&1; then
  SYSTEM_PYTHON=(python)
elif command -v py >/dev/null 2>&1; then
  SYSTEM_PYTHON=(py -3)
else
  echo "Python 3 was not found on PATH."
  exit 1
fi

log() {
  printf "\n## %s\n\n%s\n" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG_FILE"
}

cd "$ROOT_DIR"
log "Starting automated verification."

echo "== Audit files =="
test -f AUDIT_REPORT.md
test -f THIRD_PARTY_NOTICES.md
test -f docs/TEST_MATERIALS.md

echo "== Environment =="
node -v || true
pnpm -v || true
"${SYSTEM_PYTHON[@]}" --version || true
if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -version | head -n 1
else
  echo "ffmpeg not found on this shell PATH; media tests will use the backend/runtime fallback when possible."
fi
if command -v yt-dlp >/dev/null 2>&1; then
  yt-dlp --version
else
  echo "yt-dlp not found on this shell PATH; the backend Python environment installs and uses yt-dlp."
fi

echo "== Backend install/test =="
cd "$API_DIR"
if [ -f .venv/Scripts/python.exe ]; then
  VENV_PYTHON="$API_DIR/.venv/Scripts/python.exe"
else
  if command -v python.exe >/dev/null 2>&1; then
    python.exe -m venv .venv
  elif command -v py.exe >/dev/null 2>&1; then
    py.exe -3 -m venv .venv
  else
    "${SYSTEM_PYTHON[@]}" -m venv .venv
  fi
  if [ -f .venv/Scripts/python.exe ]; then
    VENV_PYTHON="$API_DIR/.venv/Scripts/python.exe"
  elif [ -x .venv/bin/python ]; then
    VENV_PYTHON="$API_DIR/.venv/bin/python"
  else
    echo "Could not locate venv Python."
    exit 1
  fi
fi
"$VENV_PYTHON" -m pip install -U pip
"$VENV_PYTHON" -m pip install -e ".[dev]"
"$VENV_PYTHON" -m ruff check app tests
"$VENV_PYTHON" -m pytest -q

echo "== API server =="
"$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" > "$ROOT_DIR/work-api.log" 2>&1 &
UVICORN_PID=$!
cleanup() {
  kill "$UVICORN_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$VENV_PYTHON" - <<PY
import time
from urllib.request import urlopen

for _ in range(80):
    try:
        with urlopen("$BASE_URL/api/health", timeout=2) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.25)
else:
    raise SystemExit("API did not become ready")
PY

cd "$ROOT_DIR"
echo "== API smoke =="
"$VENV_PYTHON" scripts/smoke_api.py "$BASE_URL"

echo "== Share links verify =="
"$VENV_PYTHON" scripts/verify_share_links.py "$BASE_URL"

echo "== Local material verify =="
"$VENV_PYTHON" scripts/verify_local_material.py "$BASE_URL"

cleanup
trap - EXIT

echo "== Frontend verify =="
if command -v powershell.exe >/dev/null 2>&1; then
  if command -v wslpath >/dev/null 2>&1; then
    WIN_ROOT="$(wslpath -w "$ROOT_DIR")"
  elif command -v cygpath >/dev/null 2>&1; then
    WIN_ROOT="$(cygpath -w "$ROOT_DIR")"
  else
    WIN_ROOT="$ROOT_DIR"
  fi
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \
    "Set-Location -LiteralPath '$WIN_ROOT'; pnpm install --no-frozen-lockfile; if (\$LASTEXITCODE) { exit \$LASTEXITCODE }; pnpm lint; if (\$LASTEXITCODE) { exit \$LASTEXITCODE }; pnpm typecheck; if (\$LASTEXITCODE) { exit \$LASTEXITCODE }; pnpm build:weapp; exit \$LASTEXITCODE"
else
  pnpm install --no-frozen-lockfile
  pnpm lint
  pnpm typecheck
  pnpm build:weapp
fi

echo "== Static no-empty-handler scan =="
"$VENV_PYTHON" scripts/scan_no_empty_handlers.py

echo "== DONE =="
log "Automated verification completed successfully."
