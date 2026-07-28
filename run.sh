#!/usr/bin/env bash
# Launch the Dashboard Agent LangGraph deployment (Agent Server) and serve the SPA.
set -euo pipefail
cd "$(dirname "$0")"

# Pick the environment: the one `uv run` (or an activated shell) hands us, then
# this project's own venv, then the sibling demo's. Bootstrap with uv if none
# exists, so `./run.sh` works from a fresh clone.
if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
  VENV="$VIRTUAL_ENV"
elif [ -x ".venv/bin/python" ]; then
  VENV=".venv"
elif [ -x "chat-langchain-lite/.venv/bin/python" ]; then
  VENV="chat-langchain-lite/.venv"
elif command -v uv >/dev/null 2>&1; then
  echo "No venv found — running 'uv sync --group dev'…" >&2
  uv sync --group dev
  VENV=".venv"
else
  echo "No venv found and uv is not installed. Create one:" >&2
  echo "  uv sync --group dev" >&2
  exit 1
fi
PY="$VENV/bin/python"

# The langgraph CLI lives in the dev group; sync it in if it's missing.
if [ ! -x "$VENV/bin/langgraph" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "langgraph CLI not found — running 'uv sync --group dev'…" >&2
    uv sync --group dev
  fi
  if [ ! -x "$VENV/bin/langgraph" ]; then
    echo "langgraph CLI still not found. Install the dev group:" >&2
    echo "  uv sync --group dev" >&2
    exit 1
  fi
fi

if [ ! -f ".env" ]; then
  echo "WARNING: no .env found — ANTHROPIC_API_KEY and LANGSMITH_API_KEY are required." >&2
  echo "         cp .env.example .env  and fill in your keys." >&2
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
    # `ci` installs package-lock.json exactly; fall back if the lockfile is absent.
    if [ -f "frontend/package-lock.json" ]; then
      npm --prefix frontend ci
    else
      npm --prefix frontend install
    fi
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
