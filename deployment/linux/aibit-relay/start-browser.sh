#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
CHROME="${ABIT_RELAY_CHROME_BIN:-$ROOT/browser/chrome-linux64/chrome}"
PROFILE="$ROOT/state/chrome"
[ -x "$CHROME" ] || { echo "Chrome for Testing not installed: $CHROME" >&2; exit 2; }
# Production Chrome must retain its browser sandbox. Never pass --no-sandbox.
# On Ubuntu, configure a confined Chrome AppArmor user-namespace profile with an administrator.
mkdir -p "$PROFILE"
export LD_LIBRARY_PATH="$ROOT/browser/ubuntu-libs/extracted/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$CHROME" --headless=new --disable-dev-shm-usage \
  --remote-debugging-address=127.0.0.1 --remote-debugging-port=9228 \
  --no-first-run --no-default-browser-check --user-data-dir="$PROFILE" about:blank
