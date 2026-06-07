#!/usr/bin/env python3
"""
Transcribe AI Mindset Sprint videos and save to education folder.

Run from Terminal:
    python3 ~/path/to/transcribe_videos.py

Requirements (install one of):
    pip install openai-whisper       # standard whisper
    pip install faster-whisper       # faster alternative
"""

import os
import sys
import subprocess
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
# Edit these if your folder paths are different
SCRIPT_DIR = Path(__file__).parent
VIDEO_DIR  = SCRIPT_DIR.parent / "Ai Mindset Sprint Videos"
OUTPUT_DIR = SCRIPT_DIR   # education folder

# ── Video → (description, date) ──────────────────────────────────────────────
VIDEO_MAP = {
    "AI Mindset AI-Native prework #1.mp4":          ("prework 1 AI Native",                       "2026-03-26"),
    "AI Mindset AI-Native prework 2.mp4":           ("prework 2 AI Native",                       "2026-03-26"),
    "AI Mindset AI-Native {prework} #3.mp4":        ("prework 3 AI Native",                       "2026-03-26"),
    "AI Mindset AI-Native {prework} #4.mp4":        ("prework 4 AI Native",                       "2026-03-26"),
    "AI Mindset S2 W1 Personal  Team Workshop.mp4": ("S2 W1 Personal Team Workshop",              "2026-03-26"),
    "AI Mindset S2 W1 Workshop.mp4":                ("S2 W1 Workshop",                            "2026-03-29"),
    "AI Tools Session.mp4":                         ("AI Tools Session",                          "2026-03-31"),
    "AI Transformation for Organizations.mp4":      ("AI Transformation for Organizations",       "2026-03-31"),
    "How to Build a Business Factory - Evgeny Lisovsky.mp4": ("How to Build a Business Factory", "2026-03-31"),
    "Personal OS Setup.mp4":                        ("Personal OS Setup",                         "2026-03-26"),
    "W1 Почему ИИ-трансформация самое важное.mp4":  ("W1 Почему ИИ-трансформация самое важное",   "2026-03-26"),
    "W2 System Architecture.mp4":                   ("W2 System Architecture",                    "2026-03-31"),
    "Виталий Клебан.mp4":                           ("Виталий Клебан",                            "2026-04-02"),
}

TEMP_DIR = Path("/tmp/whisper_audio")
TEMP_DIR.mkdir(exist_ok=True)


def detect_whisper():
    """Return ('openai', model_fn) or ('faster', model_fn)."""
    try:
        import whisper
        print("✓ Using openai-whisper")
        return "openai"
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel
        print("✓ Using faster-whisper")
        return "faster"
    except ImportError:
        pass
    sys.exit("❌  No whisper found. Run:  pip install openai-whisper  OR  pip install faster-whisper")


def extract_audio(video_path: Path, audio_path: Path):
    """Extract mono 16-kHz WAV with ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-i", str(video_path), "-vn",
         "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         str(audio_path), "-y", "-loglevel", "error"],
        check=True
    )


def fmt_time(seconds: float) -> str:
    m, s = int(seconds // 60), int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def transcribe_openai(audio_path: Path) -> list[tuple[float, str]]:
    import whisper
    model = _openai_model_cache()
    result = model.transcribe(str(audio_path), language="ru", verbose=False)
    return [(seg["start"], seg["text"].strip()) for seg in result["segments"] if seg["text"].strip()]


def transcribe_faster(audio_path: Path) -> list[tuple[float, str]]:
    from faster_whisper import WhisperModel
    model = _faster_model_cache()
    segments, _ = model.transcribe(str(audio_path), language="ru", beam_size=5,
                                   vad_filter=True)
    return [(seg.start, seg.text.strip()) for seg in segments if seg.text.strip()]


# Simple model caches so we load once
_openai_model  = None
_faster_model  = None

def _openai_model_cache():
    global _openai_model
    if _openai_model is None:
        import whisper
        print("  Loading openai-whisper medium model…")
        _openai_model = whisper.load_model("medium")
    return _openai_model

def _faster_model_cache():
    global _faster_model
    if _faster_model is None:
        from faster_whisper import WhisperModel
        print("  Loading faster-whisper medium model…")
        _faster_model = WhisperModel("medium", device="cpu", compute_type="int8")
    return _faster_model


def build_markdown(description: str, date: str, video_name: str,
                   segments: list[tuple[float, str]]) -> str:
    lines = "\n\n".join(f"**{fmt_time(t)}**\n{text}" for t, text in segments)
    return f"""---
tags:
  - type/transcript
date: {date}
status: active
source: whisper
---

# AI Mindset {{{description}}}

**Дата:** {date}
**Источник:** {video_name}

## Транскрипт

{lines}
"""


def main():
    whisper_type = detect_whisper()
    transcribe_fn = transcribe_openai if whisper_type == "openai" else transcribe_faster

    missing = [v for v in VIDEO_MAP if not (VIDEO_DIR / v).exists()]
    if missing:
        print(f"⚠  Videos not found in {VIDEO_DIR}:")
        for m in missing:
            print(f"   • {m}")

    done, skipped, errors = 0, 0, 0

    for video_name, (description, date) in VIDEO_MAP.items():
        video_path = VIDEO_DIR / video_name
        if not video_path.exists():
            continue

        out_name = f"{{course}} {{transcript}} AI Mindset {description} – {date}.md"
        out_path = OUTPUT_DIR / out_name

        if out_path.exists():
            print(f"⏭  Skip (exists): {out_name}")
            skipped += 1
            continue

        print(f"\n▶  {video_name}")
        audio_path = TEMP_DIR / (video_path.stem[:60] + ".wav")

        try:
            print("   Extracting audio…")
            extract_audio(video_path, audio_path)

            print("   Transcribing…")
            segments = transcribe_fn(audio_path)

            md = build_markdown(description, date, video_name, segments)
            out_path.write_text(md, encoding="utf-8")
            print(f"   ✓ Saved → {out_name}")
            done += 1

        except Exception as e:
            print(f"   ✗ Error: {e}")
            errors += 1
        finally:
            if audio_path.exists():
                audio_path.unlink()

    print(f"\n{'─'*60}")
    print(f"Done: {done} transcribed, {skipped} skipped, {errors} errors.")


if __name__ == "__main__":
    main()
