---
name: productivity-review
description: Analyze Anton's productivity from Google Calendar, past Focus calendar events, Daily Focus planning logs, and Second Brain context. Use when Anton asks for weekly, monthly, yearly, end-of-week, end-of-month, productivity, calendar hours, work/study/fitness time, progress review, or accumulated productivity statistics under the Productivity Review branch.
---

# Productivity Review

## Purpose

Build Anton's calendar-based productivity memory: weekly reviews, monthly rollups, yearly rollups, and accumulated stats.

This skill estimates how Anton spent time from calendar evidence. It must not pretend calendar time is perfect ground truth. Mark all calendar-derived totals as `calendar-based estimate`, and distinguish past/completed Focus blocks from future scheduled blocks.

## Core Paths

- Vault: `/Users/anton/AI AGENT FOLDER/Second Brain/`
- Output branch: `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Productivity Review/`
- Reports:
  - `reviews/weekly/`
  - `reviews/monthly/`
  - `reviews/yearly/`
- Stats ledger: `stats/productivity-stats.jsonl`
- Script: `/Users/anton/.codex/skills/productivity-review/scripts/productivity_review.py`
- Calendar token: `/Users/anton/.config/second-brain/google-calendar-token.json`
- Calendar credentials: `/Users/anton/.config/second-brain/google-calendar-credentials.json`
- Legacy manual completed log: `/Users/anton/.config/second-brain/telegram-codex-bot.completed-tasks.json`

## Workflow

1. Run the script for the requested period.
   - Weekly default:
     `python3 /Users/anton/.codex/skills/productivity-review/scripts/productivity_review.py --period week`
   - Monthly:
     `python3 /Users/anton/.codex/skills/productivity-review/scripts/productivity_review.py --period month`
   - Yearly:
     `python3 /Users/anton/.codex/skills/productivity-review/scripts/productivity_review.py --period year`
   - Use `--date YYYY-MM-DD` to anchor a specific week/month/year.
   - Use `--dry-run` for validation without saving.

2. Read the saved Markdown report.

3. Reply to Anton with a concise human summary:
   - total calendar-estimated hours
   - work / meetings / deep work / learning / fitness / admin split
   - completed tasks inferred from past `Focus:` calendar slots
   - what changed versus prior stored periods if available
   - observations about energy/focus, clearly labeled as observations
   - 3-5 questions or adjustments for next week, not commands

4. Do not create calendar events, edit Daily Focus tasks, or update canonical context from this skill.

## Interpretation Rules

- `Focus:` calendar events are planned work blocks created by Daily Focus.
- A `Focus: [task]` event whose end time is in the past counts as a completed work block by default.
- A task is fully completed only if it has past `Focus:` work and no known future `Focus:` slots with the same task name/key.
- A `Focus: [task]` event in the future counts as scheduled/open work, not completed.
- Non-Focus events in `tony@portalcg.us` and `primary` are treated as calendar commitments or meetings.
- The legacy `/done` completed log is only a manual override, not the default completion source.
- Productivity score is a heuristic, not a judgment of personal worth.
- Separate:
  - calendar-based estimate
  - past/completed Focus blocks
  - fully completed tasks
  - in-progress tasks with both past and future Focus blocks
  - future scheduled Focus blocks
  - observation
  - unknown

## Output Style

Write like a weekly board debrief, not a technical audit.

Recommended shape:

```markdown
Короткий вывод
Цифры недели
Как распределилось время
Что продвинулось
Где план/факт разошлись
Наблюдения
Что стоит уточнить перед следующей неделей
Сохранено
```

Keep the Telegram/chat answer readable. Put detailed tables in the saved Markdown report.
