#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
CHROME="${ABIT_RELAY_CHROME_BIN:-$ROOT/browser/chrome-linux64/chrome}"
PROFILE="$ROOT/state/chrome"
[ -x "$CHROME" ] || { echo "Chrome for Testing not installed: $CHROME" >&2; exit 2; }
[ -S /tmp/.X11-unix/X88 ] || { echo "Isolated Xvfb is not running" >&2; exit 2; }
command -v aa-exec >/dev/null || { echo "AppArmor execution support is required" >&2; exit 2; }
mkdir -p "$PROFILE"
export DISPLAY=:88 XAUTHORITY="$ROOT/state/Xauthority"
export LD_LIBRARY_PATH="$ROOT/browser/ubuntu-libs/extracted/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Chrome runs headful under the existing Ubuntu 'chrome' AppArmor userns policy.
# Retain the Chromium renderer sandbox; do not add --no-sandbox.
exec aa-exec -p chrome -- "$CHROME" --disable-dev-shm-usage \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9228 \
  --proxy-server=http://127.0.0.1:8812 \
  --no-first-run --no-default-browser-check \
  --user-data-dir="$PROFILE" https://chatgpt.com/
