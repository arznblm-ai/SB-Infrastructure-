# Research Department

### [[2026-04-20]]

`research department/` — это входная skill-обёртка для канонического factual Research Department, который теперь живёт в:

- `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/`

Она нужна, чтобы из других тредов можно было вызывать department как систему, а не вручную вспоминать:

- где лежит router
- какой workflow выбрать
- какие роли запускать
- в каком порядке должны появляться artifacts

## Что делает эта skill

- указывает на канонический entrance point department
- описывает, какие workflows поддерживаются
- связывает cross-thread вызов с реальным `Research Dept`
- не дублирует сам department внутри `system/skills`

## Канонический вход

- [/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/SKILL.md](/Users/anton/AI%20AGENT%20FOLDER/Second%20Brain/infrastructure/Research%20Dept/SKILL.md)

## Поддерживаемые factual workflows

- Market Intelligence
- Objective Market Scan
- Interview-Led Market Overview
- Corpus-Shift Rerun
- Verification

## Важное правило

Эта папка — не второй research department.
Это entrypoint wrapper.

Истина, contracts, router, quality gates, templates и registry logic живут в:

- `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Research Dept/`
