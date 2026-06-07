#!/bin/zsh
set -euo pipefail

VAULT="/Users/anton/AI AGENT FOLDER/Second Brain"
CODEX="/Applications/Codex.app/Contents/Resources/codex"
LOG="/Users/anton/Library/Logs/personal-context-updater.log"

mkdir -p "$(dirname "$LOG")"

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "Personal Context Updater dry run"
  echo "Vault: $VAULT"
  echo "Codex: $CODEX"
  echo "Log: $LOG"
  exit 0
fi

if [[ ! -x "$CODEX" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') Codex executable not found: $CODEX" >> "$LOG"
  exit 1
fi

cd "$VAULT"

"$CODEX" exec \
  --cd "$VAULT" \
  --skip-git-repo-check \
  --sandbox danger-full-access \
  --output-last-message "$LOG.last-message.md" \
  - <<'PROMPT' >> "$LOG" 2>&1
You are running the weekly Personal Context Updater automation.

Read and follow:
/Users/anton/AI AGENT FOLDER/Second Brain/claude.md
/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Personal Context Updater/CLAUDE.md

Task:
Update the living context file from recent meeting summaries:
/Users/anton/AI AGENT FOLDER/Second Brain/context/{self} {research} living context updates – 2026-05-23.md

Rules:
- Use meetings/index.md first.
- Analyze the last 7 days of meeting summaries.
- If fewer than 3 meaningful meeting summaries exist, expand to 14 days and say so.
- Extract only durable, source-backed updates about Anton.
- Do not write advice, strategy, priorities, or recommendations.
- Do not edit the canonical personal profile.
- Preserve previous weekly sections.
- Update Current snapshot only when evidence is strong.
- Put weak signals into Open questions / unstable signals or Candidate updates needing review.
- Every material update must include source paths.

Final response should include:
- source window
- number of meeting files reviewed
- whether the living context changed
- path to living context
- candidate updates needing Anton review, if any
PROMPT
