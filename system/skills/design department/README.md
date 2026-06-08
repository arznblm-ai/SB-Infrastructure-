# Design Department

`design department/` — это операционный хаб для дизайна презентаций внутри Second Brain.

Здесь собраны:

- канонические skill-роли и orchestration layer
- launch templates, которых не хватало в `system/skills`
- orchestration docs, перенесённые из `Agency Demo`
- пример design direction для реального deck-проекта

## Канонические skill-папки внутри отдела

- [design-orchestrator](./design-orchestrator/SKILL.md)
- [portal-designer](./portal-designer/SKILL.md)
- [presentation-art-director](./presentation-art-director/SKILL.md)
- [presentation-generator-critic](./presentation-generator-critic/SKILL.md)

## Итог проверки `Agency Demo`

В `system/skills` уже были перенесены:

- `portal-designer`
- `presentation-art-director`
- `presentation-generator-critic`

Но не были собраны в одном месте:

- `designer-launch-template.md`
- `generator-launch-template.md`
- `critic-launch-template.md`
- `launch-workflow.md`
- `presentation-runbook.md`
- `portable-setup.md`
- `design-direction.md`

Именно эти файлы теперь лежат здесь как department-level toolkit.
Design Department is organized around one master entrypoint:

- start with `SKILL.md`
- fill `references/brief-template.md`
- enter through `design-orchestrator/SKILL.md`
- let orchestrator route through `references/router.md`
- only then enter one of the specialist role-skills

Common references live at the department level. Role folders should not keep private copies of router, brief, or shared design doctrine.

The department is considered runnable only when:

- intake is explicit
- workflow mode is selected
- role sequence is explicit
- artifacts are handed off between roles
- QA can stop the workflow
- one final package is returned
