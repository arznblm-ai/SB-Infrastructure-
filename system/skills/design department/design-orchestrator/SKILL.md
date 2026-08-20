---
name: design-orchestrator
description: "Use when the user wants Design Department to run as an operating system instead of a loose set of role docs: intake the request, classify the design job, choose the workflow, sequence presentation roles, pass artifacts between them, enforce quality gates, and return a final design package with review status."
---

# Design Orchestrator

### [[2026-04-16]]

This skill is the canonical entrypoint for Design Department.

It does not replace the specialist roles and it does not do the design itself. Its job is to turn the department into a runnable sequence of roles.

## What this role does

Use this skill when the request sounds like:

- "use design department"
- "run this through design orchestrator"
- "start redesign flow"
- "start visual QA"
- "run designer -> art director -> generator critic"
- "take this deck and route it through the right design workflow"

The orchestrator is responsible for:

1. intake of the design request
2. reading explicit context and artifacts
3. classifying the task
4. selecting the correct workflow mode
5. invoking the right role sequence
6. passing the correct artifacts between roles
7. enforcing quality gates
8. returning a final design package with review verdict

## What this role must not do

- do not become another presentation designer
- do not do art-direction review in place of the art director
- do not do generator-critic work in place of the production loop
- do not rely on vague thread memory when artifacts are missing
- do not force the caller to know the right sequence of roles

## Canonical role sequence

The orchestrator manages these role-skills:

- [portal-designer](../portal-designer/SKILL.md)
- [presentation-art-director](../presentation-art-director/SKILL.md)
- [presentation-generator-critic](../presentation-generator-critic/SKILL.md)

If routing logic in another file disagrees, use these department-level contracts as the source of truth:

- [canonical-entrypoint-contract.md](../references/canonical-entrypoint-contract.md)
- [router.md](../references/router.md)
- [workflow-modes.md](../references/workflow-modes.md)
- [artifact-handoff-model.md](../references/artifact-handoff-model.md)
- [quality-gates.md](../references/quality-gates.md)

## Operating procedure

1. Require an explicit intake brief. If the caller did not provide one, build it via [brief-template.md](../references/brief-template.md).
2. Read only the current explicit artifacts: source deck, reference deck, comments, output constraints, approved version, and success criteria.
3. Classify the request into one workflow mode.
4. Produce a `Design Intake Brief` artifact before calling any specialist role.
5. Run the correct role sequence.
6. After each role, produce or update the expected handoff artifact instead of relying on memory.
7. Stop the workflow if a quality gate fails and return a revise/fail verdict.
8. Finish with a `Final Design Package`.

## Workflow modes

The orchestrator must support these modes:

- `new_design_flow`
- `polish_existing_deck_flow`
- `iterative_revision_flow`
- `visual_qa_only_flow`

Read the exact sequencing contract in [workflow-modes.md](../references/workflow-modes.md).

## Cross-thread rule

This role is specifically designed for cross-thread use.

- Assume nothing from another thread unless it is passed in explicitly.
- Work from artifacts, not memory.
- If prior context exists elsewhere, require it to be passed as files, links, comments, versions, or structured brief fields.

## Required outputs

Every orchestrated run must return:

- selected workflow mode
- roles used and in what order
- artifacts consumed
- artifacts produced
- current review status
- final package or explicit revise/fail verdict
