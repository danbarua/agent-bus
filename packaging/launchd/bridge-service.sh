#!/usr/bin/env bash
# Install and operate one agent-bridge as a launchd service.
#
#   ./bridge-service.sh install   desktop:claude
#   ./bridge-service.sh status    desktop:claude
#   ./bridge-service.sh restart   desktop:claude
#   ./bridge-service.sh logs      desktop:claude
#   ./bridge-service.sh uninstall desktop:claude
#   ./bridge-service.sh render    desktop:claude /tmp/out.plist
#
# The address is always explicit. A default would eventually restart the wrong
# service, and there is one service per address by design -- an alias is a role
# with exactly one holder, so a second connector is a second copy of this, not
# a flag on the first.
#
# macOS only. `systemd --user` is the same shape and is not written here
# because nothing runs on Linux yet.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/ai.framesift.agent-bridge.plist.template"
LOGS="$HOME/Library/Logs/agent-bus"
AGENTS="$HOME/Library/LaunchAgents"
KEYCHAIN_SERVICE="agent-bus-cloud-token"

die() { echo "bridge-service: $*" >&2; exit 1; }

usage() {
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

# `desktop:claude` -> KIND=desktop NAME=claude LABEL=desktop-claude
parse_address() {
    ADDRESS="${1:-}"
    [ -n "$ADDRESS" ] || usage
    case "$ADDRESS" in
        *:*:*) die "'$ADDRESS' has more than one ':'. An address is <kind>:<name>." ;;
        *:*)   ;;
        *)     die "'$ADDRESS' is not an address. Try desktop:claude." ;;
    esac
    KIND="${ADDRESS%%:*}"
    NAME="${ADDRESS##*:}"
    [ -n "$KIND" ] && [ -n "$NAME" ] || die "'$ADDRESS' has an empty half."
    LABEL="$KIND-$NAME"
    SERVICE="ai.framesift.agent-bridge.$LABEL"
    PLIST="$AGENTS/$SERVICE.plist"
}

# The installed binary, never a checkout: a service run out of a working tree
# changes behaviour on `git checkout` and stops existing mid-refactor.
#
# `render` only needs the path, so it can be run anywhere -- including in the
# test that proves this template still produces a plist launchd can read.
# `install` needs the binary to actually be there.
bin_dir() { BIN_DIR="$HOME/.local/bin"; }

find_binary() {
    bin_dir
    [ -x "$BIN_DIR/agent-bridge" ] || die \
        "no agent-bridge at $BIN_DIR. Install it: uv tool install agent-bus-team"
}

render() {
    local out="$1"
    [ -f "$TEMPLATE" ] || die "no template at $TEMPLATE"
    sed -e "s|__LABEL__|$LABEL|g" \
        -e "s|__KIND__|$KIND|g" \
        -e "s|__NAME__|$NAME|g" \
        -e "s|__BIN__|$BIN_DIR|g" \
        -e "s|__LOGS__|$LOGS|g" \
        -e "s|__HOME__|$HOME|g" \
        "$TEMPLATE" > "$out"

    case "$(grep -c '__' "$out" || true)" in
        0) ;;
        *) die "$out still holds a placeholder this script does not fill" ;;
    esac
    # `--` inside an XML comment is illegal, and `plutil -lint` says OK while
    # the parser silently keeps four keys of ten. Parse it, do not lint it.
    #
    # Guarded so `render` works off a Mac too: that is what lets the suite check
    # this on every pull request rather than only where someone runs it by hand.
    if command -v plutil >/dev/null 2>&1; then
        plutil -convert json -o /dev/null "$out" \
            || die "$out is not a plist launchd can read"
    fi
}

# The check that was missing when a truncated token was stored and nothing
# said so. macOS's password prompt has a 128-byte buffer; a bridge token is
# longer, so a short one means a prompt ate it.
check_token() {
    if ! security find-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1; then
        echo "note: no '$KEYCHAIN_SERVICE' in the Keychain."
        echo "      The bridge will fall back to ~/.agent-bus/cloud-token, or"
        echo "      spool to disk if there is neither. To store one:"
        echo "        security add-generic-password -U -a \"\$USER\" \\"
        echo "            -s $KEYCHAIN_SERVICE -w \"\$(cat <the token file>)\""
        return 0
    fi
    local n
    n="$(security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null \
         | tr -d '\n' | wc -c | tr -d ' ')"
    if [ "$n" -lt 200 ]; then
        die "the stored token is $n characters, which is too short to be one.
      128 is the giveaway: \`security add-generic-password -w\` with no value
      prompts through a 128-byte buffer and truncates without saying so.
      Re-store it with the value as an argument."
    fi
    echo "keychain: $KEYCHAIN_SERVICE, $n characters"
}

cmd_render() {
    parse_address "${1:-}"
    bin_dir
    local out="${2:-}"
    [ -n "$out" ] || die "render needs an output path"
    render "$out"
    echo "$out"
}

cmd_install() {
    parse_address "${1:-}"
    find_binary
    check_token
    mkdir -p "$LOGS" "$AGENTS"
    render "$PLIST"
    # Idempotent: bootout an existing one first, so `install` is also `reinstall`.
    launchctl bootout "gui/$UID/$SERVICE" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST"
    echo "installed $SERVICE"
    echo "  logs:    $0 logs $ADDRESS"
    echo "  status:  $0 status $ADDRESS"
    echo "  restart: $0 restart $ADDRESS"
}

cmd_uninstall() {
    parse_address "${1:-}"
    launchctl bootout "gui/$UID/$SERVICE" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $SERVICE. The log stays at $LOGS/$LABEL.log"
}

cmd_restart() {
    parse_address "${1:-}"
    # kickstart -k waits for the old instance. That is a second or two now; it
    # was about two minutes while a bridge left its listener behind.
    launchctl kickstart -k "gui/$UID/$SERVICE"
    echo "restarted $SERVICE"
}

cmd_status() {
    parse_address "${1:-}"
    launchctl print "gui/$UID/$SERVICE" 2>/dev/null \
        | grep -E "state =|pid =|runs =|last exit code =" \
        || die "$SERVICE is not loaded. Install it: $0 install $ADDRESS"
    echo "--- roster ---"
    agent-bus list 2>/dev/null | grep -E "NAME|$LABEL" || echo "  not on the roster"
}

cmd_logs() {
    parse_address "${1:-}"
    local log="$LOGS/$LABEL.log"
    [ -f "$log" ] || die "no log at $log yet"
    tail -f "$log"
}

case "${1:-}" in
    install)   shift; cmd_install "$@" ;;
    uninstall) shift; cmd_uninstall "$@" ;;
    restart)   shift; cmd_restart "$@" ;;
    status)    shift; cmd_status "$@" ;;
    logs)      shift; cmd_logs "$@" ;;
    render)    shift; cmd_render "$@" ;;
    *)         usage ;;
esac
