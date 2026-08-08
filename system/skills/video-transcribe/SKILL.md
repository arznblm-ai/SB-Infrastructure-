---
name: video-transcribe
description: Transcribe local video files into Markdown notes with parakeet (GPU, default) or faster-whisper. Save course/meeting raw transcripts into `transcripts/` for transcript-summarizer, but save short external videos, reels, X/Twitter videos, TikToks, YouTube Shorts, and similar internet clips into `transcripts/external resources/` unless the user asks otherwise.
model: haiku
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

Default is `--model parakeet` (`mlx-community/parakeet-tdt-0.6b-v3`, Apple Silicon GPU, multilingual ru/en). It is both faster and cleaner than whisper: on a real 65-minute Russian Zoom recording parakeet took 377 s (~10× real time) versus ~12 minutes for whisper `small`, with equal words and better punctuation.

Use `--model small` (or `tiny`/`medium`/`large-v3`) only when whisper is explicitly wanted — every whisper value keeps working exactly as before. Whisper `small` is also the automatic fallback if parakeet fails for any reason (the script prints `[warn] parakeet failed …` and finishes the file on whisper).

Keep `--beam-size 1` unless there is a quality reason to increase it. `--beam-size` and `--language` apply to whisper only: parakeet v3 detects language itself and ignores both. `--clip-start` / `--clip-end` work with both engines.

Long files are handled by slicing the decoded audio into 120-second chunks with 12-second overlap and merging on sentence boundaries — parakeet's own `transcribe(chunk_duration=…)` is unusable here because it shells out to ffmpeg, which is not installed.

## Validation

After transcription:

1. Check that the transcript Markdown file was created.
2. For `transcripts/`, check that a summary file was created in `education/` or `meetings/`.
3. For `transcripts/external resources/`, do not require an `education/` or `meetings/` summary.
4. Preview the first lines of the transcript.
5. Report the saved path back to the user.
6. Check that the transcript is not empty and its word count is plausible for the video length — a file cut off after the first seconds is a failed run, not a success.
7. Confirm the file landed in the folder required by the Destination Rules (`transcripts/` vs `transcripts/external resources/`).
8. If the script exits with an error, show its stderr to the user. Do not report success and do not fabricate a transcript or summary.

## Resource

`scripts/transcribe_video.py`

Run the local transcription workflow (parakeet by default, whisper fallback), emit progress logs, and save a Markdown transcript with timestamps.
