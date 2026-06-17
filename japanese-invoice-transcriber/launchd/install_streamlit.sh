#!/usr/bin/env bash
# Install the Streamlit invoice UI as a macOS launchd user agent.
#
# What this does:
#   - Substitutes real paths into the .plist template
#   - Copies the result to ~/Library/LaunchAgents/
#   - Loads it via launchctl bootstrap so it starts at login + auto-respawns
#
# After running: open http://localhost:8501 (or your tunnel hostname).
#
# Re-run this after the project moves directories or uv path changes.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEMPLATE="$PROJECT_ROOT/launchd/com.paststudies.invoice.streamlit.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.paststudies.invoice.streamlit.plist"
LABEL="com.paststudies.invoice.streamlit"

UV_PATH="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV_PATH" ]]; then
    echo "✗ Couldn't find 'uv' on PATH. Install uv first:"
    echo "    brew install uv"
    exit 1
fi

mkdir -p "$PROJECT_ROOT/buyee/state"

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
launchctl kickstart -k "gui/$UID/$LABEL"

echo
echo "✓ Streamlit UI installed. To check status:"
echo "    launchctl print gui/$UID/$LABEL | head -20"
echo
echo "  Logs:"
echo "    tail -f $PROJECT_ROOT/buyee/state/streamlit.log"
echo "    tail -f $PROJECT_ROOT/buyee/state/streamlit.err.log"
echo
echo "  Stop:"
echo "    launchctl bootout gui/$UID/$LABEL"
echo
echo "  Open the UI at http://localhost:8501 (give it ~10s after install)."
