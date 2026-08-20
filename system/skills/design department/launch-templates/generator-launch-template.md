# Generator Launch Template

### [[2026-04-13]]

Используй этот шаблон для чистого запуска `Generator`.

Важно: агент stateless. Он видит только этот prompt и файлы, которые ты перечислил здесь.

## Роль

Ты работаешь по инструкции из [presentation-generator-critic/references/generator.md](../presentation-generator-critic/references/generator.md).

Не используй память о прошлых раундах. Игнорируй все прошлые версии, если они не перечислены ниже.

## Активные файлы этого раунда

- `Deck HTML`: `{{ACTIVE_DECK_HTML}}`
- `PDF`: `{{ACTIVE_PDF}}`
- `Screen renders`: `{{ACTIVE_RENDERS_DIR}}`
- `Screen source HTML`: `{{SCREEN_SOURCE_HTML}}`
- `Screen source CSS`: `{{SCREEN_SOURCE_CSS}}`
- `Screen source JS`: `{{SCREEN_SOURCE_JS}}`
- `Export script`: `{{EXPORT_SCRIPT}}`
- `Design direction`: `{{DESIGN_DIRECTION_DOC}}`

## Что нужно сделать

Внеси комментарии пользователя только в эту активную версию.

Комментарии пользователя:

```text
{{USER_COMMENTS}}
```

## Ограничения

- не опирайся на прошлые deck-версии
- не выдумывай историю проекта
- правь только активные файлы этого запуска
- если меняются screen-slides, сначала правь source, потом пересобирай export
- сохраняй честные 16:9 screen renders

## Ответ

1. `Revision: ...`
2. `Что изменено`
3. `Какие проблемы решены`
4. `Что ещё может не пройти у Critic`
