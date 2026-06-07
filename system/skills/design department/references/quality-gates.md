# Quality Gates

`design-orchestrator` is allowed to stop the workflow.

The department should not always continue just because a role produced output.

## Stop before role execution if

- source artifact is missing
- goal is unclear
- constraints are missing and materially affect the work
- no workflow mode fits the task
- the caller is asking for execution but has not passed the necessary deck or reference

Verdict:

- `FAIL` if execution cannot start safely
- `REVISE` if the request is understandable but the brief is incomplete

## Stop after `presentation-designer` if

- no usable design direction was produced
- the proposed direction conflicts with the stated constraints
- the work changed meaning instead of visual system

Verdict:

- `REVISE`

## Stop after `presentation-generator-critic` if

- the new version is unclear or untracked
- the produced version cannot be tied to a specific source deck
- the critic verdict says unresolved major issues remain

Verdict:

- `REVISE`

## Stop after `presentation-art-director` if

- critical visual issues remain unresolved
- typography, alignment, or fidelity defects still block approval
- the deck is not yet close enough to the approved source or target standard

Verdict:

- `REVISE` or `FAIL`, depending on severity

## Final package rule

Do not return a `Final Design Package` with a misleading success signal.

Allowed final statuses:

- `APPROVED`
- `APPROVED_WITH_NOTES`
- `REVISE_REQUIRED`
- `FAILED_INTAKE`
