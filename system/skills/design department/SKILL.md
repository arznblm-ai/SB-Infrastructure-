---
name: design department
description: "Operating system for presentation design: use Design Department when a request should enter through `design-orchestrator`, be classified into the right workflow mode, routed through specialist roles, and return with explicit artifacts plus review status."
model: sonnet
---

# Design Department

### [[2026-08-09]]

Это master skill для презентационного дизайна.

> **Executor policy (обновлено 2026-08-09, ADR-021):** генерация HTML/кода слайдов — самая токеноёмкая стадия — выполняется **исполнителем**, а не моделью сессии. Кто именно и как вызывается — реестр и правило выбора в `system/skills/route/SKILL.md`; требование к задаче: пишет файлы в каталоге задачи, тратит не из квоты главной сессии. Мозг (главная сессия): brief, нарратив, design direction, визуальное ревью по скриншоту. Главная сессия HTML целиком не читает и не переписывает — только замечания списком, лимит 2 итерации.

Он не заменяет отдельные роли, а даёт единый вход в department:

- объясняет, что делает Design Department
- отправляет к каноническому orchestrator
- фиксирует общий brief как обязательный первый шаг
- связывает role-skills в runnable orchestration layer

## Канонический entrance point

Запускать department нужно не через выбор роли вручную, а через [design-orchestrator](./design-orchestrator/SKILL.md).

Именно orchestrator отвечает за:

- intake
- workflow selection
- sequencing
- artifact handoff
- quality gates
- final package

## Канонические роли

Исполнители живут отдельно и остаются каноническими role-skills:

- [design-orchestrator](./design-orchestrator/SKILL.md)
- [portal-designer](./portal-designer/SKILL.md)
- [presentation-art-director](./presentation-art-director/SKILL.md)
- [presentation-generator-critic](./presentation-generator-critic/SKILL.md)

## Канонические department-level references

Эти файлы стоят над отдельными ролями и являются общими источниками истины:

- routing: [references/router.md](./references/router.md)
- brief: [references/brief-template.md](./references/brief-template.md)
- Portal brand bible: `/Users/anton/AI AGENT FOLDER/Second Brain/tasks/Portal AI Portfolio/brandbible.md`
- design doctrine: [references/apple-presentation-designer.md](./references/apple-presentation-designer.md) as secondary craft reference only
- workflow modes: [references/workflow-modes.md](./references/workflow-modes.md)
- artifact handoffs: [references/artifact-handoff-model.md](./references/artifact-handoff-model.md)
- quality gates: [references/quality-gates.md](./references/quality-gates.md)
- canonical entrypoint: [references/canonical-entrypoint-contract.md](./references/canonical-entrypoint-contract.md)

## Что добавлено сюда из `Agency Demo`

То, чего раньше не было в `system/skills` как единого набора:

- launch templates для `Designer`, `Generator`, `Critic`
- orchestration workflow
- presentation runbook
- portable setup guide
- example design direction
- обзор старой агентной папки как reference

## Структура

- `launch-templates/` — шаблоны запуска для stateless deck-цикла
- `design-orchestrator/` — главный orchestration layer department
- `portal-designer/` — канонический Portal-first designer role
- `references/router.md` — единый decision tree по маршрутизации
- `references/brief-template.md` — единый входной brief для всех presentation roles
- `references/apple-presentation-designer.md` — вторичная design doctrine для craft-принципов; Portal design code важнее
- `references/workflow-modes.md` — канонические workflow modes
- `references/artifact-handoff-model.md` — handoff contracts между ролями
- `references/quality-gates.md` — правила остановки, revise/fail и final status
- `references/canonical-entrypoint-contract.md` — contract для cross-thread вызова
- `references/launch-workflow.md` — порядок оркестрации
- `references/presentation-runbook.md` — короткий runbook
- `references/portable-setup.md` — как переносить набор между проектами
- `references/agency-demo-design-direction-example.md` — референсный design direction

## Как использовать

1. Сначала собери или дополни [references/brief-template.md](./references/brief-template.md). Без brief department не должен стартовать execution.
2. Затем запускай [design-orchestrator](./design-orchestrator/SKILL.md). Это канонический orchestration layer department.
3. Orchestrator сам читает [references/router.md](./references/router.md), выбирает workflow mode и решает sequence of roles.
4. Specialist roles используются не напрямую по умолчанию, а как исполнители внутри orchestrated workflow.
5. Если нужен быстрый рабочий запуск, бери шаблоны из `launch-templates/`, но sequencing всё равно валидируй через orchestrator contracts в `references/`.

## Когда НЕ использовать

- Для правок текста, копирайтинга или структуры документа без визуальной/презентационной задачи
- Для кодовых и инфраструктурных задач — это model-pipeline и соответствующие infrastructure-проекты, а не design department
- Когда Антон просит только критику существующего дизайна без производства новых слайдов — достаточно роли `presentation-generator-critic`, полный department-цикл с brief и orchestrator не нужен

## Внешние дизайн-скиллы (добавлены 2026-08-30, по решению Антона)

- `references/frontend-design/SKILL.md` — Anthropic frontend-design: отличительный (не шаблонный) визуальный дизайн, типографика как носитель личности, signature element, restraint, копирайт как дизайн-материал. Читать перед любой вёрсткой UI/лендинга.
- `references/ui-ux-pro-max/SKILL.md` — UI/UX Pro Max: чек-листы качества по приоритетам (доступность, touch-таргеты ≥44px, контраст 4.5:1, перформанс, типографика, анимации с reduced-motion). Использовать как QA-прогон перед сдачей интерфейса. Полный репозиторий с базами стилей/палитр: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
