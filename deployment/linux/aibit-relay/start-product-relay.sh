#!/usr/bin/env bash
# Run a named product relay against the EXISTING ABiT Linux Chromium session.
# Never start a second browser or expose a public CDP listener.
set -euo pipefail
umask 077

product="${1:-}"
root="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
service="$root/service"
state="$root/state"
secrets="$root/secrets"
node_bin="${ABIT_RELAY_NODE_BIN:-/usr/local/bin/node}"

if [[ "$(id -un)" != mcpdev ]]; then
  echo 'Refusing product relay startup outside the mcpdev service account' >&2
  exit 77
fi
[[ -d "$service" && -d "$state" ]] || { echo 'Relay runtime folders missing' >&2; exit 2; }
[[ -x "$node_bin" ]] || { echo 'Node runtime missing' >&2; exit 2; }
export ABIT_SHARED_CHATGPT_LOCK_FILE="$state/browser.lock"
export ABIT_ERP_CHATGPT_RELAY_HOST=127.0.0.1
export ABIT_ERP_CHATGPT_CDP_URL=http://127.0.0.1:9228
export ABIT_SHARED_CHATGPT_LOCK_WAIT_MS="${ABIT_SHARED_CHATGPT_LOCK_WAIT_MS:-90000}"
export ABIT_ERP_CHATGPT_TIMEOUT_MS="${ABIT_ERP_CHATGPT_TIMEOUT_MS:-660000}"
unset CHATGPT_EXISTING_CHROME_USER_DATA_DIR
entry=''

case "$product" in
  erp)
    entry="$service/abit_erp_chatgpt_relay.cjs"
    token_file="$secrets/erp-relay.token"
    export ABIT_ERP_CHATGPT_RELAY_PORT=8794
    export BIGRRO_SECONDARY_LISTEN_PORT=0
    export ABIT_ERP_CHATGPT_WINDOW_NAME=ABIT_ERP_CHATGPT_RELAY
    export ABIT_ERP_CHATGPT_TARGET_FILE="$state/erp-target-id.txt"
    export ABIT_ERP_CHATGPT_PROJECT_CACHE="$state/erp-projects.json"
    export ABIT_ERP_CHATGPT_RATE_LIMIT_STATE_FILE="$state/erp-rate-limit-state.json"
    ;;
  bigrro)
    entry="$service/bigrro_dev02_chatgpt_relay.cjs"
    token_file="$secrets/bigrro-relay.token"
    export ABIT_ERP_CHATGPT_RELAY_PORT=8793
    export BIGRRO_SECONDARY_LISTEN_PORT=8795
    export ABIT_ERP_CHATGPT_WINDOW_NAME=BIGRRO_PRIMARY_CHATGPT_RELAY
    export ABIT_ERP_CHATGPT_TARGET_FILE="$state/bigrro-target-id.txt"
    export ABIT_ERP_CHATGPT_PROJECT_CACHE="$state/bigrro-projects.json"
    export ABIT_ERP_CHATGPT_RATE_LIMIT_STATE_FILE="$state/bigrro-rate-limit-state.json"
    export BIGRRO_CONVERSATION_FILE="$state/bigrro-conversations.json"
    ;;
  sbodesk)
    entry="$service/chatgpt-web-gateway.mjs"
    export CHATGPT_WEB_GATEWAY_PORT=8788
    export CHATGPT_WEB_HEADLESS=false
    export CHATGPT_WEB_SHARED_BROWSER=true
    export CHATGPT_WEB_CDP_URL=http://127.0.0.1:9228
    export CHATGPT_WEB_USER_DATA_DIR="$state/chrome"
    export CHATGPT_WEB_PROFILES_DIR="$state/sbodesk-web-profiles"
    export CHATGPT_WEB_CHROME_PATH="$root/browser/chrome-linux64/chrome"
    ;;
  *) echo 'Usage: start-product-relay.sh {erp|bigrro|sbodesk}' >&2; exit 64 ;;
esac

[[ -f "$entry" ]] || { echo "Product relay file missing for $product" >&2; exit 2; }
if [[ "$product" != sbodesk ]]; then
  [[ -s "$token_file" && -r "$token_file" ]] || { echo "Relay credential file missing for $product" >&2; exit 2; }
  export ABIT_ERP_CHATGPT_RELAY_TOKEN="$(cat "$token_file")"
else
  [[ -d "$service/node_modules/playwright" ]] || { echo 'Linux Playwright dependency missing' >&2; exit 2; }
fi

# The dedicated aibit-linux-relay-watchdog.service owns Chrome/Xvfb.
# If it is down, fail and let systemd retry; do not spawn a competing profile.
curl --silent --show-error --fail --max-time 6 http://127.0.0.1:9228/json/version >/dev/null || {
  echo 'Shared Linux Chrome CDP unavailable; refusing to start another browser' >&2
  exit 75
}
"$node_bin" --check "$entry"
exec "$node_bin" "$entry"
