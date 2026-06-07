#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from faster_whisper import WhisperModel


DEFAULT_OUTPUT_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/transcripts")
NOTE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
TRANSCRIPTS_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/transcripts")
SUMMARIZER_SCRIPT = Path(
    "/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/Transcript summarizer/Scripts/transcript_summarizer.py"
)


def clean_title(name: str) -> str:
    cleaned = name.replace("{", "").replace("}", "")
    cleaned = "".join(ch if ch not in '<>:"/\\|?*\n\r\t' else " " for ch in cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip() or "Untitled"


def format_ts(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_default_output_path(
    video_path: Path,
    output_dir: Path,
    title: str | None,
    date_str: str | None,
    prefix: str,
) -> Path:
    resolved_title = clean_title(title or video_path.stem)
    resolved_date = date_str or datetime.fromtimestamp(video_path.stat().st_mtime).strftime(
        "%Y-%m-%d"
    )
    filename = f"{prefix} {resolved_title} – {resolved_date}.md"
    return output_dir / filename


def extract_note_date(path: Path) -> str | None:
    match = NOTE_DATE_RE.search(path.name)
    return match.group(1) if match else None


def sync_note_timestamp(path: Path, explicit_date: str | None = None) -> None:
    date_str = explicit_date or extract_note_date(path)
    if not date_str:
        return
    timestamp = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=12, minute=0, second=0, microsecond=0
    ).timestamp()
    os.utime(path, (timestamp, timestamp))


def sync_import_timestamp(path: Path) -> None:
    now = datetime.now()
    os.utime(path, (now.timestamp(), now.timestamp()))
    timestamp = now.strftime("%m/%d/%Y %H:%M:%S")
    subprocess.run(
        ["/usr/bin/SetFile", "-d", timestamp, str(path)],
        check=False,
        capture_output=True,
    )


def run_transcript_summarizer(transcript_path: Path) -> Path | None:
    if not transcript_path.resolve().is_relative_to(TRANSCRIPTS_DIR):
        print(
            f"[summary] skipped: transcript is outside transcripts/: {transcript_path}",
            flush=True,
        )
        return None
    if not SUMMARIZER_SCRIPT.exists():
        print(f"[summary] skipped: summarizer script missing: {SUMMARIZER_SCRIPT}", flush=True)
        return None

    result = subprocess.run(
        ["python3", str(SUMMARIZER_SCRIPT), str(transcript_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", flush=True)
    if result.returncode != 0:
        print(f"[summary] failed with exit code {result.returncode}", flush=True)
        return None

    for line in result.stdout.splitlines():
        if line.startswith("SUMMARY_PATH="):
            return Path(line.split("=", 1)[1].strip())
    return None


def transcribe(
    video_path: Path,
    output_path: Path,
    model_name: str,
    beam_size: int,
    language: str | None,
    clip_start: float | None,
    clip_end: float | None,
) -> int:
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    transcribe_kwargs = {
        "beam_size": beam_size,
        "vad_filter": True,
        "condition_on_previous_text": False,
    }
    if language:
        transcribe_kwargs["language"] = language
    if clip_start is not None or clip_end is not None:
        start = 0.0 if clip_start is None else clip_start
        end = clip_end if clip_end is not None else -1
        transcribe_kwargs["clip_timestamps"] = f"{start},{end}"

    segments, info = model.transcribe(str(video_path), **transcribe_kwargs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# {clean_title(output_path.stem)}\n\n")
        handle.write(f"- Source: {video_path.name}\n")
        handle.write(f"- Model: {model_name}\n")
        handle.write(f"- Language: {info.language}\n")
        handle.write(f"- Language probability: {info.language_probability:.3f}\n\n")
        handle.write("## Transcript\n\n")

        segment_count = 0
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            segment_count += 1
            handle.write(
                f"**[{format_ts(segment.start)} - {format_ts(segment.end)}]** {text}\n\n"
            )
            if segment_count % 25 == 0:
                handle.flush()
                print(
                    f"[progress] segments={segment_count} up_to={format_ts(segment.end)}",
                    flush=True,
                )

    sync_import_timestamp(output_path)
    return segment_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a local video file into a Markdown note."
    )
    parser.add_argument("video_path", help="Path to the source video file")
    parser.add_argument(
        "--output-path",
        help="Full path to the output Markdown file. Overrides output-dir/title/date.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output folder for generated notes. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--title", help="Human-friendly note title")
    parser.add_argument("--date", help="Date in YYYY-MM-DD for the output filename")
    parser.add_argument(
        "--prefix",
        default="{course} {transcript}",
        help="Filename prefix used when output-path is not provided",
    )
    parser.add_argument(
        "--model",
        default="tiny",
        help="faster-whisper model name. Use tiny for speed or small for better quality.",
    )
    parser.add_argument("--beam-size", type=int, default=1, help="Whisper beam size")
    parser.add_argument("--language", help="Force a language code such as ru or en")
    parser.add_argument("--clip-start", type=float, help="Optional clip start in seconds")
    parser.add_argument("--clip-end", type=float, help="Optional clip end in seconds")
    parser.add_argument(
        "--skip-summary",
        action="store_true",
        help="Only create the transcript and skip transcript-summarizer.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    video_path = Path(args.video_path).expanduser().resolve()
    if not video_path.exists():
        raise SystemExit(f"Video not found: {video_path}")

    if args.output_path:
        output_path = Path(args.output_path).expanduser().resolve()
    else:
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_path = build_default_output_path(
            video_path=video_path,
            output_dir=output_dir,
            title=args.title,
            date_str=args.date,
            prefix=args.prefix,
        )

    print(f"[start] video={video_path}", flush=True)
    print(f"[start] output={output_path}", flush=True)
    segment_count = transcribe(
        video_path=video_path,
        output_path=output_path,
        model_name=args.model,
        beam_size=args.beam_size,
        language=args.language,
        clip_start=args.clip_start,
        clip_end=args.clip_end,
    )
    if not args.skip_summary:
        summary_path = run_transcript_summarizer(output_path)
        if summary_path:
            print(f"[done] summary={summary_path}", flush=True)
    print(f"[done] segments={segment_count} file={output_path}", flush=True)


if __name__ == "__main__":
    main()
