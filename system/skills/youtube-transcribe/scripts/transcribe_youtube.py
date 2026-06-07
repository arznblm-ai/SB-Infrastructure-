#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel


DEFAULT_OUTPUT_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/transcripts")
TRANSCRIPTS_DIR = Path("/Users/anton/AI AGENT FOLDER/Second Brain/transcripts")
SUMMARIZER_SCRIPT = Path(
    "/Users/anton/AI AGENT FOLDER/Second Brain/projects/Transcript summarizer/Scripts/transcript_summarizer.py"
)


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
        [
            "python3",
            str(SUMMARIZER_SCRIPT),
            "--preferred-folder",
            "education",
            str(transcript_path),
        ],
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


def clean_title(name: str) -> str:
    cleaned = name.replace("{", "").replace("}", "")
    cleaned = "".join(ch if ch not in '<>:"/\\|?*\n\r\t' else " " for ch in cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip() or "Untitled"


def truncate_filename_title(title: str, max_length: int = 56) -> str:
    title = clean_title(title)
    if len(title) <= max_length:
        return title
    return title[:max_length].rstrip()


def format_ts(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_date(upload_date: str | None) -> str:
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return datetime.now().strftime("%Y-%m-%d")


def build_default_output_path(
    output_dir: Path,
    title: str,
    date_str: str,
    prefix: str,
) -> Path:
    resolved_title = truncate_filename_title(title)
    filename = f"{prefix} {resolved_title} – {date_str}.md"
    return output_dir / filename


def download_audio(url: str, temp_dir: Path) -> tuple[Path, dict]:
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": False,
        "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded_path = Path(ydl.prepare_filename(info)).resolve()
    if not downloaded_path.exists():
        raise SystemExit(f"Downloaded file not found: {downloaded_path}")
    return downloaded_path, info


def transcribe_audio(
    audio_path: Path,
    output_path: Path,
    title: str,
    model_name: str,
    beam_size: int,
    language: str | None,
    clip_start: float | None,
    clip_end: float | None,
    source_url: str,
    channel: str | None,
    upload_date: str,
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

    segments, info = model.transcribe(str(audio_path), **transcribe_kwargs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    note_title = clean_title(title)

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("---\n")
        handle.write("tags:\n")
        handle.write("  - type/transcript\n")
        handle.write("  - project/self\n")
        handle.write("  - source/youtube\n")
        handle.write(f"date: {upload_date}\n")
        handle.write("status: active\n")
        handle.write(f"url: {source_url}\n")
        if channel:
            handle.write(f"channel: {channel}\n")
        handle.write("---\n\n")

        handle.write(f"# {note_title}\n\n")
        handle.write(f"- Source: {audio_path.name}\n")
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
        description="Download a public YouTube video audio track and save a Markdown transcript."
    )
    parser.add_argument("url", help="Public YouTube URL")
    parser.add_argument("--output-path", help="Full path to the output Markdown file")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output folder for generated notes. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--title", help="Override the note title")
    parser.add_argument("--date", help="Override the filename date in YYYY-MM-DD")
    parser.add_argument(
        "--prefix",
        default="{self} {transcript}",
        help="Filename prefix used when output-path is not provided",
    )
    parser.add_argument(
        "--model",
        default="tiny",
        help="faster-whisper model name. Use tiny for speed or small for better quality.",
    )
    parser.add_argument("--beam-size", type=int, default=1, help="Whisper beam size")
    parser.add_argument("--language", help="Force a language code such as en or ru")
    parser.add_argument(
        "--keep-download",
        action="store_true",
        help="Keep the temporary downloaded audio file instead of deleting it",
    )
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
    output_dir = Path(args.output_dir).expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="youtube-transcribe-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        print(f"[start] url={args.url}", flush=True)
        audio_path, info = download_audio(args.url, temp_dir)

        title = args.title or info.get("title") or "Untitled YouTube Video"
        upload_date = args.date or normalize_date(info.get("upload_date"))
        channel = info.get("channel")

        if args.output_path:
            output_path = Path(args.output_path).expanduser().resolve()
        else:
            output_path = build_default_output_path(
                output_dir=output_dir,
                title=title,
                date_str=upload_date,
                prefix=args.prefix,
            )

        print(f"[start] audio={audio_path}", flush=True)
        print(f"[start] output={output_path}", flush=True)

        segment_count = transcribe_audio(
            audio_path=audio_path,
            output_path=output_path,
            title=title,
            model_name=args.model,
            beam_size=args.beam_size,
            language=args.language,
            clip_start=args.clip_start,
            clip_end=args.clip_end,
            source_url=args.url,
            channel=channel,
            upload_date=upload_date,
        )

        if args.keep_download:
            preserved = output_path.with_suffix(audio_path.suffix)
            shutil.copy2(audio_path, preserved)
            print(f"[done] preserved_audio={preserved}", flush=True)

        if not args.skip_summary:
            summary_path = run_transcript_summarizer(output_path)
            if summary_path:
                print(f"[done] summary={summary_path}", flush=True)

        print(f"[done] segments={segment_count} file={output_path}", flush=True)


if __name__ == "__main__":
    main()
