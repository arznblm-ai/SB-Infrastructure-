---
name: developer-growth-analysis
description: Analyze Anton's recent Codex Desktop sessions and Second Brain work patterns to produce a Russian developer/vibecoding growth report with strengths, bottlenecks, skill gaps, action items, and optional learning resources. Use when Anton asks to analyze his developer growth, coding progress, Codex history, vibe coding progress, recent AI-agent work, GitHub learning, skill-building, or asks "как мне стать лучше" in the context of software, Codex, agents, automations, or Second Brain.
metadata:
  short-description: Analyze Anton's Codex growth patterns
model: sonnet
---

# Developer Growth Analysis

### [[2026-06-07]]

Personal growth-analysis workflow for Anton. This is adapted for Codex Desktop and Anton's Second Brain; do not rely on the original `~/.codex/history.jsonl`-only flow.

## Core Behavior

Use this skill to turn recent Codex work into an evidence-backed growth report:

1. Read recent Codex Desktop session evidence.
2. Identify work domains, repeated patterns, friction points, and growth opportunities.
3. Produce a concise Russian report with practical training recommendations.
4. Optionally search for learning resources when the user asks for sources or links.
5. Do not send anything to Slack, Telegram, or external tools unless Anton explicitly asks.

## Data Sources

Prefer local sources in this order:

1. `~/.codex/session_index.jsonl`
2. `~/.codex/sessions/**/*.jsonl`
3. `~/.codex/history.jsonl` only as a legacy fallback if present
4. Second Brain context only when useful:
   - `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/`
   - `/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/`
   - `/Users/anton/AI AGENT FOLDER/Second Brain/context/`

Read user requests and session metadata first. Avoid loading full assistant responses unless needed to understand an outcome. Do not include secrets, credentials, private tokens, or long pasted content in the report.

## Quick Start

Run the bundled evidence extractor first:

```bash
python3 "<skill-dir>/scripts/extract_codex_growth_context.py" --days 7
```

For a different period:

```bash
python3 "<skill-dir>/scripts/extract_codex_growth_context.py" --days 30 --out /tmp/codex-growth-evidence.md
```

Use the output as evidence, then synthesize the report yourself. The script is an evidence pack, not the final analysis.

## Report Format

Write in Russian by default. Keep it practical and mentor-like, not corporate.

Use this structure:

```markdown
# Developer Growth Report

Период: [exact dates]

## Короткий вывод
[3-6 sentences: what Anton is becoming better at, what the week/month was really about.]

## Что ты делал
[Main domains, projects, tools, agents, and workflows.]

## Сильные стороны
- [Evidence-backed strength]
- [Evidence-backed strength]
- [Evidence-backed strength]

## Зоны роста
### 1. [Specific skill area]
Почему важно: [...]
Что видно по истории: [...]
Практика: [...]

### 2. [...]

## Следующие упражнения
1. [Small concrete exercise]
2. [Small concrete exercise]
3. [Small concrete exercise]

## Риски системы
[Optional: duplication, automation drift, missing versioning, weak tests, unclear boundaries.]

## Learning Resources
[Only include if searched or if strong known references are relevant. Provide links.]
```

## Anton-Specific Heuristics

Look especially for these patterns:

- Agent and skill design: `CLAUDE.md`, `SKILL.md`, automation modules, Codex workflows.
- Second Brain architecture: folder conventions, context files, transcript and research pipelines.
- Product thinking: ICP, offer, pricing, competitive research, roadmap, deck strategy.
- GitHub maturity: repos, commits, PRs, CI, changelog, rollback, versioning.
- Frontend/application craft: local apps, Playwright/browser testing, interface polish.
- Research-to-artifact flow: whether research becomes an offer, spec, plan, or decision.
- Automation boundaries: whether Daily Focus, Productivity Review, Research Department, Strategic Board, and other agents overlap.

## Quality Bar

- Be evidence-based: cite the session topic, project, or request pattern.
- Be specific: say "GitHub/versioning hygiene" instead of "learn coding".
- Be actionable: each growth area should include a practice task.
- Be kind but honest: praise strengths, but do not flatten real gaps.
- Use exact dates when discussing periods.
- If evidence is sparse, say so and label conclusions as tentative.

## Optional Saving

When Anton asks to save the report in Second Brain, save it under:

`/Users/anton/AI AGENT FOLDER/Second Brain/context/`

Use a vault-style filename:

`{self} {review} developer growth – YYYY-MM-DD.md`

