---
name: meeting-insights-analyzer
description: Analyze Anton's recent meeting summaries and transcripts to produce Russian communication insights, meeting-memory reminders, follow-up risks, negotiation patterns, conflict/avoidance signals, and practical coaching recommendations. Use when Anton asks how recent meetings went, what to remember from calls, where he was persuasive, where he avoided conflict, how he handled negotiations, or wants a communication/leadership review from meetings.
metadata:
  short-description: Meeting communication insights for Anton
model: sonnet
---

# Meeting Insights Analyzer

### [[2026-06-07]]

Personal meeting-insights workflow for Anton. This is not a generic meeting summarizer. Use it to analyze patterns across meetings: communication, negotiation, leadership, follow-ups, risks, and what Anton should remember.

## Default Sources

Use local Second Brain sources:

1. Meeting summaries:
   `/Users/anton/AI AGENT FOLDER/Second Brain/meetings/`
2. Raw transcripts:
   `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/`
3. Meeting index when useful:
   `/Users/anton/AI AGENT FOLDER/Second Brain/meetings/index.md`

Prefer the latest 7-14 days by default. If the user asks for a named person, project, or client, search by filename and content with `rg`.

## Modes

### Quick Recent Review

Use when Anton asks:

- "как прошли последние встречи"
- "что мне полезно помнить"
- "проанализируй встречи за неделю"

Workflow:

1. Find recent meeting summaries by date and modified time.
2. Read the newest relevant 3-8 summaries.
3. Pull raw transcript excerpts only for important claims, tension, negotiation, or behavioral examples.
4. Output a concise Russian report:
   - short verdict
   - what happened
   - what Anton should remember
   - risks / stuck points
   - follow-up ledger
   - communication pattern

### Behavioral Deep Dive

Use when Anton asks about persuasion, conflict avoidance, interruptions, directness, speaking style, or leadership.

Workflow:

1. Identify Anton's speaker label in each transcript (`Anton`, `Anton Rozenblum`, or corrected labels in summary notes).
2. Read raw transcripts, not only summaries.
3. Look for:
   - direct boundary-setting
   - hedging or softening
   - conflict avoidance
   - strong commercial framing
   - unclear next steps
   - places where Anton over-explains instead of closing
   - places where others leave with ownership but no deadline
4. Include tight examples with meeting/date/timestamp when available.

### Trend Review

Use when Anton asks to compare periods or track improvement.

Workflow:

1. Compare 2-4 weeks of meeting summaries/transcripts.
2. Track repeated patterns:
   - recurring business themes
   - repeated unresolved counterparties
   - repeated project risks
   - improvement in boundaries, pricing, or follow-up clarity
3. Produce a trend report with practical exercises.

## Output Shape

Write in Russian by default.

Use this structure for quick reviews:

```markdown
Короткий вывод:
[3-5 sentences.]

Что произошло:
- [meeting/date: what happened]

Что тебе полезно помнить:
1. [memory]
2. [memory]
3. [memory]

Коммуникационный паттерн:
[what Anton did well / where he should be careful]

Follow-up ledger:
- [owner] — [next step] — [deadline if known or "deadline missing"]

Риски:
- [risk]

Практика на следующую неделю:
1. [small behavior to practice]
2. [small behavior to practice]
```

For deep dives, add:

```markdown
Примеры:
- [Meeting/date/time] quote or paraphrased moment
  Что произошло:
  Почему важно:
  Как сказать сильнее:
```

## Anton-Specific Heuristics

Pay special attention to:

- Cash-pressure projects: projects considered mainly to unlock cash or recover debt.
- Boundary setting: prepayment, paid revisions, stage fixation, clear ownership.
- Productization: whether a meeting turns into an offer, artifact, spec, package, or decision.
- Follow-up hygiene: whether someone promised a brief, payment update, client answer, or materials without a deadline.
- Marketing maturity: whether Anton connects creative/production work to business metrics, media strategy, ICP, pricing, and client value.
- Risk of "helping too much": Anton may solve for the counterparty before protecting scope, payment, and margin.

## Quality Rules

- Separate facts from interpretation.
- If using only summaries, label the analysis as a lightweight review.
- Use raw transcript evidence for behavioral claims.
- Do not invent action items or deadlines.
- Do not over-pathologize normal conversational softness.
- Keep recommendations practical and immediate.
- Link or name the meeting files used when finishing.

