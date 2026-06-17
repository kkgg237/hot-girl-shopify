#!/usr/bin/env bash
# Install the weekly Shopify snapshot as a macOS launchd user agent.
#
# What this does:
#   - Substitutes real paths into the .plist template
#   - Copies the result to ~/Library/LaunchAgents/
#   - Bootstraps it via launchctl so it runs every Monday 8am local time
#
# Run a one-off snapshot immediately after install (optional):
#   launchctl kickstart -k gui/$UID/com.paststudies.weekly_snapshot
#
# Re-run this script after the project moves directories or uv path changes.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEMPLATE="$PROJECT_ROOT/launchd/com.paststudies.weekly_snapshot.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.paststudies.weekly_snapshot.plist"
LABEL="com.paststudies.weekly_snapshot"

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

mkdir -p "$PROJECT_ROOT/buyee/state/snapshots"
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

echo
echo "✓ Weekly snapshot scheduled — every Monday 8:00am local time."
echo
echo "  Trigger an immediate one-off snapshot now (optional):"
echo "    launchctl kickstart -k gui/$UID/$LABEL"
echo
echo "  Check status:"
echo "    launchctl print gui/$UID/$LABEL | grep -E 'state|last exit code'"
echo
echo "  Tail logs as snapshots run:"
echo "    tail -f $PROJECT_ROOT/buyee/state/weekly_snapshot.log"
echo
echo "  Uninstall later:"
echo "    launchctl bootout gui/$UID/$LABEL && rm $TARGET"
