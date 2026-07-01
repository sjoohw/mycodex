#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_HOST="${HERMES_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${HERMES_BACKEND_PORT:-8000}"
FRONTEND_HOST="${HERMES_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${HERMES_FRONTEND_PORT:-5173}"

cd "$PROJECT_DIR"

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_EXE="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_EXE="${PYTHON:-python3}"
fi

"$PYTHON_EXE" -m uvicorn backend.hermes_workspace.main:app \
  --host "$BACKEND_HOST" \
  --port "$BACKEND_PORT" &
BACKEND_PID=$!

npm run frontend -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "Hermes backend:  http://$BACKEND_HOST:$BACKEND_PORT"
echo "Hermes frontend: http://localhost:$FRONTEND_PORT"
if command -v tailscale >/dev/null 2>&1; then
  TAILSCALE_IP="$(tailscale ip -4 2>/dev/null || true)"
  if [[ -n "$TAILSCALE_IP" ]]; then
    echo "Phone via Tailscale: http://$TAILSCALE_IP:$FRONTEND_PORT"
  fi
fi

wait
