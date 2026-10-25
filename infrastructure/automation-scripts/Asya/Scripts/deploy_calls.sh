#!/usr/bin/env bash
# Деплой разбора звонков Аси (ADR-033) с мака на VPS 163.5.29.10. Запускает агент сам (ADR-032):
#   bash "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Asya/Scripts/deploy_calls.sh"
#
# Идемпотентно: повтор говорит «= уже». Ничего не удаляет. Секреты не печатаются:
# токен Аси и CLAUDE_CODE_OAUTH_TOKEN уже лежат в /root/.config/asya/.env (deploy.sh), юнит читает его сам.
#
# Шаги:
#   [0] мак: файлы, py_compile, JSON, pytest; ночной LaunchAgent transcript-summarizer выгружен?
#       ssh, claude на VPS; заливка в /opt/asya/.staging
#   [1] сверка Syncthing: базы, карта треков, transcript-summarizer, footer на VPS = мак
#   [2] код в /opt/asya (calls_runner.py, calls_prompt.md, permcheck.py; asya_bot.py — только если нет),
#       /var/lib/asya-calls/workspace: .claude/settings.json, mcp.json; CLAUDE.md Аси (persona + footer)
#   [3] permcheck --profile calls на VPS-версии claude (фейковый API, без квоты); провален → таймер не включаем
#   [4] юниты asya-calls.service/.timer, daemon-reload, enable timer
#       (только если старый ночной саммаризатор на маке выгружен — две машины не пишут саммари одного звонка)
#   [5] health: один тик --dry-run (печатает кандидатов, ничего не пишет и не шлёт)

set -euo pipefail

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 $VPS"
RSH="ssh -i $KEY -o BatchMode=yes"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_DIR="$(cd "$PROJECT_DIR/../.." && pwd)"
STAGING="/opt/asya/.staging-calls"
CLAUDE_BIN="/root/.local/bin/claude"
OLD_AGENT="com.user.transcript-summarizer"

fail() { echo "  ✗ $*" >&2; exit 1; }

echo "[0] Проверяю локальные файлы"
for f in \
  "$SCRIPT_DIR/calls_runner.py" "$SCRIPT_DIR/asya_bot.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/permcheck.py" \
  "$SCRIPT_DIR/asya-calls.service" "$SCRIPT_DIR/asya-calls.timer" \
  "$PROJECT_DIR/config/calls_prompt.md" "$PROJECT_DIR/config/calls-settings.json" "$PROJECT_DIR/config/calls-mcp.json" \
  "$PROJECT_DIR/persona/asya.md" "$PROJECT_DIR/config/workspace-footer.md"
do
  [ -s "$f" ] || fail "нет файла $f"
done
python3 -m py_compile "$SCRIPT_DIR/calls_runner.py" "$SCRIPT_DIR/permcheck.py" || fail "код не компилируется"
python3 -c 'import json,sys; [json.load(open(p)) for p in sys.argv[1:]]' \
  "$PROJECT_DIR/config/calls-settings.json" "$PROJECT_DIR/config/calls-mcp.json" || fail "битый JSON в config/"
(cd "$PROJECT_DIR" && python3 -m pytest Scripts/ -q >/dev/null 2>&1) || fail "pytest не зелёный — чини до деплоя"
echo "  ✓ файлы на месте, код компилируется, pytest зелёный"

OLD_LOADED=0
if launchctl list 2>/dev/null | grep -q "$OLD_AGENT"; then
  OLD_LOADED=1
  echo "  ⚠ на маке загружен $OLD_AGENT — таймер разбора НЕ включу (спека §5: сначала выключить старое)"
else
  echo "  ✓ $OLD_AGENT на маке не загружен"
fi

$SSH "test -x '$CLAUDE_BIN'" || fail "нет ssh до VPS или нет $CLAUDE_BIN"
echo "  ✓ VPS отвечает, $CLAUDE_BIN на месте"

TMP_DIR="$(mktemp -d -t asya-calls-deploy)"
trap 'rm -rf "$TMP_DIR"' EXIT
{ cat "$PROJECT_DIR/persona/asya.md"; echo; cat "$PROJECT_DIR/config/workspace-footer.md"; } > "$TMP_DIR/CLAUDE.md"
cp "$SCRIPT_DIR/calls_runner.py" "$SCRIPT_DIR/asya_bot.py" "$SCRIPT_DIR/media_intake.py" "$SCRIPT_DIR/permcheck.py" \
   "$SCRIPT_DIR/asya-calls.service" "$SCRIPT_DIR/asya-calls.timer" \
   "$PROJECT_DIR/config/calls_prompt.md" "$PROJECT_DIR/config/calls-settings.json" "$PROJECT_DIR/config/calls-mcp.json" \
   "$TMP_DIR/"

# контрольные суммы vault-файлов, которые разбор читает/пишет на VPS (сверка Syncthing)
SUMS="$TMP_DIR/vault.sha256"
(
  cd "$VAULT_DIR"
  for rel in \
    "tasks/Луч ИИ/{startup} {research} база знаний Луч ИИ – 2026-10-21.md" \
    "tasks/Зекиро/{startup} {research} база знаний Зекиро – 2026-10-24.md" \
    "tasks/Фриланс/{startup} {research} база знаний фриланс – 2026-10-24.md" \
    "context/{self} {plan} карта треков – 2026-08-19.md" \
    "system/skills/transcript-summarizer/SKILL.md" \
    "infrastructure/Asya/config/workspace-footer.md" \
    "infrastructure/Asya/Scripts/calls_runner.py"
  do
    [ -f "$rel" ] || fail "нет в vault: $rel"
    shasum -a 256 "$rel"
  done
) > "$SUMS"

$SSH "mkdir -p '$STAGING' && chmod 700 '$STAGING'"
rsync -a --checksum -e "$RSH" "$TMP_DIR"/ "$VPS:$STAGING/"
echo "  ✓ залито в $STAGING"

set +e
$SSH "OLD_LOADED=$OLD_LOADED bash -s" <<'REMOTE'
set -uo pipefail
umask 022

STAGING=/opt/asya/.staging-calls
CODE=/opt/asya
VENV=/opt/asya/venv
HOME_DIR=/var/lib/asya-calls
WS=/var/lib/asya-calls/workspace
ASYA_WS=/var/lib/asya/workspace
ENV_FILE=/root/.config/asya/.env
VAULT=/root/second-brain
CLAUDE_BIN=/root/.local/bin/claude
TODOIST_SERVER=/root/tools/todoist-mcp/server.py
MN_ENV=/root/.config/second-brain/meeting-notes-delivery.env

PROBLEMS=()
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; PROBLEMS+=("$*"); }
warn() { printf '  ⚠ %s\n' "$*"; }
skip() { printf '  = %s\n' "$*"; }
install_if_changed() {
  if [ -f "$2" ] && cmp -s "$1" "$2"; then return 1; fi
  install -D -m "$3" "$1" "$2"
  return 0
}

# ── [1] Syncthing ───────────────────────────────────────────────────────────
echo "[1] Сверка Syncthing (vault на VPS = мак)"
SYNC_OK=1
while IFS= read -r line; do
  sum=${line%%  *}; rel=${line#*  }
  if [ ! -f "$VAULT/$rel" ]; then
    bad "на VPS нет $rel — Syncthing не довёз"; SYNC_OK=0
  elif [ "$(sha256sum "$VAULT/$rel" | cut -d' ' -f1)" != "$sum" ]; then
    warn "отличается от мака: $rel (Syncthing в пути или правка на VPS)"; SYNC_OK=0
  fi
done < "$STAGING/vault.sha256"
[ $SYNC_OK -eq 1 ] && ok "базы, карта треков, summarizer, footer, runner совпадают с маком"
[ -d "$VAULT/meetings" ] && [ -d "$VAULT/transcripts" ] || bad "нет $VAULT/meetings или transcripts"

# ── [2] Код и workspace ─────────────────────────────────────────────────────
echo "[2] Код в $CODE, workspace $WS"
mkdir -p "$CODE" "$WS/.claude" "$HOME_DIR"
chmod 700 "$HOME_DIR"
for f in calls_runner.py calls_prompt.md permcheck.py; do
  if install_if_changed "$STAGING/$f" "$CODE/$f" 0644; then ok "$f обновлён"; else skip "$f без изменений"; fi
done
for f in asya_bot.py media_intake.py; do  # их ставит deploy.sh; здесь только если нет (рестарт Аси не наш)
  if [ ! -f "$CODE/$f" ]; then install -m 0644 "$STAGING/$f" "$CODE/$f" && ok "$f поставлен (не было)"
  elif ! cmp -s "$STAGING/$f" "$CODE/$f"; then warn "$CODE/$f отличается от мака — прогони deploy.sh"
  else skip "$f совпадает"; fi
done
for pair in "calls-settings.json:$WS/.claude/settings.json" "calls-mcp.json:$WS/mcp.json" "CLAUDE.md:$ASYA_WS/CLAUDE.md"; do
  src=${pair%%:*}; dst=${pair#*:}
  if install_if_changed "$STAGING/$src" "$dst" 0644; then ok "$dst обновлён"; else skip "$dst без изменений"; fi
done
[ -x "$VENV/bin/python" ] || bad "нет $VENV — сначала deploy.sh Аси"
"$VENV/bin/python" -c 'import httpx' 2>/dev/null && ok "httpx в venv" || bad "в venv нет httpx (ставится с python-telegram-bot)"
[ -f "$ENV_FILE" ] || bad "нет $ENV_FILE — сначала deploy.sh Аси"
grep -q '^CLAUDE_CODE_OAUTH_TOKEN=.' "$ENV_FILE" 2>/dev/null && ok "OAuth-токен claude в .env есть (значение не печатаю)" \
  || bad "в $ENV_FILE нет CLAUDE_CODE_OAUTH_TOKEN — claude -p под systemd не авторизуется"
grep -q 'def todoist_set_track' "$TODOIST_SERVER" 2>/dev/null && ok "todoist-mcp с add_task/set_track" \
  || bad "в $TODOIST_SERVER нет todoist_set_track — прогони deploy.sh"

# ── [3] permcheck ───────────────────────────────────────────────────────────
echo "[3] permcheck --profile calls на $("$CLAUDE_BIN" --version 2>/dev/null | head -1 || echo '?')"
PC_OK=0
PC_OUT=$(cd /tmp && timeout 600 "$VENV/bin/python" "$CODE/permcheck.py" --claude "$CLAUDE_BIN" --profile calls 2>&1); PC_RC=$?
if [ $PC_RC -eq 0 ]; then
  ok "permcheck calls: все ожидания совпали"; PC_OK=1
else
  printf '%s\n' "$PC_OUT" | grep -E '✗|ИТОГ|stderr' | head -20
  bad "permcheck calls провален (rc=$PC_RC) — таймер НЕ включаю"
fi

# ── [4] Юниты ───────────────────────────────────────────────────────────────
echo "[4] Юниты asya-calls.service / asya-calls.timer"
for u in asya-calls.service asya-calls.timer; do
  if install_if_changed "$STAGING/$u" "/etc/systemd/system/$u" 0644; then ok "$u обновлён"; else skip "$u без изменений"; fi
done
systemctl daemon-reload
if [ "$OLD_LOADED" = "1" ]; then
  warn "таймер не включён: на маке загружен ночной transcript-summarizer (выгрузить и повторить деплой)"
elif [ $PC_OK -eq 1 ] && [ ${#PROBLEMS[@]} -eq 0 ]; then
  if systemctl is-enabled --quiet asya-calls.timer 2>/dev/null && systemctl is-active --quiet asya-calls.timer; then
    skip "asya-calls.timer уже enabled/active"
  else
    systemctl enable --now asya-calls.timer >/dev/null 2>&1 && ok "asya-calls.timer: enabled, каждые 10 мин" \
      || bad "systemctl enable --now asya-calls.timer не прошёл"
  fi
else
  warn "таймер не включён: выше есть ✗"
fi
mkdir -p /etc/systemd/system/meeting-notes.service.d
printf '[Service]\nEnvironment=MEETING_NOTES_DELIVERY=off\nEnvironment=MEETING_NOTES_TODOIST=off\n' > /etc/systemd/system/meeting-notes.service.d/asya-calls.conf
systemctl daemon-reload && ok "Meeting Notes: ноутс в TG и задачи в Todoist выключены (drop-in, ADR-033)"

# ── [5] Health: dry-run ─────────────────────────────────────────────────────
echo "[5] Health: один тик --dry-run (без claude, без записей, без отправки)"
(
  set -a; . "$ENV_FILE" 2>/dev/null; set +a
  cd "$WS" && ASYA_VAULT_DIR=$VAULT ASYA_CALLS_HOME=$HOME_DIR ASYA_CALLS_PROMPT=$CODE/calls_prompt.md \
    ASYA_CALLS_MCP_CONFIG=$WS/mcp.json timeout 120 "$VENV/bin/python" "$CODE/calls_runner.py" --dry-run 2>&1 \
    | sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/<token>/g' | head -40
) || bad "dry-run упал"
systemctl list-timers asya-calls.timer --no-pager 2>/dev/null | head -3

echo
if [ ${#PROBLEMS[@]} -eq 0 ]; then
  echo "Итог: ✓ разбор звонков установлен. Логи: tail -f $HOME_DIR/runner.log · $HOME_DIR/log.jsonl (cost_usd) · journalctl -u asya-calls -n 50"
else
  echo "Итог: ✗ проблем ${#PROBLEMS[@]}:"; printf '  - %s\n' "${PROBLEMS[@]}"
fi
echo "Бэкфилл с 01.10 (по одному звонку за тик, после живой проверки одного звонка):"
echo "  mkdir -p /etc/systemd/system/asya-calls.service.d && printf '[Service]\\nEnvironment=ASYA_CALLS_SINCE=2026-10-01\\n' > /etc/systemd/system/asya-calls.service.d/backfill.conf && systemctl daemon-reload"
echo "  (снять: в том же drop-in Environment=ASYA_CALLS_SINCE= и daemon-reload)"
[ ${#PROBLEMS[@]} -eq 0 ]
REMOTE
RC=$?
set -e
exit $RC

# ── Откат (руками на VPS) ─────────────────────────────────────────────────────
#   systemctl disable --now asya-calls.timer
#   # код /opt/asya/calls_runner.py, состояние /var/lib/asya-calls (state.json, log.jsonl) не трогаем.
#   # Асин CLAUDE.md: прогнать deploy.sh со старым footer.
