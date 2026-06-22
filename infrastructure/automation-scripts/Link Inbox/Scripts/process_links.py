#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from link_inbox_common import (
    clean_filename_part,
    configure_logging,
    load_config,
    load_state,
    paths,
    save_state,
    today,
    write_link_note,
)
from link_summary_note import write_summary_note

YOUTUBE_SCRIPT = Path("/Users/anton/AI AGENT FOLDER/Second Brain/system/skills/youtube-transcribe/scripts/transcribe_youtube.py")
UGC_SCRIPT = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/UGC Downloader/ugc_downloader.py")
INDEX_SCRIPT = Path(__file__).resolve().parent / "build_external_resources_index.py"
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
DESC_RE = re.compile(r'(?is)<meta\s+[^>]*(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\'](.*?)["\']')
MARKDOWN_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
TRANSIENT_UGC_ERRORS = (
    "failed to resolve",
    "read timed out",
    "connection timed out",
    "temporarily unavailable",
    "remote end closed connection",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process pending Link Inbox URLs.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--ids", nargs="*", help="Process only specific link ids.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def html_text(value: str) -> str:
    import html

    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def fetch_web_metadata(url: str) -> tuple[str, str]:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 LinkInbox/1.0"})
    with urlopen(req, timeout=20) as response:
        raw = response.read(512_000).decode("utf-8", errors="replace")
    title = html_text(TITLE_RE.search(raw).group(1)) if TITLE_RE.search(raw) else url
    desc_match = DESC_RE.search(raw)
    desc = html_text(desc_match.group(1)) if desc_match else ""
    return title, desc


def transcript_title(path: str | None) -> str:
    if not path:
        return ""
    transcript_path = Path(path)
    if not transcript_path.exists():
        return ""
    text = transcript_path.read_text(encoding="utf-8", errors="replace")[:4000]
    match = MARKDOWN_H1_RE.search(text)
    return clean_filename_part(match.group(1), max_len=96) if match else ""


def process_youtube(config: dict, record: dict, logger) -> None:
    if not YOUTUBE_SCRIPT.exists():
        raise RuntimeError(f"YouTube script missing: {YOUTUBE_SCRIPT}")
    out_dir = paths(config)["transcripts"]
    command = [
        "python3",
        str(YOUTUBE_SCRIPT),
        record["url"],
        "--output-dir",
        str(out_dir),
        "--prefix",
        "{link} {transcript}",
        "--date",
        record.get("date") or today(),
        "--skip-summary",
    ]
    youtube_cfg = config.get("youtube", {})
    if youtube_cfg.get("model"):
        command.extend(["--model", str(youtube_cfg["model"])])
    if youtube_cfg.get("language"):
        command.extend(["--language", str(youtube_cfg["language"])])
    logger.info(f"Transcribing YouTube: {record['url']}")
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(f"youtube transcribe failed with code {result.returncode}")
    output = None
    for line in result.stdout.splitlines():
        if line.startswith("[done] transcript="):
            output = line.split("=", 1)[1].strip()
        elif line.startswith("[done] segments=") and " file=" in line:
            output = line.split(" file=", 1)[1].strip()
    record["transcript_path"] = output or ""
    record["title"] = transcript_title(output) or record.get("title") or record["url"]
    record["status"] = "processed"


def process_web(record: dict, logger) -> None:
    title, desc = fetch_web_metadata(record["url"])
    record["title"] = clean_filename_part(title)
    record["excerpt"] = desc
    record["status"] = "processed"
    logger.info(f"Fetched web metadata: {record['url']}")


def process_ugc_video(config: dict, record: dict, logger) -> None:
    if not UGC_SCRIPT.exists():
        raise RuntimeError(f"UGC Downloader script missing: {UGC_SCRIPT}")
    ugc_cfg = config.get("ugc", {})
    command = [
        "python3",
        str(UGC_SCRIPT),
        "--whisper-model",
        str(ugc_cfg.get("whisper_model") or "tiny"),
        "--max-frames",
        str(int(ugc_cfg.get("max_frames", 4) or 0)),
    ]
    if ugc_cfg.get("skip_second_brain_summary", True):
        command.append("--skip-second-brain-summary")
    if ugc_cfg.get("language"):
        command.extend(["--language", str(ugc_cfg["language"])])
    if ugc_cfg.get("cookies_from_browser"):
        command.extend(["--cookies-from-browser", str(ugc_cfg["cookies_from_browser"])])
    if ugc_cfg.get("cookies"):
        command.extend(["--cookies", str(ugc_cfg["cookies"])])
    command.append(record["url"])

    logger.info(f"Processing UGC video: {record['url']}")
    env = os.environ.copy() | {"UGC_TRANSCRIPTS_DIR": str(paths(config)["transcripts"])}
    result = None
    for attempt in range(1, 4):
        result = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 or not any(marker in combined for marker in TRANSIENT_UGC_ERRORS):
            break
        logger.warning(f"Transient UGC error for {record['url']}; retry {attempt}/3")
        time.sleep(10 * attempt)
    assert result is not None
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(f"ugc downloader failed with code {result.returncode}")

    transcript_path = ""
    brief_path = ""
    video_path = ""
    for line in result.stdout.splitlines():
        if line.startswith("[transcript] "):
            transcript_path = line.split("] ", 1)[1].strip()
        elif line.startswith("[brief] "):
            brief_path = line.split("] ", 1)[1].strip()
        elif line.startswith("[downloaded] "):
            video_path = line.split("] ", 1)[1].strip()

    record["transcript_path"] = transcript_path
    record["brief_path"] = brief_path
    record["video_path"] = video_path
    record["title"] = Path(transcript_path).stem if transcript_path else record.get("title") or record["url"]
    record["excerpt"] = "Short-form video downloaded and transcribed through UGC Downloader."
    record["status"] = "processed"


def rebuild_external_resources_index(config: dict, logger) -> None:
    if not INDEX_SCRIPT.exists():
        logger.warning(f"External resources index script missing: {INDEX_SCRIPT}")
        return
    result = subprocess.run(
        [sys.executable, str(INDEX_SCRIPT), "--config", str(config["config_file"])],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    if result.returncode != 0:
        logger.warning(f"External resources index rebuild failed with code {result.returncode}")


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    logger = configure_logging(config, verbose=args.verbose)
    state = load_state(config)
    processed = 0
    failed = 0
    target_ids = set(args.ids or [])

    for uid, record in list(state["links"].items()):
        if target_ids and uid not in target_ids:
            continue
        if processed >= args.limit:
            break
        if record.get("status") not in {"new", "failed", "needs_manual_processing"}:
            continue
        if record.get("status") == "needs_manual_processing" and record.get("kind") not in {"instagram", "tiktok"}:
            continue
        try:
            if record.get("kind") == "youtube":
                if args.skip_youtube or not config.get("youtube", {}).get("enabled", True):
                    record["status"] = "needs_manual_processing"
                else:
                    process_youtube(config, record, logger)
            elif record.get("kind") in {"instagram", "tiktok"}:
                if not config.get("ugc", {}).get("enabled", True):
                    record["status"] = "needs_manual_processing"
                    record["excerpt"] = "Short-form video link detected, but UGC processing is disabled."
                else:
                    process_ugc_video(config, record, logger)
            else:
                process_web(record, logger)
            if record.get("status") == "processed":
                write_summary_note(config, record)
            write_link_note(config, record)
            state["links"][uid] = record
            processed += 1
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            write_link_note(config, record)
            state["links"][uid] = record
            failed += 1
            logger.exception(f"Failed to process {record.get('url')}: {exc}")

    save_state(config, state)
    rebuild_external_resources_index(config, logger)
    logger.info(f"Process done: processed={processed}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
