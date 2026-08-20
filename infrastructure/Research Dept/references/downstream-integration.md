# Downstream Integration

### [[2026-04-14]]

## Purpose

Deep Research Department не заканчивается на CEO-readable отчёте. Его outputs должны работать как supply layer для других department-level flows.

## Main downstream consumers

- Strategy agents
- Skill creation flows
- Product thinking agents
- Content agents
- Briefing agents
- Context-building workflows

## Context Packet contract for downstream

Каждый packet обязан отвечать downstream-агенту на 5 вопросов:

1. Что это за тема.
2. Когда этот packet использовать.
3. Что здесь уже проверено.
4. Где границы уверенности и caveats.
5. Куда идти за деталями: dossier, source cards, raw trail.

Он также обязан явно различать:

- safe-to-reuse findings;
- use-with-caution findings;
- weak signals not to overstate.

## Handoff rules

- CEO report предназначен для Anton и human deep-reading, а не для downstream-agent default consumption.
- Strategy получает packet + dossier summary, а не raw cards by default.
- Skill creation получает packet + implications for agents/skills/system.
- Product thinking получает findings, trade-offs и open questions.
- Content agents получают canonical findings + approved claims only.
- If reliability is insufficient, packet must be marked `exploratory only` or withheld from canonical downstream use.

## Do not hand off

- raw source inventory как будто это финальный контекст;
- synthesis без approved source trail;
- outdated packet без freshness warning.
- weak signal presented as fact;
- confidence-hidden packet sent as canonical context.
