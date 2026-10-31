#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
mkdir -p "$ROOT/state"
export DISPLAY=:88 XAUTHORITY="$ROOT/state/Xauthority"
[ -s "$XAUTHORITY" ] || { touch "$XAUTHORITY"; xauth -f "$XAUTHORITY" add "$DISPLAY" . "$(openssl rand -hex 16)"; }
exec "$ROOT/browser/ubuntu-libs/extracted/usr/bin/Xvfb" "$DISPLAY" -screen 0 1365x768x24 -nolisten tcp -auth "$XAUTHORITY"
