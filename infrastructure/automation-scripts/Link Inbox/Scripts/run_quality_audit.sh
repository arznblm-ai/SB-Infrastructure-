#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Link Inbox"
ENV_FILE="$HOME/.config/link-inbox/env"

if [[ -f "$ENV_FILE" ]]; then
  source "$ENV_FILE"
fi

cd "$PROJECT_DIR"
python3 "$PROJECT_DIR/Scripts/link_quality_audit.py"
