# Deep Research Department Operating System

### [[2026-04-15]]

## 1. Why The Department Reuses Too Much Legacy Canon

The current department is strong at:

- memory-first retrieval;
- canon reuse;
- incremental updates;
- keeping factual discipline once a backbone already exists.

That becomes a problem when the user is not asking for a standard update, but for a different evidence backbone.

Failure mode:

- prior canon is retrieved;
- prior canon silently becomes the synthesis backbone;
- new sources are appended around it;
- the final report looks “updated,” but not meaningfully rerun.

This is especially dangerous for:

- interview-led requests;
- voice-led industry overviews;
- operator-commentary runs;
- competitive messaging / advertising-corpus runs;
- account / ICP intelligence runs;
- requests that explicitly say “do not use old sources as the main basis.”

Core redesign rule:

- A change in requested source corpus is not a minor update; it is a different research operation.
- When the user asks for interview-led or voice-led research, interviews must become the primary corpus, not a supplement.
- When the user asks for competitor ads or messaging, public messaging artifacts must become the primary corpus, not a few examples appended to a market scan.
- When the user asks for ICP or account intelligence, public account/persona signals must become the primary corpus, not generic market segmentation.
- Legacy canon may be used as background only when explicitly allowed.
- Final reports must reflect the requested evidence base, not the department’s default reuse behavior.

## 2. New Modes Added

The department now supports seven factual modes:

### Market Intelligence

Purpose:

- gather maximum relevant public market information;
- map competitors, categories, economics, business models, and public numbers.

### Objective Market Scan

Purpose:

- provide a neutral map of a category, market, or topic;
- explain how the space is structured and what remains unclear.

### Interview-Led Market Overview

Purpose:

- build a factual market overview from interviews, podcasts, public talks, long-form conversations, practitioner commentary, and operator materials;
- show what practitioners and market participants repeatedly say about workflows, economics, bottlenecks, myths, consensus, and disagreement.

Primary question:

- what are people in the industry actually saying about this category?

### Competitive Messaging Intelligence

Purpose:

- map what competitors publicly say, advertise, promise, prove, and repeat across ads, landing pages, product pages, social posts, case studies, and marketplaces;
- separate observed messages from inferred positioning or sales patterns.

Primary question:

- how does this market publicly sell and frame value?

### Account / ICP Intelligence

Purpose:

- map target segments, account/persona fit, public qualification signals, disqualifying signals, and missing data;
- support factual prospect or ICP understanding without private enrichment or outreach advice.

Primary question:

- which public signals suggest account or persona fit, and where is evidence weak?

### Corpus-Shift Rerun

Purpose:

- rerun an existing topic with a different primary evidence corpus;
- build a new synthesis backbone without silently letting legacy canon dominate.

Primary question:

- if the main source corpus changes, what should the new report look like?

### Verification

Purpose:

- check a narrow claim, number, date, feature, event, or company statement.

Primary question:

- is this true, false, mixed, or insufficiently supported?

## 3. Department Mission

Deep Research Department remains an objective information and competitor intelligence system. It exists to gather and structure reality, not to recommend action.

Core mission layers:

- collect public evidence;
- normalize source units;
- extract facts, numbers, contradictions, and gaps;
- produce readable factual reports;
- preserve reusable factual context for future work;
- respect requested evidence corpus when a run is corpus-led rather than canon-led.

## 4. Core Principles

- Data layer is more important than agent persona.
- The department exists to gather and structure reality, not to recommend action.
- Reality description comes before interpretation.
- Unknown, estimated, contradictory, and confirmed values must be labeled explicitly.
- Raw layer, normalized/evidence layer, knowledge layer, CEO reading layer, and agent context layer must remain separate.
- Recommendation language is out of scope for this department.
- Existing memory is candidate context, not truth by default.
- Human-readable reports must remain visibly traceable to evidence.
- Human-readable completion defaults to both Markdown CEO report and HTML readable export.
- Official sources explain mechanics. Authoritative external sources explain the market.
- Any serious market-level research must include not only primary platform/company sources, but also high-quality external market-overview sources.
- A requested change in source corpus is a new research operation, not a minor update.
- Interview-led synthesis must be driven by interview/operator voices, not by old canon with a few interviews appended.
- Competitive messaging synthesis must be driven by observed ads/pages/social messages, not generic competitor descriptions.
- Account/ICP synthesis must be driven by public fit and disqualification signals, not unsourced prospect intuition.

## 5. 7-Layer Architecture

### 1. Human

Anton remains CEO and final judge of ambiguity, priorities, corpus expectations, and use of factual outputs.

### 2. Strategy

Strategy is outside this department. Strategy teams may consume outputs later, but this department does not produce strategic advice.

### 3. Long Memory

Registries and canonical assets support retrieval-first operation. Memory preserves factual intelligence, factual scans, canon-worthy verification memos, and corpus-specific canons where multiple evidence backbones coexist.

### 4. Coordination

Router, brief discipline, mode selection, source hierarchy, corpus planning, stopping rules, evidence-status labeling, and registry update logic live here.

### 5. Workspace

Filesystem reflects the real pipeline: intake, inventory, cards, evidence, synthesis, review, CEO report, HTML export, packet, registry.

### 6. Agents

Each role serves information gathering, normalization, evidence extraction, factual synthesis, review, packaging, or memory maintenance.

### 7. Artifacts

Artifacts, not chat messages, define the system: brief, corpus plan, inventory, cards, evidence ledger, voice map when needed, dossier, review, CEO report, HTML export, packet, and registries.

## 6. Role Map

| Role | Core job | Factual responsibility |
|---|---|---|
| Research Router | choose mode, workflow, output mix | keep scope factual and detect corpus-shift or interview-led intent |
| Research Intake Analyst | produce factual brief | define coverage, corpus expectations, and legacy reuse policy |
| Scout Collector | build source universe | collect sources appropriate to the requested corpus, not just default canon style |
| Source Normalizer | create canonical source units | preserve provenance, source quality, source role, corpus membership, and internal vs external perspective |
| Evidence Extractor | materialize evidence layer | extract facts, numbers, contradictions, repeated themes, disagreements, and corpus-weighted signals |
| Synthesis Analyst | produce factual analytical core | describe market structure, workflows, economics, or voice patterns from the requested corpus |
| Research Critic | quality gate | audit source quality, factual completeness, corpus fidelity, and legacy contamination risk |
| CEO Report Editor | produce founder-readable report | make the factual picture readable, traceable, self-contained, and faithful to the selected corpus |
| Context Packager | produce factual packet | preserve only reusable factual context without advice |
| Registry / Memory Owner | update canonical memory | record canon, supersession, corpus metadata, and retrieval links after completed runs |

## 7. Updated Workflow

### Market Intelligence Workflow

`Brief -> Inventory -> Source Mix Check -> Normalized Cards -> Evidence Ledger -> Market Intelligence Dossier -> Quality Review -> CEO Market Intelligence Report -> Optional Factual Packet -> Registry Update`

### Objective Market Scan Workflow

`Brief -> Inventory -> Source Mix Check -> Normalized Cards -> Evidence Ledger -> Neutral Market Dossier -> Quality Review -> CEO Market Scan Report -> Optional Factual Packet -> Registry Update`

### Interview-Led Market Overview Workflow

`Brief -> Corpus Plan -> Interview/Conversation Inventory -> Normalized Cards -> Evidence Ledger -> Voice Map -> Interview-Led Dossier -> Quality Review -> CEO Interview-Led Report -> Optional Factual Packet -> Registry Update`

Rules:

- interviews / podcasts / long-form conversations / operator commentary must become the primary corpus;
- old canon cannot silently dominate;
- voice map is mandatory;
- consensus, disagreement, anecdotal versus repeated pattern, and relation to broader market framing must be explicit.

### Competitive Messaging Intelligence Workflow

`Brief -> Messaging Corpus Plan -> Ad/Landing/Social Inventory -> Normalized Cards -> Evidence Ledger -> Competitive Message Map -> Competitive Messaging Dossier -> Quality Review -> CEO Messaging Intelligence Report -> HTML Export -> Optional Factual Packet -> Registry Update`

Rules:

- public messaging artifacts must become the primary corpus;
- message map is mandatory;
- observed copy/creative must be separated from inferred strategy;
- ad or landing-page evidence needs date/capture notes where possible;
- campaign performance, spend, ROAS, or internal intent cannot be inferred without credible evidence.

### Account / ICP Intelligence Workflow

`Brief -> ICP Hypothesis -> Public Account Inventory -> Normalized Cards -> Evidence Ledger -> Account/ICP Map -> ICP Intelligence Dossier -> Quality Review -> CEO ICP Intelligence Report -> HTML Export -> Optional Factual Packet -> Registry Update`

Rules:

- public account/persona signals must become the primary corpus;
- account/ICP map is mandatory;
- fit signals and disqualifying signals must both be captured;
- confidence labels and missing data are mandatory;
- private contact data or outreach intelligence must not be fabricated.

### Corpus-Shift Rerun Workflow

`Brief -> Prior Canon Review -> Corpus Shift Plan -> New Corpus Collection -> Normalized Cards -> Evidence Ledger -> New Dossier -> Quality Review -> CEO Report -> Optional Factual Packet -> Registry Update`

Rules:

- prior canon may be acknowledged;
- prior canon must not silently dominate;
- rerun must materially differ in synthesis backbone from the legacy-source run;
- registry must record that this canon is corpus-specific, not necessarily a blanket supersession.

### Verification Workflow

`Brief -> Focused Inventory -> Normalized Cards -> Evidence Ledger -> Verification Memo -> Optional Review -> Optional CEO Verification Note -> Registry Update if canon-worthy`

## 8. Output Model

### 1. Raw Layer

- source captures
- scout notes
- source inventory
- raw excerpts

### 2. Normalized / Evidence Layer

- normalized source cards
- evidence ledger
- contradiction notes
- data-status notes
- voice map where required
- competitive message map where required
- account/ICP map where required

### 3. Knowledge Layer

- Market Intelligence Dossier
- Neutral Market Dossier
- Interview-Led Dossier
- Competitive Messaging Dossier
- Account / ICP Intelligence Dossier
- Corpus-Shift Rerun Dossier
- Verification Memo

### 4. CEO Reading Layer

- CEO Market Intelligence Report
- CEO Market Scan Report
- CEO Interview-Led Report
- CEO Messaging Intelligence Report
- CEO ICP Intelligence Report
- CEO Corpus-Shift Report
- optional CEO verification note

### 5. Agent Context Layer

- factual context packet
- canonical factual packet

## 9. Source Corpus Model

Each run now declares not only source quality, but corpus composition.

Minimum corpus types:

1. company/platform primary docs
2. authoritative market overview sources
3. interviews / podcasts / long-form conversations
4. practitioner / operator commentary
5. weak-signal discourse

For every run, the department must record:

- primary corpus;
- secondary corpus;
- disallowed overreliance;
- target synthesis contribution;
- allowed legacy reuse.

## 10. Corpus Plan Requirement

Corpus-driven runs are incomplete unless a materialized corpus plan confirms:

- what the requested primary corpus is;
- what secondary corpus may support it;
- whether prior canon exists;
- whether legacy reuse is forbidden, background-only, comparative-only, moderate, or full;
- what counts as corpus failure;
- whether the new run is truly a rerun versus a light update.

## 11. Voice Map Requirement

Interview-led runs are incomplete unless a materialized voice map shows:

- who is speaking;
- founder / operator / agency / creator / expert / platform-adjacent role;
- repeated themes;
- disagreements and tensions;
- representative versus idiosyncratic signals;
- where interview-led evidence aligns or diverges from broader market framing.

## 12. Language Policy

The following language remains prohibited in all supported modes:

- best wedge
- strongest opportunity
- moat
- should build
- best direction
- recommended positioning
- v1 should
- strategic implication
- next actions
- hypothesis support
- hypothesis weakening
- falsification

Additional corpus-fidelity rule:

- do not label a report as interview-led if its synthesis backbone is still legacy canon with only supplementary interview quotes.

## 13. Decisions Anton Must Make as CEO

- When both standard canon and interview-led canon exist for one topic, which one should be treated as default human-reading surface?
- Should `Interview-Led Market Overview` automatically create a packet, or remain CEO-facing unless requested?
- When corpus shift happens, should the new run coexist with prior canon by default, or supersede only when explicitly declared?
- How strict should “legacy reuse allowed: background only” be in practice?
