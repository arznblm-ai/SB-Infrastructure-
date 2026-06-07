#!/usr/bin/env bash
set -euo pipefail

ENV_DIR="/Users/anton/.config/second-brain"
ENV_FILE="$ENV_DIR/daily-focus.env"
LOG_FILE="/Users/anton/Library/Logs/daily-focus.log"

usage() {
  cat <<'USAGE'
Usage: setup_telegram_delivery.sh

Interactive setup for Daily Focus Telegram delivery.

Before running:
  1. Open your Daily Focus bot in Telegram.
  2. Send /start to the bot.
  3. Keep the BotFather token ready, but do not paste it into chats.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ $# -gt 0 ]]; then
  usage >&2
  exit 2
fi

mkdir -p "$ENV_DIR" "$(dirname "$LOG_FILE")"

echo "Daily Focus Telegram setup"
echo
echo "Before continuing, open your bot in Telegram and send /start."
echo "Paste the bot token here. It will be hidden while typing and stored outside the vault."
printf "Telegram bot token: "
read -r -s TELEGRAM_BOT_TOKEN
printf "\n"

if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
  echo "Token is empty. Aborting." >&2
  exit 1
fi

echo
echo "Trying to detect your chat_id through getUpdates..."

UPDATES="$(curl -fsS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" || true)"

TELEGRAM_CHAT_ID="$(python3 - "$UPDATES" <<'PY'
import json
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    payload = json.loads(raw)
except Exception:
    print("")
    raise SystemExit

for item in reversed(payload.get("result", [])):
    message = item.get("message") or item.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is not None:
        print(chat_id)
        break
PY
)"

if [[ -z "$TELEGRAM_CHAT_ID" ]]; then
  echo "Could not auto-detect chat_id."
  echo "Most likely you have not sent /start to the bot yet."
  printf "Enter Telegram chat_id manually: "
  read -r TELEGRAM_CHAT_ID
fi

if [[ -z "$TELEGRAM_CHAT_ID" ]]; then
  echo "chat_id is empty. Aborting." >&2
  exit 1
fi

umask 077
cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}"
EOF
chmod 600 "$ENV_FILE"

echo "Telegram env saved to $ENV_FILE"
echo "Testing Telegram delivery..."

if curl -fsS \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=Daily Focus Telegram delivery настроен." \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null; then
  echo "Telegram test message sent successfully."
  echo "Telegram setup completed $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
else
  echo "Telegram test failed. Check token/chat_id and run setup again." >&2
  exit 1
fi
