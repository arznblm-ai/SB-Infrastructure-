# Canonical Entry Point Contract

The canonical entrypoint for Design Department is:

- [design-orchestrator](../design-orchestrator/SKILL.md)

## What this means

When a request from another thread says:

- "use design department"
- "run this through design orchestrator"
- "start redesign flow"
- "start visual QA"
- "run designer then art director then generator critic"

the system should treat `design-orchestrator` as the entrypoint.

The caller should not need to know:

- which workflow mode fits the request
- which role should run first
- how artifacts are handed off
- when QA must stop the process

That logic belongs to the orchestrator.

## Cross-thread rule

`design-orchestrator` must work from explicit inputs, not chat memory.

If context from another thread matters, it must be passed as:

- files
- links
- comments
- current version paths
- structured brief fields

## Department contract

The department is runnable only when:

1. intake is explicit
2. workflow mode is selected
3. roles are sequenced
4. artifacts are handed off
5. quality gates are enforced
6. one final package is returned

If any of those are missing, the department is acting like documentation, not like an operating system.
