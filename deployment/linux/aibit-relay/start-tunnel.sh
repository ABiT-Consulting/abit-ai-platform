#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
KEY="$ROOT/secrets/tunnel_ed25519"
KNOWN="$ROOT/state/tunnel-known-hosts"
[ -s "$KEY" ] || { echo "Linux relay tunnel identity missing" >&2; exit 2; }
[ -s "$KNOWN" ] || { echo "Pinned Windows SSH host key missing" >&2; exit 2; }
# Authenticated, encrypted, outbound-only SSH. The Windows side listens only
# on loopback:8819 and the isolated Windows sshd permits remote forwarding only.
exec /usr/bin/ssh -N -p 2226 -i "$KEY" \
  -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN" \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 -o ConnectTimeout=8 \
  -R 127.0.0.1:8819:127.0.0.1:8810 \
  abit_relay_tunnel@192.168.1.100
