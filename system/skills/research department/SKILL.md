---
name: research department
description: "Operating system for factual research: use Research Department when a request should enter through the canonical research router, be classified into the right factual workflow, routed through specialist roles, and return with explicit artifacts plus readable exports."
model: inherit
---

# Research Department

Это master skill для factual research.

Канонический department теперь живёт в `infrastructure/Research Dept/`.

Он не заменяет отдельные роли внутри `Research Dept`, а даёт единый вход в department:

- объясняет, что делает Research Department
- отправляет к каноническому department-level router
- фиксирует research brief как обязательный первый шаг
- связывает role-skills и artifact chain в runnable workflow

## Канонический entrance point

Запускать department нужно не через выбор роли вручную, а через:

- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/SKILL.md)

Именно этот skill отвечает за:

- intake discipline
- workflow selection
- sequencing
- artifact handoff
- quality gates
- HTML-readable completion
- registry update

## Канонические role-skills внутри department

Исполнители живут в `infrastructure/Research Dept/skills/` и остаются каноническими role-skills:

- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/router/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/router/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/research-intake-analyst/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/research-intake-analyst/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/scout-collector/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/scout-collector/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/source-normalizer/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/source-normalizer/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/evidence-extractor/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/evidence-extractor/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/synthesis-analyst/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/synthesis-analyst/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/research-critic/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/research-critic/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/ceo-report-editor/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/ceo-report-editor/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/context-packager/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/context-packager/SKILL.md)
- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/skills/registry-memory-owner/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/skills/registry-memory-owner/SKILL.md)

## Канонические department-level references

Эти файлы являются общими источниками истины:

- router: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/router.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/router.md)
- operating system: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/department-operating-system.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/department-operating-system.md)
- artifact contracts: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/artifact-contracts.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/artifact-contracts.md)
- workflow scenarios: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/workflow-scenarios.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/workflow-scenarios.md)
- quality control: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/quality-control.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/quality-control.md)
- memory rules: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/memory-rules.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/memory-rules.md)
- source reliability: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/source-reliability-policy.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/source-reliability-policy.md)
- coverage and stopping: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/references/coverage-and-stopping-policy.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/references/coverage-and-stopping-policy.md)
- brief template: [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/templates/research-brief.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/templates/research-brief.md)

## Как использовать

1. Сначала определи, что это действительно factual research, а не strategy memo.
2. Затем зайди через канонический department skill:
   [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/SKILL.md)
3. Обязательно собери или дополни research brief.
4. Позволь department самому выбрать workflow:
   - Market Intelligence
   - Objective Market Scan
   - Interview-Led Market Overview
   - Corpus-Shift Rerun
   - Verification
5. Не выбирай role-skills вручную по умолчанию. Они должны использоваться как исполнители внутри orchestrated workflow.

## Когда использовать

- Когда нужно factual research по новой теме
- Когда нужен interview-led overview
- Когда нужен corpus-shift rerun
- Когда нужно проверить claim, число, факт или источник
- Когда нужен CEO-readable factual report plus HTML export

## Когда не использовать

- Для стратегии, рекомендаций и выбора лучшего направления — это Strategic Board; сюда возвращайся только за фактами через research handoff
- Для простого веб-поиска без reusable artifacts
- Для casual brainstorming без brief и evidence trail
- Для поиска по собственному vault («где я писал про X») — это Vault Semantic Search, а не web research
- Для быстрого ответа из общих знаний модели, когда Антон не просил искать источники
