#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Transcribe AI Mindset Sprint videos → education folder
# Usage: bash ~/path/to/education/transcribe_videos.sh
# ──────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIDEO_DIR="$(dirname "$SCRIPT_DIR")/Ai Mindset Sprint Videos"
OUTPUT_DIR="$SCRIPT_DIR"
TEMP_DIR="/tmp/whisper_transcripts"

mkdir -p "$TEMP_DIR"

# Check dependencies
if ! command -v whisper &>/dev/null; then
  echo "❌  whisper not found. Install: pip install openai-whisper"
  exit 1
fi
if ! command -v ffmpeg &>/dev/null; then
  echo "❌  ffmpeg not found. Install: brew install ffmpeg"
  exit 1
fi

echo "✓ whisper: $(whisper --version 2>&1 | head -1)"
echo "✓ ffmpeg found"
echo "Video dir: $VIDEO_DIR"
echo "Output dir: $OUTPUT_DIR"
echo ""

# ── Helper: transcribe one video ──────────────────────────────────────────────
transcribe_video() {
  local video_file="$1"
  local description="$2"
  local date="$3"
  local video_name
  video_name="$(basename "$video_file")"
  local out_file="$OUTPUT_DIR/{course} {transcript} AI Mindset $description – $date.md"

  if [[ -f "$out_file" ]]; then
    echo "⏭  Skip (exists): $(basename "$out_file")"
    return
  fi

  echo "▶  $video_name"

  # Extract audio
  local audio_file="$TEMP_DIR/audio_$$.wav"
  ffmpeg -i "$video_file" -vn -acodec pcm_s16le -ar 16000 -ac 1 \
    "$audio_file" -y -loglevel error 2>/dev/null

  # Transcribe with whisper
  local whisper_out="$TEMP_DIR/out_$$"
  mkdir -p "$whisper_out"
  whisper "$audio_file" \
    --model medium \
    --language Russian \
    --output_format txt \
    --output_dir "$whisper_out" \
    --verbose False 2>/dev/null

  local txt_file
  txt_file=$(find "$whisper_out" -name "*.txt" | head -1)

  if [[ ! -f "$txt_file" ]]; then
    echo "   ✗ Transcription failed"
    rm -f "$audio_file"
    rm -rf "$whisper_out"
    return
  fi

  # Build markdown file
  cat > "$out_file" <<MDEOF
---
tags:
  - type/transcript
date: $date
status: active
source: whisper
---

# AI Mindset {$description}

**Дата:** $date
**Источник:** $video_name

## Транскрипт

$(cat "$txt_file")
MDEOF

  echo "   ✓ Saved: $(basename "$out_file")"

  rm -f "$audio_file"
  rm -rf "$whisper_out"
}

# ── Process all videos ────────────────────────────────────────────────────────
transcribe_video "$VIDEO_DIR/AI Mindset AI-Native prework #1.mp4"          "prework 1 AI Native"                       "2026-03-26"
transcribe_video "$VIDEO_DIR/AI Mindset AI-Native prework 2.mp4"           "prework 2 AI Native"                       "2026-03-26"
transcribe_video "$VIDEO_DIR/AI Mindset AI-Native {prework} #3.mp4"        "prework 3 AI Native"                       "2026-03-26"
transcribe_video "$VIDEO_DIR/AI Mindset AI-Native {prework} #4.mp4"        "prework 4 AI Native"                       "2026-03-26"
transcribe_video "$VIDEO_DIR/AI Mindset S2 W1 Personal  Team Workshop.mp4" "S2 W1 Personal Team Workshop"              "2026-03-26"
transcribe_video "$VIDEO_DIR/AI Mindset S2 W1 Workshop.mp4"                "S2 W1 Workshop"                            "2026-03-29"
transcribe_video "$VIDEO_DIR/AI Tools Session.mp4"                         "AI Tools Session"                          "2026-03-31"
transcribe_video "$VIDEO_DIR/AI Transformation for Organizations.mp4"      "AI Transformation for Organizations"       "2026-03-31"
transcribe_video "$VIDEO_DIR/How to Build a Business Factory - Evgeny Lisovsky.mp4" "How to Build a Business Factory"  "2026-03-31"
transcribe_video "$VIDEO_DIR/Personal OS Setup.mp4"                        "Personal OS Setup"                         "2026-03-26"
transcribe_video "$VIDEO_DIR/W1 Почему ИИ-трансформация самое важное.mp4"  "W1 Почему ИИ-трансформация самое важное"   "2026-03-26"
transcribe_video "$VIDEO_DIR/W2 System Architecture.mp4"                   "W2 System Architecture"                    "2026-03-31"
transcribe_video "$VIDEO_DIR/Виталий Клебан.mp4"                           "Виталий Клебан"                            "2026-04-02"

echo ""
echo "✅  All done! Check your education folder."
