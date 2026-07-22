#!/usr/bin/env bash
# Launch the Dashboard Agent dashboard server.
set -euo pipefail
cd "$(dirname "$0")"

# Prefer this project's own venv; fall back to the sibling demo's venv.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "chat-langchain-lite/.venv/bin/python" ]; then
  PY="chat-langchain-lite/.venv/bin/python"
else
  echo "No venv found. Create one:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
PORT="${PORT:-8137}"

echo "Dashboard Agent running on http://127.0.0.1:${PORT}  (hallucination bug: ${DASHBOARD_HALLUCINATE:-1})"
exec "$PY" -m uvicorn dashboard_agent.server:app --port "$PORT" "$@"
