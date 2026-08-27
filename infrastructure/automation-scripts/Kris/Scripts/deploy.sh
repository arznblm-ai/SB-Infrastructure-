#!/usr/bin/env bash
# Деплой Крис с мака на VPS (Hostkey, Амстердам). Идемпотентно: гонять сколько угодно раз.
#
# Что делает:
#   код -> /opt/kris, venv, каталоги /var/lib/kris/{buffer,chatlog,workspace},
#   workspace/CLAUDE.md собирается из persona/kris.md + config/workspace-footer.md,
#   .env берётся у старого моста, если своего ещё нет,
#   claude-tg-bridge выключается (решение владельца: мост упраздняется),
#   kris.service ставится и запускается.
#
# Ничего не удаляет: ни память, ни чатлоги, ни буферы.

set -euo pipefail

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes $VPS"
RSH="ssh -i $KEY"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MEMORY_DIR="/root/second-brain/infrastructure/Kris/memory"

fail() { echo "ОШИБКА: $*" >&2; exit 1; }

echo "==> [1/9] Проверяю локальные файлы"
for f in \
  "$SCRIPT_DIR/kris_bot.py" \
  "$SCRIPT_DIR/kris.service" \
  "$PROJECT_DIR/persona/kris.md" \
  "$PROJECT_DIR/config/workspace-footer.md"
do
  [ -f "$f" ] || fail "нет файла $f"
done
python3 -m py_compile "$SCRIPT_DIR/kris_bot.py" || fail "kris_bot.py не компилируется"
echo "    ок"

echo "==> [2/9] Создаю каталоги на VPS"
$SSH "mkdir -p /opt/kris /var/lib/kris/buffer /var/lib/kris/chatlog /var/lib/kris/workspace /root/.config/kris $MEMORY_DIR && chmod 700 /root/.config/kris"

echo "==> [3/9] Копирую код"
rsync -az -e "$RSH" "$SCRIPT_DIR/kris_bot.py" "$VPS:/opt/kris/"

echo "==> [4/9] Готовлю venv и зависимости"
$SSH 'test -d /opt/kris/venv || python3 -m venv /opt/kris/venv; /opt/kris/venv/bin/pip install -q --upgrade pip "python-telegram-bot[job-queue]"'
$SSH '/opt/kris/venv/bin/python -c "import telegram, apscheduler; print(\"ptb\", telegram.__version__)"'

echo "==> [5/9] Собираю workspace/CLAUDE.md из персоны"
TMP_CLAUDE_MD="$(mktemp -t kris-claude-md)"
trap 'rm -f "$TMP_CLAUDE_MD"' EXIT
{
  cat "$PROJECT_DIR/persona/kris.md"
  echo
  cat "$PROJECT_DIR/config/workspace-footer.md"
} > "$TMP_CLAUDE_MD"
rsync -az -e "$RSH" "$TMP_CLAUDE_MD" "$VPS:/var/lib/kris/workspace/CLAUDE.md"
echo "    собрано, $(wc -l < "$TMP_CLAUDE_MD") строк"

echo "==> [6/9] Проверяю .env"
$SSH bash -s <<'REMOTE'
set -euo pipefail
NEW=/root/.config/kris/.env
OLD=/root/.config/claude-tg-bridge/.env
if [ -f "$NEW" ]; then
  echo "    .env уже есть, не трогаю"
elif [ -f "$OLD" ]; then
  echo "    .env нет - копирую токен от старого моста ($OLD)"
  grep -E '^TELEGRAM_(BOT_TOKEN|ALLOWED_USER)=' "$OLD" > "$NEW"
  chmod 600 "$NEW"
  echo "    скопировано. Бот в BotFather тот же, что был у моста"
else
  echo "ОШИБКА: нет ни $NEW, ни $OLD. Положи .env с TELEGRAM_BOT_TOKEN вручную (chmod 600)" >&2
  exit 1
fi
grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$NEW" || { echo "ОШИБКА: TELEGRAM_BOT_TOKEN в $NEW пуст" >&2; exit 1; }
REMOTE

echo "==> [7/9] Выключаю старый мост claude-tg-bridge"
$SSH 'systemctl disable --now claude-tg-bridge.service 2>&1 || true; systemctl is-active claude-tg-bridge.service || echo "    мост остановлен"'

echo "==> [8/9] Ставлю юнит kris.service"
rsync -az -e "$RSH" "$SCRIPT_DIR/kris.service" "$VPS:/etc/systemd/system/"
$SSH 'systemctl daemon-reload && systemctl enable kris.service'

echo "==> [9/9] Запускаю Крис"
$SSH 'systemctl restart kris.service && sleep 4 && systemctl is-active kris.service' \
  || { echo "Крис не поднялась, последние логи:"; $SSH 'journalctl -u kris -n 40 --no-pager'; exit 1; }

echo
echo "Готово. Логи: ssh -i $KEY $VPS journalctl -u kris -f"
echo "Память проектов: $MEMORY_DIR (Syncthing довезёт на мак сам)"
