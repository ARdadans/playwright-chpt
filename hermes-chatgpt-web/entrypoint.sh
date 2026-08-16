#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Ensure data and cookie directories exist
mkdir -p "${CHATGPT_HOME:-/app/.data/prod}/profile"
mkdir -p "/app/cookies"

# Clean up stale Xvfb locks if container was restarted
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Start Xvfb if not already running on display :99
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
  echo "[ENTRYPOINT] Starting Xvfb on display :99..."
  Xvfb :99 -screen 0 1280x900x24 -nolisten tcp -ac >/tmp/xvfb99.log 2>&1 &
  
  # Wait for Xvfb to be ready
  for i in $(seq 1 25); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
      echo "[ENTRYPOINT] Xvfb is ready on :99."
      break
    fi
    sleep 0.2
  done
fi

# If legacy single cookie file exists at root and cookies directory has no json files, copy it
if [ -f "cookies.json" ] && [ ! -f "cookies/default.json" ]; then
  echo "[ENTRYPOINT] Copying cookies.json to cookies/default.json..."
  cp "cookies.json" "cookies/default.json"
elif [ -f "cookie.json" ] && [ ! -f "cookies/default.json" ]; then
  echo "[ENTRYPOINT] Copying cookie.json to cookies/default.json..."
  cp "cookie.json" "cookies/default.json"
fi

exec "$@"
