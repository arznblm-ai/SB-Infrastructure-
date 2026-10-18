#!/usr/bin/env bash
# Деплой Аси (@rznblm_inbox_bot, ADR-030) с мака на VPS 163.5.29.10. Запускает Антон:
#   bash "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Asya/Scripts/deploy.sh"
#
# Идемпотентно: гонять сколько угодно раз, повтор говорит «= уже». Ничего не удаляет.
# Секреты не печатаются и не едут на мак: токен Аси копируется НА СЕРВЕРЕ из
# /root/.hermes/profiles/inbox/.env (тот же бот), в argv не попадает.
#
# Шаги (на мак — только проверки и сборка CLAUDE.md, всё остальное на VPS):
#   [0] мак: файлы на месте, py_compile, bash -n, JSON; ssh и claude на VPS; заливка в /opt/asya/.staging
#   [1] Hermes: оба user-gateway disable --now + reset-failed, маркер архива — ДО Аси (один токен — один полер)
#   [2] код Аси в /opt/asya + venv (python-telegram-bot, faster-whisper), workspace: CLAUDE.md, settings.json, mcp.json
#   [3] /root/.config/asya/.env (600): создаётся один раз, существующий не перезаписывается
#   [4] todoist-mcp server.py из vault с бэкапом *.bak-2026-10-16-asya
#   [5] permcheck.py на VPS-версии claude (фейковый API, квоту не тратит); провален → Асю НЕ запускаем
#   [6] юниты asya.service + asya-watchdog.timer; рестарт только если что-то поменялось или юнит не active
#   [7] meeting-notes-delivery.env → токен Аси (файлом), chat 324186708, без thread — с бэкапом
#   [8] смоук: is-active, getMe (только ok/username), хвост bot.log без токенов, сводка
#
# Память Аси (infrastructure/Asya/memory/) живёт в vault-зеркале /root/second-brain — её
# привозит Syncthing, деплой её не копирует. Откат — в комментарии в конце файла.

set -euo pipefail

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 $VPS"
RSH="ssh -i $KEY -o BatchMode=yes"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"                    # infrastructure/Asya
VAULT_DIR="$(cd "$PROJECT_DIR/../.." && pwd)"                  # корень vault
TODOIST_SRC="$VAULT_DIR/infrastructure/daily focus/deploy/todoist_mcp_server.py"
STAGING="/opt/asya/.staging"
CLAUDE_BIN="/root/.local/bin/claude"

fail() { echo "  ✗ $*" >&2; exit 1; }

echo "[0] Проверяю локальные файлы (до любых изменений на сервере)"
for f in \
  "$SCRIPT_DIR/asya_bot.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/permcheck.py" \
  "$SCRIPT_DIR/asya.service" "$SCRIPT_DIR/asya_watchdog.sh" \
  "$SCRIPT_DIR/asya-watchdog.service" "$SCRIPT_DIR/asya-watchdog.timer" \
  "$PROJECT_DIR/persona/asya.md" "$PROJECT_DIR/config/workspace-footer.md" \
  "$PROJECT_DIR/config/env.example" "$PROJECT_DIR/config/settings.json" "$PROJECT_DIR/config/mcp.json" \
  "$TODOIST_SRC"
do
  [ -s "$f" ] || fail "нет файла $f"
done
python3 -m py_compile "$SCRIPT_DIR/asya_bot.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/permcheck.py" "$TODOIST_SRC" \
  || fail "код не компилируется — чини до деплоя"
bash -n "$SCRIPT_DIR/asya_watchdog.sh" || fail "asya_watchdog.sh: синтаксическая ошибка"
python3 -c 'import json,sys; [json.load(open(p)) for p in sys.argv[1:]]' \
  "$PROJECT_DIR/config/settings.json" "$PROJECT_DIR/config/mcp.json" || fail "битый JSON в config/"
grep -q "def todoist_add_task" "$TODOIST_SRC" || fail "в todoist_mcp_server.py нет todoist_add_task — не та версия"
echo "  ✓ файлы на месте, код компилируется"

$SSH "test -x '$CLAUDE_BIN'" || fail "нет ssh до VPS или нет исполняемого $CLAUDE_BIN"
echo "  ✓ VPS отвечает, $CLAUDE_BIN на месте"

TMP_DIR="$(mktemp -d -t asya-deploy)"
trap 'rm -rf "$TMP_DIR"' EXIT
{
  cat "$PROJECT_DIR/persona/asya.md"
  echo
  cat "$PROJECT_DIR/config/workspace-footer.md"
} > "$TMP_DIR/CLAUDE.md"
cp "$SCRIPT_DIR/asya_bot.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/permcheck.py" \
   "$SCRIPT_DIR/asya.service" "$SCRIPT_DIR/asya_watchdog.sh" \
   "$SCRIPT_DIR/asya-watchdog.service" "$SCRIPT_DIR/asya-watchdog.timer" \
   "$PROJECT_DIR/config/env.example" "$PROJECT_DIR/config/settings.json" "$PROJECT_DIR/config/mcp.json" \
   "$TMP_DIR/"
cp "$TODOIST_SRC" "$TMP_DIR/todoist_mcp_server.py"
echo "  ✓ workspace/CLAUDE.md собран: $(wc -l < "$TMP_DIR/CLAUDE.md" | tr -d ' ') строк (persona + footer)"

$SSH "mkdir -p '$STAGING' && chmod 700 '$STAGING'"
rsync -a --checksum -e "$RSH" "$TMP_DIR"/ "$VPS:$STAGING/"
echo "  ✓ залито в $STAGING (боевые пути не тронуты)"

# ── всё остальное — на VPS одним прогоном ─────────────────────────────────────
set +e
$SSH 'bash -s' <<'REMOTE'
set -uo pipefail
umask 022

STAGING=/opt/asya/.staging
CODE=/opt/asya
VENV=/opt/asya/venv
HOME_DIR=/var/lib/asya
WS=/var/lib/asya/workspace
CFG=/root/.config/asya
ENV_FILE=/root/.config/asya/.env
INBOX_ENV=/root/.hermes/profiles/inbox/.env
VAULT=/root/second-brain
CLAUDE_BIN=/root/.local/bin/claude
ANTON_ID=324186708
STAMP=2026-10-16
MARKER=/root/.hermes/ARCHIVED-2026-10-16.md
TODOIST_DST=/root/tools/todoist-mcp/server.py
TODOIST_PY=/root/tools/todoist-mcp/venv/bin/python
MN_SCRIPT="$VAULT/infrastructure/daily focus/Scripts/meeting_notes.py"
MN_ENV=/root/.config/second-brain/meeting-notes-delivery.env
TOKEN_RE='^[0-9]{5,}:[A-Za-z0-9_-]{20,}$'

export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus

PROBLEMS=()
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; PROBLEMS+=("$*"); }
warn() { printf '  ⚠ %s\n' "$*"; }
skip() { printf '  = %s\n' "$*"; }
redact() { sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/<token>/g'; }

# install_if_changed <src> <dst> <mode> → rc 0 = поставлен новый, rc 1 = уже совпадал
install_if_changed() {
  if [ -f "$2" ] && cmp -s "$1" "$2"; then return 1; fi
  install -D -m "$3" "$1" "$2"
  return 0
}

# значение KEY из env-файла в переменную окружения процесса (не в argv, не в вывод)
read_env_value() {  # read_env_value <file> <KEY>
  local t
  t=$(sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$2[[:space:]]*=[[:space:]]*//p" "$1" 2>/dev/null | tail -1)
  printf '%s' "$t" | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

HERMES_OFF=0
ASYA_STARTED=0
CHANGED=0

# ── [1] Hermes → архив ───────────────────────────────────────────────────────
echo "[1] Hermes: выключаю оба gateway (до старта Аси — один токен, один полер)"
if ! systemctl --user show-environment >/dev/null 2>&1; then
  bad "user-менеджер root не отвечает (XDG_RUNTIME_DIR=/run/user/0) — не могу выключить Hermes, Асю не ставлю"
  printf '\nИтог: ✗ остановлено на шаге [1], на сервере ничего не изменено.\n'
  exit 1
fi
for u in hermes-gateway.service hermes-inbox-gateway.service; do
  en=$(systemctl --user is-enabled "$u" 2>/dev/null); act=$(systemctl --user is-active "$u" 2>/dev/null)
  if [ "$en" = enabled ] || [ "$act" = active ] || [ "$act" = activating ]; then
    systemctl --user disable --now "$u" >/dev/null 2>&1 && ok "$u: disable --now (было: ${en:-?}/${act:-?})" \
      || bad "$u: disable --now не прошёл"
  else
    skip "$u уже выключен (${en:-?}/${act:-?})"
  fi
  systemctl --user reset-failed "$u" >/dev/null 2>&1 || true
done
# сторож Hermes inbox (ADR-029), если его успели поставить, иначе он начнёт алертить про выключенный бот
if systemctl cat hermes-inbox-watchdog.timer >/dev/null 2>&1; then
  if systemctl is-enabled --quiet hermes-inbox-watchdog.timer 2>/dev/null || systemctl is-active --quiet hermes-inbox-watchdog.timer; then
    systemctl disable --now hermes-inbox-watchdog.timer >/dev/null 2>&1 && ok "hermes-inbox-watchdog.timer: disable --now" \
      || bad "hermes-inbox-watchdog.timer: disable не прошёл"
  else
    skip "hermes-inbox-watchdog.timer уже выключен"
  fi
fi
still=""
for u in hermes-gateway.service hermes-inbox-gateway.service; do
  case "$(systemctl --user is-active "$u" 2>/dev/null)" in active|activating|reloading) still="$still $u" ;; esac
done
stray=$(pgrep -af 'hermes.*gateway' 2>/dev/null | grep -v pgrep | head -3 || true)
if [ -n "$still" ]; then
  bad "Hermes всё ещё активен:$still — Асю не запускаю (два полера на одном токене)"
else
  HERMES_OFF=1
  ok "оба gateway Hermes не активны"
  [ -n "$stray" ] && warn "висят процессы, похожие на gateway Hermes (проверь руками, полер может драться с Асей):
$(printf '%s' "$stray" | redact)"
fi
if [ -f "$MARKER" ]; then
  skip "маркер архива уже есть: $MARKER"
else
  cat > "$MARKER" <<'MD'
# Hermes в архиве — 2026-10-16

Оба gateway (`hermes-gateway`, `hermes-inbox-gateway`) выключены деплоем Аси
(`infrastructure/Asya/Scripts/deploy.sh`), решение Антона — ADR-030
(`tasks/Asya/{automation} {plan} Ася ассистент встречи и задачи – 2026-10-16.md`).

Причина: токен inbox-бота (@rznblm_inbox_bot) теперь у Аси — один токен, один полер.
Календарь, задачи Todoist и ответы о проектах ведёт Ася (systemd `asya.service`).
Ничего не удалено: конфиги, профили, SOUL, venv, бэкапы — на месте.

Вернуть Hermes (СНАЧАЛА выключить Асю, иначе два полера на одном токене):

    systemctl disable --now asya asya-watchdog.timer
    XDG_RUNTIME_DIR=/run/user/0 systemctl --user enable --now hermes-gateway hermes-inbox-gateway
MD
  ok "маркер архива: $MARKER"
fi
if [ "$HERMES_OFF" != 1 ]; then
  printf '\nИтог: ✗ Hermes не выключился — Ася не ставилась.\n'
  exit 1
fi

# ── [2] Код, venv, workspace ─────────────────────────────────────────────────
echo "[2] Код Аси в $CODE, venv, workspace"
mkdir -p "$CODE" "$WS/.claude" "$WS/inbox" "$HOME_DIR/chatlog" "$CFG"
chmod 700 "$CFG"
for f in asya_bot.py media_intake.py permcheck.py; do
  if install_if_changed "$STAGING/$f" "$CODE/$f" 0644; then ok "$f обновлён"; CHANGED=1; else skip "$f без изменений"; fi
done
if [ -x "$VENV/bin/python" ]; then
  skip "venv уже есть"
else
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --upgrade pip && ok "venv создан" || bad "venv не создался"
fi
# ставим один раз: faster-whisper тянет ctranslate2 на сотни МБ; модель small уже в /root/.cache/huggingface
for pkg in python-telegram-bot faster-whisper; do
  if "$VENV/bin/pip" show "$pkg" >/dev/null 2>&1; then
    skip "$pkg уже стоит"
  else
    "$VENV/bin/pip" install -q "$pkg" && { ok "$pkg поставлен"; CHANGED=1; } || bad "$pkg не поставился"
  fi
done
if vers=$("$VENV/bin/python" -c 'import telegram, faster_whisper; print("ptb", telegram.__version__, "· faster-whisper", faster_whisper.__version__)' 2>&1); then
  ok "импорт: $vers"
else
  bad "venv не импортирует telegram/faster_whisper: $(printf '%s' "$vers" | tail -1)"
fi
[ -d /root/.cache/huggingface/hub/models--Systran--faster-whisper-small ] \
  && ok "модель faster-whisper small в кэше" \
  || warn "модели small нет в кэше — первое голосовое скачает её (~500 МБ, дольше обычного)"
for pair in "CLAUDE.md:$WS/CLAUDE.md" "settings.json:$WS/.claude/settings.json" "mcp.json:$WS/mcp.json"; do
  src=${pair%%:*}; dst=${pair#*:}
  if install_if_changed "$STAGING/$src" "$dst" 0644; then ok "$dst обновлён"; CHANGED=1; else skip "$dst без изменений"; fi
done
MEM_FILE="$VAULT/infrastructure/Asya/memory/{self} {research} память Аси – 2026-10-16.md"
[ -f "$MEM_FILE" ] && ok "память Аси в vault-зеркале на месте (Syncthing)" \
  || warn "нет файла памяти в $VAULT/infrastructure/Asya/memory/ — Syncthing не довёз? Бот стартует, памяти не будет"
[ -x /root/tools/gcal-mcp/venv/bin/python ] && [ -f /root/tools/gcal-mcp/server.py ] && ok "gcal-mcp на месте" \
  || bad "нет /root/tools/gcal-mcp (server.py или venv) — календарь у Аси не заработает"

# ── [3] .env ─────────────────────────────────────────────────────────────────
echo "[3] $ENV_FILE"
if [ -f "$ENV_FILE" ]; then
  chmod 600 "$ENV_FILE"
  if [ -n "$(read_env_value "$ENV_FILE" TELEGRAM_BOT_TOKEN)" ] && grep -qE '^USER_ANTON_ID=[0-9]+' "$ENV_FILE"; then
    skip ".env есть, токен и USER_ANTON_ID заполнены — не трогаю"
  else
    bad ".env есть, но пуст TELEGRAM_BOT_TOKEN или USER_ANTON_ID — не перезаписываю, заполни руками: nano $ENV_FILE"
  fi
else
  TOKEN=$(read_env_value "$INBOX_ENV" TELEGRAM_BOT_TOKEN)
  if ! printf '%s' "$TOKEN" | grep -qE "$TOKEN_RE"; then
    TOKEN=""
    bad "в $INBOX_ENV нет валидного TELEGRAM_BOT_TOKEN — .env не создан"
  else
    TMP_ENV=$(umask 077; mktemp "$CFG/.env.XXXXXX")
    # токен идёт через окружение awk, не через argv
    if ASYA_TOKEN="$TOKEN" ASYA_UID="$ANTON_ID" awk '
         /^TELEGRAM_BOT_TOKEN=/ { print "TELEGRAM_BOT_TOKEN=" ENVIRON["ASYA_TOKEN"]; next }
         /^USER_ANTON_ID=/      { print "USER_ANTON_ID=" ENVIRON["ASYA_UID"]; next }
         { print }' "$STAGING/env.example" > "$TMP_ENV" \
       && chmod 600 "$TMP_ENV" && mv -n "$TMP_ENV" "$ENV_FILE" && [ ! -e "$TMP_ENV" ]; then
      ok ".env создан из env.example (600): токен скопирован из inbox-профиля Hermes, USER_ANTON_ID=$ANTON_ID"
      CHANGED=1
    else
      bad ".env не создан"
    fi
    TOKEN=""
  fi
fi
# claude -p под systemd авторизуется долгоживущим токеном (как Марко/Крис); без него — «OAuth session expired» (17.10)
if [ -f "$ENV_FILE" ] && ! grep -q '^CLAUDE_CODE_OAUTH_TOKEN=.' "$ENV_FILE"; then
  grep '^CLAUDE_CODE_OAUTH_TOKEN=.' /root/.config/marco-group/.env >> "$ENV_FILE" 2>/dev/null && ok "CLAUDE_CODE_OAUTH_TOKEN скопирован из .env Марко" && CHANGED=1 || bad "нет CLAUDE_CODE_OAUTH_TOKEN — claude -p не авторизуется (claude setup-token → $ENV_FILE)"
fi

# ── [4] Todoist MCP ─────────────────────────────────────────────────────────
echo "[4] Todoist MCP server.py (тулзы «Мои»)"
if [ ! -f "$TODOIST_DST" ] || [ ! -x "$TODOIST_PY" ]; then
  bad "нет $TODOIST_DST или $TODOIST_PY — задачи у Аси не заработают"
else
  BAK="$TODOIST_DST.bak-$STAMP-asya"
  if cmp -s "$STAGING/todoist_mcp_server.py" "$TODOIST_DST"; then
    skip "server.py уже совпадает с vault-версией"
  else
    if [ -f "$BAK" ]; then
      skip "бэкап уже есть: $BAK (не перезаписываю — он хранит версию ДО Аси)"
    else
      cp -p "$TODOIST_DST" "$BAK" && ok "бэкап: $BAK"
    fi
    install -m 0644 "$STAGING/todoist_mcp_server.py" "$TODOIST_DST" && ok "установлен новый server.py ($(wc -l < "$TODOIST_DST") строк)"
  fi
  if "$TODOIST_PY" -m py_compile "$TODOIST_DST"; then
    ok "py_compile веновским питоном: OK, тулз в коде: $(grep -c '^@mcp.tool()' "$TODOIST_DST" || true)"
  else
    bad "py_compile server.py упал"
    if [ -f "$BAK" ]; then cp -p "$BAK" "$TODOIST_DST" && warn "вернул прежний server.py из $BAK"; fi
  fi
fi

# ── [5] permcheck ───────────────────────────────────────────────────────────
echo "[5] permcheck.py на $("$CLAUDE_BIN" --version 2>/dev/null | head -1 || echo '?') (фейковый API, без квоты)"
PERM_OK=0
if [ ${#PROBLEMS[@]} -gt 0 ]; then
  bad "permcheck пропущен: выше уже есть ✗ — Асю не запускаю"
else
  PC_OUT=$(cd /tmp && timeout 600 "$VENV/bin/python" "$CODE/permcheck.py" --claude "$CLAUDE_BIN" 2>&1); PC_RC=$?
  printf '%s\n' "$PC_OUT" | redact | tail -40 | sed 's/^/    /'
  if [ "$PC_RC" = 0 ]; then
    PERM_OK=1
    ok "permcheck: все ожидания совпали"
  else
    bad "permcheck провален (rc=$PC_RC) — права на VPS-версии claude не те, Асю НЕ запускаю"
  fi
fi

# ── [6] Юниты ───────────────────────────────────────────────────────────────
echo "[6] Юниты asya.service и asya-watchdog.timer"
UNITS_CHANGED=0
if install_if_changed "$STAGING/asya.service" /etc/systemd/system/asya.service 0644; then ok "asya.service обновлён"; UNITS_CHANGED=1; CHANGED=1; else skip "asya.service без изменений"; fi
for f in asya-watchdog.service asya-watchdog.timer; do
  if install_if_changed "$STAGING/$f" "/etc/systemd/system/$f" 0644; then ok "$f обновлён"; UNITS_CHANGED=1; else skip "$f без изменений"; fi
done
if install_if_changed "$STAGING/asya_watchdog.sh" /usr/local/bin/asya-watchdog 0755; then ok "/usr/local/bin/asya-watchdog обновлён"; else skip "asya-watchdog без изменений"; fi
systemctl daemon-reload
if [ "$PERM_OK" = 1 ]; then
  WAS_ACTIVE=$(systemctl is-active asya 2>/dev/null)
  systemctl enable --now asya >/dev/null 2>&1 || bad "systemctl enable --now asya не прошёл"
  if [ "$WAS_ACTIVE" = active ] && [ "$CHANGED" = 1 ]; then
    systemctl restart asya && ok "asya перезапущена (менялись код/конфиг/юнит)"
  elif [ "$WAS_ACTIVE" = active ]; then
    skip "asya уже active, ничего не менялось — без рестарта"
  else
    ok "asya запущена (было: ${WAS_ACTIVE:-?})"
  fi
  ASYA_STARTED=1
  systemctl enable --now asya-watchdog.timer >/dev/null 2>&1 && ok "asya-watchdog.timer: enabled, каждые 10 мин" \
    || bad "asya-watchdog.timer не включился"
else
  warn "asya.service и watchdog поставлены, но НЕ включены (см. ✗ выше)"
fi

# ── [7] Доставка ноутсов встреч ─────────────────────────────────────────────
echo "[7] meeting-notes-delivery.env → Ася, chat $ANTON_ID, без топика"
if [ "$ASYA_STARTED" != 1 ]; then
  skip "Ася не запущена — доставку не переключаю (ноутсы идут прежним каналом)"
elif [ ! -f "$MN_SCRIPT" ]; then
  bad "нет $MN_SCRIPT — не могу проверить контракт доставки"
elif grep -qF '("MEETING_NOTES_THREAD_ID", thread_id),' "$MN_SCRIPT"; then
  warn "meeting_notes.py пока требует MEETING_NOTES_THREAD_ID: без топика он счёл бы конфиг неполным и"
  warn "ушёл бы в daily-focus.env (канал отключён 24.08) — ноутсы терялись бы. Доставку НЕ переключаю:"
  warn "ноутсы идут как раньше — в топик Гермеса его токеном (sendMessage работает и без gateway)."
  warn "Нужна правка meeting_notes.py (thread необязателен) → затем перезапусти deploy.sh."
else
  WANT="MEETING_NOTES_TG_TOKEN_FILE=$ENV_FILE"
  if [ -f "$MN_ENV" ] && grep -qxF "$WANT" "$MN_ENV" && grep -qx "MEETING_NOTES_CHAT_ID=$ANTON_ID" "$MN_ENV" \
     && ! grep -qE '^MEETING_NOTES_TG_TOKEN=.+' "$MN_ENV" && ! grep -qE '^MEETING_NOTES_THREAD_ID=.+' "$MN_ENV"; then
    skip "уже переключено на Асю"
  else
    MN_BAK="$MN_ENV.bak-$STAMP-asya"
    if [ -f "$MN_ENV" ] && [ ! -f "$MN_BAK" ]; then cp -p "$MN_ENV" "$MN_BAK" && ok "бэкап: $MN_BAK"; fi
    [ -f "$MN_BAK" ] && skip "бэкап $MN_BAK не перезаписываю — он хранит канал Гермеса"
    mkdir -p "$(dirname "$MN_ENV")"
    TMP_MN=$(umask 077; mktemp "$MN_ENV.XXXXXX")
    {
      echo "# meeting-notes-delivery.env — ноутсы встреч приходят Асе в личку (ADR-030, deploy.sh $STAMP)."
      echo "# Токен не дублируется: meeting_notes.py читает TELEGRAM_BOT_TOKEN из .env Аси."
      echo "# Прежний канал (топик Гермеса): $MN_BAK"
      echo "$WANT"
      echo "MEETING_NOTES_CHAT_ID=$ANTON_ID"
    } > "$TMP_MN" && chmod 600 "$TMP_MN" && mv -f "$TMP_MN" "$MN_ENV" \
      && ok "доставка переключена: токен — файлом $ENV_FILE, chat $ANTON_ID, без топика" \
      || bad "meeting-notes-delivery.env не записан"
  fi
  # проверка тем же кодом, что читает meeting_notes (печатаются только chat/thread и факт наличия токена)
  MN_CHECK=$(SECOND_BRAIN_VAULT="$VAULT" VPS_SYNC_DISABLE=1 python3 - "$(dirname "$MN_SCRIPT")" <<'PY' 2>&1
import sys
sys.path.insert(0, sys.argv[1])
import meeting_notes as m
ch = m.load_delivery_channel()
if not ch:
    print("NONE")
else:
    print("chat=%s thread=%s token=%s" % (ch.chat_id, ch.thread_id or "-", "есть" if ch.token else "нет"))
PY
)
  case "$MN_CHECK" in
    *"chat=$ANTON_ID thread=- token=есть"*) ok "meeting_notes видит канал: $(printf '%s' "$MN_CHECK" | tail -1)" ;;
    *) bad "meeting_notes не видит новый канал: $(printf '%s' "$MN_CHECK" | redact | tail -1)" ;;
  esac
fi

# ── [8] Смоук ───────────────────────────────────────────────────────────────
echo "[8] Смоук"
if [ "$ASYA_STARTED" = 1 ]; then
  sleep 8
  if systemctl is-active --quiet asya; then ok "asya: active"; else bad "asya не active: $(systemctl is-active asya 2>/dev/null)"; fi
  TOKEN=$(read_env_value "$ENV_FILE" TELEGRAM_BOT_TOKEN)
  ME=$(printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$TOKEN" | curl -sS --max-time 20 --config - 2>&1 \
       | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print("ok=%s username=@%s" % (d.get("ok"), (d.get("result") or {}).get("username", "?")))
except Exception:
    print("ok=False (ответ не JSON)")' 2>&1)
  TOKEN=""
  case "$ME" in
    ok=True*) ok "getMe: $ME" ;;
    *) bad "getMe: $ME" ;;
  esac
  echo "  --- хвост $CFG/bot.log ---"
  tail -n 15 "$CFG/bot.log" 2>/dev/null | redact | sed 's/^/    /' || echo "    (bot.log ещё нет)"
  if tail -n 50 "$CFG/bot.log" 2>/dev/null | grep -qi 'conflict'; then
    bad "в bot.log Conflict — токен поллит кто-то ещё (Hermes? второй экземпляр?)"
  fi
fi

# ── Сводка ──────────────────────────────────────────────────────────────────
echo
if [ ${#PROBLEMS[@]} -eq 0 ]; then
  echo "Итог: ✓ Ася развёрнута и работает. Hermes в архиве ($MARKER)."
else
  echo "Итог: ✗ проблем: ${#PROBLEMS[@]}"
  for p in "${PROBLEMS[@]}"; do echo "  ✗ $p"; done
fi
if [ "$HERMES_OFF" = 1 ] && [ "$ASYA_STARTED" != 1 ]; then
  echo
  echo "  ⚠ Hermes уже выключен, а Ася НЕ запущена — календарного бота сейчас нет."
  echo "    Починить и перезапустить deploy.sh, либо вернуть Hermes:"
  echo "    XDG_RUNTIME_DIR=/run/user/0 systemctl --user enable --now hermes-gateway hermes-inbox-gateway"
fi
echo
echo "Руками (Антон):"
echo "  1. BotFather → /setname → @rznblm_inbox_bot → «Ася»."
echo "  2. Todoist: создать проект «Мои» (и архивировать «Концерты») — до этого задачи Аси получат ответ «проекта «Мои» нет»."
echo "  3. Тест: переслать Асе переписку про синк 17.10 → одно событие на итоговую дату; голосом — задачу; «что по Зекиро»."
echo "Логи: journalctl -u asya -f · tail -f $CFG/bot.log · сторож: journalctl -u asya-watchdog -n 20"
[ ${#PROBLEMS[@]} -eq 0 ]
REMOTE
RC=$?
set -e
exit $RC

# ── Откат (руками, на VPS: ssh -i ~/.ssh/id_ed25519_vps root@163.5.29.10) ─────
# Порядок важен: сначала выключить Асю, потом поднимать Hermes (один токен — один полер).
#
#   systemctl disable --now asya asya-watchdog.timer
#   XDG_RUNTIME_DIR=/run/user/0 systemctl --user enable --now hermes-gateway hermes-inbox-gateway
#   # доставка ноутсов обратно в топик Гермеса:
#   cp -p /root/.config/second-brain/meeting-notes-delivery.env.bak-2026-10-16-asya \
#         /root/.config/second-brain/meeting-notes-delivery.env
#   # todoist-mcp: новая версия обратно совместима с тулзами Гермеса; вернуть старую — только если сломалось:
#   cp -p /root/tools/todoist-mcp/server.py.bak-2026-10-16-asya /root/tools/todoist-mcp/server.py
#   # маркер архива не удалять, а переименовать:
#   mv /root/.hermes/ARCHIVED-2026-10-16.md /root/.hermes/ARCHIVED-2026-10-16.reverted.md
#
# Код /opt/asya, /var/lib/asya (чатлоги), /root/.config/asya (.env, bot.log) откат не трогает.
