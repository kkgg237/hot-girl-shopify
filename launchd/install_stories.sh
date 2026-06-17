#!/usr/bin/env bash
# Install the Shopify→IG story review UI as a macOS launchd user agent.
#
# What this does:
#   - Substitutes real paths (npm + node bin dir) into the .plist template
#   - Copies the result to ~/Library/LaunchAgents/
#   - Loads it via launchctl bootstrap so it starts at login + auto-respawns
#
# Pair with the shared Cloudflare Tunnel (see launchd/install_tunnel_stories.sh)
# to expose it at https://stories.paststudies-tools.com.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEMPLATE="$PROJECT_ROOT/launchd/com.paststudies.stories.review.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.paststudies.stories.review.plist"
LABEL="com.paststudies.stories.review"

NPM_PATH="$(command -v npm 2>/dev/null || true)"
if [[ -z "$NPM_PATH" ]]; then
    echo "✗ Couldn't find 'npm' on PATH."
    exit 1
fi
NODE_BIN_DIR="$(dirname "$NPM_PATH")"

mkdir -p "$PROJECT_ROOT/launchd/state"

echo "Project root: $PROJECT_ROOT"
echo "npm path:     $NPM_PATH"
echo "Node bin dir: $NODE_BIN_DIR"
echo "Target:       $TARGET"

mkdir -p "$(dirname "$TARGET")"
sed -e "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" \
    -e "s|{{NPM_PATH}}|$NPM_PATH|g" \
    -e "s|{{NODE_BIN_DIR}}|$NODE_BIN_DIR|g" \
    "$TEMPLATE" > "$TARGET"

if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing agent..."
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
fi

echo "Bootstrapping launchd agent..."
launchctl bootstrap "gui/$UID" "$TARGET"
launchctl kickstart -k "gui/$UID/$LABEL"

echo
echo "✓ Stories review UI installed. Status:"
echo "    launchctl print gui/$UID/$LABEL | head -20"
echo
echo "  Logs:"
echo "    tail -f $PROJECT_ROOT/launchd/state/stories.log"
echo "    tail -f $PROJECT_ROOT/launchd/state/stories.err.log"
echo
echo "  Stop:"
echo "    launchctl bootout gui/$UID/$LABEL"
echo
echo "  Local:  http://localhost:3001"
echo "  Public: https://stories.paststudies-tools.com (after tunnel update)"
