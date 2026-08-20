# Ранбук: установка радара цен (Марко) на VPS

### [[2026-08-15]]

Ставит Антон руками. Агентам ssh-write к проду запрещён — ниже команды для копипаста,
агент их не исполняет.

Сервер: `163.5.29.10` (Hostkey, Амстердам), юзер `root`.
Код приезжает на VPS через Syncthing вместе с vault: `/root/second-brain/infrastructure/Travel Agent/`.
Секреты через Syncthing **не** ходят — env-файл кладётся на сервер отдельно.

## 0. Предпосылки

- vault на VPS синкается (`/root/second-brain` существует и свежий);
- `python3` есть (`python3 -V`); внешних пакетов не требуется — только stdlib
  (`certifi` используется, если установлен, иначе системный набор CA).

## 1. Секреты

```bash
mkdir -p /root/.config/second-brain
nano /root/.config/second-brain/travel.env
chmod 600 /root/.config/second-brain/travel.env
```

Содержимое (три ключа, формат `KEY=VALUE` без кавычек и без `export` — так его
понимают и `get_secret()` скрипта, и systemd `EnvironmentFile`):

```
TRAVELPAYOUTS_TOKEN=...
TRAVEL_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
TRAVEL_WEBHOOK_SECRET=...
```

**Где взять `TRAVELPAYOUTS_TOKEN`:** аккаунт на travelpayouts.com → личный кабинет →
раздел API / «Токен для API» (доступ к Data API выдаётся партнёрам Travelpayouts).
Тот же токен кладётся и на мак, в `~/.config/second-brain/travel.env`, если Антон
хочет гонять `--once` из сессии Марко локально.

Без токена скрипт не выдумывает данные: печатает
`not verified: нет TRAVELPAYOUTS_TOKEN …` и выходит с кодом 2.

## 2. Таблица «Anton travel» и веб-хук

1. Создать Google-таблицу с именем **Anton travel** (вкладку «Радар цен» создаст сам
   веб-хук при первой записи, руками её заводить не нужно).
2. Скопировать `SPREADSHEET_ID` из URL: `docs.google.com/spreadsheets/d/<ID>/edit`.
3. script.google.com → New project → вставить содержимое
   `infrastructure/Travel Agent/Scripts/apps_script_webhook.gs`.
4. Подставить в код `SPREADSHEET_ID` и `SECRET` (секрет — любая длинная случайная
   строка; она же идёт в `travel.env` как `TRAVEL_WEBHOOK_SECRET`).
5. Deploy → New deployment → тип **Web app**, «Execute as: Me»,
   «Who has access: Anyone». Скопировать URL `…/exec` в `travel.env` как
   `TRAVEL_WEBHOOK_URL`.
6. Проверка деплоя: открыть этот URL в браузере — должно вернуться
   `{"ok":true,"service":"travel-price-radar"}`.

Если веб-хук не настроен, прогон не падает: печатает warning и пишет только md-радар.

## 3. Проверка вручную до таймера

```bash
cd "/root/second-brain/infrastructure/Travel Agent/Scripts"
python3 price_radar.py --dry-run      # без сети и без записи: покажет URL запросов и что записал бы
python3 price_radar.py --once -v      # боевой прогон + компактная таблица в stdout
```

Ожидаемо: `--dry-run` всегда выходит с кодом 0 (без токена — с warning),
`--once` без токена — код 2. Ошибка по одному маршруту не валит прогон:
маршрут помечается `error` в md-радаре, код возврата 1 только если упали все.

## 4. Юниты

```bash
cd "/root/second-brain/infrastructure/Travel Agent/Scripts/deploy"
cp price-radar.service price-radar.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now price-radar.timer
```

Проверка таймзоны: `Timezone=` в `.timer` требует systemd >= 251 (`systemctl --version`).
Если версия старше — убрать строку `Timezone=Europe/Moscow` и поставить
`OnCalendar=*-*-* 06:00:00 UTC` (то же 09:00 МСК).

## 5. Контроль

```bash
systemctl list-timers price-radar.timer     # когда следующий запуск
systemctl start price-radar.service         # прогон прямо сейчас
journalctl -u price-radar -n 100 --no-pager
```

## 6. Откат

```bash
systemctl disable --now price-radar.timer
rm /etc/systemd/system/price-radar.{service,timer}
systemctl daemon-reload
```

## Грабли

- **State на VPS свой.** `~/.config/second-brain/travel-radar-state.json` лежит вне
  vault и Syncthing'ом не ходит: у мака и у VPS свои истории минимумов. Δ в md-радаре
  считается относительно предыдущего минимума **той машины, что делала прогон**.
  Боевой ежедневный прогон — только на VPS; на маке гонять `--once` точечно, понимая,
  что его Δ считается от локальной истории.
- **Снапшоты и md — в vault, значит синкаются.** `data/aviasales/YYYY-MM-DD.json` и
  `outputs/{self} {research} радар цен – YYYY-MM-DD.md` приезжают на мак через
  Syncthing. Повторный прогон в тот же день перезаписывает файл дня (не плодит дубли),
  но строки в Google-таблице дозаписываются — там будет несколько прогонов за дату.
- **Цена за одного пассажира.** Эндпоинт `v3/prices_for_dates` не принимает число
  пассажиров; поле `adults` в `config/routes.json` — контекст для комментария, итог на
  двоих Марко считает сам и говорит об этом вслух.
- **Это кэш, а не живой поиск.** API отдаёт находки пользователей Aviasales за
  последние 48 часов — по ссылке цена может отличаться. Радар нужен для динамики
  и сигнала «пора смотреть», не как цена покупки.
