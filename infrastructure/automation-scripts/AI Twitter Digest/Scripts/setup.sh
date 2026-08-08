#!/bin/bash
# setup.sh — первичная подготовка runtime для AI Twitter Digest.
#
# Что делает (идемпотентно, безопасно запускать повторно):
#   1. создаёт ~/.config/ai-twitter-digest/ с правами 700;
#   2. создаёт шаблон env-файла (600) с плейсхолдерами — существующий НЕ перезаписывает;
#   3. создаёт venv и ставит в него twscrape;
#   4. печатает ручные шаги Антона (BotFather, chat_id, аккаунт X).
#
# Ничего никуда не отправляет и не логинится в X.

set -uo pipefail

RUNTIME_DIR="${HOME}/.config/ai-twitter-digest"
ENV_FILE="${RUNTIME_DIR}/env"
VENV_DIR="${RUNTIME_DIR}/venv"
VENV_PY="${VENV_DIR}/bin/python3"
PROJECT_DIR="/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/AI Twitter Digest"
BASE_PYTHON="${BASE_PYTHON:-python3}"

echo "==> Runtime-каталог: ${RUNTIME_DIR}"
mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

if [[ -f "$ENV_FILE" ]]; then
  echo "==> env уже существует, не трогаю: ${ENV_FILE}"
else
  cat > "$ENV_FILE" <<'ENVEOF'
# AI Twitter Digest — секреты. Файл живёт ВНЕ vault, в git не попадает.
# Токен бота: BotFather → /newbot → скопировать токен сюда.
AI_DIGEST_BOT_TOKEN="PASTE_BOT_TOKEN_HERE"
# chat_id: напиши боту любое сообщение, затем
#   ~/.config/ai-twitter-digest/venv/bin/python3 "<проект>/Scripts/send_digest.py" --get-chat-id
AI_DIGEST_CHAT_ID="PASTE_CHAT_ID_HERE"
ENVEOF
  echo "==> Создал шаблон env: ${ENV_FILE}"
fi
chmod 600 "$ENV_FILE"

if [[ -x "$VENV_PY" ]]; then
  echo "==> venv уже есть: ${VENV_DIR}"
else
  echo "==> Создаю venv: ${VENV_DIR}"
  "$BASE_PYTHON" -m venv "$VENV_DIR" || { echo "Не удалось создать venv"; exit 1; }
fi

echo "==> Ставлю twscrape в venv"
"$VENV_PY" -m pip install --quiet --upgrade pip || true
"$VENV_PY" -m pip install --quiet --upgrade twscrape || { echo "pip install twscrape не удался"; exit 1; }
"$VENV_PY" -c 'import twscrape; print("twscrape OK:", getattr(twscrape, "__version__", "?"))'

cat <<EOF

────────────────────────────────────────────────────────────
Автоматическая часть готова. Дальше — ручные шаги Антона:

1) Telegram-бот
   • BotFather → /newbot → получить токен
   • вписать токен в ${ENV_FILE} (AI_DIGEST_BOT_TOKEN)
   • написать боту любое сообщение в Telegram
   • узнать chat_id:
       "${VENV_PY}" "${PROJECT_DIR}/Scripts/send_digest.py" --get-chat-id
   • вписать полученный chat_id в ${ENV_FILE} (AI_DIGEST_CHAT_ID)

2) Аккаунт X для twscrape (креды вводит только Антон, скрипты их не трогают)
   ⚠️ Рекомендация: заводить ОТДЕЛЬНЫЙ burner-аккаунт X, не основной —
      скрейпинг может привести к блокировке аккаунта.
       cd "${RUNTIME_DIR}"
       "${VENV_DIR}/bin/twscrape" add_accounts --db "${RUNTIME_DIR}/accounts.db" <username> <password> <email> <email_password>
       "${VENV_DIR}/bin/twscrape" login_accounts --db "${RUNTIME_DIR}/accounts.db"
   Проверить:
       "${VENV_DIR}/bin/twscrape" accounts --db "${RUNTIME_DIR}/accounts.db"

3) Список аккаунтов для дайджеста
   Заполнить ${PROJECT_DIR}/config/accounts.json
   (формат: {"accounts": [{"handle": "...", "name": "...", "category": "..."}]})

4) Смоук-тест и сухой прогон
       "${VENV_PY}" "${PROJECT_DIR}/Scripts/fetch_tweets.py" --account karpathy --limit 5
       "${PROJECT_DIR}/Scripts/run_digest.sh" --dry-run

5) Автозапуск (09:00 и 21:00) — только когда шаги 1-4 прошли
       python3 "${PROJECT_DIR}/Scripts/install_launch_agent.py"
────────────────────────────────────────────────────────────
EOF
