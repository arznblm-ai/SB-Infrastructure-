---
name: strategic-board
description: "Strategic Board router for Anton's CEO decisions: use when the user asks for strategic decision support, business/advisor/board review, prioritization, what to choose, whether to do something, trade-offs, focus, opportunity cost, pre-mortem, CEO decision logs, or source-backed strategy based on Second Brain context. Trigger on Russian and English phrasing such as 'стратегическое решение', 'борд', 'business advisor', 'что выбрать', 'стоит ли', 'приоритизация', 'trade-off', 'decision brief', 'CEO decision'."
---

# Strategic Board Router

This skill is the canonical entrypoint for strategic decision support in Anton's Second Brain.

It does not duplicate the board operating system. The source of truth lives here:

`/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Strategic Board/CLAUDE.md`

## When To Use

Use this skill when Anton asks for:

- strategic decision support
- business advisor / board review
- prioritization between projects or options
- whether to start, pause, kill, or defer an initiative
- trade-off analysis
- opportunity cost analysis
- focus decisions
- pre-mortem or downside review
- CEO decision brief
- decision log creation after Anton has chosen

Do not use this skill for factual market research by itself. If the request is primarily factual research, use Research Department first, then use Strategic Board only after the evidence is available.

## Required Startup

1. Read `/Users/anton/AI AGENT FOLDER/Second Brain/claude.md` for vault routing.
2. Read `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Strategic Board/CLAUDE.md`.
3. Follow the Strategic Board protocol from that file.

## Operating Rules

- Do not confirm Anton's hypothesis by default.
- Separate `facts`, `interpretations`, `hypotheses`, `preferences`, and `unknowns`.
- Every important conclusion must point to source context from the vault or be marked as `unknown` / `inference`.
- If the decision question is unclear, propose 1-3 possible formulations and ask Anton to pick or edit.
- If context confidence is below 90%, ask concise clarifying questions before giving a recommendation.
- If a question requires fresh market data, state what can be answered from the vault and what should be sent to Research Department.
- Do not create a decision log until Anton explicitly makes a decision or asks to record it.

## Default Output

Use the Strategic Board format unless the question is small:

1. Decision question
2. Context used
3. What we know
4. What we do not know
5. Options
6. Board review
7. Risks / pre-mortem
8. Recommended next move
9. What Anton needs to decide
10. Suggested decision log entry
