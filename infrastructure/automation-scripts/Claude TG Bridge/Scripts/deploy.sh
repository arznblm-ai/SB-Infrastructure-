#!/usr/bin/env bash
# Деплой Claude TG Bridge с мака на VPS (Hostkey, Амстердам).
#
# ВАЖНО: перед ПЕРВЫМ деплоем .env с токеном уже должен лежать на VPS:
#   /root/.config/claude-tg-bridge/.env  (chmod 600, заполнить TELEGRAM_BOT_TOKEN)
# Этот скрипт .env не создаёт и не перезаписывает.

set -euo pipefail

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes $VPS"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Создаю каталоги на VPS"
$SSH 'mkdir -p /opt/claude-tg-bridge /root/claude-workspace /root/.config/claude-tg-bridge'

echo "==> Копирую claude_tg_bridge.py"
rsync -az -e "ssh -i $KEY" "$SCRIPT_DIR/claude_tg_bridge.py" "$VPS:/opt/claude-tg-bridge/"

echo "==> Готовлю venv и зависимости"
$SSH 'test -d /opt/claude-tg-bridge/venv || python3 -m venv /opt/claude-tg-bridge/venv; /opt/claude-tg-bridge/venv/bin/pip install -q --upgrade pip python-telegram-bot'

echo "==> Копирую workspace CLAUDE.md"
rsync -az -e "ssh -i $KEY" "$SCRIPT_DIR/../config/workspace-CLAUDE.md" "$VPS:/root/claude-workspace/CLAUDE.md"

echo "==> Копирую systemd-юнит"
rsync -az -e "ssh -i $KEY" "$SCRIPT_DIR/claude-tg-bridge.service" "$VPS:/etc/systemd/system/"

echo "==> Перезапускаю сервис"
$SSH 'systemctl daemon-reload && systemctl enable claude-tg-bridge && systemctl restart claude-tg-bridge && sleep 3 && systemctl is-active claude-tg-bridge'

echo "Deployed. Check: ssh -i $KEY $VPS journalctl -u claude-tg-bridge -f"
