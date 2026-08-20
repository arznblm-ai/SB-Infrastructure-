---
name: youtube-transcribe
description: Transcribe public YouTube videos from a URL into Markdown notes, then optionally run `transcript-summarizer` and save the resulting summary into Second Brain. Use when the user pastes a YouTube link and wants the external-resource transcript stored in `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources` unless they explicitly route it to a course, meeting, or project folder.
model: haiku
---

# YouTube Transcribe

### [[2026-08-03]]

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
3. For external saved links, use the default external resources destination and usually `--skip-summary` unless the user asks for a full education/meeting summary.
4. If the YouTube video is explicitly a course, lecture, workshop, meeting, or user asks for a processed summary, let the script call [$transcript-summarizer](/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md) on the new transcript.
5. Verify that the transcript note was created in the chosen destination and, when summary routing was requested, the summary was created in `education/` or `meetings/`.
6. Preview the first lines and report both saved paths back to the user.

## Destination Rules

Prefer `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts/external resources` for ordinary public YouTube links, saved links, Shorts, podcasts, interviews, lectures from the internet, and other external resources.

Use `/Users/anton/AI AGENT FOLDER/Second Brain/transcripts` only when the material belongs to the main course/meeting ingestion pipeline and should be processed by `transcript-summarizer`.

If the user gives a more specific destination, pass it with `--output-dir`.

Examples:

- `transcripts/external resources/` for the default YouTube saved-link inbox
- `transcripts/` for course/meeting ingestion that should route into `education/` or `meetings/`
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

Default is `--model parakeet` (`mlx-community/parakeet-tdt-0.6b-v3`, Apple Silicon GPU, multilingual ru/en). It is both faster and cleaner than whisper: on a real 65-minute Russian recording parakeet took 377 s (~10× real time) versus ~12 minutes for whisper `small`, with equal words and better punctuation.

Use `--model small` (or `tiny`/`medium`/`large-v3`) only when whisper is explicitly wanted — every whisper value keeps working exactly as before. Whisper `small` is also the automatic fallback if parakeet fails (the script prints `[warn] parakeet failed …` and finishes on whisper).

Keep `--beam-size 1` unless there is a quality reason to change it. `--beam-size` and `--language` apply to whisper only: parakeet v3 detects language itself and ignores both.

Long videos are sliced into 120-second chunks with 12-second overlap and merged on sentence boundaries — parakeet's built-in chunking needs ffmpeg, which is not installed, so audio is decoded via `faster_whisper.audio.decode_audio` and passed to the model as an array.

## Summary Rules

After the transcript is created, hand it off to [$transcript-summarizer](/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/transcript-summarizer/SKILL.md) only when the user wants the material processed into `education/` or `meetings/`.

For external saved links, `--skip-summary` is acceptable and often preferred, because the transcript belongs in `transcripts/external resources/` and may be reviewed later by Link Inbox.

For YouTube course/lecture/workshop imports, call `transcript-summarizer` with an `education` preference.

That skill then routes the summary into:

- `education/` for lectures, workshops, courses, and conference-style learning content
- `meetings/` for calls, 1-on-1s, and working discussions

Do not create an ad-hoc sibling summary inside `transcripts/`. `transcripts/` is the raw inbox; `transcripts/external resources/` is the external-material inbox; `education/` and `meetings/` are the processed knowledge layers.

## Validation

After transcription and summary creation:

1. Check that the transcript exists in the selected destination, usually `transcripts/external resources/`.
2. If summary routing was requested, check that the summary exists in `education/` or `meetings/`.
3. Preview the first lines of the transcript.
4. Preview the first lines of the summary.
5. Tell the user the exact saved paths.
6. Check that the transcript is not empty and its word count is plausible for the video length — a note cut off after the first seconds means the run failed.
7. If the script exits with an error, show its stderr to the user. Do not report success and do not fabricate a transcript or summary.

## Resource

`scripts/transcribe_youtube.py`

Download the best available audio from a public YouTube URL, run local transcription (parakeet by default, whisper fallback), and save the result into the Vault as Markdown.
