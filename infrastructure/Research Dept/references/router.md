# Router Logic

## Core question

Router chooses not an agent, but:

- research mode
- factual scope
- information depth
- source posture
- output mix
- gating path
- source mix requirement
- corpus plan requirement
- legacy reuse rule

Router supports:

- `Market Intelligence`
- `Objective Market Scan`
- `Interview-Led Market Overview`
- `Competitive Messaging Intelligence`
- `Account / ICP Intelligence`
- `Corpus-Shift Rerun`
- `Verification`

If a request asks for:

- what Anton should build
- which wedge is best
- which option to choose
- recommendation
- positioning
- strategy

Router must not transform it into a supported mode silently. It should either narrow it into a factual information request or state that strategic recommendation is outside the department's scope.

## Step 1. Classify by research mode

### Market Intelligence

Use when:

- the user wants maximum relevant market information on a topic;
- competitor, category, pricing, business model, economics, funding, or public-number mapping is needed;
- the goal is factual intelligence, not advice.

### Objective Market Scan

Use when:

- the user wants a neutral map of a market, category, or topic;
- the goal is understanding structure, not deciding what to do.

### Interview-Led Market Overview

Use when:

- the user asks to go through interviews, podcasts, talks, founder/operator conversations, long-form discussion, or practitioner commentary;
- the user wants to know what people in the industry are saying;
- the desired output is voice-led and practitioner-led, not just document-led.

### Competitive Messaging Intelligence

Use when:

- the user asks how competitors sell, advertise, frame value, present offers, write CTAs, or position themselves publicly;
- the goal is a factual map of observed ad/landing/social/sales messaging, not a recommendation about what Anton should copy;
- competitor ad libraries, landing pages, pricing/product pages, case studies, social posts, search snippets, marketplaces, and public creative centers are the primary evidence corpus.

### Account / ICP Intelligence

Use when:

- the user asks for target accounts, ICP, lead research, buyer personas, prospect qualification, or account/company fit signals;
- the goal is factual segmentation and evidence-backed qualification, not outreach advice or private enrichment;
- public company pages, hiring signals, funding/news, product pages, LinkedIn/company profiles when accessible, marketplace/review footprints, case studies, and public databases are the primary evidence corpus.

### Corpus-Shift Rerun

Use when:

- prior canon exists on the topic;
- the user explicitly wants a different primary corpus than before;
- “do not use old sources as the main basis” or equivalent intent is present;
- a new synthesis backbone is required.

### Verification

Use when:

- the claim is narrow;
- the desired output is a verdict on truth status.

## Step 2. Detect corpus intent

Router must explicitly determine:

- requested primary corpus;
- requested secondary corpus;
- is interview-led synthesis required;
- is corpus shift required;
- allowed legacy reuse;
- target source composition.

Signal examples:

- “найди интервью”
- “собери, что индустрия говорит”
- “сделай обзор по интервью”
- “пройди по подкастам и разговорам”
- “не бери старые сорсы как основу”
- “хочу новый research через другой тип источников”
- “посмотри рекламу конкурентов”
- “какие офферы и CTA у рынка”
- “разбери лендинги конкурентов”
- “собери ICP”
- “найди похожие аккаунты”
- “какие компании подходят под этот продукт”

These must not default to standard update logic.

## Step 3. Decide whether memory-first or fresh-run-first

Check first:

- topic registry
- source registry
- context packet index
- CEO report index
- existing dossiers by topic slug

If corpus shift is not requested:

- prior canon may support standard update or reuse.

If corpus shift is requested:

- prior canon is retrieved as background only;
- prior canon must not become default synthesis backbone;
- the run must be marked `fresh run` or `corpus-shift rerun`, not ordinary update.

## Step 4. Set source posture and corpus posture

Allowed source postures:

- `primary-heavy`
- `mixed analytical`
- `interview-led`
- `verification`

Router must record source-mix expectation explicitly:

- whether market-overview coverage is required;
- whether authoritative external sources are required;
- minimum external-market-source coverage;
- minimum interview/operator source coverage;
- required balance between primary, external, and interview-led sources.

Router must also record corpus metadata:

- primary evidence corpus;
- secondary evidence corpus;
- allowed legacy reuse;
- source composition target;
- risk of legacy dominance.

## Step 5. Validate the brief gate

Workflow does not start until the brief answers:

1. What exactly is being researched.
2. Why the research is needed.
3. Who the downstream consumer is.
4. What counts as a good factual result.
5. Which supported mode applies.
6. What factual scope is required.
7. What depth is required.
8. What source mix is expected and what shortcuts are forbidden.
9. Which claims require strict verification.
10. Which numbers or company facts require explicit coverage.
11. What evidence-status labeling is required.
12. What output mix is expected.
13. Whether market-overview coverage is required.
14. Whether authoritative external sources are required.
15. What interview/operator coverage must exist if interview-led mode is requested.
16. Whether corpus shift is required.
17. What legacy reuse level is allowed.
18. What corpus would count as failure if it dominates or is too shallow.

## Step 6. Choose the workflow

| Research mode | Required chain | Default output mix |
|---|---|---|
| Market Intelligence | Brief -> Inventory -> Source Mix Check -> Normalized Cards -> Evidence Ledger -> Market Intelligence Dossier -> Review -> CEO Market Intelligence Report -> HTML Export -> Optional Factual Packet -> Registry Update | dossier + CEO report + HTML export |
| Objective Market Scan | Brief -> Inventory -> Source Mix Check -> Normalized Cards -> Evidence Ledger -> Neutral Market Dossier -> Review -> CEO Market Scan Report -> HTML Export -> Optional Factual Packet -> Registry Update | dossier + CEO report + HTML export |
| Interview-Led Market Overview | Brief -> Corpus Plan -> Interview/Conversation Inventory -> Normalized Cards -> Evidence Ledger -> Voice Map -> Interview-Led Dossier -> Review -> CEO Interview-Led Report -> HTML Export -> Optional Factual Packet -> Registry Update | dossier + CEO report + HTML export |
| Competitive Messaging Intelligence | Brief -> Messaging Corpus Plan -> Ad/Landing/Social Source Inventory -> Normalized Cards -> Evidence Ledger -> Competitive Message Map -> Messaging Dossier -> Review -> CEO Messaging Intelligence Report -> HTML Export -> Optional Factual Packet -> Registry Update | message map + dossier + CEO report + HTML export |
| Account / ICP Intelligence | Brief -> ICP Hypothesis -> Public Account Source Inventory -> Normalized Cards -> Evidence Ledger -> Account/ICP Map -> ICP Intelligence Dossier -> Review -> CEO ICP Intelligence Report -> HTML Export -> Optional Factual Packet -> Registry Update | ICP/account map + dossier + CEO report + HTML export |
| Corpus-Shift Rerun | Brief -> Prior Canon Review -> Corpus Shift Plan -> New Corpus Collection -> Normalized Cards -> Evidence Ledger -> New Dossier -> Review -> CEO Report -> HTML Export -> Optional Factual Packet -> Registry Update | dossier + CEO report + HTML export |
| Verification | Brief -> Focused Inventory -> Normalized Cards -> Evidence Ledger -> Verification Memo -> Optional Review -> Optional CEO Note -> Optional HTML Export -> Registry Update when canon-worthy | verification memo + optional HTML export |

## Step 7. Hard scope gating

- No unsupported mode may be inferred.
- No recommendation output may be produced.
- No strategy wording may be introduced into factual modes.
- No dossier without normalized cards and evidence ledger.
- No interview-led dossier without corpus plan and voice map.
- No competitive messaging dossier without a message map that separates observed claims from inferred patterns.
- No account/ICP dossier without explicit qualification criteria, public evidence signals, and disqualification signals.
- No corpus-shift rerun without explicit prior-canon review and corpus-shift plan.
- No CEO report without approved factual dossier.
- No completed research run without HTML export of the final CEO-readable report.
- No packet that adds advice, prioritization, or product direction.
- No market-level dossier if authoritative external overview coverage was required but is missing.
- No interview-led report if interview/operator voices are only marginal additions to a legacy document backbone.

## Step 8. Stop conditions

Router must stop the run if:

- the user is primarily asking for recommendation or choice, not information;
- the scope is too vague to define factual coverage;
- required company, pricing, or numbers coverage is not specified for a consequential intelligence run;
- freshness requirement is undefined for time-sensitive verification;
- expected evidence-status labeling is unspecified for a numbers-heavy run;
- a market-level run relies only on official docs, help centers, pricing pages, and company blogs;
- an interview-led request does not materialize enough interviews / operator sources to credibly drive the report;
- a corpus-shift request is still being executed as a light update.

## Step 9. Handoff discipline

- No Market Intelligence dossier without company and business-model mapping where relevant.
- No Objective Market Scan without category and player map.
- No Interview-Led dossier without repeated-theme analysis, disagreement analysis, and voice map.
- No Competitive Messaging Intelligence report without channel coverage notes, observed message patterns, proof/claim table, CTA/offer table, and source limitations.
- No Account / ICP Intelligence report without ICP boundaries, account/persona evidence table, confidence labels, and clear limits on missing private or proprietary data.
- No Corpus-Shift rerun without explicit statement of legacy canon role.
- No market-overview synthesis without authoritative external overview sources when the brief requires category-level understanding.
- No Verification memo without explicit verdict and evidence-status labeling.
- No final report without visible factual traceability.
- No registry update for a completed human-readable run until the current HTML export path is materialized.
- No registry update without corpus-aware canonical artifact assignment.
