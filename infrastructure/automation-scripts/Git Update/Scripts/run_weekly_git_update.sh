#!/bin/zsh
set -euo pipefail

SYNC_SCRIPT="/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/git-update/scripts/sync_infrastructure_repo.py"
MESSAGE="Weekly Second Brain infrastructure snapshot"

python3 "$SYNC_SCRIPT" --push --message "$MESSAGE"
