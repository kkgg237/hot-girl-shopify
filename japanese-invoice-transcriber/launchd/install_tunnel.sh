#!/usr/bin/env bash
# Set up a Cloudflare Tunnel that exposes the Streamlit invoice UI
# (http://localhost:8501) at a public hostname, and install it as a
# launchd user agent so it runs at login and auto-restarts.
#
# Prerequisites:
#   - A domain already added to your Cloudflare account
#   - `cloudflared` installed (`brew install cloudflared`)
#
# Usage:
#   ./launchd/install_tunnel.sh <hostname>           # e.g. invoices.paststudies.com
#   ./launchd/install_tunnel.sh <hostname> <tname>   # custom tunnel name (default: invoice-ui)
#
# This script is idempotent — re-run it any time. It will:
#   1. cloudflared tunnel login (only if you haven't logged in yet)
#   2. Create the named tunnel (or reuse an existing one with the same name)
#   3. Write ~/.cloudflared/config.yml mapping hostname → localhost:8501
#   4. Route the hostname's DNS through the tunnel
#   5. Render + bootstrap the launchd plist

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <hostname> [tunnel-name]"
    echo "Example: $0 invoices.paststudies.com"
    exit 1
fi

HOSTNAME="$1"
TUNNEL_NAME="${2:-invoice-ui}"
LOCAL_URL="http://localhost:8501"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEMPLATE="$PROJECT_ROOT/launchd/com.paststudies.invoice.tunnel.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.paststudies.invoice.tunnel.plist"
LABEL="com.paststudies.invoice.tunnel"
CF_DIR="$HOME/.cloudflared"
CONFIG_FILE="$CF_DIR/config.yml"

CLOUDFLARED_PATH="$(command -v cloudflared 2>/dev/null || true)"
if [[ -z "$CLOUDFLARED_PATH" ]]; then
    echo "✗ cloudflared not found. Install it first:"
    echo "    brew install cloudflared"
    exit 1
fi

mkdir -p "$CF_DIR" "$PROJECT_ROOT/buyee/state"

# 1. Login if no cert.pem exists yet (interactive — opens browser)
if [[ ! -f "$CF_DIR/cert.pem" ]]; then
    echo "→ Step 1: Cloudflare login"
    echo "  A browser will open. Pick the zone for $HOSTNAME and authorize."
    "$CLOUDFLARED_PATH" tunnel login
fi

# 2. Create tunnel if it doesn't exist
if "$CLOUDFLARED_PATH" tunnel list --output json 2>/dev/null | grep -q "\"name\":\"$TUNNEL_NAME\""; then
    echo "→ Tunnel '$TUNNEL_NAME' already exists, reusing."
else
    echo "→ Step 2: Creating tunnel '$TUNNEL_NAME'"
    "$CLOUDFLARED_PATH" tunnel create "$TUNNEL_NAME"
fi

TUNNEL_ID="$("$CLOUDFLARED_PATH" tunnel list --output json | python3 -c "import json,sys; print(next(t['id'] for t in json.load(sys.stdin) if t['name']=='$TUNNEL_NAME'))")"
CREDS_FILE="$CF_DIR/$TUNNEL_ID.json"

if [[ ! -f "$CREDS_FILE" ]]; then
    echo "✗ Credentials file not found: $CREDS_FILE"
    echo "  Try: cloudflared tunnel delete $TUNNEL_NAME && re-run this script"
    exit 1
fi

# 3. Write config.yml
echo "→ Step 3: Writing $CONFIG_FILE"
cat > "$CONFIG_FILE" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CREDS_FILE

ingress:
  - hostname: $HOSTNAME
    service: $LOCAL_URL
  - service: http_status:404
EOF

# 4. Route DNS
echo "→ Step 4: Routing $HOSTNAME → tunnel"
"$CLOUDFLARED_PATH" tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" || \
    echo "  (DNS route may already exist — continuing.)"

# 5. Render + install launchd plist
echo "→ Step 5: Installing launchd agent"
sed -e "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" \
    -e "s|{{CLOUDFLARED_PATH}}|$CLOUDFLARED_PATH|g" \
    -e "s|{{TUNNEL_NAME}}|$TUNNEL_NAME|g" \
    -e "s|{{HOME}}|$HOME|g" \
    "$TEMPLATE" > "$TARGET"

if launchctl list | grep -q "$LABEL"; then
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
fi
launchctl bootstrap "gui/$UID" "$TARGET"
launchctl kickstart -k "gui/$UID/$LABEL"

echo
echo "✓ Tunnel running. Once DNS propagates (usually seconds), open:"
echo "    https://$HOSTNAME"
echo
echo "  Logs:"
echo "    tail -f $PROJECT_ROOT/buyee/state/tunnel.log"
echo "    tail -f $PROJECT_ROOT/buyee/state/tunnel.err.log"
echo
echo "  Stop:"
echo "    launchctl bootout gui/$UID/$LABEL"
echo
echo "  Next: gate the URL behind Cloudflare Access (free for small teams)"
echo "  → https://one.dash.cloudflare.com/  → Zero Trust → Access → Applications"
