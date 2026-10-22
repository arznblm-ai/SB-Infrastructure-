#!/usr/bin/env bash
# Деплой синка занятости (личные календари → anton@looch.ai) с мака на VPS 163.5.29.10:
#   bash "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Calendar Busy Sync/Scripts/deploy.sh"
#
# Идемпотентно: гонять сколько угодно раз, повтор говорит «= уже». Ничего не удаляет.
# Секреты не печатаются и не едут с VPS на мак: токен looch.ai кладёт на VPS auth_work.py (H3),
# токен Аси для алертов скрипт читает на VPS из /root/.config/asya/.env.
#
# Шаги:
#   [0] мак: файлы на месте, py_compile, bash -n, unit-тесты, ssh до VPS; заливка в /opt/busy-sync/.staging
#   [1] код в /opt/busy-sync, юниты в /etc/systemd/system, daemon-reload
#   [2] нет токен-файла → таймер НЕ включаем («сначала auth_work.py»); есть → --check, успех → enable --now timer
#   [3] смоук: статус таймера, последний журнал прогона
#
# Откат — в комментарии в конце файла.

set -euo pipefail

VPS="root@163.5.29.10"
KEY="$HOME/.ssh/id_ed25519_vps"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 $VPS"
RSH="ssh -i $KEY -o BatchMode=yes"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGING="/opt/busy-sync/.staging"

fail() { echo "  ✗ $*" >&2; exit 1; }

echo "[0] Проверяю локальные файлы (до любых изменений на сервере)"
for f in "$SCRIPT_DIR/busy_sync.py" "$SCRIPT_DIR/test_busy_sync.py" "$SCRIPT_DIR/auth_work.py" \
         "$SCRIPT_DIR/busy-sync.service" "$SCRIPT_DIR/busy-sync.timer" "$SCRIPT_DIR/deploy.sh"; do
  [ -s "$f" ] || fail "нет файла $f"
done
python3 -m py_compile "$SCRIPT_DIR/busy_sync.py" "$SCRIPT_DIR/auth_work.py" "$SCRIPT_DIR/test_busy_sync.py" \
  || fail "код не компилируется — чини до деплоя"
bash -n "$SCRIPT_DIR/deploy.sh" || fail "deploy.sh: синтаксическая ошибка"
( cd "$SCRIPT_DIR" && python3 -m unittest -q test_busy_sync 2>&1 | tail -3 ) | sed 's/^/    /'
( cd "$SCRIPT_DIR" && python3 -m unittest -q test_busy_sync >/dev/null 2>&1 ) || fail "тесты красные — не деплою"
echo "  ✓ файлы на месте, код компилируется, тесты зелёные"

$SSH "command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'" \
  || fail "нет ssh до VPS или python3 на VPS старше 3.9"
echo "  ✓ VPS отвечает, python3 на месте"

$SSH "mkdir -p '$STAGING' && chmod 700 '$STAGING'"
rsync -a --checksum -e "$RSH" \
  "$SCRIPT_DIR/busy_sync.py" "$SCRIPT_DIR/busy-sync.service" "$SCRIPT_DIR/busy-sync.timer" \
  "$VPS:$STAGING/"
echo "  ✓ залито в $STAGING (боевые пути не тронуты)"

# ── всё остальное — на VPS одним прогоном ─────────────────────────────────────
set +e
$SSH 'bash -s' <<'REMOTE'
set -uo pipefail
umask 022

STAGING=/opt/busy-sync/.staging
CODE=/opt/busy-sync
TOKEN_FILE=/root/.config/second-brain/busy-sync-work-token.json
ASYA_ENV=/root/.config/asya/.env

PROBLEMS=()
ok()   { printf '  ✓ %s\n' "$*"; }
bad()  { printf '  ✗ %s\n' "$*"; PROBLEMS+=("$*"); }
warn() { printf '  ⚠ %s\n' "$*"; }
skip() { printf '  = %s\n' "$*"; }
redact() { sed -E 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/<token>/g; s/ya29\.[A-Za-z0-9._-]+/<access>/g'; }

# install_if_changed <src> <dst> <mode> → rc 0 = поставлен новый, rc 1 = уже совпадал
install_if_changed() {
  if [ -f "$2" ] && cmp -s "$1" "$2"; then return 1; fi
  install -D -m "$3" "$1" "$2"
  return 0
}

TIMER_ON=0

# ── [1] Код и юниты ─────────────────────────────────────────────────────────
echo "[1] Код в $CODE, юниты в /etc/systemd/system"
mkdir -p "$CODE"
if install_if_changed "$STAGING/busy_sync.py" "$CODE/busy_sync.py" 0755; then ok "busy_sync.py обновлён"; else skip "busy_sync.py без изменений"; fi
for f in busy-sync.service busy-sync.timer; do
  if install_if_changed "$STAGING/$f" "/etc/systemd/system/$f" 0644; then ok "$f обновлён"; else skip "$f без изменений"; fi
done
systemctl daemon-reload && ok "daemon-reload" || bad "daemon-reload не прошёл"
python3 -m py_compile "$CODE/busy_sync.py" && ok "py_compile на VPS: OK ($(python3 --version 2>&1))" \
  || bad "busy_sync.py не компилируется на VPS-питоне"
[ -r "$ASYA_ENV" ] && grep -q '^[[:space:]]*TELEGRAM_BOT_TOKEN=.' "$ASYA_ENV" \
  && ok "токен Аси для алертов есть в $ASYA_ENV" \
  || warn "нет TELEGRAM_BOT_TOKEN в $ASYA_ENV — синк будет работать, но алерты не уйдут"

# ── [2] Токен looch.ai → check → таймер ─────────────────────────────────────
echo "[2] Токен anton@looch.ai, проверка доступа, таймер"
if [ ! -f "$TOKEN_FILE" ]; then
  warn "нет $TOKEN_FILE — таймер НЕ включаю."
  warn "Сначала auth_work.py на маке (H3), потом снова deploy.sh:"
  warn "  python3 \"/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Calendar Busy Sync/Scripts/auth_work.py\""
  if systemctl is-enabled --quiet busy-sync.timer 2>/dev/null; then
    warn "при этом busy-sync.timer уже enabled — токен пропал? прогоны будут падать с алертом"
  fi
else
  chmod 600 "$TOKEN_FILE"
  CHECK_OUT=$(timeout 90 python3 "$CODE/busy_sync.py" --check 2>&1); CHECK_RC=$?
  printf '%s\n' "$CHECK_OUT" | redact | sed 's/^/    /'
  if [ "$CHECK_RC" = 0 ]; then
    ok "--check: доступ есть"
    if systemctl is-enabled --quiet busy-sync.timer 2>/dev/null && systemctl is-active --quiet busy-sync.timer; then
      skip "busy-sync.timer уже enabled и active"
    else
      systemctl enable --now busy-sync.timer >/dev/null 2>&1 && ok "busy-sync.timer: enabled, каждые 10 мин" \
        || bad "busy-sync.timer не включился"
    fi
    TIMER_ON=1
  else
    bad "--check провален (rc=$CHECK_RC) — таймер не включаю; чини по строкам ✗ выше (H1/H2 — шаринг, H3 — токен)"
  fi
fi

# ── [3] Смоук ───────────────────────────────────────────────────────────────
echo "[3] Смоук"
systemctl list-timers busy-sync.timer --no-pager 2>/dev/null | head -3 | sed 's/^/    /'
echo "  --- последний прогон: journalctl -u busy-sync -n 15 ---"
journalctl -u busy-sync -n 15 --no-pager 2>/dev/null | redact | sed 's/^/    /' || echo "    (журнала ещё нет)"
if [ -f /var/lib/busy-sync/health.json ]; then
  python3 -c 'import json; d=json.load(open("/var/lib/busy-sync/health.json")); print("    health:", d.get("status"), "·", d.get("at"), "·", d.get("detail") or "", "·", d.get("stats") or "")' 2>/dev/null
else
  echo "    health.json ещё нет (таймер не отработал ни разу)"
fi

# ── Сводка ──────────────────────────────────────────────────────────────────
echo
if [ ${#PROBLEMS[@]} -eq 0 ] && [ "$TIMER_ON" = 1 ]; then
  echo "Итог: ✓ синк занятости развёрнут, таймер каждые 10 мин."
elif [ ${#PROBLEMS[@]} -eq 0 ]; then
  echo "Итог: = код и юниты на месте, таймер ждёт токена (auth_work.py)."
else
  echo "Итог: ✗ проблем: ${#PROBLEMS[@]}"
  for p in "${PROBLEMS[@]}"; do echo "  ✗ $p"; done
fi
echo
echo "Руками (Антон), если ещё не сделано:"
echo "  H1. a.rznblm@gmail.com → настройки календаря → доступ → anton@looch.ai «только свободно/занято»."
echo "  H2. То же в tony@portalcg.us."
echo "  H3. На маке: auth_work.py → вход как anton@looch.ai → согласие (токен сам ляжет на VPS)."
echo "Логи: journalctl -u busy-sync -n 50 · план без записи: python3 $CODE/busy_sync.py --dry-run"
[ ${#PROBLEMS[@]} -eq 0 ]
REMOTE
RC=$?
set -e
exit $RC

# ── Откат (руками, на VPS: ssh -i ~/.ssh/id_ed25519_vps root@163.5.29.10) ─────
# Порядок важен: сначала таймер, потом блоки — иначе следующий тик поставит их обратно.
#
#   systemctl disable --now busy-sync.timer
#   python3 /opt/busy-sync/busy_sync.py --purge --dry-run   # сколько блоков «Busy» удалит
#   python3 /opt/busy-sync/busy_sync.py --purge             # удалить все наши блоки в окне
#
# Код /opt/busy-sync, токен /root/.config/second-brain/busy-sync-work-token.json и
# /var/lib/busy-sync откат не трогает. Блоки в прошлом --purge не удаляет (история).
