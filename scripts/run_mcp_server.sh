#!/usr/bin/env bash
# Run the Fieldlink demo MCP server, optionally behind an ngrok tunnel.
#
#   ./scripts/run_mcp_server.sh            # local only  -> http://127.0.0.1:8765/mcp
#   ./scripts/run_mcp_server.sh --tunnel   # public URL  -> https://<random>.ngrok.app/mcp
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
[ "${1:-}" = "--tunnel" ] && TUNNEL=1

# Which tunnel. cloudflared is PREFERRED and it is not a style choice: ngrok's
# free tier answers any request carrying a browser User-Agent with an
# interstitial warning page (ERR_NGROK_6024, content-type text/html) instead of
# the resource. MCP itself is unaffected - the client is not a browser - but an
# <img> in a generated proof-of-delivery document gets HTML and renders broken,
# and a tag cannot send the `ngrok-skip-browser-warning` header that would opt
# out. A trycloudflare URL has no interstitial, so images just work.
#   brew install cloudflared
TUNNEL_KIND="${FIELDLINK_TUNNEL:-auto}"
if [ "$TUNNEL_KIND" = "auto" ]; then
  if command -v cloudflared >/dev/null 2>&1; then TUNNEL_KIND=cloudflared; else TUNNEL_KIND=ngrok; fi
fi

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

if [ "$TUNNEL" = "1" ] && ! command -v "$TUNNEL_KIND" >/dev/null 2>&1; then
  echo "$TUNNEL_KIND is not installed. brew install $TUNNEL_KIND" >&2
  [ "$TUNNEL_KIND" = "ngrok" ] && echo "  (then: ngrok config add-authtoken ...)" >&2
  exit 1
fi

FIELDLINK_PORT="$PORT" FIELDLINK_PATH="$PATH_" "$PY" -m mcp_demo_server &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; kill "${NGROK_PID:-}" 2>/dev/null || true' EXIT

if [ "$TUNNEL" = "0" ]; then
  echo "Connection string:  http://127.0.0.1:${PORT}${PATH_}"
  wait "$SERVER_PID"
  exit 0
fi

LOG=/tmp/fieldlink-tunnel.log
URL=""

if [ "$TUNNEL_KIND" = "cloudflared" ]; then
  cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate > "$LOG" 2>&1 &
  NGROK_PID=$!
  for _ in $(seq 1 60); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1) || true
    [ -n "$URL" ] && break
    sleep 0.5
  done
else
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
fi

if [ -z "$URL" ]; then
  echo "$TUNNEL_KIND did not report a tunnel. See $LOG" >&2
  exit 1
fi

echo
echo "  Connection string:  ${URL}${PATH_}"
echo "  Paste that into Settings -> MCP servers, then press Test."
echo "  A new hostname is issued each run, so re-paste after a restart."
if [ "$TUNNEL_KIND" = "ngrok" ]; then
  echo
  echo "  NOTE: ngrok free serves an interstitial to browser requests, so a signature"
  echo "        image embedded in a generated document will render broken. MCP itself"
  echo "        is fine. Install cloudflared for a tunnel without one."
fi
echo
wait "$SERVER_PID"
