#!/bin/bash
# run_digest.sh — оркестратор выпуска AI Twitter Digest: fetch → build → send.
#
#   run_digest.sh                # обычный выпуск (fetch, дайджест, отправка в TG)
#   run_digest.sh --dry-run      # напечатать дайджест в stdout, ничего не слать
#   run_digest.sh --window-hours 24
#   run_digest.sh --no-llm       # выпуск без вызова claude (дешёвый прогон)
#
# Лог: ~/Library/Logs/ai-twitter-digest.log
# Все пути абсолютные, cwd не важен.

set -uo pipefail

PROJECT_DIR="/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/AI Twitter Digest"
SCRIPTS_DIR="${PROJECT_DIR}/Scripts"
RUNTIME_DIR="${HOME}/.config/ai-twitter-digest"
VENV_PY="${RUNTIME_DIR}/venv/bin/python3"
LOG_FILE="${HOME}/Library/Logs/ai-twitter-digest.log"

FETCH="${SCRIPTS_DIR}/fetch_tweets.py"
BUILDER="${SCRIPTS_DIR}/digest_builder.py"
SENDER="${SCRIPTS_DIR}/send_digest.py"

DRY_RUN=0
NO_LLM=0
EXTRA_FETCH_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-llm) NO_LLM=1; shift ;;
    --window-hours) EXTRA_FETCH_ARGS+=("--window-hours" "${2:-12}"); shift 2 ;;
    --ignore-state) EXTRA_FETCH_ARGS+=("--ignore-state"); shift ;;
    --limit) EXTRA_FETCH_ARGS+=("--limit" "${2:-40}"); shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '[%s] run: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG_FILE"
}

if [[ ! -x "$VENV_PY" ]]; then
  log "нет venv: ${VENV_PY}. Запусти Scripts/setup.sh"
  exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ai-twitter-digest.XXXXXX")"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

TWEETS_JSON="${TMP_DIR}/tweets.json"
DIGEST_TXT="${TMP_DIR}/digest.txt"

# Один ❌-алерт на серию поломок: флаг живёт в state.json, не спамим каждый прогон.
alert_once() {
  local message="$1"
  local flag
  flag="$("$VENV_PY" "$FETCH" --alert-state get 2>/dev/null || echo 0)"
  if [[ "$flag" == "1" ]]; then
    log "алерт уже отправлен ранее, повтор не шлю"
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    log "dry-run: алерт не отправляю: ${message}"
    return 0
  fi
  if "$VENV_PY" "$SENDER" --text "$message" >>"$LOG_FILE" 2>&1; then
    "$VENV_PY" "$FETCH" --alert-state set >/dev/null 2>&1
    log "алерт отправлен"
  else
    log "алерт отправить не удалось"
  fi
}

log "старт (dry_run=${DRY_RUN}, no_llm=${NO_LLM})"

# ── 1. Сбор твитов ────────────────────────────────────────────────────────
"$VENV_PY" "$FETCH" --out "$TWEETS_JSON" "${EXTRA_FETCH_ARGS[@]+"${EXTRA_FETCH_ARGS[@]}"}" >>"$LOG_FILE" 2>&1
FETCH_RC=$?
if [[ "$FETCH_RC" != "0" ]]; then
  log "fetch упал (rc=${FETCH_RC})"
  alert_once "❌ AI Twitter Digest: не смог прочитать ленту (fetch rc=${FETCH_RC}).
Возможные причины: протух логин X в twscrape, X поменял GraphQL, пустой config/accounts.json.
Лог: ~/Library/Logs/ai-twitter-digest.log
Повторные попытки логина не делаю — проверь вручную: twscrape accounts"
  exit "$FETCH_RC"
fi

# ── 2. Сборка дайджеста (один вызов claude) ───────────────────────────────
BUILD_ARGS=("--in" "$TWEETS_JSON" "--out" "$DIGEST_TXT")
[[ "$NO_LLM" == "1" ]] && BUILD_ARGS+=("--no-llm")
"$VENV_PY" "$BUILDER" "${BUILD_ARGS[@]}" >>"$LOG_FILE" 2>&1
BUILD_RC=$?
if [[ "$BUILD_RC" != "0" || ! -s "$DIGEST_TXT" ]]; then
  log "сборка дайджеста упала (rc=${BUILD_RC})"
  alert_once "❌ AI Twitter Digest: не смог собрать выпуск (builder rc=${BUILD_RC}).
Лог: ~/Library/Logs/ai-twitter-digest.log"
  exit 1
fi

# ── 3. Отправка ───────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run: ничего не отправляю, печатаю дайджест"
  cat "$DIGEST_TXT"
  exit 0
fi

"$VENV_PY" "$SENDER" --file "$DIGEST_TXT" >>"$LOG_FILE" 2>&1
SEND_RC=$?
if [[ "$SEND_RC" != "0" ]]; then
  log "отправка не удалась (rc=${SEND_RC}); state не коммичу — твиты попадут в следующий выпуск"
  exit 1
fi

# ── 4. Только после успешной отправки двигаем state и гасим флаг алерта ────
"$VENV_PY" "$FETCH" --commit-state "$TWEETS_JSON" >>"$LOG_FILE" 2>&1 || log "commit-state не удался"
"$VENV_PY" "$FETCH" --alert-state clear >/dev/null 2>&1
log "выпуск отправлен"
exit 0
