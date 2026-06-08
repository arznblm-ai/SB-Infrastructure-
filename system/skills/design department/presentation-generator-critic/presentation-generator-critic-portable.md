# presentation-generator-critic

Переносимая версия навыка для быстрого копирования в другие системы.

Если софт поддерживает структуру навыка с папками, используй весь пакет:
- `system/skills/design department/presentation-generator-critic/SKILL.md`
- `system/skills/design department/presentation-generator-critic/agents/openai.yaml`
- `system/skills/design department/references/brief-template.md`
- `system/skills/design department/presentation-generator-critic/references/generator.md`
- `system/skills/design department/presentation-generator-critic/references/critic.md`
- `system/skills/design department/references/router.md`
- `system/skills/design department/presentation-generator-critic/references/workflow.md`

Если нужен один файл для быстрого копирования, используй блок ниже как основной текст навыка:

```xml
<skill>
<name>presentation-generator-critic</name>
<path>/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/design department/presentation-generator-critic/SKILL.md</path>
---
name: presentation-generator-critic
description: "Use when the user wants to iterate a presentation quickly through a stateless builder-plus-critic loop: apply comments to the active deck, preserve meaning, create a new version, export the PDF, and run a strict review afterward. Trigger when the user asks to make deck revisions faster, keep old versions, improve slides while keeping the text, or explicitly asks for `$presentation-generator-critic`. This skill is for presentation production and review loops, not for pure visual redesign."
---

# Presentation Generator + Critic

This skill turns Codex into a fast presentation production loop built from two roles:

- a builder/editor role
- a strict critic/reviewer role

Use it when the task is not “invent a new visual language from scratch”, but “iterate this deck quickly and safely”.

## What this skill is for

Use this skill when the user wants to:

- update an existing deck faster
- keep old deck versions while creating a new revision
- preserve the meaning or copy while improving execution
- apply comments to slides and then immediately review the result
- fix layout, flow, proof screens, or export issues
- maintain a stateless workflow from one deck round to the next

If the user wants a deeper visual redesign, stronger art direction, or a more radical design pass, use `portal-designer` instead.

## Working model

Treat every round as stateless.

1. Explicitly identify the active deck.
2. Explicitly identify the active brief for that round.
3. If version history matters, create a new version instead of overwriting.
4. Route the task to the right role before editing:
   - `portal-designer` for Portal-branded redesign, composition, hierarchy, and visual language
   - `Generator` for content, structure, proof screens, and production changes
5. Apply the requested changes to that active version only.
6. Export the updated PDF and any screen renders.
7. Review that exact same version with a strict critic pass.
8. If `Critic` returns `PASS` and fidelity to an approved source matters, run `presentation-art-director` as the final visual gate.
9. Return a concise verdict plus the top remaining fixes.

## Delegation model

If subagents are available and the user has explicitly allowed delegation, prefer this split:

- builder subagent edits the active version
- critic subagent reviews the same version read-only

If delegation is not available, follow the same loop locally.

## Hard rules

- Do not rely on memory of past iterations unless they are explicitly provided.
- Do not silently choose the “current” deck from history; the active files must be explicit.
- Do not start a real round without an explicit brief. If the brief is missing, stop and ask for it or use the brief template.
- Preserve meaning unless the user explicitly asks to rewrite the message.
- Keep proof screens honest and sourced from the real prototype.
- If a screen-slide needs to change, fix the source screen first, then re-export.
- Review only the version that was just edited.

## Outputs

Depending on the request, this skill should help produce:

- a new deck HTML revision
- a new PDF revision
- updated screen renders
- a strict review verdict
- a short list of next fixes

## References

Read these files only as needed:

- Full builder role: [references/generator.md](./references/generator.md)
- Full critic role: [references/critic.md](./references/critic.md)
- Routing logic: [../references/router.md](../references/router.md)
- Brief template: [../references/brief-template.md](../references/brief-template.md)
- Stateless orchestration notes: [references/workflow.md](./references/workflow.md)

</skill>
```
