# Personal OS Finance (RUB-first)

## Purpose

Local-first RUB cashflow cockpit for daily control:

1. Сколько денег сейчас.
2. Какие подтвержденные приходы ожидаются.
3. Какие обязательства впереди.
4. Где возможен кассовый разрыв.
5. Какой upside есть в CRM (без включения в базовый прогноз).

## Rules

- Расчетная валюта всегда RUB.
- Внешние API, cloud sync, telemetry, auth providers не используются.
- USD/FX допускаются только в `reference_note` как справка.
- Все вычисления делаются локально на SQLite.

## Data Model

- `accounts`: cash/bank/card/crypto/reserve, `balance_rub`, `reference_note`
- `clients`
- `confirmed_projects`: подтвержденные приходы для базового forecast
- `crm_opportunities`: sales upside, не входит в base forecast
- `obligations`: business_debt/personal_debt/tax/monthly_expense/one_time_expense

## Commands

```bash
node .tools/npm/bin/npm-cli.js install
node .tools/npm/bin/npm-cli.js run db:backup
node .tools/npm/bin/npm-cli.js run db:migrate
node .tools/npm/bin/npm-cli.js run db:seed
node .tools/npm/bin/npm-cli.js run db:empty
node .tools/npm/bin/npm-cli.js run dev
node .tools/npm/bin/npm-cli.js run build
```

Важно:
- Перед любыми миграциями запускать `node .tools/npm/bin/npm-cli.js run db:backup`.
- Перед началом ручного ввода реальных данных также запускать `node .tools/npm/bin/npm-cli.js run db:backup`.

## First Real Data Run

```bash
node .tools/npm/bin/npm-cli.js run db:backup
node .tools/npm/bin/npm-cli.js run db:migrate
node .tools/npm/bin/npm-cli.js run db:empty
node .tools/npm/bin/npm-cli.js run dev
```

Дальше открыть `/` для проверки read-only дешборда. Новые проекты, обязательства, счета и CRM-запросы вводятся через чатовый слой/агента, который разбирает текст или скриншоты и обновляет SQLite. Веб-интерфейс не должен содержать формы ручного ввода.

## Input Model

- Web UI is read-only: только дешборд, прогноз, таблицы и общие цифры.
- Data entry happens through chat: пользователь присылает текст, скриншот, договоренность или финансовый апдейт.
- Chat/agent layer must normalize input into existing SQLite entities: `accounts`, `clients`, `confirmed_projects`, `crm_opportunities`, `obligations`.
- Month values are stored internally as `YYYY-MM`, but displayed in UI as Russian month names, for example `май 2026`.

## Forecast Logic

- Месячный прогноз на 6 месяцев.
- Налог удерживается в месяц прихода подтвержденного проекта.
- `monthly expenses` могут считаться из `obligations` типа `monthly_expense` или задаваться manual override-слайдером.
- `one_time_expense` учитывается в одном месяце, не превращается в постоянный burn.
- CRM upside считается отдельно и явно не входит в базовый прогноз.

## Migration Safety

- Текущая миграция `drizzle/0001_rub_cashflow_cockpit.sql` удаляет legacy demo-таблицы (`DROP TABLE`) как разовый переход.
- Перед любой миграцией обязательно делать backup:
  - `node .tools/npm/bin/npm-cli.js run db:backup`
- Для следующих миграций не использовать `DROP TABLE` без явного backup/export.
