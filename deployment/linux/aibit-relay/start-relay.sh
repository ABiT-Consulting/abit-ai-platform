#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
TOKEN_FILE="${ABIT_RELAY_TOKEN_FILE:-$ROOT/secrets/relay.token}"
[ -s "$TOKEN_FILE" ] || { echo "Secure relay token missing: $TOKEN_FILE" >&2; exit 2; }
[ -f "$ROOT/service/aibit_relay.cjs" ] || { echo "Relay source missing" >&2; exit 2; }
mkdir -p "$ROOT/state"
export ABIT_ERP_CHATGPT_RELAY_HOST="${ABIT_ERP_CHATGPT_RELAY_HOST:-127.0.0.1}"
export ABIT_ERP_CHATGPT_RELAY_PORT="${ABIT_ERP_CHATGPT_RELAY_PORT:-8810}"
export ABIT_ERP_CHATGPT_CDP_URL="${ABIT_ERP_CHATGPT_CDP_URL:-http://127.0.0.1:9228}"
export ABIT_ERP_CHATGPT_RELAY_TOKEN="$(cat "$TOKEN_FILE")"
export ABIT_ERP_CHATGPT_WINDOW_NAME="AIBIT_CHATGPT_RELAY"
export ABIT_ERP_CHATGPT_TARGET_FILE="$ROOT/state/aibit-relay-target-id.txt"
export ABIT_ERP_CHATGPT_PROJECT_CACHE="$ROOT/state/aibit-relay-projects.json"
export ABIT_ERP_CHATGPT_RATE_LIMIT_STATE_FILE="$ROOT/state/aibit-relay-rate-limit.json"
export ABIT_SHARED_CHATGPT_LOCK_FILE="$ROOT/state/browser.lock"
export BIGRRO_SECONDARY_LISTEN_PORT=0
unset CHATGPT_EXISTING_CHROME_USER_DATA_DIR
exec /usr/local/bin/node "$ROOT/service/aibit_relay.cjs"
