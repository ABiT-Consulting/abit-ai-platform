#!/usr/bin/env bash
# Read-only readiness check. Do not create tabs, submit messages or restart jobs.
set -euo pipefail
node_bin="${ABIT_RELAY_NODE_BIN:-/usr/local/bin/node}"
root="${ABIT_RELAY_RUNTIME_ROOT:-/srv/abit-dev-linux/relay-runtime}"
failures=0

check() {
  local name="$1" port="$2" kind="$3" body result
  if ! body="$(curl --silent --show-error --fail --max-time 8 "http://127.0.0.1:${port}/health" 2>/dev/null)"; then
    printf 'FAIL %s on 127.0.0.1:%s (unreachable)\n' "$name" "$port"
    failures=$((failures + 1))
    return
  fi
  if result="$(printf '%s' "$body" | "$node_bin" -e '
    let raw = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", chunk => { raw += chunk; });
    process.stdin.on("end", () => {
      try {
        const p = JSON.parse(raw);
        const valid = process.argv[1] === "sbodesk"
          ? p.ready === true && p.reauthenticationRequired === false
          : p.ok === true && p.ready === true && p.authenticated === true && p.target_ready === true;
        if (!valid) process.exitCode = 2;
        process.stdout.write(valid ? "ready and authenticated" : "not fully ready or signed out");
      } catch (_) {
        process.exitCode = 2;
        process.stdout.write("invalid readiness response");
      }
    });
  ' "$kind")"; then
    printf 'PASS %s on 127.0.0.1:%s (%s)\n' "$name" "$port" "$result"
  else
    printf 'FAIL %s on 127.0.0.1:%s (%s)\n' "$name" "$port" "$result"
    failures=$((failures + 1))
  fi
}

[[ -x "$node_bin" ]] || { echo 'Node runtime missing' >&2; exit 2; }
[[ -d "$root/state" ]] || { echo 'Relay state directory missing' >&2; exit 2; }
products=("${@:-all}")
if [[ "${products[0]}" == all ]]; then products=(erp bigrro sbodesk); fi
for product in "${products[@]}"; do
  case "$product" in
    erp) check ERP 8794 erp ;;
    bigrro) check BIGRRO 8793 bigrro; check BIGRRO-secondary 8795 bigrro ;;
    sbodesk) check SBODesk 8788 sbodesk ;;
    *) echo "Unknown product: $product" >&2; exit 64 ;;
  esac
done
if (( failures != 0 )); then echo "Readiness failed for $failures endpoint(s)" >&2; exit 1; fi
echo 'All requested product relay endpoints are ready (read-only checks).'
