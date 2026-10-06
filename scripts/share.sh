#!/usr/bin/env bash
# Share this machine's app with reviewers through an ngrok tunnel (free). Run it in a terminal and keep that terminal open:
#
#   make share                                  # asks for an access code (typing is hidden), then starts the app and ngrok
#   NGROK_DOMAIN=your-name.ngrok-free.app make share   # use your free static dev domain so the link never changes
#   TOKEN_CAP=300000 make share                 # change the daily model-token cap for the Assistant (default 1,000,000)
#
# What it does: starts the app on 127.0.0.1 only (nothing is reachable except through the tunnel), view-only (viewers cannot start live
# generation or use the Admin engine controls), behind an access code, with a daily token cap on the model. The database stays on this machine.
# Ctrl+C stops the tunnel and the app. Reviewers see ngrok's one-time "Visit Site" page on the free plan (free plan: 1 GB/month, 20,000 requests/month).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8501}"
CODE="${NSW_ACCESS_CODE:-}"
if [ -z "$CODE" ]; then
  read -r -s -p "Choose an access code for reviewers (not stored anywhere): " CODE || true
  echo
fi
if [ -z "$CODE" ]; then
  echo "An access code is required: refusing to share without one." >&2
  exit 1
fi
command -v ngrok >/dev/null 2>&1 || { echo "ngrok is not installed (brew install ngrok, then ngrok config add-authtoken <token>)." >&2; exit 1; }
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use; choose another with PORT=8610 make share." >&2
  exit 1
fi

export NSW_ACCESS_CODE="$CODE" NSW_VIEW_ONLY=1 NSW_TOKEN_BUDGET_DAY="${TOKEN_CAP:-1000000}"
LOG="data/share_app.log"
mkdir -p data
.venv/bin/streamlit run app/main.py --server.address 127.0.0.1 --server.port "$PORT" --server.headless true \
  --browser.gatherUsageStats false >"$LOG" 2>&1 &
APP=$!
caffeinate -i -w "$APP" >/dev/null 2>&1 &            # keep the Mac awake while the app runs
cleanup() { kill "$APP" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for _ in $(seq 1 40); do
  curl -fs "http://127.0.0.1:$PORT/_stcore/health" >/dev/null 2>&1 && break
  kill -0 "$APP" 2>/dev/null || { echo "The app failed to start; see $LOG" >&2; exit 1; }
  sleep 0.5
done
echo "App is up on http://127.0.0.1:$PORT (view-only, access code required, token cap $NSW_TOKEN_BUDGET_DAY a day)."
echo "Starting ngrok: share the https:// 'Forwarding' address below together with the access code."
if [ -n "${NGROK_DOMAIN:-}" ]; then
  ngrok http "$PORT" --url="$NGROK_DOMAIN"
else
  ngrok http "$PORT"
fi
