---
name: video-transcribe
description: Transcribe local video files into Markdown notes with faster-whisper, save raw transcripts into `transcripts/`, then run `transcript-summarizer` to place structured summaries into `education/` or `meetings/`.
---

# Video Transcribe

Use the bundled script for every transcription instead of re-writing Whisper code inline.

## Workflow

1. Resolve the source video path.
2. Decide the destination Markdown file.
3. Run `scripts/transcribe_video.py`.
4. Let the script call [$transcript-summarizer](/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md) on the new transcript.
5. Verify the transcript and summary files exist and show both saved paths.

## Destination Rules

Prefer `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts` unless the user asks for another folder for the raw transcript.

When there is already a related note in `transcripts`, reuse its human title and date in the new transcript filename.
After the transcript lands in `transcripts`, summary routing is handled by `transcript-summarizer`.

Examples:

- Summary exists as `{course} {summary} AI Mindset prework 1 что такое POS – 2026-03-23.md`
- Transcript should be `{course} {transcript} AI Mindset prework 1 что такое POS – 2026-03-23.md`

When there is no related note, default to:

- Prefix: `{course} {transcript}`
- Title: cleaned video filename
- Date: video modified date

## Commands

Use the script directly:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/video-transcribe/scripts/transcribe_video.py" \
  "/absolute/path/to/video.mp4" \
  --title "AI Mindset prework 1 что такое POS" \
  --date 2026-03-23
```

Use `--output-path` when the exact filename is already known:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/video-transcribe/scripts/transcribe_video.py" \
  "/absolute/path/to/video.mp4" \
  --output-path "/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/{course} {transcript} AI Mindset prework 1 что такое POS – 2026-03-23.md"
```

## Quality Defaults

Use the default `tiny` model for fast, zero-API local transcripts.

Use `--model small` when the user explicitly wants better quality and is okay with a slower run.

Keep `--beam-size 1` unless there is a quality reason to increase it.

## Validation

After transcription:

1. Check that the transcript Markdown file was created.
2. Check that a summary file was created in `education/` or `meetings/`.
3. Preview the first lines of both files.
4. Report both saved paths back to the user.

## Resource

`scripts/transcribe_video.py`

Run the local faster-whisper transcription workflow, emit progress logs, and save a Markdown transcript with timestamps.
