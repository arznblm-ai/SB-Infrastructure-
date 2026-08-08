#!/usr/bin/env python3
"""Batch-download Instagram reels into a per-author folder and build auto notes.

Agent-driven CHANNEL workflow (NO automated profile scraping — keeps the IG
account safe). The flow:

  1. You provide a LIST of single reel/post URLs (Anton pastes them, or the
     agent gathers them manually from the profile in-session via the browser).
  2. This script downloads + transcribes each via UGC Downloader into
     `transcripts/external resources/<author>/` and builds the AUTO note.
  3. The agent then ENRICHES each note (full analysis: суть, инсайты, готовые
     решения, Strategic Board, verified links) with:
       external_resource_note.py --path <note> --summary ... --essence ...

Only specific reel/post URLs are downloaded (low account risk); a bare profile
feed is never enumerated here.

Usage:
  batch_reels.py --author longevitypenguin URL1 URL2 URL3 ...
  batch_reels.py --author longevitypenguin --url-file /tmp/reels.txt
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from external_resource_note import build_auto_note
from link_inbox_common import EXTERNAL_TRANSCRIPTS_DIR, canonicalize_url, clean_filename_part, load_config

UGC_SCRIPT = Path("/Users/anton/AI AGENT FOLDER/Second Brain/infrastructure/UGC Downloader/ugc_downloader.py")
REEL_PATH_RE = re.compile(r"/(reel|reels|p|tv)/", re.IGNORECASE)
SHORTCODE_RE = re.compile(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
AUTH_ERRORS = (
    "empty media response",
    "login required",
    "rate-limit",
    "rate limit",
    "requested content is not available",
    "use --cookies",
)


def author_folder(author: str) -> Path:
    safe = clean_filename_part(author.lstrip("@"), max_len=60) or "unknown-author"
    return EXTERNAL_TRANSCRIPTS_DIR / safe


def already_have(folder: Path, url: str) -> Path | None:
    m = SHORTCODE_RE.search(url)
    if not m or not folder.exists():
        return None
    code = m.group(1)
    for path in folder.glob("*.md"):
        head = path.read_text(encoding="utf-8", errors="replace")[:600]
        if f'shortcode: "{code}"' in head or code in path.name:
            return path
    return None


def download_one(url: str, dest: Path, cookies: str, model: str) -> tuple[str | None, str]:
    env = os.environ.copy() | {"UGC_TRANSCRIPTS_DIR": str(dest)}
    cmd = [
        sys.executable,
        str(UGC_SCRIPT),
        "--whisper-model",
        model,
        "--max-frames",
        "4",
        "--skip-second-brain-summary",
    ]
    if cookies:
        cmd += ["--cookies-from-browser", cookies]
    cmd.append(url)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    transcript = ""
    for line in res.stdout.splitlines():
        if line.startswith("[transcript] "):
            transcript = line.split("] ", 1)[1].strip()
    if transcript and Path(transcript).exists():
        return transcript, ""
    combined = f"{res.stdout}\n{res.stderr}".lower()
    if any(m in combined for m in AUTH_ERRORS):
        return None, "Instagram требует авторизацию/cookies или пост недоступен."
    tail = (res.stderr or res.stdout or "").strip().splitlines()
    return None, (tail[-1] if tail else f"exit {res.returncode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-download reels into a per-author folder + build auto notes.")
    parser.add_argument("--author", required=True, help="Author username / channel handle (folder name).")
    parser.add_argument("urls", nargs="*", help="Reel/post URLs.")
    parser.add_argument("--url-file", help="Text file with one URL per line.")
    parser.add_argument("--cookies-from-browser", default=None)
    parser.add_argument("--whisper-model", default=None)
    parser.add_argument("--config", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    ugc_cfg = config.get("ugc", {})
    cookies = args.cookies_from_browser or ugc_cfg.get("cookies_from_browser") or ""
    model = args.whisper_model or ugc_cfg.get("whisper_model") or "small"

    urls: list[str] = list(args.urls)
    if args.url_file:
        urls += [ln.strip() for ln in Path(args.url_file).expanduser().read_text(encoding="utf-8").splitlines() if ln.strip()]
    urls = [canonicalize_url(u) for u in urls]

    dest = author_folder(args.author)
    dest.mkdir(parents=True, exist_ok=True)

    built, skipped, failed = [], [], []
    for url in urls:
        if not REEL_PATH_RE.search(urlparse(url).path):
            failed.append((url, "не reel/пост URL (профиль или иная страница)"))
            continue
        existing = already_have(dest, url)
        if existing:
            skipped.append((url, existing.name))
            continue
        transcript, err = download_one(url, dest, cookies, model)
        if not transcript:
            failed.append((url, err))
            continue
        note = Path(transcript)
        note.write_text(build_auto_note({"url": url, "kind": "instagram"}, note.read_text(encoding="utf-8")), encoding="utf-8")
        built.append(note)

    print(f"AUTHOR_FOLDER={dest}")
    print(f"\nBUILT ({len(built)}):")
    for p in built:
        print(f"  {p}")
    print(f"\nSKIPPED already present ({len(skipped)}):")
    for url, name in skipped:
        print(f"  {url} -> {name}")
    print(f"\nFAILED ({len(failed)}):")
    for url, why in failed:
        print(f"  {url}: {why}")
    print("\nNEXT: enrich each BUILT note with external_resource_note.py --path <note> --summary ... --essence ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
