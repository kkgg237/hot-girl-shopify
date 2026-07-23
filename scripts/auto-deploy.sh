#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/kat/workspace/hot-girl-shopify"
BRANCH="main"
SERVICE_NAME="invoice-streamlit.service"

cd "$REPO_DIR"

git fetch origin "$BRANCH" >/dev/null 2>&1 || exit 0

LOCAL_HASH=$(git rev-parse HEAD)
REMOTE_HASH=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] New commits detected on $BRANCH ($LOCAL_HASH -> $REMOTE_HASH). Deploying..."
    git pull origin "$BRANCH"
    sudo systemctl restart "$SERVICE_NAME"
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Auto-deploy complete!"
fi
