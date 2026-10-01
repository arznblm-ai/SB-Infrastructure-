#!/usr/bin/env bash
# Запускается НА МАКЕ. Находит свежескачанный ключ сервисного аккаунта Google в ~/Downloads,
# показывает его email (на него надо расшарить календарь) и кладёт ключ на VPS для Марко.
set -euo pipefail
KEY="$(grep -l '"type": *"service_account"' "$HOME"/Downloads/*.json 2>/dev/null | xargs ls -t 2>/dev/null | head -1 || true)"
[ -n "$KEY" ] || { echo "Не нашёл ключ сервисного аккаунта (*.json) в ~/Downloads. Скачай его в Google Cloud и запусти ещё раз."; exit 1; }
EMAIL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["client_email"])' "$KEY")"
echo "Ключ: $(basename "$KEY")"
scp -q -i "$HOME/.ssh/id_ed25519_vps" "$KEY" root@163.5.29.10:/root/.config/marco-group/gcal-key.json
ssh -i "$HOME/.ssh/id_ed25519_vps" root@163.5.29.10 "chown root:root /root/.config/marco-group/gcal-key.json && chmod 600 /root/.config/marco-group/gcal-key.json"
echo
echo "Ключ на сервере. Теперь расшарь календарь «Наш трип» на этот адрес"
echo "с правом «Вносить изменения в мероприятия»:"
echo
echo "    $EMAIL"
echo
echo "Ключ в Downloads больше не нужен — можно удалить: $KEY"
