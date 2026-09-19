#!/usr/bin/env bash
# Деплой группового Марко с мака на VPS (Hostkey, Амстердам). Идемпотентно: гонять сколько угодно раз.
#
# Что делает:
#   код -> /opt/marco-group, venv (python-telegram-bot[job-queue] + свежий yt-dlp),
#   каталоги /var/lib/marco-group/workspace/ref и /root/.config/marco-group (700),
#   workspace/CLAUDE.md собирается из persona/marco-group.md + config/group-workspace-footer.md,
#   .env: если на сервере нет — кладётся образец и деплой останавливается (заполни и запусти снова),
#   marco-group.service ставится, enable --now, в конце status + хвост журнала.
#
# Состояние поездки (group/) НЕ заливается rsync'ом: оно приезжает Syncthing'ом из vault.
# Ничего не удаляет: ни state.json, ни логи, ни файлы плана.

set -euo pipefail

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes $VPS"
RSH="ssh -i $KEY"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # infrastructure/Travel Agent

REMOTE_CODE="/opt/marco-group"
REMOTE_WS="/var/lib/marco-group/workspace"
REMOTE_CFG="/root/.config/marco-group"
REMOTE_GROUP="/root/second-brain/infrastructure/Travel Agent/group"
CLAUDE_BIN="/root/.local/bin/claude"

fail() { echo "ОШИБКА: $*" >&2; exit 1; }

echo "==> [1/9] Проверяю локальные файлы (до любых изменений на сервере)"
for f in \
  "$SCRIPT_DIR/marco_group_bot.py" \
  "$SCRIPT_DIR/reel_meta.py" \
  "$SCRIPT_DIR/media_intake.py" \
  "$SCRIPT_DIR/marco-group.service" \
  "$PROJECT_DIR/persona/marco-group.md" \
  "$PROJECT_DIR/config/group-workspace-footer.md" \
  "$PROJECT_DIR/config/group.env.example"
do
  [ -f "$f" ] || fail "нет файла $f"
done
python3 -m py_compile "$SCRIPT_DIR/marco_group_bot.py" "$SCRIPT_DIR/reel_meta.py" \
  "$SCRIPT_DIR/media_intake.py" \
  || fail "код бота не компилируется — чини до деплоя"
echo "    ок"

echo "==> [2/9] Проверяю claude CLI на VPS"
$SSH "test -x '$CLAUDE_BIN'" \
  || fail "на VPS нет исполняемого $CLAUDE_BIN — поставь claude CLI, без него бот бесполезен"
echo "    $CLAUDE_BIN на месте"

echo "==> [3/9] Создаю каталоги на VPS"
# inbox: сюда бот кладёт скачанные фото, PDF и голосовые (волна 2).
$SSH "mkdir -p '$REMOTE_CODE' '$REMOTE_WS/ref' '$REMOTE_WS/inbox' '$REMOTE_CFG' && chmod 700 '$REMOTE_CFG'"

echo "==> [4/9] Копирую код"
rsync -az -e "$RSH" "$SCRIPT_DIR"/*.py "$VPS:$REMOTE_CODE/"

echo "==> [5/9] Готовлю venv и зависимости"
$SSH "test -d '$REMOTE_CODE/venv' || python3 -m venv '$REMOTE_CODE/venv'; '$REMOTE_CODE/venv/bin/pip' install -q --upgrade pip 'python-telegram-bot[job-queue]'"
# yt-dlp обновляем при каждом деплое: Instagram регулярно ломает старые версии.
$SSH "'$REMOTE_CODE/venv/bin/pip' install -q --upgrade yt-dlp"
# faster-whisper ставим один раз: он тянет CUDA-агностичный ctranslate2 на сотни МБ,
# гонять --upgrade каждый деплой незачем. Модель small уже в кэше ~/.cache/huggingface.
$SSH "'$REMOTE_CODE/venv/bin/pip' show faster-whisper >/dev/null 2>&1 || '$REMOTE_CODE/venv/bin/pip' install -q faster-whisper"
$SSH "'$REMOTE_CODE/venv/bin/python' -c 'import telegram, apscheduler, yt_dlp, faster_whisper; print(\"ptb\", telegram.__version__, \"yt-dlp\", yt_dlp.version.__version__, \"faster-whisper ок\")'"

echo "==> [6/9] Собираю workspace/CLAUDE.md из персоны"
TMP_CLAUDE_MD="$(mktemp -t marco-group-claude-md)"
trap 'rm -f "$TMP_CLAUDE_MD"' EXIT
{
  cat "$PROJECT_DIR/persona/marco-group.md"
  echo
  cat "$PROJECT_DIR/config/group-workspace-footer.md"
} > "$TMP_CLAUDE_MD"
rsync -az -e "$RSH" "$TMP_CLAUDE_MD" "$VPS:$REMOTE_WS/CLAUDE.md"
echo "    собрано, $(wc -l < "$TMP_CLAUDE_MD") строк"

echo "==> [7/9] Проверяю каталог состояния поездки (приезжает Syncthing'ом, не заливаю)"
if $SSH "test -f '$REMOTE_GROUP/plan.md'"; then
  echo "    ок: $REMOTE_GROUP/plan.md на месте"
else
  echo "    ВНИМАНИЕ: нет '$REMOTE_GROUP/plan.md'." >&2
  echo "    Проверь, что Syncthing довёз vault на VPS и что папка group/ создана в vault на маке." >&2
  echo "    Бот запустится, но плану неоткуда взяться." >&2
fi

echo "==> [8/9] Проверяю .env"
if $SSH "test -f '$REMOTE_CFG/.env'"; then
  if $SSH "grep -qE '^TELEGRAM_BOT_TOKEN=.+' '$REMOTE_CFG/.env'"; then
    echo "    .env есть, токен заполнен — не трогаю"
  else
    echo
    echo "TELEGRAM_BOT_TOKEN в $REMOTE_CFG/.env пуст."
    echo "Заполни .env и запусти deploy.sh ещё раз. Юнит не стартую."
    echo "  ssh -t -i $KEY $VPS nano '$REMOTE_CFG/.env'"
    exit 1
  fi
else
  rsync -az -e "$RSH" "$PROJECT_DIR/config/group.env.example" "$VPS:$REMOTE_CFG/.env"
  $SSH "chmod 600 '$REMOTE_CFG/.env'"
  echo
  echo "Положил образец конфига в $REMOTE_CFG/.env (chmod 600)."
  echo "Заполни .env и запусти deploy.sh ещё раз. Юнит не стартую."
  echo "  ssh -t -i $KEY $VPS nano '$REMOTE_CFG/.env'"
  exit 1
fi

echo "==> [9/9] Ставлю юнит и запускаю"
rsync -az -e "$RSH" "$SCRIPT_DIR/marco-group.service" "$VPS:/etc/systemd/system/"
$SSH "systemctl daemon-reload && systemctl enable --now marco-group.service && systemctl restart marco-group.service"
sleep 4
$SSH "systemctl --no-pager status marco-group.service" || true
echo
echo "--- последние 20 строк журнала ---"
$SSH "journalctl -u marco-group -n 20 --no-pager" || true

echo
echo "Готово. Логи: ssh -i $KEY $VPS journalctl -u marco-group -f"
echo "Состояние поездки: $REMOTE_GROUP (Syncthing довезёт правки на мак сам)"
