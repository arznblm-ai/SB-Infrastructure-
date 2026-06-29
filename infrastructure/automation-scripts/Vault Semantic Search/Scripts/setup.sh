#!/usr/bin/env bash
# Vault Semantic Search — one-time setup.
# Creates an isolated venv OUTSIDE the vault and installs local-only deps.
# Nothing here touches the system python that other automations rely on.
set -euo pipefail

ROOT="$HOME/.vault-semantic-search"
VENV="$ROOT/venv"
REQ="$(cd "$(dirname "$0")/.." && pwd)/requirements.txt"

mkdir -p "$ROOT"
if [ ! -d "$VENV" ]; then
  echo "Creating venv at $VENV"
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install -r "$REQ"

echo ""
echo "Setup complete."
echo "  venv python : $VENV/bin/python"
echo "  next        : build the index (see CLAUDE.md → Commands)"
