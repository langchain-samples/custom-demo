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
echo "Dashboard SPA → http://127.0.0.1:${SPA_PORT}  (set its URL/assistant via ⚙️ or static/config.js)"

# Serve the SPA in the background; run the deployment in the foreground.
( cd dashboard_agent/static && exec "$PY" -m http.server "$SPA_PORT" ) &
SPA_PID=$!
trap 'kill "$SPA_PID" 2>/dev/null || true' EXIT
exec "$VENV/bin/langgraph" dev --no-browser --port "$PORT" --allow-blocking "$@"
