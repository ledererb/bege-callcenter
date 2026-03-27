#!/bin/bash
# Bégé Design Voice Agent — Single-command launcher for Railway/production
# Starts both the LiveKit agent worker and the FastAPI web server.
# Usage: ./start.sh         (production — "start" mode)
#        ./start.sh dev     (dev mode with hot-reload)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-start}"

echo "🚀 Starting Bégé Design Voice Agent..."
echo "   Agent worker: python server.py $MODE"
echo "   Web server:   python web_server.py (port ${PORT:-8000})"

# Handle shutdown: kill both processes when this script exits
cleanup() {
    echo "🛑 Shutting down..."
    kill $AGENT_PID $WEB_PID 2>/dev/null || true
    wait $AGENT_PID $WEB_PID 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# Start the agent worker in the background
NO_COLOR=1 python server.py "$MODE" &
AGENT_PID=$!

# Start the web server in the background
python web_server.py &
WEB_PID=$!

echo "✅ Both processes started (agent=$AGENT_PID, web=$WEB_PID)"

# Wait for both — if either exits, the cleanup trap handles the other
wait $AGENT_PID $WEB_PID
