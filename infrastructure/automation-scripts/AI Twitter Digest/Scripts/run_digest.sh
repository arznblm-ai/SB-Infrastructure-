#!/bin/bash
# run_digest.sh — оркестратор AI Twitter Digest.
#
# Накопительный режим (штатный): частый fetch копит твиты в спул, редкая
# digest-фаза раз в 2-3 суток строит и шлёт один выпуск из накопленного.
#
#   run_digest.sh --fetch-only   # fetch-фаза: спул + коммит last_seen_id, без LLM и отправки
#   run_digest.sh --digest-only  # digest-фаза: гейт каденции → build → send → очистка спула
#   run_digest.sh                # legacy: полный прогон fetch → build → send (ручные тесты)
#   run_digest.sh --dry-run      # напечатать дайджест в stdout, ничего не слать и не чистить
#   run_digest.sh --window-hours 24
#   run_digest.sh --no-llm       # выпуск без вызова claude (дешёвый прогон)
#
# --dry-run и --no-llm относятся к digest-части и в --fetch-only игнорируются.
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
SPOOL="${SCRIPTS_DIR}/spool.py"

DRY_RUN=0
NO_LLM=0
MODE="legacy"          # legacy | fetch | digest
EXTRA_FETCH_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fetch-only) MODE="fetch"; shift ;;
    --digest-only) MODE="digest"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-llm) NO_LLM=1; shift ;;
    --window-hours) EXTRA_FETCH_ARGS+=("--window-hours" "${2:-12}"); shift 2 ;;
    --ignore-state) EXTRA_FETCH_ARGS+=("--ignore-state"); shift ;;
    --limit) EXTRA_FETCH_ARGS+=("--limit" "${2:-40}"); shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
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

log "старт (mode=${MODE}, dry_run=${DRY_RUN}, no_llm=${NO_LLM})"

# Сборка дайджеста из готового JSON: build → send → (для digest-фазы) очистка спула.
build_and_send() {
  local phase="$1"
  local build_args=("--in" "$TWEETS_JSON" "--out" "$DIGEST_TXT")
  [[ "$NO_LLM" == "1" ]] && build_args+=("--no-llm")
  "$VENV_PY" "$BUILDER" "${build_args[@]}" >>"$LOG_FILE" 2>&1
  local build_rc=$?
  if [[ "$build_rc" != "0" || ! -s "$DIGEST_TXT" ]]; then
    log "сборка дайджеста упала (rc=${build_rc})"
    alert_once "❌ AI Twitter Digest: не смог собрать выпуск (builder rc=${build_rc}).
Лог: ~/Library/Logs/ai-twitter-digest.log"
    return 1
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    log "dry-run: ничего не отправляю, спул не трогаю, печатаю дайджест"
    cat "$DIGEST_TXT"
    return 0
  fi

  "$VENV_PY" "$SENDER" --file "$DIGEST_TXT" >>"$LOG_FILE" 2>&1
  local send_rc=$?
  if [[ "$send_rc" != "0" ]]; then
    if [[ "$phase" == "digest" ]]; then
      log "отправка не удалась (rc=${send_rc}); спул НЕ чищу — следующий прогон повторит"
    else
      log "отправка не удалась (rc=${send_rc}); state не коммичу — твиты попадут в следующий выпуск"
    fi
    return 1
  fi
  return 0
}

# ── Накопительный режим: fetch-фаза ───────────────────────────────────────
if [[ "$MODE" == "fetch" ]]; then
  "$VENV_PY" "$FETCH" --spool "${EXTRA_FETCH_ARGS[@]+"${EXTRA_FETCH_ARGS[@]}"}" >>"$LOG_FILE" 2>&1
  FETCH_RC=$?
  if [[ "$FETCH_RC" != "0" ]]; then
    log "fetch упал (rc=${FETCH_RC})"
    alert_once "❌ AI Twitter Digest: не смог прочитать ленту (fetch rc=${FETCH_RC}).
Возможные причины: протух логин X в twscrape, X поменял GraphQL, пустой config/accounts.json.
Лог: ~/Library/Logs/ai-twitter-digest.log
Повторные попытки логина не делаю — проверь вручную: twscrape accounts"
    exit "$FETCH_RC"
  fi
  log "fetch-фаза: спул пополнен, state закоммичен"
  exit 0
fi

# ── Накопительный режим: digest-фаза ──────────────────────────────────────
if [[ "$MODE" == "digest" ]]; then
  # Гейт каденции — детерминированный код до любого LLM.
  "$VENV_PY" "$SPOOL" --gate >>"$LOG_FILE" 2>&1
  GATE_RC=$?
  if [[ "$GATE_RC" == "10" ]]; then
    log "гейт каденции: выпуск сегодня не собираю"
    exit 0
  fi
  if [[ "$GATE_RC" != "0" ]]; then
    log "гейт каденции упал (rc=${GATE_RC})"
    exit 1
  fi

  "$VENV_PY" "$SPOOL" --materialize "$TWEETS_JSON" >>"$LOG_FILE" 2>&1
  MATERIALIZE_RC=$?
  if [[ "$MATERIALIZE_RC" != "0" || ! -s "$TWEETS_JSON" ]]; then
    log "спул не материализовался (rc=${MATERIALIZE_RC})"
    alert_once "❌ AI Twitter Digest: не смог прочитать спул накопленных твитов (rc=${MATERIALIZE_RC}).
Лог: ~/Library/Logs/ai-twitter-digest.log"
    exit 1
  fi

  build_and_send "digest" || exit 1
  if [[ "$DRY_RUN" == "1" ]]; then
    exit 0
  fi

  # Только после успешной отправки: спул и накопленные ошибки чистятся.
  "$VENV_PY" "$SPOOL" --clear >>"$LOG_FILE" 2>&1 || log "очистка спула не удалась"
  "$VENV_PY" "$FETCH" --alert-state clear >/dev/null 2>&1
  log "выпуск отправлен"
  exit 0
fi

# ── Legacy: полный прогон одним заходом (ручные тесты) ────────────────────
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

# ── 2-3. Сборка дайджеста (один вызов claude) и отправка ──────────────────
build_and_send "legacy" || exit 1
if [[ "$DRY_RUN" == "1" ]]; then
  exit 0
fi

# ── 4. Только после успешной отправки двигаем state и гасим флаг алерта ────
"$VENV_PY" "$FETCH" --commit-state "$TWEETS_JSON" >>"$LOG_FILE" 2>&1 || log "commit-state не удался"
"$VENV_PY" "$FETCH" --alert-state clear >/dev/null 2>&1
log "выпуск отправлен"
exit 0
