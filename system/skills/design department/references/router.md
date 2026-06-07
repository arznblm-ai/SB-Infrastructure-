# Router

Этот файл задает канонический decision tree для `design-orchestrator`.

Если routing-логика в других файлах расходится с этим документом, верить нужно этому файлу.

## Правило ноль

Если нет брифа, не выбирай исполнителя и не стартуй workflow.

Сначала заполни [brief-template.md](./brief-template.md), потом запускай раунд.

## Сначала выбери workflow mode

- если задача про новый deck или большой redesign → `new_design_flow`
- если deck уже существует и его нужно улучшить без полной перестройки → `polish_existing_deck_flow`
- если уже есть comments, versioning и быстрый revision loop → `iterative_revision_flow`
- если нужен только visual audit → `visual_qa_only_flow`

Подробная последовательность фиксируется в [workflow-modes.md](./workflow-modes.md).

## Затем выбери specialist role

### Используй `presentation-designer`, если задача про:

- визуальный язык
- композицию
- иерархию
- типографический ритм
- ощущение премиальности
- редизайн narrative-слайдов
- смену визуальной системы

### Используй `Generator`, если задача про:

- содержательные правки
- структуру deck
- proof screens
- production-изменения
- замену контента
- подготовку новой ревизии без смены дизайн-языка

### Используй `presentation-art-director`, если задача только про:

- проверку сходства с оригиналом
- проверку шрифтов
- проверку spacing, alignment и пропорций
- финальный fidelity pass
- визуальный QA без переписывания смысла

## Канонический orchestration pipeline

```text
Cross-thread request
  ↓
Design Orchestrator
  ↓
Design Intake Brief
  ↓
Router
  ↓
Workflow mode
  ↓
Specialist roles in sequence
  ↓
Artifact handoff after each role
  ↓
Quality gate
  ↓
Final Design Package
```

## Правила маршрутизации

- `design-orchestrator` — единственная canonical entrypoint for department-level execution.
- После `presentation-designer` нужен review gate, обычно через `presentation-art-director`, а не через неявную память прошлого раунда.
- `presentation-generator-critic` используется как production loop, а не как redesign brain.
- `presentation-art-director` является финальным визуальным gate, когда нужно приблизить deck к approved source или остановить workflow.
- Если пользователь просит только visual audit без production-раунда, orchestrator должен запускать `visual_qa_only_flow`.
- Если у caller нет явных artifacts, orchestrator должен вернуть `REVISE`, а не гадать по chat history.
