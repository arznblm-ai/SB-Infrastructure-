#!/bin/zsh
set -euo pipefail

CONFIG_DIR="$HOME/.config/link-inbox"
ENV_FILE="$CONFIG_DIR/env"
PROJECT_DIR="/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox"

if [[ -f "$ENV_FILE" ]]; then
  source "$ENV_FILE"
fi

cd "$PROJECT_DIR"
exec python3 Scripts/link_inbox_runner.py --bot-only "$@"
