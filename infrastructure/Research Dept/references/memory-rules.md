# Memory / Retrieval Rules

## Retrieval-first discipline

Before new search, the department must check:

1. `workspace/00_registry/topic-registry.md`
2. `workspace/00_registry/source-registry.md`
3. `workspace/00_registry/context-packet-index.md`
4. `workspace/00_registry/ceo-report-index.md`
5. dossier, CEO report, and packet by similar topic slug

## Operational memory principle

Registry layer is not decorative. It must be updated after every completed research run.

If registry is not updated:

- research is operationally incomplete;
- update workflow cannot be considered memory-first;
- canonical artifacts are unassigned.

## Canon retrieval principle

Memory-first retrieval does not mean canon-first dominance.

If corpus shift is requested:

- prior canon is retrieved as background;
- prior canon must not become the default synthesis backbone;
- the new run must state its corpus intent explicitly.

## Registry owner logic

Primary owner: `Registry / Memory Owner`.

Fallback owner in low-volume mode:

- `Context Packager`, if a separate registry role is not launched.

Registry owner must:

- assign canonical dossier;
- assign canonical CEO report;
- record current canonical HTML export for the CEO-readable artifact where one exists;
- assign canonical packet if any;
- write `last reviewed`;
- record corpus type;
- record run type;
- record legacy reuse level;
- record whether asset is interview-led;
- record whether asset supersedes prior canon or sits alongside it;
- update source registry entries for new or changed source cards.

## Canonicality rules

Canonicality is no longer only `topic -> one current canon`.

Canonicality must be understood as:

- `topic slug`
- `corpus type`
- `question type`

This allows multiple valid canons to coexist when they answer different questions or use different evidence backbones.

Examples:

- one standard market-intelligence canon for a topic;
- one interview-led canon for the same topic;
- one verification memo canon for a narrow claim.

## Supersession and coexistence rules

- Standard update may supersede a prior canon of the same corpus/question type.
- Corpus-shift rerun may coexist with prior canon rather than automatically supersede it.
- Registry must explicitly say whether an asset:
  - supersedes prior canon
  - coexists alongside it
  - is background-only relative to another canon

If this is absent, the run cannot be closed.

## Source registry rules

- Every canonical source card gets an entry in source registry.
- Delta update may update an existing source card instead of duplicating it.
- One source ID should resolve to one current canonical card path per corpus-specific run context.

## Packet and CEO report indexing rules

- Context packet index gets an entry only after `PASS` review.
- CEO report index gets an entry only after `PASS` review and finished CEO report.
- Human-readable run completion requires finished HTML export alongside the CEO report, except narrow verification runs where no CEO-readable note was produced.
- Review link must be attached to both indexes.
- Indexes must indicate corpus type and run type where relevant.

## Reuse rules

- Human readers default to CEO report, not packet.
- Downstream agents default to packet, not CEO report.
- If the packet is insufficient, dossier may be raised.
- If the dossier is insufficient or stale, only then run update or rerun workflow.
- If the user explicitly requests corpus shift, old canon can only act according to the allowed legacy reuse level.

## Completion rule

Research is complete only if:

- canonical artifact mix exists;
- HTML readable export exists for completed CEO-readable runs;
- registries are updated;
- supersession or coexistence state is explicit;
- last-reviewed dates are materialized;
- corpus metadata is materialized where relevant.
