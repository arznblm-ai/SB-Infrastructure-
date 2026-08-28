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
PLANFACT_API_KEY=...        # есть с 2026-08-15
# TG_BOT_TOKEN=...          # когда решится механика CRM-топика
# TG_CRM_CHAT_ID=...
# TG_CRM_TOPIC_ID=...
```

Проще всего перенести обе строки с мака (выполнять **на маке**, пароль root спросит ssh):

```bash
scp -i ~/.ssh/id_ed25519_vps ~/.config/second-brain/finance.env root@163.5.29.10:/root/.config/second-brain/finance.env
```

Формат `KEY=VALUE` без кавычек и без `export` — его понимают и `common.load_env`,
и systemd `EnvironmentFile`. Закомментированные строки systemd игнорирует.

## 2. Проверка вручную до таймера

```bash
cd "/root/second-brain/infrastructure/Financial Coach/Scripts"
python3 sync_daily.py            # dry-run: без сети и без записи
python3 sync_daily.py --live -v  # боевой прогон (первый забор ZenMoney = вся история)
```

Ожидаемо сейчас (2026-08-27, оба ключа на месте, модель собрана):
`planfact=OK · zenmoney=OK · crm=SKIP` (механика топика не выбрана) `· render=OK`, exit 0.
`crm=SKIP` — это штатно, не ошибка (код возврата 3 у шага, общий прогон остаётся зелёным).
История уже импортирована с мака и приехала Syncthing'ом, так что первый прогон на VPS —
обычный инкремент, не полная выгрузка.

## 3. Юниты

```bash
cd "/root/second-brain/infrastructure/Financial Coach/Scripts/deploy"
cp financial-coach-sync.service financial-coach-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now financial-coach-sync.timer
```

Проверка таймзоны: `Timezone=` в `.timer` требует systemd >= 251 (`systemctl --version`).
Если версия старше — убрать строку `Timezone=Europe/Moscow` и поставить
`OnCalendar=*-*-* 18:00:00 UTC` (то же 21:00 МСК).

## 4. Контроль

```bash
systemctl list-timers financial-coach-sync.timer   # когда следующий запуск
systemctl start financial-coach-sync.service       # прогон прямо сейчас
journalctl -u financial-coach-sync -n 100 --no-pager
```

Снапшоты появляются в `/root/second-brain/infrastructure/Financial Coach/data/<источник>/YYYY-MM-DD.json`
и уезжают на мак Syncthing'ом. Копия дашборда в `~/Desktop/Vibecode OUT/` на VPS
не делается (нет Desktop) — шаг логирует предупреждение и не падает.

## 5. Публикация дашборда (веб-доступ через Caddy)

### [[2026-08-27]]

Цель: `https://163.5.29.10.sslip.io/finance` отдаёт `dashboard/index.html` под basic auth.
Всё ниже — руками Антона на сервере; агент к проду не ходит (ADR-013).

Как это работает: Caddy на VPS запущен **не от root** и `/root/second-brain/...` прочитать не
может. Поэтому HTML публикуется копией в `/var/www/finance/index.html` — двумя путями сразу:

- `financial-coach-publish.path` — ловит изменение файла (Syncthing довёз новый HTML с мака)
  и копирует за секунды, ждать вечернего таймера не нужно;
- сам `render_dashboard.py` — если в env задан `FINANCE_PUBLISH_DIR`, он после записи
  дашборда атомарно (temp + `os.replace`) кладёт копию туда же. Страховка на случай, если
  path-юнит не сработал; нет каталога или нет прав — предупреждение в логе, не падение.

Дублирование безобидно: обе дороги пишут один и тот же файл, атомарно.

### 5.1. Каталог

```bash
mkdir -p /var/www/finance
chmod 755 /var/www /var/www/finance      # Caddy (не root) должен пройти внутрь и прочитать
```

Владелец может остаться `root` — Caddy только читает. Права `755` на каталог и `644` на
`index.html` (их выставляют и `install -m 644`, и сам скрипт) — этого достаточно.

### 5.2. Хеш пароля — генерирует Антон, не агент

```bash
caddy hash-password        # пароль вводится с клавиатуры, на экран идёт только bcrypt-хеш
```

Скопировать полученный `$2a$14$...`. **Пароль нигде не записывать** — ни в vault, ни в
Caddyfile: там лежит только хеш. Агент пароль не придумывает, не спрашивает и не хранит.

### 5.3. Вставка блока в Caddyfile (без ручного редактирования)

Ручная правка через `nano` 27.08 не сохранилась и стоила одного круга отладки — поэтому
вставка автоматическая. Зайти на сервер интерактивно:

```bash
ssh -i ~/.ssh/id_ed25519_vps root@163.5.29.10
```

Получить хеш (пароль вводится с клавиатуры, на экран идёт только bcrypt):

```bash
caddy hash-password
```

Подставить его в первую строку и выполнить блок целиком:

```bash
H='ВСТАВЬ_СЮДА_ХЕШ'
D="/root/second-brain/infrastructure/Financial Coach/Scripts/deploy"
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date +%s)
sed "s|PASTE_BCRYPT_HASH_HERE|$H|" "$D/Caddyfile.finance-block" > /tmp/fb
awk 'NR==FNR{b[NR]=$0;n=NR;next} /^[[:space:]]*handle[[:space:]]*\{/ && !d {for(i=1;i<=n;i++) print b[i]; d=1} 1' /tmp/fb /etc/caddy/Caddyfile > /tmp/Caddyfile.new
mv /tmp/Caddyfile.new /etc/caddy/Caddyfile && rm -f /tmp/fb
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy && echo "CADDY OK"
```

`awk` вставляет блок перед ПЕРВЫМ `handle {` — то есть перед catch-all Гермеса, как и нужно.
Проверено на копии боевого конфига 27.08. Не увидел `CADDY OK` — конфиг не применился,
откат: `cp /etc/caddy/Caddyfile.bak.<время> /etc/caddy/Caddyfile && systemctl reload caddy`.

Файл блока — `Caddyfile.finance-block` (чистый конфиг, плейсхолдер `PASTE_BCRYPT_HASH_HERE`).
Комментированная версия с альтернативами — `Caddyfile.finance-snippet`.
На сервере Caddy **v2.11.4** (проверено 27.08) → директива `basic_auth`, не `basicauth`.

### 5.4. Валидация и перезагрузка

```bash
caddy validate --config /etc/caddy/Caddyfile
caddy fmt --overwrite /etc/caddy/Caddyfile   # опционально, выровняет отступы
systemctl reload caddy                       # reload, НЕ restart: не рвём текущие соединения
systemctl status caddy --no-pager
```

Если `validate` ругается на `basic_auth` — версия Caddy старая, переименовать директиву в
`basicauth` (комментарий про это есть в самом сниппете) и провалидировать заново.
Гермес на корне должен продолжать открываться — проверить до и после.

### 5.5. Юниты мгновенной публикации

```bash
cd "/root/second-brain/infrastructure/Financial Coach/Scripts/deploy"
cp financial-coach-publish.service financial-coach-publish.path /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now financial-coach-publish.path
systemctl start financial-coach-publish.service    # первая публикация прямо сейчас
systemctl list-units 'financial-coach-publish*'
```

### 5.6. Ключ в env

```bash
echo 'FINANCE_PUBLISH_DIR=/var/www/finance' >> /root/.config/second-brain/finance.env
```

Формат тот же `KEY=VALUE` без кавычек — его читают и `common.load_env`, и systemd
`EnvironmentFile` в `financial-coach-sync.service`. На маке этот ключ **не добавлять**:
без него локальный рендер работает ровно как раньше.

### 5.7. Проверка

```bash
ls -l /var/www/finance/index.html                       # файл есть, 644, свежая дата
systemctl start financial-coach-sync.service            # полный прогон синка + рендера
journalctl -u financial-coach-sync -n 40 --no-pager | grep -i опубликовано
curl -I https://163.5.29.10.sslip.io/finance            # ожидаем 308 → /finance/
curl -I https://163.5.29.10.sslip.io/finance/           # ожидаем 401 без пароля
curl -u anton:ПАРОЛЬ https://163.5.29.10.sslip.io/finance/ | head -5   # ожидаем HTML
curl -I https://163.5.29.10.sslip.io/                   # Гермес жив, не сломали корень
```

Чтобы пароль не осел в истории shell — вводить его интерактивно: `curl -u anton ...`
(curl спросит) вместо `-u anton:пароль`.

### 5.8. Откат публикации

```bash
systemctl disable --now financial-coach-publish.path
rm /etc/systemd/system/financial-coach-publish.{path,service}
systemctl daemon-reload

cp /etc/caddy/Caddyfile.bak.<дата> /etc/caddy/Caddyfile   # либо руками убрать блок /finance
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy

sed -i '/^FINANCE_PUBLISH_DIR=/d' /root/.config/second-brain/finance.env
rm -rf /var/www/finance
```

Откат независим от синка: таймер `financial-coach-sync.timer` продолжает работать, дашборд
просто перестаёт быть доступен по вебу.

### 5.9. Грабли публикации

- **Порядок handle-блоков.** Голый `handle { reverse_proxy ... }` — это catch-all; если
  положить блок `/finance` после него, /finance уедет в Гермеса и вернёт его 404.
  Симптом «дашборд не открывается, а Гермес открывается» — почти всегда это.
- **Caddy не читает `/root`.** Прямой `root * "/root/second-brain/.../dashboard"` даст
  403/404 даже при верном пути — процесс caddy идёт от юзера `caddy`. Только копия в
  `/var/www/finance`.
- **Версия директивы.** `basic_auth` — Caddy 2.8+; в более старых только `basicauth`.
  Проверять `caddy version` до правки, иначе `systemctl reload caddy` не поднимет конфиг.
- **Смена пароля** = новый `caddy hash-password` + замена хеша в Caddyfile +
  `caddy validate` + `systemctl reload caddy`. Правка только в env или в юнитах ничего
  не поменяет — пароль живёт исключительно в Caddyfile.
- **Один бот — один полер... и одна машина для синка.** Публикация не отменяет правила из
  раздела «Грабли» ниже: боевой `--live` крутится только на VPS.
- **Дашборд открылся, но цифры старые** — значит новый HTML ещё не доехал Syncthing'ом.
  Смотреть дату файла в `/root/second-brain/.../dashboard/index.html`, а не в `/var/www`.

> ⚠️ **Basic auth поверх TLS — это минимальная защита, а не приватность.** За ссылкой
> лежат все деньги Антона: остатки, долги, прогноз, контрагенты. Ссылку никому не
> пересылать, в рабочие и публичные чаты не класть, в браузере чужого устройства не
> открывать. `sslip.io`-домен угадывается по IP, так что единственный барьер — пароль.
> Если ссылка куда-то утекла — сменить пароль (грабли выше), это дешевле разбирательств.

## 6. Откат синка

```bash
systemctl disable --now financial-coach-sync.timer
rm /etc/systemd/system/financial-coach-sync.{service,timer}
systemctl daemon-reload
```

Откат публикации — отдельно, п. 5.8.

## Грабли

- **Один бот — один полер.** Когда механика CRM-топика станет Bot API `getUpdates`,
  нельзя держать второй полер того же токена (Гермес/inbox-бот) — иначе оба будут
  воровать апдейты друг у друга.
- **Состояние не синхронизируется.** `data/*/state.json` — это offsets; они лежат в
  vault и приезжают на мак. Не запускать `--live` одновременно на маке и на VPS,
  иначе инкремент ZenMoney разъедется. Боевой прогон — только на VPS.
- **Первый прогон ZenMoney тяжёлый:** `serverTimestamp=0` тянет всю историю.
  Повторить полный импорт можно `python3 pull_zenmoney.py --live --full`.
