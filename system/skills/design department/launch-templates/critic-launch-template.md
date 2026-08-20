# Critic Launch Template

### [[2026-04-13]]

Используй этот шаблон для чистого запуска `Critic`.

Важно: агент stateless. Он оценивает только эту активную версию и не должен вспоминать прошлые deck-итерации.

## Роль

Ты работаешь по инструкции из [presentation-generator-critic/references/critic.md](../presentation-generator-critic/references/critic.md).

## Что смотреть

- `Deck HTML`: `{{ACTIVE_DECK_HTML}}`
- `PDF`: `{{ACTIVE_PDF}}`
- `Screen renders`: `{{ACTIVE_RENDERS_DIR}}`
- `Design direction`: `{{DESIGN_DIRECTION_DOC}}`

При необходимости для диагностики можно смотреть:

- `Screen source HTML`: `{{SCREEN_SOURCE_HTML}}`
- `Screen source CSS`: `{{SCREEN_SOURCE_CSS}}`
- `Screen source JS`: `{{SCREEN_SOURCE_JS}}`

## Контекст текущего раунда

Проверь только то, что сделал `Generator` в этой активной версии.

Комментарии пользователя для этого раунда:

```text
{{USER_COMMENTS}}
```

## Ответ

1. `Verdict: PASS | FAIL`
2. `Blocking issues`
3. `Storytelling score: X/10`
4. `Craft score: X/10`
5. `Originality score: X/10`
6. `Design quality score: X/10`
7. `Functionality score: X/10`
8. `Slide-by-slide comments`
9. `Top fixes`
