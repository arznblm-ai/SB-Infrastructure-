# Ранбук: Роки на VPS

### [[2026-10-22]]

Бот @rznblm_rocky_bot живёт только на VPS (163.5.29.10). Деплой с мака: `bash deploy.sh` из этой папки. Код и решения — `../CLAUDE.md`, план — `tasks/Rocky/`.

## 1. BotFather

Бот уже создан (@rznblm_rocky_bot), токен лежит на VPS в `/root/.config/coach-bot/.env` — деплой копирует его сам. Проверить в @BotFather:
- `/setprivacy` → бот → **Disable** (иначе в группе бот видит только команды);
- `/setjoingroups` → **Enable**.

## 2. Группа

Создать группу, добавить Ралину и бота. Админ боту не нужен.

## 3. Деплой

```bash
bash deploy.sh
```

Скрипт гоняет pytest на маке, проверяет claude на VPS (≥ 2.1.283), кладёт код в `/opt/rocky`, собирает `workspace/CLAUDE.md` (персона + футер), создаёт `/root/.config/rocky/.env` и сам подставляет `TELEGRAM_BOT_TOKEN` (из `coach-bot/.env`), `USER_ANTON_ID`, `USER_RALINA_ID`, `CLAUDE_CODE_OAUTH_TOKEN` (из `.env` группового Марко). Значения не печатаются. Затем permcheck и старт юнита. `GROUP_CHAT_ID` пуст — бот в режиме настройки.

Только права, без перезапуска бота: `bash deploy.sh --permcheck`.

## 4. Сбор GROUP_CHAT_ID

Написать что-нибудь в группе. Бот ответит `chat_id` и `user_id`. Вписать `GROUP_CHAT_ID` (отрицательное число) в `.env` и перезапустить:

```bash
ssh -t -i ~/.ssh/id_ed25519_vps root@163.5.29.10 nano /root/.config/rocky/.env
ssh -i ~/.ssh/id_ed25519_vps root@163.5.29.10 systemctl restart rocky
```

Если user_id в ответе не совпадает с `USER_ANTON_ID`/`USER_RALINA_ID` — поправить там же.

## 5. Smoke-тесты

1. Ралина пишет «привет» — Роки начинает онбординг (профиля нет). Ответить данными — в ответе норма, в `/var/lib/rocky/data/profiles.json` профиль.
2. Фото еды — оценка ккал/БЖУ вилкой, запись в `/var/lib/rocky/data/journal/<дата>.jsonl`.
3. Альбом из 2 фото + подпись — один ответ (склейка 15 с).
4. «Бег 35 мин, пульс 148» — тренировка в журнале. «Вес 72.4» — замер.
5. «Купи хлеба» между вами — Роки молчит (`NO_REPLY` в bot.log).
6. `/today` — цифры за сегодня без модели; `/status` — состояние.
7. `/daily_now` (только Антон) — итог дня в группу; `/weekly_now` — неделя, копия в `data/reports/`.

## 6. Диагностика

```bash
ssh -i ~/.ssh/id_ed25519_vps root@163.5.29.10
systemctl --no-pager status rocky
journalctl -u rocky -f
tail -f /root/.config/rocky/bot.log          # Claude ok/ошибки, Журнал: ..., Actions отклонены: ...
cat /root/.config/rocky/state.json           # daily_sent / weekly_sent / last_error
ls /var/lib/rocky/data/journal/ /var/lib/rocky/chatlog/
```

## 7. Грабли

- **Privacy mode.** Не выключен — бот молчит на всё, кроме команд. Лечится в BotFather + рестарт.
- **Один токен — один процесс.** Если токен coach-bot ещё кем-то поллится (старый юнит?), Telegram рвёт long polling (в bot.log `Conflict`). Держать только `rocky.service`.
- **Перезапуск изнутри.** Из сессии внутри этого же юнита — только `systemd-run --on-active=30 systemctl restart rocky`.
- **«OAuth session expired»** — пуст или протух `CLAUDE_CODE_OAUTH_TOKEN` в `.env`. Роки пишет в группу «не может ответить».
- **claude на VPS обновили** — прогнать `bash deploy.sh --permcheck`: на версиях < 2.1.283 Grep по data мог вернуть содержимое `.env`.
- **Итог дня/неделя не пришли.** Тик догоняет только окно (21:00–22:59, пн 09:00–13:59). Флаг ставится ДО прогона: если модель упала, в группу уходят голые цифры. Повторить — `/daily_now`, `/weekly_now`.
- **Квота Claude общая** с Асей, Крис, Марко и дайджестом, модель — Opus на каждую пачку.
- **Данные только на VPS.** Журнал, профили, фото, чатлог в vault и GitHub не уезжают. Фото чистятся через 30 дней, журнал вечный.
- **HEIC** не поддерживается: пусть шлют как фото, не файлом. Голосовые Роки не слушает.
