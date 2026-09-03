# Ранбук: установка Pavel на VPS

### [[2026-09-01]]

Ставит Антон руками. Агентам ssh-write к проду запрещён — ниже команды для
копипаста, агент их не исполняет.

Сервер: `163.5.29.10` (Hostkey, Амстердам), юзер `root`.
Код приезжает на VPS через Syncthing вместе с vault: `/root/second-brain/infrastructure/Pavel/`.
Секреты через Syncthing **не** ходят — env-файл кладётся на сервер отдельно.

**Без таймера.** Pavel не ставится ни на systemd timer, ни на cron — прогон
только по прямой команде Антона (ssh или через Claude TG Bridge).
**Не запускать с мака.** Единственный писатель состояния (sqlite) и файлов
vault (`transcripts/telegram/`, `outputs/`) — VPS. Второй экземпляр на маке с
той же сессией/дедуп-состоянием всё разъедет.

## 0. Предпосылки

- vault на VPS синкается (`/root/second-brain` существует и свежий);
- `python3.11` есть (`python3 -V`);
- api_id/api_hash Telegram уже получены на my.telegram.org (аккаунт Антона →
  API development tools → создать приложение).

## 1. Venv и зависимости

```bash
mkdir -p /opt/pavel
python3 -m venv /opt/pavel/venv
cd "/root/second-brain/infrastructure/Pavel"
/opt/pavel/venv/bin/pip install -r requirements.txt
```

## 2. Секреты

```bash
mkdir -p /root/.config/second-brain
nano /root/.config/second-brain/pavel.env
chmod 600 /root/.config/second-brain/pavel.env
```

Содержимое (`KEY=VALUE`, без кавычек, без `export` — так понимают и
`load_env()` скрипта, и systemd `EnvironmentFile`, если он когда-нибудь
понадобится для разового прогона):

```
# my.telegram.org → API development tools → создать приложение
PAVEL_API_ID=...
PAVEL_API_HASH=...
# Номер телефона аккаунта Антона в международном формате
PAVEL_PHONE=+7...
# Файл сессии Telethon (создаётся при первом login, дальше не трогать)
PAVEL_SESSION=/root/.config/second-brain/pavel.session
# Состояние (sqlite) — вне vault, никогда не синкается и не коммитится
PAVEL_STATE_DIR=/root/.local/state/pavel
# Чаты, которые Pavel не должен видеть вообще (в т.ч. Ралина) — обязательно
# заполнить до первого backfill/run, иначе команды откажутся работать
PAVEL_EXCLUDE_CHAT_IDS=
# Заполняются после шага H2 (Google-таблица + Apps Script), см. ниже
PAVEL_SHEETS_WEBHOOK_URL=
PAVEL_SHEETS_WEBHOOK_SECRET=
```

## 3. Логин

```bash
cd "/root/second-brain/infrastructure/Pavel"
/opt/pavel/venv/bin/python Scripts/pavel.py login
```

Telethon спросит код из Telegram (придёт в само приложение Telegram Антона) —
ввести интерактивно. При успехе выведет id аккаунта и путь к файлу сессии.

## 4. Исключить чат(ы) с Ралиной (обязательно до чтения текста)

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py resolve --query "Ралина"
```

Скопировать `id` нужного чата (или нескольких, через запятую) в
`PAVEL_EXCLUDE_CHAT_IDS` в `pavel.env`. Команды `backfill`/`run` откажутся
читать текст сообщений, пока список пуст (защита по умолчанию, не забыть
на первом прогоне).

## 5. Первый сбор метаданных

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py scan-meta
```

Без LLM, без текста сообщений — только диалоги, контакты, участники групп,
bio, общие чаты. Проверить вывод (JSON-статистика) на разумность.

## 6. Бэкфилл текста (12 месяцев)

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py backfill --months 12
```

Инкрементально, с паузами между чатами (`chat_pause_seconds` в
`config/settings.json`) и обработкой FloodWait
(`flood_sleep_threshold`). Полная история (`--full`) — отдельный осознанный
второй этап, не запускать вместе с первым бэкфиллом.

## 7. Экспорт сырья в vault

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py export-md
```

Пишет md-файлы в `/root/second-brain/transcripts/telegram/` (по чату один
файл, только для чатов с ≥ `min_messages_for_md` сообщений) и
`transcripts/telegram/index.md`.

## H2. Google-таблица «Anton Telegram» и веб-хук

1. Создать Google-таблицу с именем **Anton Telegram** (листы веб-хук создаст
   сам при первой записи, руками заводить не нужно).
2. Скопировать `SPREADSHEET_ID` из URL: `docs.google.com/spreadsheets/d/<ID>/edit`.
3. script.google.com → New project → вставить содержимое
   `infrastructure/Pavel/Scripts/apps_script_webhook.gs`.
4. Подставить в код `SPREADSHEET_ID` и `SECRET` (секрет — любая длинная
   случайная строка; она же идёт в `pavel.env` как
   `PAVEL_SHEETS_WEBHOOK_SECRET`).
5. Deploy → New deployment → тип **Web app**, «Execute as: Me»,
   «Who has access: Anyone». URL `…/exec` → `pavel.env` как
   `PAVEL_SHEETS_WEBHOOK_URL`.
6. Проверка деплоя: открыть URL в браузере — должно вернуться
   `{"ok":true,"service":"pavel-telegram-network"}`.

Если веб-хук не настроен, `push-sheets` не выдумывает данные — печатает
ошибку и не пишет в таблицу; остальной конвейер (`run --skip-sheets`)
продолжает работать.

## 8. Профилирование

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py profile --only-new
```

По одному вызову `claude -p --model sonnet --output-format json` (без
инструментов) на активного контакта (≥ `profile_min_msgs` сообщений за
`profile_months` месяцев, по умолчанию 20/12). Требует, чтобы `claude` CLI
был доступен на VPS.

## 9. Выгрузка в таблицу

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py push-sheets --dry-run   # сначала проверка без отправки
/opt/pavel/venv/bin/python Scripts/pavel.py push-sheets
```

## 10. Отчёт

```bash
/opt/pavel/venv/bin/python Scripts/pavel.py report
```

Пишет `outputs/{automation} {report} Pavel прогон – YYYY-MM-DD.md` и
`outputs/network.json` (без текстов сообщений — это то, что читает скилл
`/pavel`).

## Дальше — одной командой

После первого ручного прохода по шагам 3–10 весь конвейер (без логина)
запускается одной командой:

```bash
cd "/root/second-brain/infrastructure/Pavel"
/opt/pavel/venv/bin/python Scripts/pavel.py run
```

`run` = `scan-meta → backfill (инкремент) → export-md → profile --only-new →
push-sheets → report`. Флаги: `--months`, `--limit`, `--min-msgs`,
`--skip-sheets` (если веб-хук ещё не настроен),
`--allow-empty-exclude` (осознанно, не по умолчанию).

Таймер не ставим. Запускать только когда Антон явно попросил — вручную по
ssh, либо через Claude TG Bridge (`@rznblm_claude_bot`), у которого уже есть
доступ к серверу.

## Контроль

```bash
# Последний прогон и статистика
cat "/root/second-brain/infrastructure/Pavel/outputs/network.json" | head -50
ls -la "/root/second-brain/transcripts/telegram/"

# Тесты (после любого обновления кода на VPS)
cd "/root/second-brain/infrastructure/Pavel"
.venv/bin/pytest -q   # или /opt/pavel/venv/bin/pytest -q, если тестовый venv отдельный
```

## Откат

Прогонов, которые нужно «остановить», нет — таймера не существует.
Чтобы полностью отключить Pavel:

```bash
rm -rf /root/.config/second-brain/pavel.session /root/.local/state/pavel
# .env оставить или удалить по решению Антона — секреты, не рантайм-состояние
```

## Грабли

- **Состояние на VPS своё и не синкается.** `$PAVEL_STATE_DIR/pavel.db`
  (полный текст сообщений) живёт только на VPS, вне Syncthing и вне git.
  Второй запуск с той же сессией с мака невозможен по правилу «один и тот же
  бот/сессия — одна машина» (Agent Operating Standard).
- **Пустой `PAVEL_EXCLUDE_CHAT_IDS` — это стоп, а не варнинг.** `backfill` и
  `run` откажутся читать текст, пока список пуст, если не передан
  `--allow-empty-exclude`. Это специально — чтобы чат(ы) с Ралиной не попали
  внутрь по забывчивости.
- **`claude` CLI должен быть на VPS для `profile`.** Если его нет —
  `profile`/`run` упадут на этом шаге; остальной конвейер (`--skip-sheets`
  не помогает тут, нужен отдельный флаг или ручной пропуск шага) не
  профилирует новых людей, но метаданные и текст всё равно соберутся.
- **`push-sheets` ничего не выдумывает без веб-хука.** Нет
  `PAVEL_SHEETS_WEBHOOK_URL`/`SECRET` — команда сообщает об этом явно и не
  пишет в таблицу; `run --skip-sheets` пропускает шаг осознанно.
- **`python -m pavel` не работает** — точка входа только
  `Scripts/pavel.py <command>` (пакет не собран как `__main__`-модуль).
