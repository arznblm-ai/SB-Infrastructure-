---
name: deep-research-department
description: "Операционная система для factual Research Department: маршрутизирует market intelligence, objective scan, interview-led, competitive messaging intelligence, account/ICP intelligence, corpus-shift, update и verification запросы в правильный workflow, принуждает artifact chain от brief до registry update, выпускает CEO-readable reports и agent-safe packets, и удерживает corpus-aware memory layer."
---

# Deep Research Department

Этот skill является department-level router и operating system.

Он не делает весь ресёрч сам. Его задача:

1. Превратить запрос в правильный workflow.
2. Проверить, есть ли уже usable memory по теме.
3. Определить роли, глубину, output mix и gating path.
4. Следить, чтобы handoff шёл через materialized артефакты.
5. Не закрывать run без review, final outputs и registry update там, где они обязательны.
6. По умолчанию выпускать HTML readable export для каждого завершённого CEO-readable run.

## Когда использовать

- Когда нужен factual research по новой теме.
- Когда нужен market intelligence или objective market scan.
- Когда нужно обновить существующее исследование.
- Когда нужно пройтись по интервью, подкастам и operator commentary как по основному корпусу.
- Когда нужно разобрать рекламу конкурентов, офферы, лендинги, CTA, позиционирование, sales narratives и creative patterns.
- Когда нужно собрать factual ICP/account intelligence по сегменту, лидам, buyer personas или target accounts.
- Когда нужно сделать corpus-shift rerun по той же теме.
- Когда нужно проверить отдельный claim, цифру или источник.

## Когда не использовать

- Для ежедневного новостного патруля без research brief.
- Для простого веб-поиска без цели на reusable asset.
- Для творческого эссе без evidence trail.

## Обязательный порядок работы

1. Сначала прочитай [references/department-operating-system.md](./references/department-operating-system.md).
2. Затем открой [references/router.md](./references/router.md) и классифицируй запрос.
3. Если brief ещё не сформирован, начни с шаблона [templates/research-brief.md](./templates/research-brief.md).
4. Перед fresh search проверь memory rules в [references/memory-rules.md](./references/memory-rules.md).
5. Определи search posture, final output mix и expected source mix через [references/router.md](./references/router.md) и [references/source-reliability-policy.md](./references/source-reliability-policy.md). Для advertising/messaging запросов дополнительно открой [references/competitive-messaging-intelligence.md](./references/competitive-messaging-intelligence.md); для ICP/account запросов дополнительно открой [references/account-icp-intelligence.md](./references/account-icp-intelligence.md).
6. Не пропускай normalized cards, evidence layer и review, если workflow не является чистым scout pass.
7. Перед CEO report и packet прогоняй dossier через [references/quality-control.md](./references/quality-control.md) и [references/coverage-and-stopping-policy.md](./references/coverage-and-stopping-policy.md).
8. Закрывай run только после registry update по [references/memory-rules.md](./references/memory-rules.md).

## Канонические role-skills

- [research-intake-analyst](./skills/research-intake-analyst/SKILL.md)
- [scout-collector](./skills/scout-collector/SKILL.md)
- [source-normalizer](./skills/source-normalizer/SKILL.md)
- [evidence-extractor](./skills/evidence-extractor/SKILL.md)
- [synthesis-analyst](./skills/synthesis-analyst/SKILL.md)
- [research-critic](./skills/research-critic/SKILL.md)
- [ceo-report-editor](./skills/ceo-report-editor/SKILL.md)
- [context-packager](./skills/context-packager/SKILL.md)
- [registry-memory-owner](./skills/registry-memory-owner/SKILL.md)

## Главные правила отдела

- Центр системы: data layer, а не агент.
- Raw, normalized/evidence, knowledge, CEO reading и agent context нельзя смешивать.
- Архив не равен контексту; usable context для людей собирается в CEO report, для агентов — в packet.
- Каждый handoff должен иметь input artifact и output artifact.
- Если consumer, success criteria или time horizon неясны, workflow останавливается до уточнения.
- Research is not considered complete when enough text has been collected. Research is complete only when the department can justify source quality, source diversity, coverage sufficiency, and confidence level.
- Convenient sources are not automatically valid sources.
- The department must prefer source hierarchy over search convenience.
- No dossier without evidence.
- No CEO report without approved dossier and review.
- No completed CEO-readable run without HTML readable export.
- No packet without approved dossier, review and downstream safety note.
- No run completion without registry updates.
