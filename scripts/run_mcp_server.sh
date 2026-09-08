#!/usr/bin/env bash
# Run the Fieldlink demo MCP server, optionally behind an ngrok tunnel.
#
#   ./scripts/run_mcp_server.sh                   # Fieldlink (logistics), local only
#   ./scripts/run_mcp_server.sh --wealth          # Meridian Wealth, local only
#   ./scripts/run_mcp_server.sh --wealth --tunnel # ... behind a public ngrok URL
#
# Why the tunnel: a deployed agent connects OUTBOUND to the MCP server's URL, so
# `localhost` inside the deployment's container is the container, not your laptop.
# The tunnel is what gives your machine an address the deployment can reach. If
# you are running the agent locally too (`./run.sh`), skip it and paste the
# 127.0.0.1 URL straight into Settings.
#
# The printed URL goes in Settings -> MCP servers -> Connection string. Note the
# `/mcp` path: the tunnel's bare hostname is not the endpoint.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${FIELDLINK_PORT:-8765}"
PATH_="${FIELDLINK_PATH:-/mcp}"
TUNNEL=0
DOMAIN=""
for arg in "$@"; do
  case "$arg" in
    --tunnel) TUNNEL=1 ;;
    --wealth) DOMAIN="--wealth" ;;
  esac
done

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
  echo "No .venv found - running 'uv sync --group dev'..." >&2
  uv sync --group dev
  PY=".venv/bin/python"
else
  echo "No .venv and uv is not installed. Run: uv sync --group dev" >&2
  exit 1
fi

if [ "$TUNNEL" = "1" ] && ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok is not installed. brew install ngrok  (then: ngrok config add-authtoken ...)" >&2
  exit 1
fi

FIELDLINK_PORT="$PORT" FIELDLINK_PATH="$PATH_" "$PY" -m mcp_demo_server $DOMAIN &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; kill "${NGROK_PID:-}" 2>/dev/null || true' EXIT

if [ "$TUNNEL" = "0" ]; then
  echo "Connection string:  http://127.0.0.1:${PORT}${PATH_}"
  wait "$SERVER_PID"
  exit 0
fi

LOG=/tmp/fieldlink-tunnel.log
URL=""

# `--log stdout` because ngrok's TUI repaints the terminal and hides the server's
# own output; the public URL is read back off the local agent API instead.
ngrok http "$PORT" --log stdout > "$LOG" 2>&1 &
NGROK_PID=$!
for _ in $(seq 1 40); do
  URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
    | "$PY" -c 'import json,sys
try: print(next(t["public_url"] for t in json.load(sys.stdin)["tunnels"] if t["public_url"].startswith("https")))
except Exception: pass' 2>/dev/null) || true
  [ -n "$URL" ] && break
  sleep 0.5
done

if [ -z "$URL" ]; then
  echo "ngrok did not report a tunnel. See $LOG" >&2
  exit 1
fi

echo
echo "  Connection string:  ${URL}${PATH_}"
echo "  Paste that into Settings -> MCP servers, then press Test."
echo "  The free tier hands out a NEW hostname each run, so re-paste after a restart."
echo
wait "$SERVER_PID"
