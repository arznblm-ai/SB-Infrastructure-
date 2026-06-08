#!/usr/bin/env bash
set -euo pipefail

VAULT="/Users/anton/AI AGENT FOLDER/Second Brain"
DAILY_DIR="$VAULT/infrastructure/daily focus"
RUN_DAILY_FOCUS="$DAILY_DIR/Scripts/run_daily_focus.sh"
OUTPUT_DIR="$DAILY_DIR/daily focus messages"
LOG_FILE="/Users/anton/Library/Logs/daily-focus.log"
LOCK_DIR="/tmp/com.user.daily-focus-catchup.lock"

CATCHUP_START_HHMM="${DAILY_FOCUS_CATCHUP_START_HHMM:-1100}"
CATCHUP_END_HHMM="${DAILY_FOCUS_CATCHUP_END_HHMM:-1800}"

usage() {
  cat <<'USAGE'
Usage: daily_focus_catchup.sh [--dry-run]

Runs Daily Focus only if today's brief is missing and the current time is inside
the catch-up window. Default window: 11:00-18:00 local machine time.
USAGE
}

log_line() {
  mkdir -p "$(dirname "$LOG_FILE")"
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] catchup: $*" >> "$LOG_FILE"
}

today_brief_exists() {
  local today="$1"
  find "$OUTPUT_DIR" -maxdepth 1 -type f \( \
    -name "{focus} daily planning brief – ${today}*.md" -o \
    -name "{focus} daily focus brief – ${today}*.md" \
  \) -print -quit | grep -q .
}

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ $# -gt 0 ]]; then
  usage >&2
  exit 2
fi

today="$(date '+%Y-%m-%d')"
now_hhmm="$(date '+%H%M')"

if [[ ! -x "$RUN_DAILY_FOCUS" ]]; then
  log_line "run script missing or not executable: $RUN_DAILY_FOCUS"
  echo "Daily Focus catch-up failed: run script missing or not executable."
  exit 1
fi

if (( 10#$now_hhmm < 10#$CATCHUP_START_HHMM )); then
  log_line "skip; before catch-up window now=$now_hhmm start=$CATCHUP_START_HHMM"
  [[ "$DRY_RUN" -eq 1 ]] && echo "skip: before catch-up window"
  exit 0
fi

if (( 10#$now_hhmm > 10#$CATCHUP_END_HHMM )); then
  log_line "skip; after catch-up window now=$now_hhmm end=$CATCHUP_END_HHMM"
  [[ "$DRY_RUN" -eq 1 ]] && echo "skip: after catch-up window"
  exit 0
fi

if today_brief_exists "$today"; then
  log_line "skip; today's brief already exists for $today"
  [[ "$DRY_RUN" -eq 1 ]] && echo "skip: today's brief already exists"
  exit 0
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log_line "skip; another catch-up/run appears active"
  [[ "$DRY_RUN" -eq 1 ]] && echo "skip: lock already held"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "would run Daily Focus catch-up for $today"
  log_line "dry-run would run Daily Focus for $today"
  exit 0
fi

log_line "running Daily Focus catch-up for $today"
exec "$RUN_DAILY_FOCUS"
