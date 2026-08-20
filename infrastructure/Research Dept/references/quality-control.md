# Quality Control System

### [[2026-04-15]]

## Quality gate

Research Critic issues:

- `PASS`
- `REVISE`
- `FAIL`

Special verdicts:

- `Insufficient primary evidence`
- `Insufficient market-overview sourcing`
- `Overfit to weak sources`
- `Premature stopping`
- `Insufficient competitor coverage`
- `Public numbers under-collected`
- `Economics coverage too weak`
- `Unknown values hidden`
- `Narrative-heavy, data-light report`
- `Strategic contamination in factual mode`
- `Too platform-centric`
- `Category claims unsupported by external reporting`
- `Leader map built from self-description only`
- `Corpus drift`
- `Legacy-source dominance`
- `Interview coverage too shallow`
- `Interview-led request not respected`
- `Old canon reused as primary backbone`
- `New corpus insufficiently materialized`
- `Voice map missing or weak`
- `Report not meaningfully different from prior canon`

## Required checks

1. Is there a clear Research Brief.
2. Is downstream consumer defined.
3. Is research mode one of the supported factual modes.
4. Do normalized cards exist.
5. Does an evidence ledger exist.
6. Is source hierarchy respected.
7. Is visible claim traceability present in the final report.
8. Is factual completeness adequate for the declared scope.
9. Is competitor coverage adequate where relevant.
10. Are public numbers collected where available.
11. Is economics coverage adequate where relevant.
12. Are confirmed, estimated, contradictory, and unknown values clearly labeled.
13. Is uncertainty explicit.
14. Is the report free from strategic contamination.
15. Is packet safe for downstream without advice or prioritization.
16. Does the run include authoritative external overview sources when market-level coverage was required.
17. Is the report over-dependent on self-description by market participants.
18. Are category-level claims supported by external reporting where needed.
19. Is independent leader mapping supported by external sources where claimed.
20. Did the report actually follow the requested source corpus.
21. Is legacy canon dominating where it should not.
22. Is interview-led coverage deep enough when interview-led mode is selected.
23. Is the voice map present and materially useful when required.
24. Is the run meaningfully different from prior canon when corpus shift was requested.
25. Does the completed CEO-readable run include a current HTML export.

## Review axes

- source quality
- source diversity
- factual verification
- visible traceability
- factual completeness
- competitor coverage completeness
- public number collection quality
- economics coverage quality
- honesty about unknowns
- absence of strategic contamination
- packet factual safety
- market-overview source mix
- independence of market framing
- corpus fidelity
- degree of true rerun vs light update
- interview-source depth
- voice diversity
- repeated-theme quality
- legacy contamination risk
- export completeness

## Red flags

The critic must explicitly flag:

1. Report is narrative-heavy and data-light.
2. Strongest sources are too weak for the claims made.
3. Competitor map is obviously incomplete for the declared scope.
4. Public pricing, funding, revenue, or headcount data is skipped without noting absence.
5. Unknown values silently disappear.
6. Numbers are presented without evidence-status labeling.
7. Final report is source-backed only invisibly, not visibly traceable.
8. Strategic or recommendation language appears in factual output.
9. Packet introduces prioritization or advice.
10. Search stopped before the factual coverage floor was met.
11. Report relies mostly on official docs and help centers for a market-level claim set.
12. Market growth, market evolution, or leader claims are made without external reporting.
13. Category map is built only from vendor websites.
14. External perspective is missing where overview was required.
15. Interview-led request is answered with old canon plus a few interviews.
16. Corpus-shift rerun still uses legacy canon as silent backbone.
17. Voice map is absent, shallow, or not synthesis-relevant.
18. Requested interview/operator corpus contributes too little to final synthesis.
19. CEO-readable run ends without HTML export in the canonical exports zone.

## Fitness-by-audience model

### Dossier fitness

Check:

- factual coherence
- evidence traceability
- scope completeness
- explicit handling of unknowns and contradictions
- corpus fidelity when corpus is specified

### CEO report fitness

Check:

- self-contained readability
- visible traceability on key claims
- tables and maps are useful without becoming advice
- Anton can understand the topic without jumping through raw sources
- report reflects the requested corpus rather than department defaults

### Agent packet fitness

Check:

- remains purely factual
- preserves uncertainty and evidence status
- does not convert information into recommendation
- links back to canon cleanly
- does not hide corpus-specific limitations

## Revision protocol

If any of the following is true, `REVISE` should be default:

- competitor coverage is materially incomplete;
- numbers collection is thin for a numbers-heavy run;
- economics section is weak for an economics-heavy run;
- unknown values are hidden;
- report relies on prose where missing data should be acknowledged;
- packet is not fact-safe;
- a market-level run lacks authoritative external overview sources;
- category-level framing depends mostly on participant self-description;
- corpus fidelity is weak;
- interview-led request is not materially interview-led;
- corpus-shift rerun is not meaningfully different from prior canon;
- a CEO-readable run has no current HTML export.
