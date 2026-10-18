#!/usr/bin/env bash
# asya_watchdog.sh — сторож бота Аси (@rznblm_inbox_bot, ADR-030).
#
# Раз в 10 минут (asya-watchdog.timer) смотрит на системный юнит asya.service.
# Бот считается живым, только если юнит active и не перезапускался последние
# MIN_UPTIME секунд: так ловится и ручной stop, и крэш-петля (Restart=on-failure
# + RestartSec=10 не доводит до start limit — юнит мигает activating/active).
#
#   лежит, ещё не алертил   → одно сообщение Антону, запоминаем время простоя;
#   лежит, уже алертил      → молчим (одно сообщение на один простой);
#   снова жив после алерта  → «Ася снова работает», сброс состояния.
#
# Шлёт токеном самой Аси (sendMessage не требует живого полера). Токен читается
# из /root/.config/asya/.env, не логируется и не попадает в argv (URL уходит
# curl через --config со stdin).
#
# Второй, независимый блок — здоровье синка Krisp (мак → health-файл → Syncthing):
#   auth_error            → одно сообщение «Krisp не синкает встречи с …» + как починить;
#   reauth_soon           → одно предупреждение «скоро переавторизация»;
#   снова ok после алерта → одно сообщение о восстановлении.
# Дедуп: одно сообщение на смену статуса (state: $STATE_DIR/krisp-alerted).
# Нет health-файла / битый JSON → молчим, warning в журнал. Блок крутится в
# subshell: его сбой не ломает проверку самого бота.
#
# ASYA_WATCHDOG_DRY_RUN=1 — ничего не отправлять, печатать текст (для проверки).
# Ставится деплоем Scripts/deploy.sh в /usr/local/bin/asya-watchdog.
set -euo pipefail

UNIT=asya.service
CHAT_ID=324186708
ENV_FILE=/root/.config/asya/.env
STATE_DIR=${STATE_DIRECTORY:-/var/lib/asya-watchdog}
STATE_FILE=$STATE_DIR/down-since     # есть файл = алерт про текущий простой уже ушёл
MIN_UPTIME=120                       # сек непрерывной работы, чтобы считать бота живым
PROBES=4                             # сколько раз смотреть, если юнит в переходном состоянии
PROBE_SLEEP=40
KRISP_HEALTH=${KRISP_HEALTH_FILE:-"/root/second-brain/infrastructure/Krisp to obsidian/health/krisp-health.json"}
KRISP_STATE=$STATE_DIR/krisp-alerted # «status|since» последнего ушедшего Krisp-алерта
KRISP_OAUTH_CMD='python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Krisp to obsidian/Scripts/krisp_oauth.py"'
KRISP_CHECK_CMD='python3 ~/.krisp-obsidian/krisp_to_obsidian.py --check-auth'
DRY_RUN=${ASYA_WATCHDOG_DRY_RUN:-0}
export TZ=Europe/Moscow

mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/.lock"
flock -n 9 || { echo "другой прогон watchdog ещё идёт — выхожу"; exit 0; }

AS="" ENTER=0 INACTIVE=0
read_props() {  # заполняет AS / ENTER / INACTIVE; rc≠0 — systemd не ответил
  local out line
  out=$(systemctl show "$UNIT" --timestamp=unix \
          -p ActiveState -p ActiveEnterTimestamp -p InactiveEnterTimestamp 2>/dev/null) || return 1
  AS="" ENTER=0 INACTIVE=0
  while IFS= read -r line; do
    case "$line" in
      ActiveState=*)             AS=${line#ActiveState=} ;;
      ActiveEnterTimestamp=@*)   ENTER=${line#ActiveEnterTimestamp=@} ;;
      InactiveEnterTimestamp=@*) INACTIVE=${line#InactiveEnterTimestamp=@} ;;
    esac
  done <<< "$out"
  ENTER=${ENTER%%.*}; INACTIVE=${INACTIVE%%.*}
  [ -n "$AS" ]
}

read_token() {
  local t
  [ -r "$ENV_FILE" ] || return 1
  t=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?TELEGRAM_BOT_TOKEN[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -1)
  t=$(printf '%s' "$t" | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")
  [ -n "$t" ] || return 1
  TOKEN=$t
}

send() {  # send <text>; rc 0 — Telegram ответил ok:true
  local text="$1" resp
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] → %s: %s\n' "$CHAT_ID" "$text"
    return 0
  fi
  TOKEN=""
  read_token || { echo "✗ нет TELEGRAM_BOT_TOKEN в $ENV_FILE — алерт не отправлен"; return 1; }
  resp=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TOKEN" \
           | curl -sS --max-time 20 --config - \
                  --data-urlencode "chat_id=$CHAT_ID" \
                  --data-urlencode "text=$text" 2>&1) || true
  TOKEN=""
  if printf '%s' "$resp" | grep -q '"ok":[[:space:]]*true'; then
    return 0
  fi
  # ответ Telegram токена не содержит; на всякий случай вырезаем похожее на токен
  echo "✗ sendMessage не прошёл: $(printf '%s' "$resp" | sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/<token>/g' | head -c 300)"
  return 1
}

hhmm() {  # epoch → «HH:MM» сегодня или «DD.MM HH:MM»
  if [ "$(date -d "@$1" +%F)" = "$(date +%F)" ]; then date -d "@$1" +%H:%M; else date -d "@$1" '+%d.%m %H:%M'; fi
}

# ── блок Krisp: здоровье синка встреч ────────────────────────────────────────
krisp_check() {  # запускается в subshell; rc≠0 — только warning, бот проверяется дальше
  local out st since_raw since_h det key prev msg
  if [ ! -f "$KRISP_HEALTH" ]; then
    echo "⚠ krisp: нет health-файла ($KRISP_HEALTH) — молчу"
    return 0
  fi
  # status / since (ISO) / since (человеческое, МСК) / detail — по строке
  if ! out=$(python3 -I -c '
import json, sys
from datetime import datetime
with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
if not isinstance(d, dict):
    sys.exit(2)
since = str(d.get("since") or "")
try:
    human = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone().strftime("%d.%m %H:%M")
except ValueError:
    human = since or "?"
print(str(d.get("status") or ""))
print(" ".join(since.split()))
print(human)
print(" ".join(str(d.get("detail") or "").split())[:200])
' "$KRISP_HEALTH" 2>/dev/null); then
    echo "⚠ krisp: health-файл не читается / битый JSON — молчу"
    return 0
  fi
  { IFS= read -r st; IFS= read -r since_raw; IFS= read -r since_h; IFS= read -r det; } <<< "$out" || true
  key="$st|$since_raw"
  prev=""
  if [ -f "$KRISP_STATE" ]; then prev=$(cat "$KRISP_STATE" 2>/dev/null || true); fi

  case "$st" in
    ok)
      if [ -z "$prev" ]; then
        echo "krisp: ok"
        return 0
      fi
      case "${prev%%|*}" in
        reauth_soon) msg="Krisp: переавторизация сделана, предупреждение снято." ;;
        *)           msg="Krisp снова синкает встречи." ;;
      esac
      if send "$msg"; then
        [ "$DRY_RUN" = 1 ] || rm -f "$KRISP_STATE"   # dry-run состояние не трогает
        echo "krisp: снова ok — сообщение отправлено, состояние сброшено"
      else
        return 1   # состояние не сбрасываем — повторим на следующем тике
      fi
      ;;
    auth_error|reauth_soon)
      if [ "$prev" = "$key" ]; then
        echo "krisp: $st с $since_h, алерт уже отправлен — молчу"
        return 0
      fi
      if [ "$st" = auth_error ]; then
        msg="Krisp не синкает встречи с $since_h: ${det:-auth_error}.
Почини: на маке $KRISP_OAUTH_CMD
Проверка: $KRISP_CHECK_CMD"
      else
        msg="Krisp: ${det:-refresh_token скоро истечёт — нужна переавторизация}.
Сделай на маке: $KRISP_OAUTH_CMD"
      fi
      if send "$msg"; then
        [ "$DRY_RUN" = 1 ] || printf '%s\n' "$key" > "$KRISP_STATE"   # dry-run состояние не трогает
        echo "krisp: $st — алерт отправлен"
      else
        return 1   # не запоминаем — повторим на следующем тике
      fi
      ;;
    *)
      echo "⚠ krisp: неизвестный статус «$st» в health-файле — молчу"
      ;;
  esac
}

( krisp_check ) || echo "⚠ krisp: блок завершился с ошибкой — проверка бота продолжается"

HEALTHY=0
UPTIME=0
for ((i = 1; i <= PROBES; i++)); do
  if ! read_props; then
    AS="unknown (systemctl show не ответил)"
    break
  fi
  NOW=$(date +%s)
  if [ "$AS" = active ] && [ "$ENTER" -gt 0 ]; then
    UPTIME=$((NOW - ENTER))
    if [ "$UPTIME" -ge "$MIN_UPTIME" ]; then
      HEALTHY=1
      break
    fi
  fi
  case "$AS" in
    active|activating|reloading|deactivating)
      # переходное состояние или свежий старт — даём шанс доказать, что не крэш-петля
      if [ "$i" -lt "$PROBES" ]; then sleep "$PROBE_SLEEP"; fi
      ;;
    *) break ;;   # failed / inactive — ждать нечего
  esac
done

if [ "$HEALTHY" = 1 ]; then
  if [ -f "$STATE_FILE" ]; then
    SINCE=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    if send "Ася снова работает (лежала с $(hhmm "${SINCE:-0}"))."; then
      [ "$DRY_RUN" = 1 ] || rm -f "$STATE_FILE"
      echo "восстановилась — сообщение отправлено, состояние сброшено"
    else
      exit 1   # состояние не сбрасываем — повторим на следующем тике
    fi
  else
    echo "ok: $UNIT active, uptime $((UPTIME / 60)) мин"
  fi
  exit 0
fi

# ── бот лежит ───────────────────────────────────────────────────────────────
if [ -f "$STATE_FILE" ]; then
  echo "лежит ($AS), алерт уже отправлен $(hhmm "$(cat "$STATE_FILE")") — молчу"
  exit 0
fi

SINCE=$(date +%s)
case "$AS" in
  failed|inactive) if [ "$INACTIVE" -gt 0 ]; then SINCE=$INACTIVE; fi ;;
esac

MSG="Ася лежит с $(hhmm "$SINCE"). Подними: systemctl restart asya
Состояние юнита: $AS. Лог: tail -50 /root/.config/asya/bot.log"
if send "$MSG"; then
  [ "$DRY_RUN" = 1 ] || echo "$SINCE" > "$STATE_FILE"
  echo "лежит ($AS) — алерт отправлен"
else
  exit 1   # не запоминаем — повторим на следующем тике
fi
