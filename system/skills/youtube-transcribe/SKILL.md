---
name: youtube-transcribe
description: Transcribe public YouTube videos from a URL into Markdown notes, then run `transcript-summarizer` and save the resulting summary into Second Brain. Use when the user pastes a YouTube link and wants the transcript stored in `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts` and the summary routed into `education/` or `meetings/`.
---

# YouTube Transcribe

Use the bundled script for every YouTube transcription instead of manually downloading media or re-writing Whisper code inline.

## What this skill is for

Use this skill when the user:

- pastes a YouTube URL
- wants the full transcript saved into the Vault
- wants a lecture, interview, podcast, or workshop turned into a Markdown note

This skill is for **public YouTube links**. If the video is private, login-gated, or blocked, explain the issue and stop.

## Workflow

1. Resolve the destination folder inside the Vault.
2. Run `scripts/transcribe_youtube.py` with the YouTube URL.
3. Let the script call [$transcript-summarizer](/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md) on the new transcript.
4. Verify that the transcript note was created in `transcripts/` and the summary was created in `education/` or `meetings/`.
5. Preview the first lines and report both saved paths back to the user.

## Destination Rules

Prefer `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts` unless the user explicitly asks for another folder in the Vault.

If the user gives a more specific destination, pass it with `--output-dir`.

Examples:

- `transcripts/` for the default YouTube inbox
- `context/Psychology/Naval Ravikant/` for a specific research thread
- `tasks/...` when the transcript belongs to an active task

## Command

Use the script directly:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/youtube-transcribe/scripts/transcribe_youtube.py" \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Use `--output-dir` when the transcript destination is already known:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/youtube-transcribe/scripts/transcribe_youtube.py" \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  --output-dir "/Users/anton/AI AGENT FOLDER/Second Brain/context/Psychology/Naval Ravikant"
```

Use `--output-path` when the exact transcript filename should be fixed:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/youtube-transcribe/scripts/transcribe_youtube.py" \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  --output-path "/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/{course} {transcript} Naval interview – 2025-03-31.md"
```

## Quality Defaults

Use the default `tiny` model for fast local transcripts.

Use `--model small` when the user explicitly wants better quality and accepts a slower run.

Keep `--beam-size 1` unless there is a quality reason to change it.

## Summary Rules

After the transcript is created, always hand it off to [$transcript-summarizer](/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md).

For YouTube links, call `transcript-summarizer` with an `education` preference, because these imports are part of the learning pipeline by default.

That skill then routes the summary into:

- `education/` for lectures, workshops, courses, and conference-style learning content
- `meetings/` for calls, 1-on-1s, and working discussions

Do not create an ad-hoc sibling summary inside `transcripts/`. `transcripts/` is the raw inbox; `education/` and `meetings/` are the processed knowledge layers.

## Validation

After transcription and summary creation:

1. Check that the transcript exists in `transcripts/`.
2. Check that the summary exists in `education/` or `meetings/`.
3. Preview the first lines of the transcript.
4. Preview the first lines of the summary.
5. Tell the user the exact saved paths.

## Resource

`scripts/transcribe_youtube.py`

Download the best available audio from a public YouTube URL, run local faster-whisper transcription, and save the result into the Vault as Markdown.
