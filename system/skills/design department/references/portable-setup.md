# Portable Setup

Этот комплект можно переносить в любой проект как единый presentation design department.

## Что нужно скопировать

Скопируй всю папку `design department` целиком или возьми из неё только нужные подпакеты и шаблоны.

Минимальный набор:

- `portal-designer/`
- `presentation-art-director/`
- `presentation-generator-critic/`
- `launch-templates/designer-launch-template.md`
- `launch-templates/generator-launch-template.md`
- `launch-templates/critic-launch-template.md`
- `references/launch-workflow.md`
- `references/presentation-runbook.md`
- `README.md`

## Что нужно заменить в новом проекте

В шаблонах запуска подставь свои проектные файлы:

- `{{ACTIVE_DECK_HTML}}`
- `{{ACTIVE_PDF}}`
- `{{ACTIVE_RENDERS_DIR}}`
- `{{SCREEN_SOURCE_HTML}}`
- `{{SCREEN_SOURCE_CSS}}`
- `{{SCREEN_SOURCE_JS}}`
- `{{EXPORT_SCRIPT}}`
- `{{DESIGN_DIRECTION_DOC}}`
- `{{USER_COMMENTS}}`

## Минимальный сценарий использования

1. Определи активную версию презентации.
2. Если нужен редизайн, сначала передай ее `Portal Designer`.
3. Если после этого нужны содержательные правки, передай ту же версию `Generator`.
4. После правок передай ту же версию `Critic`.
5. Повторяй цикл, пока не устроит результат.

## Важно

- агенты `stateless`
- они не должны знать ничего о предыдущих итерациях, если ты не дал это явно
- они не привязаны к текущему проекту, версии deck или конкретной папке
