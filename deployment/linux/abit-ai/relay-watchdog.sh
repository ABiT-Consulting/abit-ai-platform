#!/usr/bin/env bash
# Single-instance, unprivileged ABiT Linux Browser Relay watchdog.
# Restarts missing components, never restarts an existing browser/relay job.
# Must be started by the Linux service manager after reboot for boot persistence.
set -euo pipefail
umask 077
ROOT="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
STATE="$ROOT/state"
mkdir -p "$STATE"
exec 9>"$STATE/watchdog.lock"
flock -n 9 || { echo "Watchdog is already active"; exit 0; }

is_running() {
  local name="$1" signature="$2" idfile="$STATE/$name.pid" pid cmd
  [ -s "$idfile" ] || return 1
  pid="$(cat "$idfile")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cmd" == *"$signature"* ]]
}
port_busy() {
  local port="$1"
  ss -H -lnt 2>/dev/null | grep -Fq "127.0.0.1:$port "
}
log_note() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >>"$STATE/watchdog-events.log"
}
ensure_process() {
  local name="$1" signature="$2" port="$3" pid
  if is_running "$name" "$signature"; then return 0; fi
  if [ "$port" != "-" ] && port_busy "$port"; then
    log_note "Blocked duplicate $name: port $port is occupied by another process"
    return 1
  fi
  log_note "Starting missing $name process"
  local launcher="$name"
  if [ "$name" = "chrome" ]; then launcher="browser"; fi
  nohup setsid "$ROOT/service/start-$launcher.sh" >"$STATE/$name.log" 2>&1 </dev/null &
  pid=$!
  echo "$pid" >"$STATE/$name.pid"
  sleep 3
  if ! is_running "$name" "$signature"; then
    log_note "Failed to launch $name; manual inspection required"
    return 1
  fi
}
while :; do
  if ensure_process egress "browser-egress.cjs" "8812"; then
    if ensure_process xvfb "Xvfb :88" "-"; then
      if ensure_process chrome "chrome-linux64/chrome" "9228"; then
        if ensure_process relay "aibit_relay.cjs" "8810"; then
          if [ "${ABIT_RELAY_ENABLE_WINDOWS_BRIDGE:-1}" = "1" ]; then
            ensure_process tunnel "abit_relay_tunnel@192.168.1.100" "-" || true
          fi
        fi
      fi
    fi
  fi
  sleep 20
done