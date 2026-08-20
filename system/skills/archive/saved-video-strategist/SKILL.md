---
name: saved-video-strategist
description: Analyze Anton's saved Telegram/Link Inbox videos and external-resource summaries through Strategic Board lenses, while always preserving the core summary of each saved item. Use when Anton asks for insights from saved videos/links, asks what is useful from today's saved videos, wants Strategic Board analysis of saved reels/posts, or wants to turn saved content into practical ideas for his projects. If no strong Anton-specific insight is visible, provide a clear summary anyway instead of discarding the item as noise.
model: sonnet
---

# Saved Video Strategist

### [[2026-07-05]]

This skill reads Anton's saved-link summaries from the Link Inbox layer (`transcripts/external resources/index.md` + `resources/link-inbox/summaries/`, full transcripts only as fallback) and returns a compact readout in chat: the preserved core summary of each saved item plus a Strategic Board layer where a clear connection to Anton's projects exists.

## Purpose

Turn saved internet videos and links into a compact readout for Anton. The skill has two jobs: preserve the useful meaning of each saved item, then add a Strategic Board layer when there is a clear connection to Anton's projects.

Assume Anton saved the item for a reason. If the strategic relevance is not obvious, do not suppress the item. Provide a clean summary and label the personal-usefulness layer as uncertain.

## Source Map

Canonical Link Inbox paths:

- Index: `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/index.md`
- Summary notes: `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/summaries/`
- Source cards: `/Users/anton/AI AGENT FOLDER/Second Brain/resources/link-inbox/links/`
- Full transcripts: `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/`
- Processing state: `/Users/anton/.config/link-inbox/state.json`

## Mandatory Retrieval Order

Always use this order unless Anton explicitly asks for full transcripts:

1. Read the external resources index.
2. Read the relevant summary notes.
3. Open full transcripts only when a summary is ambiguous, low-confidence, or a specific detail matters.
4. If transcript quality is poor, label the insight as low confidence instead of pretending certainty.

This skill exists to reduce token burn. Do not scan the entire transcript folder by default.

## Quick Context Collector

Use the collector before analysis:

```bash
python3 "/Users/anton/.codex/skills/saved-video-strategist/scripts/collect_saved_video_summaries.py" --date YYYY-MM-DD
```

Useful variants:

```bash
python3 "/Users/anton/.codex/skills/saved-video-strategist/scripts/collect_saved_video_summaries.py" --days 7
python3 "/Users/anton/.codex/skills/saved-video-strategist/scripts/collect_saved_video_summaries.py" --date YYYY-MM-DD --max-chars 6000
```

The collector prints a compact packet with source URLs, saved paths, summary text, transcript path, and weak-quality markers.

## Analysis Workflow

1. Gather candidate saved videos with the collector.
2. Read only the summaries that match the requested date/topic.
3. First preserve the source meaning:
   - What is the video/post actually about?
   - What tools, systems, resources, people, companies, or links are mentioned?
   - What workflow or mental model does the author show?
   - What result or benefit does the author claim?
4. Then apply a Strategic Board filter:
   - What is useful for Anton's current projects?
   - What could affect Content Factory, UGC factory, agent departments, Daily Focus, Link Inbox, AI Tutor, vibe coding, or strategic decision workflows?
   - What is actionable in the next 7-14 days?
   - What is only inspiration, hype, or generic noise?
   - What has weak evidence or bad transcription quality?
5. If no strong Anton-specific conclusion is visible, fall back to useful summary mode:
   - Do not force a strategic conclusion.
   - Say: `Для тебя связь пока неочевидна, но само видео полезно как reference по ...`
   - Still include the short summary, tools/resources, system/workflow, and source path.
6. Distinguish three layers:
   - `Source fact`: what the saved material explicitly says.
   - `Inference`: what we can reasonably infer for Anton.
   - `Experiment`: a concrete next action Anton could try.
7. If the output affects a strategic business decision, also use `$strategic-board`.

## Output Format

Keep the answer short, practical, and selective.

Recommended structure:

```markdown
**Главный сигнал**
1-2 sentences.

**Саммари видео**
- What the video is about.
- Tools/resources/links mentioned.
- Author's workflow/system.
- Claimed result or benefit.

**Полезно для тебя**
- Insight with source name and why it matters.

**Что можно применить**
- Small concrete experiment or system change.

**Шум / низкая уверенность**
- What to ignore or re-check.

**Источники**
- Title -> summary path.
```

Do not produce a full report unless Anton asks. Do not output only strategic advice. Every saved item should get at least a compact summary unless it is a clear duplicate or unreadable.

## Quality Rules

- Prefer summaries over transcripts, but verify from transcript when exact tools, numbers, or claims matter.
- Never over-trust auto-transcribed tool names; mark suspicious terms.
- Do not turn every saved video into advice. Some links are reference material, and that reference value should still be preserved.
- Do not discard a saved item just because Anton-specific relevance is unclear.
- Be explicit when an insight is Anton-specific rather than stated by the author.
- Avoid motivational language, generic productivity advice, and broad market claims not supported by the saved source.
- If a saved note is duplicated, stale, or missing a summary, mention the data-quality issue briefly and continue with the best available context.
