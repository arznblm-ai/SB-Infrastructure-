# Ранбук: установка синка Financial Coach на VPS

### [[2026-08-14]]

Ставит Антон руками. Агентам ssh-write к проду запрещён — ниже команды для копипаста,
агент их не исполняет.

Сервер: `163.5.29.10` (Hostkey, Амстердам), юзер `root`.
Код приезжает на VPS через Syncthing вместе с vault: `/root/second-brain/infrastructure/Financial Coach/`.
Секреты через Syncthing **не** ходят — env-файл кладётся на сервер отдельно.

## 0. Предпосылки

- vault на VPS синкается (`/root/second-brain` существует и свежий);
- `python3` есть (`python3 -V`); внешних пакетов не требуется — `requests` используется,
  если он установлен, иначе скрипты падают на `urllib` из stdlib.

## 1. Секреты

```bash
mkdir -p /root/.config/second-brain
nano /root/.config/second-brain/finance.env
chmod 600 /root/.config/second-brain/finance.env
```

Содержимое (значения — из такого же файла на маке, руками, не через синк):

```
ZENMONEY_TOKEN=...
# PLANFACT_API_KEY=...      # когда появится ключ
# TG_BOT_TOKEN=...          # когда решится механика CRM-топика
# TG_CRM_CHAT_ID=...
# TG_CRM_TOPIC_ID=...
```

Формат `KEY=VALUE` без кавычек и без `export` — его понимают и `common.load_env`,
и systemd `EnvironmentFile`. Закомментированные строки systemd игнорирует.

## 2. Проверка вручную до таймера

```bash
cd "/root/second-brain/infrastructure/Financial Coach/Scripts"
python3 sync_daily.py            # dry-run: без сети и без записи
python3 sync_daily.py --live -v  # боевой прогон (первый забор ZenMoney = вся история)
```

Ожидаемо на старте: `planfact=SKIP` (нет ключа), `crm=SKIP` (механика не выбрана),
`zenmoney=OK`, `render=FAIL` до появления `data/model.json` — это нормально,
пока модель не собирается. Код возврата станет нулевым, когда рендер получит модель.

## 3. Юниты

```bash
cd "/root/second-brain/infrastructure/Financial Coach/Scripts/deploy"
cp financial-coach-sync.service financial-coach-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now financial-coach-sync.timer
```

Проверка таймзоны: `Timezone=` в `.timer` требует systemd >= 251 (`systemctl --version`).
Если версия старше — убрать строку `Timezone=Europe/Moscow` и поставить
`OnCalendar=*-*-* 05:30:00 UTC` (то же 08:30 МСК).

## 4. Контроль

```bash
systemctl list-timers financial-coach-sync.timer   # когда следующий запуск
systemctl start financial-coach-sync.service       # прогон прямо сейчас
journalctl -u financial-coach-sync -n 100 --no-pager
```

Снапшоты появляются в `/root/second-brain/infrastructure/Financial Coach/data/<источник>/YYYY-MM-DD.json`
и уезжают на мак Syncthing'ом. Копия дашборда в `~/Desktop/Vibecode OUT/` на VPS
не делается (нет Desktop) — шаг логирует предупреждение и не падает.

## 5. Откат

```bash
systemctl disable --now financial-coach-sync.timer
rm /etc/systemd/system/financial-coach-sync.{service,timer}
systemctl daemon-reload
```

## Грабли

- **Один бот — один полер.** Когда механика CRM-топика станет Bot API `getUpdates`,
  нельзя держать второй полер того же токена (Гермес/inbox-бот) — иначе оба будут
  воровать апдейты друг у друга.
- **Состояние не синхронизируется.** `data/*/state.json` — это offsets; они лежат в
  vault и приезжают на мак. Не запускать `--live` одновременно на маке и на VPS,
  иначе инкремент ZenMoney разъедется. Боевой прогон — только на VPS.
- **Первый прогон ZenMoney тяжёлый:** `serverTimestamp=0` тянет всю историю.
  Повторить полный импорт можно `python3 pull_zenmoney.py --live --full`.
