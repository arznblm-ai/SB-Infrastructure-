---
name: pull-krisp
description: Pull recent Krisp meeting transcripts into Anton's Second Brain on command, then run transcript summarization so raw Krisp notes land in transcripts/ and structured summaries land in meetings/ or education/. Use when Anton says "pull krisp", "забери транскрипты из Krisp", "подтяни встречи", "импортируй Krisp", or asks to manually fetch recent meeting transcripts.
model: haiku
---

# Pull Krisp

On command, this skill pulls ready meeting transcripts from Krisp (MCP + `krisp_import_all.py`) into `transcripts/`, then runs the summarizer so structured summaries land in `meetings/` or `education/`.

This skill is the manual command layer for the existing Krisp -> Second Brain pipeline.

It does not replace the LaunchAgent automation. It runs an explicit pull/backfill when Anton asks.

## Canonical Paths

- Vault: `/Users/anton/AI AGENT FOLDER/Second Brain`
- Raw transcripts: `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts`
- Meeting summaries: `/Users/anton/AI AGENT FOLDER/Second Brain/meetings`
- Education summaries: `/Users/anton/AI AGENT FOLDER/Second Brain/education`
- Krisp import script: `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Krisp to obsidian/Scripts/krisp_import_all.py`
- Summarizer script: `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Transcript summarizer/Scripts/transcript_summarizer.py`
- OAuth repair script: `/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Krisp to obsidian/Scripts/krisp_oauth.py`
- Krisp token: `~/.claude/krisp_token.json`

## Default Workflow

When invoked, run:

```bash
/Users/anton/AI\ AGENT\ FOLDER/Second\ Brain/system/skills/pull-krisp/scripts/pull_krisp.sh --lookback-days 7
```

If Anton asks for a specific range, adjust `--lookback-days`.

Examples:

```bash
/Users/anton/AI\ AGENT\ FOLDER/Second\ Brain/system/skills/pull-krisp/scripts/pull_krisp.sh --lookback-days 3
/Users/anton/AI\ AGENT\ FOLDER/Second\ Brain/system/skills/pull-krisp/scripts/pull_krisp.sh --lookback-days 30
/Users/anton/AI\ AGENT\ FOLDER/Second\ Brain/system/skills/pull-krisp/scripts/pull_krisp.sh --lookback-days 7 --skip-summary
```

## What The Script Does

1. Calls `krisp_import_all.py --lookback-days N`.
2. Pulls ready Krisp MCP documents.
3. Creates or updates raw markdown notes in `transcripts/`.
4. Deduplicates by `krisp_mcp_id`.
5. Skips meetings where Krisp transcript is not ready.
6. Runs `transcript_summarizer.py` unless `--skip-summary` is set.
7. Summarizer creates structured notes in `meetings/` or `education/`.

## Output Contract

After running, report:

- whether Krisp pull succeeded
- lookback window used
- how many raw notes were created/updated/skipped, if visible in output
- how many summaries were created, if visible in output
- exact error if OAuth/MCP failed
- next action only if needed

Keep the response short. Do not paste full logs unless Anton asks.

## Verification

Before reporting success:

1. Check that the new/updated transcript files actually exist in `transcripts/` (e.g. list the most recent files for the lookback window).
2. Open at least one new transcript and confirm it is not empty and not cut off after the first seconds; a real meeting transcript has a plausible word count.
3. Confirm raw notes landed in `transcripts/` — not in `meetings/`, `education/`, or `transcripts/external resources/`.
4. If summarization ran, confirm the corresponding summary notes appeared in `meetings/` or `education/`.
5. If the script failed, show the exact stderr/error output to Anton. Never report success the script did not actually produce.

## Failure Handling

If output says token is missing/expired or MCP auth fails, run:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Krisp to obsidian/Scripts/krisp_oauth.py"
```

If Krisp says transcript is not ready, do not fabricate a summary. Tell Anton to retry later or use a larger lookback.

If summarizer skips files as pending/empty, this is normal; report it as skipped, not failed.

## Safety Rules

- Do not edit raw transcripts manually.
- Do not rename existing transcript files during pull.
- Do not move summaries by hand unless Anton explicitly asks.
- Do not touch `sessions/`; Claude/Codex sessions are not Krisp meetings.
- Use existing scripts instead of rewriting the Krisp pipeline.
