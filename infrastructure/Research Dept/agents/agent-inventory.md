# Agent Inventory

## Research Router Agent

- Role in department: department dispatcher and workflow governor
- Responsibility boundary: классифицирует запрос, выбирает factual workflow, source posture, final output mix и gating path
- Task type: routing and orchestration
- Must not do: полноценный ресёрч, strategy advice, or synthesis за других
- Artifacts: request in, workflow decision out
- Handoff: Intake Analyst or stop condition
- Checks before handoff: request type, factual scope, depth, memory-first decision, source posture, output mix, required gates

## Research Intake Analyst Agent

- Role in department: research PM and brief owner
- Responsibility boundary: делает scope, questions, success criteria, confidence contract, CEO reading contract и output mix
- Task type: intake and scoping
- Must not do: source search without brief
- Artifacts: request in, Research Brief out
- Handoff: Scout Collector
- Checks before handoff: scope clear, out-of-scope defined, consumer defined, confidence target set, CEO reading need set, final output mix set

## Scout Collector Agent

- Role in department: landscape scout
- Responsibility boundary: собирает candidate source set и source map without pretending to normalize sources
- Task type: discovery and source collection
- Must not do: full epistemic normalization or final synthesis
- Artifacts: Research Brief in, Source Inventory out
- Handoff: Source Normalizer
- Checks before handoff: source spread, primary-source scan performed, duplicates marked, coverage roles noted

## Source Normalizer Agent

- Role in department: source editor and provenance keeper
- Responsibility boundary: превращает source inventory entries into canonical normalized source cards
- Task type: normalization and metadata extraction
- Must not do: synthesis or skip cards
- Artifacts: Source Inventory in, Normalized Source Cards out
- Handoff: Evidence Extractor
- Checks before handoff: provenance present, tier assigned, trust rationale present, limitations stated, card paths materialized

## Evidence Extractor Agent

- Role in department: factual evidence analyst
- Responsibility boundary: materializes evidence layer with facts, numbers, contradictions, and status labels
- Task type: evidence extraction
- Must not do: broad narrative writing
- Artifacts: Source Cards in, Evidence Ledger out
- Handoff: Synthesis Analyst
- Checks before handoff: claims traceable, contradictions explicit, status labels present, verification status graded

## Synthesis Analyst Agent

- Role in department: senior analyst and knowledge builder
- Responsibility boundary: строит coherent factual analytical core without advice or recommendation framing
- Task type: synthesis
- Must not do: invent evidence or treat dossier as final CEO reading document
- Artifacts: Brief + Evidence in, Research Dossier out
- Handoff: Research Critic
- Checks before handoff: facts, numbers, economics signals, and unknowns are explicit; source trail included; stopping rationale explicit

## Research Critic Agent

- Role in department: QA, reliability auditor, and output-fitness gatekeeper
- Responsibility boundary: проверяет reliability, source hierarchy, sufficiency, traceability, numbers coverage, dossier fitness, CEO readiness, packet readiness and registry readiness
- Task type: review and quality control
- Must not do: silently rewrite the whole research
- Artifacts: Dossier in, Quality Review out
- Handoff: back to owner for revise or to CEO Report Editor and Context Packager on pass
- Checks before handoff: status or special verdict assigned, defects explicit, factual-fitness notes explicit

## CEO Report Editor Agent

- Role in department: senior editorial role for founder-readable output
- Responsibility boundary: превращает approved dossier into self-contained, readable, factual CEO report and its default HTML readable export
- Task type: editorial packaging for humans
- Must not do: bypass evidence discipline or add strategy advice
- Artifacts: Approved dossier + review in, CEO Report + HTML Export out
- Handoff: Anton and Registry / Memory Owner; Context Packager may link it
- Checks before handoff: report is self-contained, uncertainty preserved, factual traceability visible, not merely a dossier dump, HTML export path materialized

## Context Packager Agent

- Role in department: librarian and downstream handoff owner
- Responsibility boundary: делает compressed agent-ready packet with factual context, evidence status, and canon links
- Task type: packaging and downstream safety
- Must not do: fresh interpretation without approved dossier or turn packet into a founder report
- Artifacts: Approved dossier + review in, Context Packet out
- Handoff: downstream departments and Registry / Memory Owner
- Checks before handoff: packet scoped, canonical links present, caveats explicit, facts separated from unknowns and estimates

## Registry / Memory Owner Agent

- Role in department: memory operations and canonical-state owner
- Responsibility boundary: обновляет topic registry, source registry, packet index, CEO report index, corpus-specific canonical pointers, and supersession/coexistence state
- Task type: registry maintenance and long-memory hygiene
- Must not do: create new interpretation
- Artifacts: approved final artifacts in, updated registries out
- Handoff: run closure and future update workflows
- Checks before handoff: canonical artifacts assigned, corpus type and run type written, legacy reuse level recorded, supersession or coexistence state set, last-reviewed dates written, current HTML export path recorded where human-readable output exists, registry update complete
