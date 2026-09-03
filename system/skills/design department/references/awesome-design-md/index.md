# Awesome DESIGN.md — библиотека дизайн-систем брендов

### [[2026-09-02]]

Источник: https://github.com/VoltAgent/awesome-design-md (MIT, снимок main от 2026-09-02, ~113k звёзд). Добавлено в дизайн-отдел по решению Антона 02.09.2026. Оригинальный `README.md` и `LICENSE` лежат рядом; `.git` не копировался.

## Что это

74 файла `design-md/<brand>/DESIGN.md` — реверс-инжиниринг дизайн-систем реальных сайтов в формате Google Stitch DESIGN.md: YAML-frontmatter с токенами (цвета, типографика по ролям, радиусы, тени, spacing) + текстовые правила (компоненты, do/don't, характер). Рядом `README.md` бренда с превью. Читается любым coding-агентом как plain text — никакой Figma и JSON-схем.

## Как использовать в отделе

1. **Референс направления, не копия.** Берём токены и логику системы как стартовую точку для своего бренда (например, «взрослый инженерный консалтинг» → смотреть `ibm`, `stripe`, `cohere`, `linear.app`, `wise`; «тёплый editorial» → `claude`, `cursor`, `elevenlabs`, `intercom`). Чужие фирменные цвета и шрифты в клиентскую работу не переносим.
2. **Вход в вёрстку.** Один `DESIGN.md` (свой или адаптированный) кладётся в корень проекта сайта/лендинга — исполнитель (`route`) строит UI по нему. Это тот же контракт, что `brand.md` у Frontify.
3. **Формат для собственных бренд-спек.** Финальную спеку айдентики (например, ИИнизации) оформлять в этой же структуре frontmatter + правила — тогда она сразу пригодна для агентной вёрстки.
4. Читать точечно: `ls design-md/`, затем один `DESIGN.md`. Всю папку в контекст не тянуть (2,5 МБ).

## Состав по кластерам

- **AI / dev-tools:** claude, cohere, cursor, composio, elevenlabs, expo, hashicorp, linear.app, lovable, minimax, mintlify, mistral.ai, mongodb, ollama, opencode.ai, posthog, raycast, replicate, resend, runwayml, sanity, sentry, supabase, superhuman, together.ai, vercel, voltagent, warp, webflow, x.ai, zapier, clickhouse
- **Финтех / крипто:** binance, coinbase, kraken, mastercard, revolut, stripe, wise
- **Продукт / SaaS:** airtable, cal, clay, figma, framer, intercom, miro, notion, slack
- **Потребительские / маркетплейсы:** airbnb, pinterest, spotify, starbucks, uber, nike, shopify
- **Авто / люкс / индустрия:** bmw, bmw-m, bugatti, ferrari, lamborghini, renault, tesla, spacex
- **Корпорации / hardware:** apple, dell-1996, hp, ibm, meta, nvidia, vodafone, playstation, nintendo-2001
- **Медиа:** theverge, wired

## Обновление

Снимок статичный. Обновить: скачать tarball `main`, заменить `design-md/`, `README.md`, `LICENSE`, поправить дату здесь. Локальные правки внутри `design-md/` не делать — только свои файлы рядом.
