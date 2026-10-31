#!/usr/bin/env bash
set -euo pipefail
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
exec /usr/local/bin/node "$ROOT/service/browser-egress.cjs"
