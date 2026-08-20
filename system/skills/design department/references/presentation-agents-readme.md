# Presentation Agents

### [[2026-04-13]]

В этом отделе лежит новая версия агентной системы для deck-цикла.

Главный принцип теперь такой: агенты `stateless`.

Это значит:

- у них нет памяти о прошлых итерациях по умолчанию
- они не привязаны к `v3`, `v4`, `v5`, `v6` или любому другому deck автоматически
- активная версия всегда задается явно в текущем запуске
- если в prompt не передан файл, агент не должен делать выводы о нем по памяти

## Роли

- [portal-designer](../portal-designer/SKILL.md) — дизайнерский агент для редизайна, композиции, ритма и визуальной системы deck внутри Portal design code, без Portal logo по умолчанию
- [generator](../presentation-generator-critic/references/generator.md) — агент, который вносит содержательные и структурные изменения в активную версию
- [critic](../presentation-generator-critic/references/critic.md) — read-only reviewer

`Critic` теперь оценивает deck по явной рубрике:

- `Storytelling`
- `Craft`
- `Originality`
- `Design quality`
- `Functionality`

Это базовые гайды для каждого нового review.

## Как этим пользоваться

1. Выбери активную версию deck для текущего раунда.
2. Передай эту версию явно в prompt агенту, которого запускаешь.
3. Передай только те комментарии, которые относятся к текущему раунду.
4. Не проси агента “вспомнить, что было до этого”, если это не вложено в prompt.

Если нужен именно редизайн, а не просто правки:

1. Сначала запускай `Portal Designer`.
2. Потом запускай `Generator`, если после редизайна нужны ещё содержательные правки.
3. В конце запускай `Critic`.

## Файлы запуска

- [designer-launch-template.md](../launch-templates/designer-launch-template.md)
- [generator-launch-template.md](../launch-templates/generator-launch-template.md)
- [critic-launch-template.md](../launch-templates/critic-launch-template.md)
- [launch-workflow.md](./launch-workflow.md)
- [presentation-runbook.md](./presentation-runbook.md)

## Портируемость

Этот отдел можно копировать в любой проект без изменений структуры.

Чтобы начать работу в новом проекте:

1. Скопируй весь `design department`.
2. Выбери активные файлы нового проекта: deck HTML, PDF, рендеры, исходники экранов, export script, дизайн-гайд.
3. Подставь их пути в шаблоны запуска.
4. Если нужен редизайн, сначала запускай `Portal Designer`.
5. Потом запускай `Generator`, если нужны содержательные правки.
6. В конце запускай `Critic`.
