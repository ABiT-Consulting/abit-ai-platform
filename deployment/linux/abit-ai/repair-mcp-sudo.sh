#!/usr/bin/env bash
# ADMINISTRATOR CONSOLE ONLY. Do not run through the MCP connector.
# Creates two managed files; rollback removes only byte-identical managed files.
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

SERVICE=abit-dev-linux-mcp.service
ACCOUNT=mcpdev
STATE=/var/lib/abit-mcp-sudo-repair-v1
DROP_DIR=/etc/systemd/system/abit-dev-linux-mcp.service.d
DROP_FILE=$DROP_DIR/90-abit-mcp-sudo-managed.conf
SUDO_FILE=/etc/sudoers.d/90-abit-mcp-root-managed
RUN_DIR=/run/systemd/system/abit-dev-linux-mcp.service.d
TEMP_FILE=$RUN_DIR/zzzz-abit-mcp-sudo-preserve-children.conf
MODE=${1:-}
CHANGED=0
RESTART_ATTEMPTED=0
TEMP_OWNED=0
ORIGINAL_KILL_MODE=
RELAY_SNAPSHOT=$STATE/relay-processes.apply.before.tsv

say() { printf '%s\n' "$*"; }
die() { say "ERROR: $*" >&2; exit 1; }
property() { systemctl show "$SERVICE" --property="$1" --value; }
absent() { [[ ! -e $1 && ! -L $1 ]]; }
root_private_directory() {
    [[ -d $1 && ! -L $1 && $(stat -c %u "$1") == 0 ]] || return 1
    local mode
    mode=$(stat -c %a "$1")
    (( (8#$mode & 0022) == 0 ))
}
managed_matches() {
    [[ -f $1 && ! -L $1 && $(stat -c %u "$1") == 0 ]] && cmp -s -- "$1" "$2"
}
# Link from an exclusive temporary file on the same filesystem: no overwrite race.
install_new() {
    local source=$1 target=$2 mode=$3 temporary
    absent "$target" || { say "Refusing existing path: $target" >&2; return 1; }
    temporary=$(mktemp "${target%/*}/.abit-mcp-repair.XXXXXXXX") || return 1
    if ! install -o root -g root -m "$mode" "$source" "$temporary"; then
        rm -f -- "$temporary"; return 1
    fi
    if ! ln -- "$temporary" "$target"; then
        rm -f -- "$temporary"; return 1
    fi
    rm -f -- "$temporary"
}
remove_managed() {
    local target=$1 expected=$2
    absent "$target" && return 0
    managed_matches "$target" "$expected" || {
        say "Refusing to remove changed/unowned path: $target" >&2; return 1;
    }
    rm -- "$target"
}
record_relay_processes() {
    local name file pid raw fields
    : >"$RELAY_SNAPSHOT"
    # These three are required; stale/retired xvfb and tunnel PID files are ignored.
    for name in egress chrome relay; do
        file="/srv/abit-dev-linux/relay-runtime/state/$name.pid"
        [[ -f $file && ! -L $file ]] || die "Required relay process record missing: $name"
        pid=
        IFS= read -r pid <"$file" || [[ -n $pid ]] || die "Empty relay process record: $name"
        [[ $pid =~ ^[1-9][0-9]*$ ]] || die "Invalid relay process record: $name"
        [[ -r /proc/$pid/stat ]] || die "Required relay process is not running: $name"
        raw=$(</proc/"$pid"/stat) || die "Cannot inspect required relay process: $name"
        read -r -a fields <<<"${raw##*) }"
        (( ${#fields[@]} >= 20 )) || die "Incomplete relay process state: $name"
        [[ ${fields[0]} != Z && ${fields[0]} != X ]] || die "Required relay process has exited: $name"
        [[ ${fields[1]} == 1 && ${fields[2]} == "$pid" ]] ||
            die "Relay process $name is not detached; review shutdown behavior first."
        printf '%s\t%s\t%s\n' "$name" "$pid" "${fields[19]}" >>"$RELAY_SNAPSHOT"
    done
}
verify_relay_processes() {
    local name pid birth raw fields count=0
    local -a required=(egress chrome relay)
    [[ -f $RELAY_SNAPSHOT && ! -L $RELAY_SNAPSHOT ]] || {
        say 'Required relay preservation snapshot is missing.' >&2; return 1;
    }
    while IFS=$'\t' read -r name pid birth; do
        [[ $name == "${required[$count]:-}" && $pid =~ ^[1-9][0-9]*$ && $birth =~ ^[0-9]+$ ]] || {
            say 'Invalid or duplicate relay preservation snapshot entry.' >&2; return 1;
        }
        [[ -r /proc/$pid/stat ]] || { say "Relay process disappeared: $name" >&2; return 1; }
        raw=$(</proc/"$pid"/stat) || { say "Cannot inspect relay process: $name" >&2; return 1; }
        read -r -a fields <<<"${raw##*) }"
        (( ${#fields[@]} >= 20 )) || { say "Incomplete relay process state: $name" >&2; return 1; }
        [[ ${fields[0]} != Z && ${fields[0]} != X ]] || { say "Relay process has exited: $name" >&2; return 1; }
        [[ ${fields[19]} == "$birth" ]] || { say "Relay process identity changed: $name" >&2; return 1; }
        (( count += 1 ))
    done <"$RELAY_SNAPSHOT"
    (( count == 3 )) || { say 'Relay preservation snapshot is incomplete.' >&2; return 1; }
}
cleanup_temporary_override() {
    (( TEMP_OWNED == 1 )) || return 0
    remove_managed "$TEMP_FILE" "$STATE/preserve-children.conf" || return 1
    systemctl daemon-reload || return 1
    [[ $(property KillMode) == "$ORIGINAL_KILL_MODE" ]] || {
        say 'KillMode no longer matches the pre-operation value; inspect concurrent edits.' >&2
        return 1
    }
    # Keep cleanup pending if either reload or effective-setting verification fails.
    TEMP_OWNED=0
}
safe_restart() {
    # Cleanup may have removed the file before a failed daemon-reload; reinstall it.
    if absent "$TEMP_FILE"; then
        if absent "$RUN_DIR"; then install -d -o root -g root -m 0755 "$RUN_DIR"; fi
        root_private_directory "$RUN_DIR" || return 1
        install_new "$STATE/preserve-children.conf" "$TEMP_FILE" 0644 || return 1
        TEMP_OWNED=1
    else
        (( TEMP_OWNED == 1 )) && managed_matches "$TEMP_FILE" "$STATE/preserve-children.conf" || {
            say 'Refusing unowned or changed temporary preservation override.' >&2; return 1;
        }
    fi
    systemctl daemon-reload || return 1
    [[ $(property KillMode) == process ]] || {
        say 'Temporary KillMode=process is not effective; refusing restart.' >&2; return 1;
    }
    RESTART_ATTEMPTED=1
    systemctl restart "$SERVICE" || return 1
    systemctl is-active --quiet "$SERVICE" || return 1
}
restore_managed_configuration() {
    # Check both files before changing either; never source any saved shell text.
    absent "$SUDO_FILE" || managed_matches "$SUDO_FILE" "$STATE/desired.sudoers" || return 1
    absent "$DROP_FILE" || managed_matches "$DROP_FILE" "$STATE/desired.conf" || return 1
    remove_managed "$SUDO_FILE" "$STATE/desired.sudoers" || return 1
    remove_managed "$DROP_FILE" "$STATE/desired.conf" || return 1
    visudo -c >"$STATE/visudo.rollback.log" 2>&1 || return 1
    systemctl daemon-reload || return 1
}
on_exit() {
    local result=$? rollback_verified=1
    trap - EXIT INT TERM HUP
    set +e
    if (( result != 0 && CHANGED == 1 )) && [[ $MODE == --apply ]]; then
        say 'Apply failed; restoring only managed configuration.' >&2
        if restore_managed_configuration; then
            if (( RESTART_ATTEMPTED == 1 )); then
                safe_restart || {
                    say 'Baseline connector restart failed; administrator intervention required.' >&2
                    rollback_verified=0
                }
            fi
            verify_relay_processes || {
                say 'Relay/browser preservation after automatic rollback is unverified.' >&2
                rollback_verified=0
            }
            if (( rollback_verified == 1 )); then
                printf '%s\n' 'apply-failed-configuration-restored-relays-verified' >"$STATE/status"
            else
                printf '%s\n' 'apply-failed-configuration-restored-needs-administrator' >"$STATE/status"
            fi
        else
            say 'Automatic restoration refused/failed; inspect root-only state and managed paths.' >&2
        fi
    fi
    cleanup_temporary_override || {
        say "Temporary override cleanup failed: inspect $TEMP_FILE, then daemon-reload." >&2
        result=1
    }
    exit "$result"
}

[[ $# == 1 && ( $MODE == --apply || $MODE == --rollback ) ]] ||
    die "Usage: $0 --apply | --rollback"
(( EUID == 0 )) || die 'Run from an existing administrator/root console.'
[[ $(awk '/^NoNewPrivs:/ {print $2}' /proc/$$/status) == 0 ]] ||
    die 'This administrator shell already has NoNewPrivileges; use an unrestricted administrator console.'
if grep -Eq '/abit-dev-linux-mcp\.service(/|$)' /proc/$$/cgroup; then
    die 'Do not execute this privilege repair inside the MCP connector.'
fi
for command in systemctl systemd-analyze visudo runuser sudo stat cmp install mktemp ln flock awk grep; do
    command -v "$command" >/dev/null || die "Required command missing: $command"
done
root_private_directory /run || die 'Unsafe /run directory.'
[[ ! -L /run/abit-mcp-sudo-repair.lock ]] || die 'Refusing symlink lock.'
exec 9>/run/abit-mcp-sudo-repair.lock
flock -n 9 || die 'Another repair operation is running.'
id "$ACCOUNT" >/dev/null || die 'Expected service account missing.'
[[ $(property LoadState) == loaded && $(property User) == "$ACCOUNT" ]] ||
    die 'Expected unit/account does not match; no changes made.'
[[ -z $(property ExecStop) && -z $(property ExecStopPost) ]] ||
    die 'Explicit stop hooks require separate review to preserve relay/browser children.'
absent "$TEMP_FILE" || die 'A temporary preservation override already exists; inspect it first.'
ORIGINAL_KILL_MODE=$(property KillMode)
[[ $ORIGINAL_KILL_MODE == control-group || $ORIGINAL_KILL_MODE == mixed ||
   $ORIGINAL_KILL_MODE == process ]] || die 'Unexpected KillMode; inspect before proceeding.'
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [[ $MODE == --rollback ]]; then
    root_private_directory "$STATE" || die 'Missing or unsafe root-owned repair state.'
    for file in desired.conf desired.sudoers preserve-children.conf; do
        [[ -f $STATE/$file && ! -L $STATE/$file && $(stat -c %u "$STATE/$file") == 0 ]] ||
            die "Unsafe/incomplete repair state: $file"
    done
    RELAY_SNAPSHOT=$STATE/relay-processes.rollback.before.tsv
    record_relay_processes
    was_active=0
    systemctl is-active --quiet "$SERVICE" && was_active=1
    restore_managed_configuration || die 'Rollback refused; managed files changed or configuration is invalid.'
    if (( was_active == 1 )); then
        safe_restart || die 'Original configuration restored, but connector restart failed.'
    fi
    verify_relay_processes || die 'Configuration restored; relay/browser process preservation could not be verified.'
    cleanup_temporary_override || die 'Configuration restored; temporary override cleanup needs attention.'
    printf '%s\n' 'rolled-back' >"$STATE/status"
    say "Managed changes removed. Original files were not overwritten. Audit backup: $STATE"
    exit 0
fi

systemctl is-active --quiet "$SERVICE" || die 'Connector is not active; inspect before repair.'
[[ $(property DynamicUser) == no ]] || die 'DynamicUser requires separate review.'
absent "$STATE" || die "Repair state already exists: $STATE; use --rollback or inspect it."
absent "$DROP_FILE" || die 'Managed drop-in path already exists.'
absent "$SUDO_FILE" || die 'Managed sudoers path already exists.'
root_private_directory /etc/sudoers.d || die 'Unsafe/missing sudoers.d directory.'
root_private_directory /var/lib || die 'Unsafe /var/lib directory.'
if absent "$DROP_DIR"; then install -d -o root -g root -m 0755 "$DROP_DIR"; fi
root_private_directory "$DROP_DIR" || die 'Unsafe systemd drop-in directory.'
install -d -o root -g root -m 0700 "$STATE"
systemctl cat "$SERVICE" >"$STATE/unit.before.txt"
systemctl show "$SERVICE" --property=User,Group,MainPID,KillMode,NoNewPrivileges,RestrictSUIDSGID,ProtectSystem,ProtectHome,PrivateTmp,ProtectControlGroups,ProtectKernelTunables,ProtectKernelModules,ProtectKernelLogs,ProtectClock >"$STATE/properties.before.txt"
cp -a -- /etc/sudoers "$STATE/sudoers.before"
visudo -c >"$STATE/visudo.before.log" 2>&1 || die 'Existing sudoers configuration is invalid.'
old_pid=$(property MainPID)
[[ $old_pid =~ ^[1-9][0-9]*$ ]] || die 'Expected running connector PID missing.'
printf '%s\n' "$old_pid" >"$STATE/mainpid.before"
record_relay_processes

cat >"$STATE/desired.sudoers" <<'SUDOERS'
# Managed by the administrator-run ABIT MCP sudo repair; root only, no group grant.
Defaults:mcpdev !requiretty
mcpdev ALL=(root) NOPASSWD: ALL
SUDOERS
cat >"$STATE/desired.conf" <<'UNIT'
# Managed by the administrator-run ABIT MCP sudo repair.
[Service]
User=mcpdev
NoNewPrivileges=no
RestrictSUIDSGID=no
ProtectSystem=no
ProtectKernelTunables=no
ProtectKernelModules=no
ProtectKernelLogs=no
ProtectClock=no
# Preserve existing ProtectHome, PrivateTmp and ProtectControlGroups settings.
UNIT
cat >"$STATE/preserve-children.conf" <<'TEMP'
# Temporary, removed after this administrator-controlled connector restart.
[Service]
KillMode=process
TEMP
visudo -cf "$STATE/desired.sudoers" >"$STATE/visudo.fragment.log" 2>&1 || die 'Proposed sudoers failed validation.'
CHANGED=1
install_new "$STATE/desired.sudoers" "$SUDO_FILE" 0440
install_new "$STATE/desired.conf" "$DROP_FILE" 0644
visudo -c >"$STATE/visudo.after.log" 2>&1
systemctl daemon-reload
systemd-analyze verify "$SERVICE" >"$STATE/unit.verify.log" 2>&1
[[ $(property User) == "$ACCOUNT" && $(property NoNewPrivileges) == no ]] ||
    die 'The proposed override is not effective.'
safe_restart
new_pid=$(property MainPID)
[[ $new_pid =~ ^[1-9][0-9]*$ && $new_pid != "$old_pid" ]] || die 'New connector MainPID not established.'
[[ $(awk '/^Uid:/ {print $2}' "/proc/$new_pid/status") == "$(id -u "$ACCOUNT")" ]] ||
    die 'New connector is not running as the expected unprivileged account.'
[[ $(awk '/^NoNewPrivs:/ {print $2}' "/proc/$new_pid/status") == 0 ]] ||
    die 'New connector still has NoNewPrivileges; retain other restrictions and investigate.'
[[ $(runuser -u "$ACCOUNT" -- sudo -n -u root -- /usr/bin/id -u) == 0 ]] ||
    die 'Noninteractive account-to-root sudo verification failed.'
verify_relay_processes || die 'Relay/browser process preservation could not be verified.'
cleanup_temporary_override
printf '%s\n' 'applied' >"$STATE/status"
printf '%s\n' "$new_pid" >"$STATE/mainpid.after"
CHANGED=0
say "Administrator policy and new MainPID checks passed. Audit backup: $STATE"
say 'Reconnect the connector and verify sudo -n id there before proceeding with migration.'
say 'Existing children retain their original restrictions. Future control-group restarts can still terminate them.'
