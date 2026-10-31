#!/usr/bin/env bash
# Деплой Роки (@rznblm_rocky_bot) с мака на VPS 163.5.29.10. Идемпотентно: гонять сколько угодно раз.
#
#   bash "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Rocky/Scripts/deploy.sh"              # полный деплой
#   bash "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Rocky/Scripts/deploy.sh" --permcheck  # только заливка кода + permcheck, юнит не трогаем
#
# Шаги:
#   [0] мак: файлы на месте, py_compile, bash -n, pytest; сборка workspace/CLAUDE.md (персона + футер)
#   [1] VPS: ssh, claude CLI есть и версия ≥ 2.1.283 (на старых deny Read не режет Grep - см. Асю)
#   [2] каталоги /opt/rocky, /var/lib/rocky/{workspace/inbox,data/journal,data/reports,chatlog}, /root/.config/rocky (700)
#   [3] код -> /opt/rocky, venv (python-telegram-bot[job-queue]), CLAUDE.md -> workspace
#   [4] .env (600): нет - кладётся образец; пустые TELEGRAM_BOT_TOKEN (из /root/.config/coach-bot/.env),
#       USER_ANTON_ID, USER_RALINA_ID, CLAUDE_CODE_OAUTH_TOKEN (из /root/.config/marco-group/.env)
#       подставляются на сервере, значения не печатаются и не едут на мак.
#       Токен бота так и пуст - стоп ДО старта юнита.
#   [5] permcheck.py на VPS-версии claude (фейковый API, без квоты); провален - юнит не стартует
#   [6] rocky.service: enable --now + restart, проверка active, хвост bot.log без токенов
#
# Ничего не удаляет: журнал, профили, отчёты, чатлог, state.json, bot.log остаются.

set -euo pipefail

MODE="full"
case "${1:-}" in
  "") ;;
  --permcheck) MODE="permcheck" ;;
  *) echo "usage: deploy.sh [--permcheck]" >&2; exit 2 ;;
esac

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 $VPS"
RSH="ssh -i $KEY -o BatchMode=yes"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # infrastructure/Rocky
STAGING="/opt/rocky/.staging"
CLAUDE_BIN="/root/.local/bin/claude"

fail() { echo "  ✗ $*" >&2; exit 1; }

echo "[0] Мак: проверки до любых изменений на сервере"
for f in \
  "$SCRIPT_DIR/rocky_bot.py" "$SCRIPT_DIR/journal.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/hermes_reserve.py" "$SCRIPT_DIR/permcheck.py" "$SCRIPT_DIR/rocky_vision.py" \
  "$SCRIPT_DIR/rocky.service" "$PROJECT_DIR/persona/rocky.md" "$PROJECT_DIR/config/workspace-footer.md" \
  "$PROJECT_DIR/config/env.example"
do
  [ -s "$f" ] || fail "нет файла $f"
done
python3 -m py_compile "$SCRIPT_DIR/rocky_bot.py" "$SCRIPT_DIR/journal.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/hermes_reserve.py" \
  "$SCRIPT_DIR/permcheck.py" "$SCRIPT_DIR/rocky_vision.py" || fail "код не компилируется"
bash -n "$0" || fail "deploy.sh: синтаксическая ошибка"
(cd "$PROJECT_DIR" && python3 -m pytest Scripts -q) || fail "pytest красный — чини до деплоя"
echo "  ✓ файлы на месте, pytest зелёный"

TMP_DIR="$(mktemp -d -t rocky-deploy)"
trap 'rm -rf "$TMP_DIR"' EXIT
{
  cat "$PROJECT_DIR/persona/rocky.md"
  echo
  cat "$PROJECT_DIR/config/workspace-footer.md"
} > "$TMP_DIR/CLAUDE.md"
cp "$SCRIPT_DIR/rocky_bot.py" "$SCRIPT_DIR/journal.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/hermes_reserve.py" "$SCRIPT_DIR/permcheck.py" "$SCRIPT_DIR/rocky_vision.py" \
   "$SCRIPT_DIR/rocky.service" "$PROJECT_DIR/config/env.example" "$TMP_DIR/"
echo "  ✓ workspace/CLAUDE.md собран: $(wc -l < "$TMP_DIR/CLAUDE.md" | tr -d ' ') строк"

echo "[1] VPS: ssh и claude CLI"
$SSH "test -x '$CLAUDE_BIN'" || fail "нет ssh до VPS или нет исполняемого $CLAUDE_BIN"
$SSH "mkdir -p '$STAGING' && chmod 700 '$STAGING'"
rsync -a --checksum -e "$RSH" "$TMP_DIR"/ "$VPS:$STAGING/"
echo "  ✓ залито в $STAGING"

set +e
$SSH "MODE=$MODE bash -s" <<'REMOTE'
set -uo pipefail
umask 022

STAGING=/opt/rocky/.staging
CODE=/opt/rocky
VENV=/opt/rocky/venv
HOME_DIR=/var/lib/rocky
WS=/var/lib/rocky/workspace
DATA=/var/lib/rocky/data
CFG=/root/.config/rocky
ENV_FILE=/root/.config/rocky/.env
MARCO_ENV=/root/.config/marco-group/.env
COACH_ENV=/root/.config/coach-bot/.env
CLAUDE_BIN=/root/.local/bin/claude
MIN_CLAUDE=2.1.283

PROBLEMS=()
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; PROBLEMS+=("$*"); }
warn() { printf '  ⚠ %s\n' "$*"; }
skip() { printf '  = %s\n' "$*"; }
redact() { sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/<token>/g; s/sk-ant-[A-Za-z0-9_-]+/<token>/g'; }

# значение KEY из env-файла (в переменную, не в argv и не в вывод)
read_env_value() {  # read_env_value <file> <KEY>
  local t
  t=$(sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$2[[:space:]]*=[[:space:]]*//p" "$1" 2>/dev/null | tail -1)
  printf '%s' "$t" | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# fill_key <KEY> <src-file>: пустой KEY в .env Роки заполняется из src. Значение идёт через окружение awk.
fill_key() {
  local key=$1 src=$2 cur val tmp
  cur=$(read_env_value "$ENV_FILE" "$key")
  if [ -n "$cur" ]; then skip "$key уже заполнен"; return 0; fi
  if [ ! -f "$src" ]; then warn "$key пуст, а $src нет — заполни руками"; return 1; fi
  val=$(read_env_value "$src" "$key")
  if [ -z "$val" ]; then warn "$key пуст и в $src — заполни руками"; return 1; fi
  tmp=$(umask 077; mktemp "$CFG/.env.XXXXXX")
  if ROCKY_K="$key" ROCKY_V="$val" awk '
       BEGIN { k = ENVIRON["ROCKY_K"]; v = ENVIRON["ROCKY_V"]; done = 0 }
       $0 ~ "^[[:space:]]*" k "[[:space:]]*=" { print k "=" v; done = 1; next }
       { print }
       END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp" \
     && chmod 600 "$tmp" && mv -f "$tmp" "$ENV_FILE"; then
    ok "$key скопирован из $src"
  else
    rm -f "$tmp"; bad "$key не записан"
  fi
  val=""
}

echo "[1] claude CLI ≥ $MIN_CLAUDE"
CV=$("$CLAUDE_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [ -z "$CV" ]; then
  bad "не смог узнать версию claude"
elif printf '%s\n%s\n' "$MIN_CLAUDE" "$CV" | sort -V -C; then
  ok "claude $CV"
else
  bad "claude $CV < $MIN_CLAUDE: deny Read не режет Grep по .env (грабли Аси). Обнови: $CLAUDE_BIN update"
fi

echo "[2] Каталоги"
mkdir -p "$CODE" "$WS/inbox" "$DATA/journal" "$DATA/reports" "$HOME_DIR/chatlog" "$CFG"
chmod 700 "$CFG" "$DATA"
ok "$CODE, $WS/inbox, $DATA/{journal,reports}, $HOME_DIR/chatlog, $CFG"

echo "[3] Код, venv, CLAUDE.md"
for f in rocky_bot.py journal.py media_intake.py hermes_reserve.py permcheck.py rocky_vision.py rocky.service; do
  install -m 0644 "$STAGING/$f" "$CODE/$f"
done
install -m 0644 "$STAGING/CLAUDE.md" "$WS/CLAUDE.md"
ok "код в $CODE, $WS/CLAUDE.md обновлён"
if [ -x "$VENV/bin/python" ]; then
  skip "venv уже есть"
else
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --upgrade pip && ok "venv создан" || bad "venv не создался"
fi
if "$VENV/bin/python" -c 'import telegram, apscheduler' >/dev/null 2>&1; then
  skip "python-telegram-bot[job-queue] уже стоит"
else
  "$VENV/bin/pip" install -q 'python-telegram-bot[job-queue]' && ok "python-telegram-bot[job-queue] поставлен" \
    || bad "python-telegram-bot не поставился"
fi
vers=$("$VENV/bin/python" -c 'import telegram, apscheduler; print("ptb", telegram.__version__)' 2>&1) \
  && ok "импорт: $vers" || bad "venv не импортирует telegram/apscheduler"

if [ "$MODE" = full ]; then
  echo "[4] $ENV_FILE"
  if [ -f "$ENV_FILE" ]; then
    skip ".env есть — дозаполняю только пустые ключи"
  else
    install -m 0600 "$STAGING/env.example" "$ENV_FILE" && ok ".env создан из образца (600)"
  fi
  chmod 600 "$ENV_FILE"
  fill_key TELEGRAM_BOT_TOKEN "$COACH_ENV"
  for k in USER_ANTON_ID USER_RALINA_ID CLAUDE_CODE_OAUTH_TOKEN; do
    fill_key "$k" "$MARCO_ENV"
  done
  [ -n "$(read_env_value "$ENV_FILE" BOT_USERNAME)" ] || warn "BOT_USERNAME пуст (не критично)"
  [ -n "$(read_env_value "$ENV_FILE" GROUP_CHAT_ID)" ] && ok "GROUP_CHAT_ID задан" \
    || warn "GROUP_CHAT_ID пуст — бот стартует в режиме настройки (ответит chat_id в группе)"
  if [ -z "$(read_env_value "$ENV_FILE" TELEGRAM_BOT_TOKEN)" ]; then
    printf '\nTELEGRAM_BOT_TOKEN пуст и в %s его нет. Заполни: nano %s — и запусти deploy.sh ещё раз.\nЮнит не стартую.\n' "$COACH_ENV" "$ENV_FILE"
    exit 1
  fi
  [ -n "$(read_env_value "$ENV_FILE" CLAUDE_CODE_OAUTH_TOKEN)" ] \
    || bad "CLAUDE_CODE_OAUTH_TOKEN пуст — claude -p не авторизуется (claude setup-token → $ENV_FILE)"
fi

echo "[5] permcheck.py (фейковый API, без квоты)"
PERM_OK=0
if [ ${#PROBLEMS[@]} -gt 0 ]; then
  bad "permcheck пропущен: выше уже есть ✗"
else
  PC_OUT=$(cd /tmp && timeout 600 "$VENV/bin/python" "$CODE/permcheck.py" --claude "$CLAUDE_BIN" 2>&1); PC_RC=$?
  printf '%s\n' "$PC_OUT" | redact | tail -30 | sed 's/^/    /'
  if [ "$PC_RC" = 0 ]; then PERM_OK=1; ok "permcheck: все ожидания совпали"
  else bad "permcheck провален (rc=$PC_RC) — права не те, юнит не стартую"; fi
fi

if [ "$MODE" = permcheck ]; then
  echo
  [ ${#PROBLEMS[@]} -eq 0 ] && echo "Итог: ✓ permcheck зелёный (юнит не трогал)" || echo "Итог: ✗ проблем: ${#PROBLEMS[@]}"
  [ ${#PROBLEMS[@]} -eq 0 ]
  exit $?
fi

echo "[6] Юнит rocky.service"
install -m 0644 "$CODE/rocky.service" /etc/systemd/system/rocky.service
systemctl daemon-reload
if [ "$PERM_OK" = 1 ]; then
  systemctl enable --now rocky.service >/dev/null 2>&1 || bad "enable --now rocky не прошёл"
  systemctl restart rocky.service && ok "rocky перезапущен"
  sleep 6
  if systemctl is-active --quiet rocky; then ok "rocky: active"; else bad "rocky не active: $(systemctl is-active rocky 2>/dev/null)"; fi
  echo "  --- хвост $CFG/bot.log ---"
  tail -n 15 "$CFG/bot.log" 2>/dev/null | redact | sed 's/^/    /' || echo "    (bot.log ещё нет)"
  if tail -n 50 "$CFG/bot.log" 2>/dev/null | grep -qi 'conflict'; then
    bad "в bot.log Conflict — токен поллит кто-то ещё (старый coach-bot?)"
  fi
else
  warn "rocky.service поставлен, но НЕ запущен (см. ✗ выше)"
fi

echo
if [ ${#PROBLEMS[@]} -eq 0 ]; then
  echo "Итог: ✓ Роки развёрнут. Логи: journalctl -u rocky -f · tail -f $CFG/bot.log"
else
  echo "Итог: ✗ проблем: ${#PROBLEMS[@]}"
  for p in "${PROBLEMS[@]}"; do echo "  ✗ $p"; done
fi
[ ${#PROBLEMS[@]} -eq 0 ]
REMOTE
RC=$?
set -e
exit $RC
