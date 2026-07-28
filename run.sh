#!/usr/bin/env bash
# Launch the Dashboard Agent LangGraph deployment (Agent Server) and serve the SPA.
set -euo pipefail
cd "$(dirname "$0")"

# Prefer this project's own venv; fall back to the sibling demo's venv.
if [ -x ".venv/bin/python" ]; then
  VENV=".venv"
elif [ -x "chat-langchain-lite/.venv/bin/python" ]; then
  VENV="chat-langchain-lite/.venv"
else
  echo "No venv found. Create one:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
PY="$VENV/bin/python"

if [ ! -x "$VENV/bin/langgraph" ]; then
  echo "langgraph CLI not found. Install it:" >&2
  echo "  $VENV/bin/pip install -U 'langgraph-cli[inmem]'" >&2
  exit 1
fi

PORT="${PORT:-2024}"          # Agent Server
SPA_PORT="${SPA_PORT:-3000}"  # static SPA

echo "Agent Server → http://127.0.0.1:${PORT}   (Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:${PORT})"
echo "Dashboard UI  → http://127.0.0.1:${SPA_PORT}  (set its URL/assistant via ⚙️ or static/config.js)"

# Serve the front-end in the background; run the deployment in the foreground.
# Prefer the React app in ./frontend (Vite dev server) when present; otherwise
# fall back to the legacy static SPA served by scripts/serve_spa.py.
if [ -f "frontend/package.json" ]; then
  if [ ! -d "frontend/node_modules" ]; then
    echo "Installing front-end dependencies (frontend/node_modules missing)…"
    npm --prefix frontend install
  fi
  echo "Serving React app via Vite (npm --prefix frontend run dev)."
  ( exec npm --prefix frontend run dev -- --port "$SPA_PORT" ) &
  SPA_PID=$!
else
  # Use an absolute interpreter path so it resolves regardless of the server's
  # working directory.
  PY_ABS="$(cd "$(dirname "$PY")" && pwd)/$(basename "$PY")"
  ( exec "$PY_ABS" scripts/serve_spa.py "$SPA_PORT" ) &
  SPA_PID=$!
fi
trap 'kill "$SPA_PID" 2>/dev/null || true' EXIT
exec "$VENV/bin/langgraph" dev --no-browser --port "$PORT" --allow-blocking "$@"
