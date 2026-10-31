#!/usr/bin/env bash
# Install reviewed definitions only. Never start or enable production here.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "An authorized Linux administrator must run this installer." >&2; exit 1; }
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
units=(abit-ai.service abit-ai-tunnel.service aibit-linux-relay-watchdog.service abit-ai-ollama.service)
for binary in /usr/bin/pamtester /usr/bin/curl /usr/local/bin/cloudflared /srv/abit-dev-linux/abit-ai/venv/bin/python /srv/abit-dev-linux/abit-ai/ollama/runtime/bin/ollama; do
  [ -x "$binary" ] || { echo "Required executable is missing: $binary" >&2; exit 1; }
done
check_destination() {
  [ -f "$1" ] || { echo "Missing source definition: $1" >&2; exit 1; }
  if [ -e "$2" ] && ! cmp -s "$1" "$2"; then
    echo "Existing definition differs: $2. Review before replacement." >&2; exit 1
  fi
}
for unit in "${units[@]}"; do check_destination "$source_dir/$unit" "/etc/systemd/system/$unit"; done
check_destination "$source_dir/pam-abit-ai" /etc/pam.d/abit-ai
systemd-analyze verify "${units[@]/#/$source_dir/}"
for unit in "${units[@]}"; do
  install -o root -g root -m 0644 "$source_dir/$unit" "/etc/systemd/system/$unit"
done
install -o root -g root -m 0644 "$source_dir/pam-abit-ai" /etc/pam.d/abit-ai
systemctl daemon-reload
echo "Definitions installed; no service has been started or enabled."
echo "Verify SSSD online authentication/account policy and mcpdev's access to abitcons.com."
echo "Complete the quiesced final data copy and single-writer cutover before activation."
