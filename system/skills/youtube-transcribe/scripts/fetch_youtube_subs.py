#!/usr/bin/env python3
"""Fetch YouTube auto-captions (no audio download) and write external-resource notes.

Companion to `transcribe_youtube.py`: same family, same note conventions, but
instead of downloading `bestaudio` and running parakeet locally (minutes per
video) it pulls the channel's automatic captions track and parses the VTT
(seconds per video). Use it for batch ingest of talking-head channels that have
auto-captions; fall back to `transcribe_youtube.py` for videos without any
caption track.

Notes are produced by the canonical Link Inbox builder
(`infrastructure/Link Inbox/Scripts/external_resource_note.py`), so every file
carries `type: external-resource` and lands in the external-resources index.

Examples
--------
    # single video into a channel subfolder
    fetch_youtube_subs.py --url M6mYodf0dJM --subfolder "Matt Pocock"

    # batch from a list ("ID | human readable title" lines, "#" comments)
    fetch_youtube_subs.py --from-file /tmp/list.txt --subfolder "Matt Pocock" \
        --state-file ~/.config/second-brain/matt-pocock-subs.json

    # many short videos collapsed into ONE summary note
    fetch_youtube_subs.py --from-file /tmp/shorts.txt --subfolder "Matt Pocock" \
        --combine "{self} {transcript} Matt Pocock AI shorts сводка – 2026-08-06.md" \
        --combine-title "Matt Pocock AI shorts сводка"
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yt_dlp

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
DEFAULT_OUTPUT_DIR = VAULT / "transcripts" / "external resources"
LINK_INBOX_SCRIPTS = VAULT / "infrastructure" / "Link Inbox" / "Scripts"
DEFAULT_STATE_FILE = Path("~/.config/second-brain/youtube-subs-state.json").expanduser()

FILENAME_PREFIX = "{self} {transcript}"
FILENAME_MAX = 80
NOTE_MODEL = "youtube-auto-captions"

# yt-dlp: metadata only. `ignore_no_formats_error` is required — without a JS
# runtime some clients expose no playable formats and format selection would
# abort the whole extraction even though we never download media.
BASE_YDL_OPTS = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "ignore_no_formats_error": True,
    "extractor_args": {"youtube": {"player_client": ["web_safari", "mweb"]}},
}

RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "rate-limit",
    "rate limit",
    "temporarily blocked",
    "please try again later",
)
BACKOFF_SECONDS = (60, 180, 420)
MAX_CONSECUTIVE_FAILURES = 5

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CUE_TIME_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
INLINE_TS_RE = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")
TAG_RE = re.compile(r"</?c[^>]*>|</?[ibu]>|</?v[^>]*>|</?ruby>|</?rt>")


# --------------------------------------------------------------------------- #
# Note builder (canonical external-resource format)
# --------------------------------------------------------------------------- #


def load_note_builder():
    if str(LINK_INBOX_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(LINK_INBOX_SCRIPTS))
    from external_resource_note import build_auto_note  # noqa: WPS433

    return build_auto_note


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #


def extract_video_id(raw: str) -> str | None:
    raw = raw.strip()
    if VIDEO_ID_RE.match(raw):
        return raw
    for pattern in (
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"/shorts/([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
        r"/live/([A-Za-z0-9_-]{11})",
    ):
        match = re.search(pattern, raw)
        if match:
            return match.group(1)
    return None


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def read_list_file(path: Path) -> list[str]:
    entries: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        line = line.split("|", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def collect_targets(args: argparse.Namespace) -> list[str]:
    raw_entries: list[str] = list(args.url or [])
    if args.from_file:
        raw_entries.extend(read_list_file(Path(args.from_file).expanduser()))

    ids: list[str] = []
    seen: set[str] = set()
    for entry in raw_entries:
        video_id = extract_video_id(entry)
        if not video_id:
            print(f"[warn] not a YouTube video reference, skipped: {entry}", flush=True)
            continue
        if video_id in seen:
            continue
        seen.add(video_id)
        ids.append(video_id)
    if args.limit:
        ids = ids[: args.limit]
    return ids


# --------------------------------------------------------------------------- #
# VTT parsing
# --------------------------------------------------------------------------- #


def cue_seconds(stamp: str) -> float:
    stamp = stamp.replace(",", ".")
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def clean_cue_text(lines: list[str]) -> str:
    text = " ".join(lines)
    text = INLINE_TS_RE.sub(" ", text)
    text = TAG_RE.sub("", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return " ".join(text.split())


def parse_vtt_cues(vtt: str) -> list[tuple[float, float, str]]:
    """Raw cues as (start, end, text) with markup and entities stripped."""
    cues: list[tuple[float, float, str]] = []
    current: tuple[float, float] | None = None
    buffer: list[str] = []

    for raw in vtt.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        match = CUE_TIME_RE.match(stripped)
        if match:
            if current is not None:
                text = clean_cue_text(buffer)
                if text:
                    cues.append((current[0], current[1], text))
            current = (cue_seconds(match.group(1)), cue_seconds(match.group(2)))
            buffer = []
            continue
        if current is None:
            continue  # header block: WEBVTT / Kind: / Language: / blank
        if not stripped:
            continue
        if stripped.startswith("NOTE") or stripped.startswith("STYLE"):
            continue
        buffer.append(stripped)

    if current is not None:
        text = clean_cue_text(buffer)
        if text:
            cues.append((current[0], current[1], text))
    return cues


def dedupe_cues(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Drop the rolling-caption overlap.

    YouTube auto-captions scroll: every cue repeats the tail of the previous
    one. For each cue we cut the longest leading run of words that matches the
    trailing words already accumulated, and drop the cue if nothing is left.
    Word-level (not char-level) matching keeps words intact.
    """
    tail_window = 60  # words of context kept for overlap matching
    tail: list[str] = []
    out: list[tuple[float, float, str]] = []

    for start, end, text in cues:
        words = text.split()
        if not words:
            continue
        limit = min(len(tail), len(words))
        overlap = 0
        for size in range(limit, 0, -1):
            if tail[len(tail) - size :] == words[:size]:
                overlap = size
                break
        fresh = words[overlap:]
        if not fresh:
            continue
        out.append((start, end, " ".join(fresh)))
        tail = (tail + fresh)[-tail_window:]
    return out


def format_marker(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_paragraphs(
    segments: list[tuple[float, float, str]],
    gap_seconds: float,
    max_chars: int,
) -> list[tuple[float, str]]:
    paragraphs: list[tuple[float, str]] = []
    current: list[str] = []
    current_start = 0.0
    previous_end: float | None = None

    for start, end, text in segments:
        gap = start - previous_end if previous_end is not None else 0.0
        too_long = sum(len(part) + 1 for part in current) >= max_chars
        if current and (gap > gap_seconds or too_long):
            paragraphs.append((current_start, " ".join(current)))
            current = []
        if not current:
            current_start = start
        current.append(text)
        previous_end = end

    if current:
        paragraphs.append((current_start, " ".join(current)))
    return paragraphs


def render_transcript(paragraphs: list[tuple[float, str]]) -> str:
    return "\n\n".join(f"[{format_marker(start)}] {text}" for start, text in paragraphs)


# --------------------------------------------------------------------------- #
# yt-dlp access
# --------------------------------------------------------------------------- #


def build_ydl_opts(args: argparse.Namespace) -> dict:
    opts = dict(BASE_YDL_OPTS)
    opts["sleep_interval_requests"] = 1
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = (args.cookies_from_browser, None, None, None)
    return opts


def fetch_info(video_id: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(watch_url(video_id), download=False)


def pick_caption_track(info: dict, langs: list[str]) -> tuple[str, str]:
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for lang in langs:
        for source in (manual, auto):
            entries = source.get(lang) or []
            for entry in entries:
                if entry.get("ext") == "vtt" and entry.get("url"):
                    return lang, entry["url"]
    available = sorted(set(list(manual) + list(auto)))
    raise RuntimeError(
        f"no vtt caption track for langs={','.join(langs)} "
        f"(available: {', '.join(available[:8]) or 'none'})"
    )


def download_vtt(url: str, opts: dict) -> str:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.urlopen(url).read().decode("utf-8", "replace")


def is_rate_limited(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


# --------------------------------------------------------------------------- #
# Note assembly
# --------------------------------------------------------------------------- #


def normalize_date(upload_date: str | None) -> str:
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return datetime.now().strftime("%Y-%m-%d")


def clean_title(name: str) -> str:
    cleaned = (name or "").replace("{", "(").replace("}", ")")
    cleaned = "".join(ch if ch not in '<>:"/\\|?*\n\r\t' else " " for ch in cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip() or "Untitled"


def truncate_title(title: str, budget: int) -> str:
    title = clean_title(title)
    if len(title) <= budget:
        return title
    cut = title[:budget]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-–—") or title[:budget].strip()


def note_path_for(folder: Path, title: str, date_str: str, video_id: str) -> Path:
    suffix = f" – {date_str}.md"
    budget = FILENAME_MAX - len(FILENAME_PREFIX) - 1 - len(suffix)
    base = truncate_title(title, budget)
    candidate = folder / f"{FILENAME_PREFIX} {base}{suffix}"
    if not candidate.exists() or video_id in candidate.read_text(
        encoding="utf-8", errors="replace"
    ):
        return candidate
    for index in range(2, 10):
        tag = f" {index}"
        base_short = truncate_title(title, budget - len(tag))
        candidate = folder / f"{FILENAME_PREFIX} {base_short}{tag}{suffix}"
        if not candidate.exists() or video_id in candidate.read_text(
            encoding="utf-8", errors="replace"
        ):
            return candidate
    return candidate


def human_duration(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "unknown"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def build_caption(info: dict, lang: str) -> str:
    description = (info.get("description") or "").strip()
    first_line = " ".join(description.split())[:300]
    lines = [
        "## Source",
        "",
        f"- platform: youtube",
        f"- title: {clean_title(info.get('title'))}",
        f"- channel: {info.get('channel') or info.get('uploader') or 'unknown'}",
        f"- handle: {info.get('uploader_id') or 'unknown'}",
        f"- url: {info.get('webpage_url') or watch_url(info.get('id', ''))}",
        f"- published: {normalize_date(info.get('upload_date'))}",
        f"- duration: {human_duration(info.get('duration'))}",
        f"- views: {info.get('view_count') if info.get('view_count') is not None else 'unknown'}",
        f"- captions: {lang} (automatic)",
        f"- description: {first_line or 'unknown'}",
        "",
        "## Description",
        "",
        description or "unknown",
    ]
    return "\n".join(lines)


def build_raw_note(info: dict, lang: str, transcript: str) -> str:
    title = clean_title(info.get("title"))
    return (
        f"# {title}\n\n"
        f"- Source: {info.get('id')} (youtube auto captions)\n"
        f"- Model: {NOTE_MODEL}\n"
        f"- Language: {lang}\n\n"
        f"## Transcript\n\n{transcript}\n"
    )


NO_TOOLS_PLACEHOLDER = "_Инструменты автоматически не выделены._"


def patch_author(note: str, info: dict) -> str:
    """Fill the builder's author fields from channel metadata.

    The shared builder derives author identity from Instagram-style titles, so
    for YouTube it writes "unknown". Same fields, real values.
    """
    handle = (info.get("uploader_id") or "").lstrip("@")
    name = info.get("channel") or info.get("uploader") or ""
    if handle:
        note = note.replace('author_username: "unknown"', f'author_username: "{handle}"', 1)
    if name:
        note = note.replace('author_name: "unknown"', f'author_name: "{name}"', 1)
    return note


def drop_junk_tools(note: str) -> str:
    """Reset the heuristic tool list when it clearly misfired.

    The shared builder's numbered-list heuristic expects one short whisper
    segment per line; on paragraph-shaped transcripts any "7.5 million" style
    figure swallows a whole paragraph as a "tool name". Rather than invent a
    new note shape we fall back to the builder's own empty-state values and let
    the enrich stage fill the section properly.
    """
    match = re.search(r"^tools:\s*\[(.*)\]\s*$", note, flags=re.M)
    if not match:
        return note
    entries = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
    if not entries or all(len(entry) <= 60 for entry in entries):
        return note
    note = note[: match.start()] + "tools: []" + note[match.end() :]
    return re.sub(
        r"(^## Упомянутые инструменты и skills\n\n)(.*?)(?=\n## )",
        lambda m: f"{m.group(1)}{NO_TOOLS_PLACEHOLDER}\n",
        note,
        count=1,
        flags=re.M | re.S,
    )


def write_note(
    build_auto_note,
    folder: Path,
    info: dict,
    lang: str,
    transcript: str,
) -> Path:
    date_str = normalize_date(info.get("upload_date"))
    title = clean_title(info.get("title"))
    video_id = info.get("id") or ""
    path = note_path_for(folder, title, date_str, video_id)
    record = {
        "url": info.get("webpage_url") or watch_url(video_id),
        "kind": "youtube",
        "date": date_str,
        "title": title,
    }
    note = build_auto_note(record, build_raw_note(info, lang, transcript), build_caption(info, lang))
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(drop_junk_tools(patch_author(note, info)), encoding="utf-8")
    return path


def write_combined_note(
    build_auto_note,
    path: Path,
    title: str,
    source_url: str,
    items: list[dict],
) -> Path:
    blocks: list[str] = []
    for item in items:
        info = item["info"]
        blocks.append(
            "\n".join(
                [
                    f"### {clean_title(info.get('title'))}",
                    "",
                    f"- URL: {info.get('webpage_url') or watch_url(info.get('id', ''))}",
                    f"- Published: {normalize_date(info.get('upload_date'))}",
                    f"- Duration: {human_duration(info.get('duration'))}",
                    f"- Views: {info.get('view_count') if info.get('view_count') is not None else 'unknown'}",
                    "",
                    item["transcript"],
                ]
            )
        )
    raw = (
        f"# {title}\n\n"
        f"- Source: {len(items)} youtube shorts (auto captions)\n"
        f"- Model: {NOTE_MODEL}\n"
        f"- Language: en\n\n"
        f"## Transcript\n\n" + "\n\n".join(blocks) + "\n"
    )
    caption_lines = ["## Source", "", "- platform: youtube", f"- url: {source_url}"]
    for item in items:
        info = item["info"]
        caption_lines.append(
            f"- {clean_title(info.get('title'))}: "
            f"{info.get('webpage_url') or watch_url(info.get('id', ''))}"
        )
    record = {
        "url": source_url,
        "kind": "youtube",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "title": title,
    }
    note = build_auto_note(record, raw, "\n".join(caption_lines))
    note = drop_junk_tools(patch_author(note, items[0]["info"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def load_state(path: Path | None) -> dict:
    if path and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("done", {})
            data.setdefault("failed", {})
            return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[warn] unreadable state file ({exc}); starting fresh", flush=True)
    return {"done": {}, "failed": {}}


def save_state(path: Path | None, state: dict) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Per-video pipeline
# --------------------------------------------------------------------------- #


def process_video(video_id: str, args: argparse.Namespace, opts: dict) -> dict:
    """Fetch metadata + captions for one video. Retries only on rate limits."""
    attempt = 0
    while True:
        try:
            info = fetch_info(video_id, opts)
            lang, url = pick_caption_track(info, args.langs)
            vtt = download_vtt(url, opts)
            cues = dedupe_cues(parse_vtt_cues(vtt))
            if not cues:
                raise RuntimeError("caption track parsed to zero lines")
            paragraphs = build_paragraphs(cues, args.paragraph_gap, args.max_paragraph_chars)
            return {
                "info": info,
                "lang": lang,
                "transcript": render_transcript(paragraphs),
                "cues": len(cues),
                "paragraphs": len(paragraphs),
            }
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises a wide range
            message = f"{type(exc).__name__}: {exc}"
            if not is_rate_limited(message) or attempt >= len(BACKOFF_SECONDS):
                raise RuntimeError(message) from exc
            wait = BACKOFF_SECONDS[attempt]
            attempt += 1
            print(
                f"[backoff] {video_id}: rate-limited ({message[:120]}); "
                f"sleeping {wait}s (attempt {attempt}/{len(BACKOFF_SECONDS)})",
                flush=True,
            )
            time.sleep(wait)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch YouTube auto-captions and write external-resource notes."
    )
    parser.add_argument("--url", action="append", default=[], help="Video URL or bare ID (repeatable).")
    parser.add_argument("--from-file", help="File with one URL/ID per line; '#' comments, text after '|' ignored.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--subfolder", default="", help="Subfolder inside output-dir (e.g. channel name).")
    parser.add_argument("--lang", default="en-orig,en", help="Caption languages, first available wins.")
    parser.add_argument("--sleep", type=float, default=5.0, help="Pause between videos in seconds (+/-2s jitter).")
    parser.add_argument("--cookies-from-browser", help="Passed to yt-dlp (chrome, safari, ...) for bot checks.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help=f"Processed-ID ledger. Default: {DEFAULT_STATE_FILE}")
    parser.add_argument("--no-state", action="store_true", help="Ignore and do not write the state file.")
    parser.add_argument("--limit", type=int, help="Process at most N videos.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and exit, no network.")
    parser.add_argument("--combine", help="Write ONE aggregated note at this path/filename instead of per-video notes.")
    parser.add_argument("--combine-title", default="", help="Title for the combined note.")
    parser.add_argument("--paragraph-gap", type=float, default=1.5, help="Silence in seconds that starts a new paragraph.")
    parser.add_argument("--max-paragraph-chars", type=int, default=600, help="Soft paragraph length cap.")
    args = parser.parse_args()
    args.langs = [part.strip() for part in args.lang.split(",") if part.strip()]
    return args


def main() -> int:
    args = parse_args()
    if not args.url and not args.from_file:
        print("[error] nothing to do: pass --url and/or --from-file", flush=True)
        return 2

    output_dir = Path(args.output_dir).expanduser()
    folder = output_dir / args.subfolder if args.subfolder else output_dir
    state_path = None if args.no_state else Path(args.state_file).expanduser()
    state = load_state(state_path)

    targets = collect_targets(args)
    combine_mode = bool(args.combine)
    pending = [vid for vid in targets if vid not in state["done"] or combine_mode]
    skipped = [vid for vid in targets if vid not in pending]

    print(f"[plan] folder={folder}")
    print(f"[plan] videos={len(pending)} skipped_done={len(skipped)} langs={','.join(args.langs)}")
    print(f"[plan] state={state_path or 'disabled'} combine={'yes' if combine_mode else 'no'}")
    if args.dry_run:
        for vid in pending:
            print(f"[plan] {vid} -> {watch_url(vid)}")
        return 0

    opts = build_ydl_opts(args)
    build_auto_note = load_note_builder()

    processed: list[dict] = []
    failures: list[tuple[str, str]] = []
    consecutive = 0

    for index, video_id in enumerate(pending, start=1):
        print(f"[{index}/{len(pending)}] {video_id}", flush=True)
        try:
            result = process_video(video_id, args, opts)
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)
            failures.append((video_id, reason))
            state["failed"][video_id] = reason
            save_state(state_path, state)
            consecutive += 1
            print(f"[fail] {video_id}: {reason[:200]}", flush=True)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"[abort] {consecutive} consecutive failures — stopping instead of "
                    f"hammering YouTube",
                    flush=True,
                )
                break
        else:
            consecutive = 0
            info = result["info"]
            if combine_mode:
                processed.append(result)
                print(
                    f"[ok] {video_id} cues={result['cues']} "
                    f"paragraphs={result['paragraphs']} title={clean_title(info.get('title'))}",
                    flush=True,
                )
            else:
                path = write_note(build_auto_note, folder, info, result["lang"], result["transcript"])
                processed.append(result)
                state["done"][video_id] = {
                    "path": str(path),
                    "title": clean_title(info.get("title")),
                    "at": datetime.now().isoformat(timespec="seconds"),
                }
                state["failed"].pop(video_id, None)
                save_state(state_path, state)
                print(
                    f"[ok] {video_id} cues={result['cues']} paragraphs={result['paragraphs']} "
                    f"file={path.name}",
                    flush=True,
                )

        if index < len(pending):
            pause = max(0.0, args.sleep + random.uniform(-2.0, 2.0))
            time.sleep(pause)

    if combine_mode and processed:
        combine_arg = Path(args.combine).expanduser()
        combined_path = combine_arg if combine_arg.is_absolute() else folder / combine_arg
        title = args.combine_title or combined_path.stem
        channel = processed[0]["info"].get("uploader_id") or ""
        source_url = (
            f"https://www.youtube.com/{channel}/shorts"
            if channel.startswith("@")
            else processed[0]["info"].get("channel_url") or ""
        )
        write_combined_note(build_auto_note, combined_path, title, source_url, processed)
        for result in processed:
            vid = result["info"].get("id")
            if vid:
                state["done"][vid] = {
                    "path": str(combined_path),
                    "title": clean_title(result["info"].get("title")),
                    "at": datetime.now().isoformat(timespec="seconds"),
                }
                state["failed"].pop(vid, None)
        save_state(state_path, state)
        print(f"[done] combined={combined_path}", flush=True)

    print(f"[done] processed={len(processed)} failed={len(failures)} skipped={len(skipped)}")
    for video_id, reason in failures:
        print(f"[failed] {video_id}: {reason[:200]}")
    return 1 if failures and not processed else 0


if __name__ == "__main__":
    raise SystemExit(main())
