#!/bin/zsh
set -euo pipefail

SYNC_SCRIPT="/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/git-update/scripts/sync_infrastructure_repo.py"
MESSAGE="Weekly Second Brain infrastructure snapshot"
ENV_FILE="${HOME}/.config/second-brain/daily-focus.env"
LOG_HINT="~/Library/Logs/weekly-git-update.log"

OUT_FILE="$(mktemp -t weekly-git-update)"
trap 'rm -f "$OUT_FILE"' EXIT

# --- best-effort Telegram alert ----------------------------------------------
# Only fires on failure. Missing env / no network => line in the log, never fatal.
read_env_value() {
  [[ -f "$ENV_FILE" ]] || return 0
  grep -m1 "^${1}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\042\047' | tr -d '\r' || true
}

notify_failure() {
  ALERT_TEXT="❌ Weekly git-update не прошёл: ${1}. Лог: ${LOG_HINT}"

  BOT_TOKEN="$(read_env_value TELEGRAM_BOT_TOKEN)"
  CHAT_ID="$(read_env_value TELEGRAM_CHAT_ID)"

  if [[ -z "${BOT_TOKEN}" || -z "${CHAT_ID}" ]]; then
    print -r -- "[alert] no Telegram credentials in ${ENV_FILE}; alert NOT sent: ${ALERT_TEXT}"
    return 0
  fi

  # --fail: HTTP 4xx (bad token / wrong chat) must be logged as a failure, not as "sent"
  if curl -sS --fail --max-time 20 \
      -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${CHAT_ID}" \
      --data-urlencode "text=${ALERT_TEXT}" \
      --data-urlencode "disable_web_page_preview=true" >/dev/null 2>&1; then
    print -r -- "[alert] Telegram alert sent: ${ALERT_TEXT}"
  else
    print -r -- "[alert] Telegram alert FAILED to send: ${ALERT_TEXT}"
  fi
  return 0
}

# --- one-line reason from the run output --------------------------------------
failure_reason() {
  if grep -q "SAFETY SCAN FAILED" "$OUT_FILE" 2>/dev/null; then
    # findings live after the marker; earlier "Cleaned forbidden paths" lines also start with "- "
    FINDING="$(sed -n '/SAFETY SCAN FAILED/,$p' "$OUT_FILE" 2>/dev/null | grep -m1 '^- ' | sed 's/^- //' || true)"
    if [[ -n "${FINDING}" ]]; then
      print -r -- "SAFETY SCAN FAILED — ${FINDING[1,160]}"
    else
      print -r -- "SAFETY SCAN FAILED"
    fi
    return 0
  fi

  if [[ "${STATUS}" -eq 3 ]] || grep -q '^Push failed' "$OUT_FILE" 2>/dev/null; then
    print -r -- "push в origin/main не прошёл (коммит остался локально)"
    return 0
  fi

  LAST_LINE="$(grep -v '^[[:space:]]*$' "$OUT_FILE" 2>/dev/null | tail -1 || true)"
  if [[ -n "${LAST_LINE}" ]]; then
    print -r -- "код возврата ${STATUS} — ${LAST_LINE[1,160]}"
  else
    print -r -- "код возврата ${STATUS}, вывода нет"
  fi
}

# --- run ----------------------------------------------------------------------
# tee: log keeps streaming live (survives a mid-run kill), copy is kept for reason extraction
set +e
python3 "$SYNC_SCRIPT" --push --message "$MESSAGE" 2>&1 | tee "$OUT_FILE"
STATUS=${pipestatus[1]}
set -e

if [[ "${STATUS}" -ne 0 ]] || grep -q "SAFETY SCAN FAILED" "$OUT_FILE" 2>/dev/null; then
  notify_failure "$(failure_reason)"
  if [[ "${STATUS}" -ne 0 ]]; then
    exit "${STATUS}"
  fi
  exit 1
fi

exit 0
