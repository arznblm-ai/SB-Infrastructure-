---
name: video-transcribe
description: Transcribe local video files into Markdown notes with faster-whisper. Save course/meeting raw transcripts into `transcripts/` for transcript-summarizer, but save short external videos, reels, X/Twitter videos, TikToks, YouTube Shorts, and similar internet clips into `transcripts/external resources/` unless the user asks otherwise.
---

# Video Transcribe

Use the bundled script for every transcription instead of re-writing Whisper code inline.

## Workflow

1. Resolve the source video path.
2. Decide the destination Markdown file.
3. Run `scripts/transcribe_video.py`.
4. For course/meeting transcripts in `transcripts/`, let the script call [$transcript-summarizer](/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md).
5. For short external social/video clips in `transcripts/external resources/`, use `--skip-summary` and write the user-facing summary in the chat unless the user asks for a separate saved summary.
6. Verify the transcript file exists and show the saved path.

## Destination Rules

Use `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources` for short external videos and clips from X/Twitter, Instagram, TikTok, YouTube Shorts, reels, demos, saved links, and similar one-off internet resources.

Prefer `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts` only for course materials, lectures, meetings, workshops, Krisp/Zoom recordings, and other source material that should be routed into `education/` or `meetings/`.

When there is already a related note in `transcripts`, reuse its human title and date in the new transcript filename.
After the transcript lands in `transcripts`, summary routing is handled by `transcript-summarizer`.

Examples:

- Summary exists as `{course} {summary} AI Mindset prework 1 что такое POS – 2026-03-23.md`
- Transcript should be `{course} {transcript} AI Mindset prework 1 что такое POS – 2026-03-23.md`

When there is no related note, default to:

- Prefix: `{course} {transcript}`
- Title: cleaned video filename
- Date: video modified date

For `transcripts/external resources`, use cleaner filenames without `{course}` / `{transcript}` prefixes unless an existing pipeline already adds a useful prefix:

- `{platform} {title-or-status-id} – YYYY-MM-DD.md`

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

For short external social/video clips, save into the external resources transcript folder:

```bash
python3 "/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/video-transcribe/scripts/transcribe_video.py" \
  "/absolute/path/to/video.mp4" \
  --output-path "/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources/X video 2065622685108064631 – 2026-06-13.md" \
  --skip-summary
```

## Quality Defaults

Use the default `tiny` model for fast, zero-API local transcripts.

Use `--model small` when the user explicitly wants better quality and is okay with a slower run.

Keep `--beam-size 1` unless there is a quality reason to increase it.

## Validation

After transcription:

1. Check that the transcript Markdown file was created.
2. For `transcripts/`, check that a summary file was created in `education/` or `meetings/`.
3. For `transcripts/external resources/`, do not require an `education/` or `meetings/` summary.
4. Preview the first lines of the transcript.
5. Report the saved path back to the user.

## Resource

`scripts/transcribe_video.py`

Run the local faster-whisper transcription workflow, emit progress logs, and save a Markdown transcript with timestamps.
