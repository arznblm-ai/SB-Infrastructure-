# Artifact Contracts

## Contract rules

- Every artifact has an owner role.
- Every artifact has a canonical template.
- Every stage consumes a materialized artifact, not an implied state.
- Final outputs are invalid if mandatory intermediate artifacts are missing.
- Registry update remains mandatory.
- Human-readable CEO output and agent packet remain separate deliverables.
- Human-readable completion defaults to both Markdown CEO report and HTML readable export.
- The department produces factual outputs only.
- Corpus-driven runs must materialize corpus intent, not imply it.

## Output model

### 1. Raw Layer

- source captures
- scout notes
- source inventory
- raw excerpts
- ad/page/social captures when competitive messaging is in scope
- public account/persona captures when ICP research is in scope

### 2. Normalized / Evidence Layer

- normalized source cards
- evidence ledger
- contradiction notes
- data-status notes
- corpus plan where required
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
- optional CEO Verification Note

### 5. Export Layer

- HTML readable export of the current CEO report
- optional PDF packaged export
- export index or export registry note where maintained

### 6. Agent Context Layer

- factual context packet
- canonical factual packet

## Mandatory chains

Serious factual research must follow:

`Research Brief -> Source Inventory -> Normalized Source Cards -> Evidence Ledger -> Dossier -> Quality Review -> CEO Report -> HTML Export -> Optional Context Packet -> Registry Update`

Additional chain requirements:

- Interview-led runs: `Research Brief -> Corpus Plan -> Interview Inventory -> Normalized Cards -> Evidence Ledger -> Voice Map -> Dossier -> Review -> CEO Report -> HTML Export -> Registry Update`
- Competitive messaging runs: `Research Brief -> Messaging Corpus Plan -> Ad/Landing/Social Inventory -> Normalized Cards -> Evidence Ledger -> Competitive Message Map -> Messaging Dossier -> Review -> CEO Report -> HTML Export -> Registry Update`
- Account / ICP runs: `Research Brief -> ICP Hypothesis -> Public Account Inventory -> Normalized Cards -> Evidence Ledger -> Account/ICP Map -> ICP Dossier -> Review -> CEO Report -> HTML Export -> Registry Update`
- Corpus-shift reruns: `Research Brief -> Prior Canon Review -> Corpus Shift Plan -> New Corpus Collection -> Normalized Cards -> Evidence Ledger -> Dossier -> Review -> CEO Report -> HTML Export -> Registry Update`

## Hard gates

### Dossier gate

Dossier is invalid if:

- no normalized source cards exist;
- no evidence ledger exists;
- source trail is not materialized;
- source quality overview is missing;
- source mix check is missing for a market-level run;
- visible traceability is missing;
- corpus plan is missing for interview-led or corpus-shift run;
- voice map is missing for interview-led run;
- competitive message map is missing for competitive messaging run;
- account/ICP map is missing for account/ICP run;
- prior canon role is not explicit for a corpus-shift rerun.

### Review gate

Quality Review is invalid if:

- dossier input is missing;
- evidence trace is missing;
- factual review axes are absent;
- special verdicts and revision instructions are absent when needed;
- corpus fidelity was required but not reviewed.

### CEO gate

CEO Report is invalid if:

- dossier is not approved;
- review is not materialized;
- confirmed vs estimated vs contradictory vs unknown labeling is absent;
- report lacks visible evidence anchors on important claims;
- interview-led report is not visibly driven by interview/operator sources when that mode was selected.
- competitive messaging report does not separate observed ads/pages/social messages from inferred patterns.
- ICP report does not separate observed public account signals from inferred qualification.

### Export gate

HTML Export is invalid if:

- approved CEO report is missing;
- exported file path is not materialized in `workspace/09_exports/`;
- export is stale relative to the final CEO report revision;
- export loses visible structure enough to reduce readability materially.

### Packet gate

Context Packet is invalid if:

- dossier is not approved;
- packet includes advice, prioritization, or product direction;
- uncertainties and data gaps are hidden;
- approved sources and canon links are missing;
- packet silently inherits the wrong corpus framing.

## Artifact stack

### Research Brief

Owner: Intake Analyst

Required fields:

- request title
- request type
- business goal or research purpose
- downstream consumer
- research questions
- out of scope
- required depth
- time horizon
- preferred sources
- required confidence level
- preferred source tiers
- is market-overview coverage required
- are authoritative external sources required
- is category evolution required
- is independent leader mapping required
- minimum external-market-source coverage
- minimum number of authoritative overview sources
- required balance between primary and external sources
- primary evidence corpus
- secondary evidence corpus
- is corpus shift required
- allowed legacy reuse
- is interview-led synthesis required
- source composition target
- minimum interview/operator source coverage
- minimum external-market background coverage
- legacy canon role
- claims requiring strict verification
- freshness requirement
- expected coverage dimensions
- forbidden source shortcuts
- CEO reading need
- desired report depth
- final output mix
- research mode
- output expectations
- success criteria
- HTML export required by default

### Corpus Plan

Owner: Router or Intake Analyst

Required fields:

- topic
- prior canon exists yes/no
- requested primary corpus
- requested secondary corpus
- allowed legacy reuse
- intended output style
- source composition target
- risk of legacy dominance
- what would count as corpus failure

### Source Inventory

Owner: Scout Collector

Required fields:

- source id
- title
- type
- tier
- location
- relevance
- rough trust rationale
- scouting note
- duplicate mark
- coverage role

When the run is interview-led, inventory must also distinguish:

- interview / podcast / talk / long-form conversation;
- operator or founder voice;
- market background source;
- legacy support source if any.

Inventory remains candidate-set only, not normalization.

When the run is competitive messaging intelligence, inventory must also distinguish:

- ad library entries;
- landing pages;
- pricing/product pages;
- case studies;
- social posts;
- search snippets;
- marketplaces/review pages;
- public creative centers;
- archived or date-sensitive captures.

When the run is account/ICP intelligence, inventory must also distinguish:

- target account pages;
- buyer/persona evidence;
- hiring and technology signals;
- funding/news signals;
- marketplace/review footprint;
- customer/case-study evidence;
- disqualifying public signals.

### Normalized Source Card

Owner: Source Normalizer

Required fields:

- source id
- canonical title
- source type
- source tier
- source role
- corpus membership
- internal or external perspective
- source speaker category
- representative or idiosyncratic
- provenance
- author or organization
- date
- short description
- core claims
- important facts
- number relevance
- company fact relevance
- economics relevance
- evidence-status usefulness
- safe for factual description / number support / company fact support / economics support
- does this source describe itself or the market around it
- trust rationale
- trust signals
- limitations
- freshness note
- dependency concentration note
- relevance to current request
- reuse value for future agents

For competitive messaging cards, also capture:

- competitor or brand;
- channel;
- observed offer;
- observed CTA;
- stated audience or implied audience;
- pain point addressed;
- value proposition;
- proof or claim type;
- creative/message format;
- screenshot or capture note if available;
- whether the point is directly observed or inferred.

For account/ICP cards, also capture:

- company or persona;
- segment;
- public fit signals;
- disqualifying signals;
- buyer role evidence;
- budget/scale proxy when public;
- urgency or trigger signal;
- confidence label;
- missing data that would be needed before outreach or prioritization.

### Evidence Ledger

Owner: Evidence Extractor

Required fields:

- claim
- claim type
- supporting sources
- contradiction status
- evidence strength
- confidence
- verification status
- data status
- platform and company facts
- market-overview facts
- category-development signals
- leader or positioning consensus
- external market narrative
- repeated themes
- disagreement patterns
- isolated opinions
- known facts
- estimated facts
- contradictory facts
- unknown values
- economics notes
- notes

For competitive messaging claims, ledger rows must also label:

- observed message / inferred pattern / unsupported hypothesis;
- channel;
- competitors represented;
- offer, CTA, pain point, proof type, and creative format where applicable.

For account/ICP claims, ledger rows must also label:

- fit signal / disqualifying signal / trigger signal / persona signal;
- account or segment represented;
- public evidence basis;
- confidence and missing data.

### HTML Export

Owner: CEO Report Editor or delegated export step after CEO report completion

Required fields:

- source CEO report path
- output HTML path
- export date
- export freshness relative to CEO report
- whether this HTML is canonical current readable export
- reusable yes/no

### Voice Map

Owner: Evidence Extractor

Required for interview-led runs.

Required fields:

- speaker or source
- role in market
- source type
- main claims
- repeated themes
- tensions and disagreements
- confidence
- source anchor
- representative or idiosyncratic

### Research Dossier

Owner: Synthesis Analyst

Rules by mode:

#### Market Intelligence dossier must include

- topic
- market structure
- category segmentation
- key players
- business models
- public numbers
- economics signals
- what is confirmed
- what is estimated
- what is contradictory
- what is unknown
- source quality overview
- source mix overview
- why the search was stopped
- source trail / evidence anchors

#### Objective Market Scan dossier must include

- topic
- market structure
- category segmentation
- key players
- visible models
- what is confirmed
- what is contradictory
- what is unknown
- source quality overview
- source mix overview
- why the search was stopped
- source trail / evidence anchors

#### Interview-Led dossier must include

- topic
- what practitioners are talking about
- repeated themes
- consensus patterns
- disagreement patterns
- operator / founder / agency / creator perspectives
- economics / workflow / bottleneck insights
- what appears widely repeated
- what appears anecdotal
- where interviews align or diverge from broader market coverage
- what remains unclear
- source quality overview
- source mix overview
- corpus fidelity note
- why the search was stopped
- source trail / evidence anchors

#### Competitive Messaging dossier must include

- topic
- competitors and channels covered
- message-map summary
- repeated pains
- repeated offers
- repeated CTAs
- repeated proof types
- landing-page structure patterns
- creative/message format patterns
- what is directly observed
- what is inferred
- contradictions and outliers
- source quality overview
- source mix overview
- channel coverage limits
- why the search was stopped
- source trail / evidence anchors

#### Account / ICP dossier must include

- topic
- ICP hypothesis
- inclusion rules
- exclusion rules
- segment map
- account/persona evidence table
- fit signals by strength
- disqualifying signals
- trigger events and timing
- buyer-role evidence
- public data gaps
- no-contact/private-data constraints
- source quality overview
- source mix overview
- why the search was stopped
- source trail / evidence anchors

#### Corpus-Shift rerun dossier must include

- topic
- prior canon review summary
- new corpus backbone
- legacy canon role
- new findings from the new corpus
- overlap with prior canon
- divergence from prior canon
- source quality overview
- source mix overview
- corpus fidelity note
- why the search was stopped
- source trail / evidence anchors

#### Verification memo must include

- claim under review
- verdict
- strongest supporting evidence
- strongest conflicting evidence
- source quality overview
- freshness note
- visible evidence anchors

### Quality Review

Owner: Research Critic

Must include:

- status
- special verdicts
- scores by axis
- defects
- missing evidence
- revision instructions
- downstream safety note
- CEO readability note
- corpus fidelity note where relevant

### CEO Report

Owner: CEO Report Editor

Mode-sensitive requirements:

#### Interview-led CEO report must include

- what people in the industry are saying
- main recurring themes
- where voices converge
- where voices diverge
- what appears widely believed
- what appears anecdotal or weakly supported
- how interview signals compare to broader market coverage
- visible evidence anchors
- source trail

#### Corpus-shift CEO report must include

- what the new source corpus was
- how it differs from prior canon
- what changed in the backbone
- what remained stable
- what is now foregrounded
- visible evidence anchors
- source trail

#### Competitive messaging CEO report must include

- what public messaging corpus was reviewed
- competitor/channel coverage
- repeated pains, offers, CTAs, and proof types
- observed message patterns
- inferred patterns clearly labeled as inference
- outliers and contradictions
- source limitations and freshness limits
- visible evidence anchors
- source trail

#### Account / ICP CEO report must include

- ICP boundaries
- segment/account/persona map
- public fit signals
- disqualifying signals
- trigger signals where supported
- confidence labels
- missing data before prioritization or outreach
- privacy/source limits
- visible evidence anchors
- source trail

### Context Packet

Owner: Context Packager

Required fields:

- topic description
- corpus type
- run type
- factual findings safe for reuse
- evidence-status labeling
- uncertainties and gaps
- approved sources
- linked dossier and CEO report
- note on whether this packet is corpus-specific

### Registry contracts

Registry rows must now support:

- corpus type
- run type
- legacy reuse level
- whether the asset is interview-led
- whether it supersedes old canon or sits alongside it
- canonical for which question type
