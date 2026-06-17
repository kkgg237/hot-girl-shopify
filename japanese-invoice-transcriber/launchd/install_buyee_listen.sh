#!/usr/bin/env bash
# Install the Telegram bot listener as a macOS launchd user agent.
#
# After running:
#   - Bot runs at login + auto-restarts if it crashes (KeepAlive=true)
#   - You can send commands to the bot from your phone any time
#   - When you update the bot's code, restart it with:
#       launchctl kickstart -k gui/$UID/com.paststudies.buyee.listen

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEMPLATE="$PROJECT_ROOT/launchd/com.paststudies.buyee.listen.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.paststudies.buyee.listen.plist"
LABEL="com.paststudies.buyee.listen"

UV_PATH="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV_PATH" ]]; then
    echo "✗ Couldn't find 'uv' on PATH. Install uv first:"
    echo "    brew install uv"
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "✗ Template not found: $TEMPLATE"
    exit 1
fi

mkdir -p "$PROJECT_ROOT/buyee/state"
mkdir -p "$(dirname "$TARGET")"

echo "Project root: $PROJECT_ROOT"
echo "uv path:      $UV_PATH"
echo "Target plist: $TARGET"

sed -e "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" \
    -e "s|{{UV_PATH}}|$UV_PATH|g" \
    "$TEMPLATE" > "$TARGET"

if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing agent..."
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
fi

echo "Bootstrapping launchd agent..."
launchctl bootstrap "gui/$UID" "$TARGET"
launchctl kickstart -k "gui/$UID/$LABEL"

echo
echo "✓ Bot is now running and will auto-restart if it crashes."
echo
echo "  Check status:"
echo "    launchctl print gui/$UID/$LABEL | grep -E 'state|last exit code'"
echo
echo "  Tail logs:"
echo "    tail -f $PROJECT_ROOT/buyee/state/listen.log"
echo
echo "  After future code changes, restart with:"
echo "    launchctl kickstart -k gui/$UID/$LABEL"
echo
echo "  Uninstall later:"
echo "    launchctl bootout gui/$UID/$LABEL && rm $TARGET"
