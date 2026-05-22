#!/usr/bin/env bash
# Install the notes-digest scheduler as a macOS launchd user agent.
# Sends a Telegram digest of stale pending feedback notes every Mon/Wed/Fri
# at 9:00am. Silent when no notes are pending >= 2 days old.
#
# Re-run this after the project moves directories or uv path changes.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
TEMPLATE="$PROJECT_ROOT/buyee/launchd/com.paststudies.buyee.notesdigest.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.paststudies.buyee.notesdigest.plist"
LABEL="com.paststudies.buyee.notesdigest"

UV_PATH="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV_PATH" ]]; then
    echo "✗ Couldn't find 'uv' on PATH. Install uv first."
    exit 1
fi

echo "Project root: $PROJECT_ROOT"
echo "uv path:      $UV_PATH"
echo "Target:       $TARGET"

mkdir -p "$(dirname "$TARGET")"
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
echo "✓ Notes digest scheduled."
echo "  Runs Mon/Wed/Fri at 9:00am."
echo "  Sends a Telegram digest of pending notes >= 2 days old."
echo "  Silent when nothing is stale."
echo
echo "  Test now:"
echo "    cd $PROJECT_ROOT && uv run --with playwright --with pyyaml --with pydantic --with python-dotenv python -m buyee notes-digest --dry-run"
echo
echo "  Run immediately (skips schedule):"
echo "    launchctl kickstart -k gui/\$UID/$LABEL"
echo
echo "  Stop:"
echo "    launchctl bootout gui/\$UID/$LABEL"
