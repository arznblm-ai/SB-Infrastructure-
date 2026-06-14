#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

VAULT = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
CONFIG_DIR = Path.home() / ".config" / "link-inbox"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_STATE_FILE = CONFIG_DIR / "state.json"
DEFAULT_LOG_FILE = Path.home() / "Library" / "Logs" / "link-inbox.log"
DEFAULT_OUTPUT_DIR = "resources/link-inbox"
EXTERNAL_TRANSCRIPTS_DIR = VAULT / "transcripts" / "external resources"

URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
BAD_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\n\r\t]+')
MULTISPACE_RE = re.compile(r"\s+")
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igsh",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "si",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "telegram": {
        "api_id": "",
        "api_hash": "",
        "session_file": "~/.config/link-inbox/telegram.session",
        "source_channel": "",
        "review_channel": "",
        "allowed_chat_ids": [],
        "max_messages": 100,
        "bot_token_env": "LINK_INBOX_BOT_TOKEN",
    },
    "vault_path": str(VAULT),
    "output_dir": DEFAULT_OUTPUT_DIR,
    "state_file": str(DEFAULT_STATE_FILE),
    "log_file": str(DEFAULT_LOG_FILE),
    "youtube": {
        "enabled": True,
        "model": "tiny",
        "language": None,
    },
    "ugc": {
        "enabled": True,
        "whisper_model": "tiny",
        "language": None,
        "max_frames": 4,
        "skip_second_brain_summary": True,
        "cookies_from_browser": "",
        "cookies": "",
    },
    "review": {
        "max_items": 12,
    },
}

_LOGGING_READY = False


def expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = expand_path(config_path or CONFIG_FILE)
    if not path.exists():
        ensure_dir(path.parent)
        write_json(path, DEFAULT_CONFIG)
    config = deep_merge(DEFAULT_CONFIG, read_json(path, DEFAULT_CONFIG))
    config["config_file"] = path
    config["vault_path"] = expand_path(config["vault_path"])
    config["output_root"] = ensure_dir(config["vault_path"] / config["output_dir"])
    config["state_file"] = expand_path(config["state_file"])
    config["log_file"] = expand_path(config["log_file"])
    config["telegram"]["session_file"] = expand_path(config["telegram"]["session_file"])
    return config


def configure_logging(config: dict[str, Any], verbose: bool = False) -> logging.Logger:
    global _LOGGING_READY
    logger = logging.getLogger("link_inbox")
    if _LOGGING_READY:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    ensure_dir(config["log_file"].parent)
    file_handler = logging.FileHandler(config["log_file"], encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    _LOGGING_READY = True
    return logger


def default_state() -> dict[str, Any]:
    return {
        "telegram": {"last_message_id": 0},
        "links": {},
        "reviews": {"last_reviewed_at": None},
    }


def load_state(config: dict[str, Any]) -> dict[str, Any]:
    state = read_json(config["state_file"], default_state())
    state.setdefault("telegram", {}).setdefault("last_message_id", 0)
    state.setdefault("telegram_bot", {}).setdefault("offset", None)
    state.setdefault("links", {})
    state.setdefault("reviews", {}).setdefault("last_reviewed_at", None)
    return state


def save_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    write_json(config["state_file"], state)


def canonicalize_url(url: str) -> str:
    cleaned = url.strip().rstrip(".,;:)]}")
    parsed = urlparse(cleaned)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc.lower(),
            path,
            parsed.params,
            urlencode(query, doseq=True),
            "",
        )
    )


def extract_urls(text: str) -> list[str]:
    urls = []
    seen = set()
    for raw in URL_RE.findall(text or ""):
        url = canonicalize_url(raw)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def url_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def clean_filename_part(text: str, max_len: int = 64) -> str:
    text = html.unescape(text or "")
    text = BAD_FILENAME_CHARS_RE.sub(" ", text)
    text = MULTISPACE_RE.sub(" ", text).strip()
    if not text:
        text = "untitled link"
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].strip() or text[:max_len].strip()
    return text


def link_kind(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "web"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return datetime.now().date().isoformat()


def paths(config: dict[str, Any]) -> dict[str, Path]:
    root = config["output_root"]
    return {
        "root": root,
        "links": ensure_dir(root / "links"),
        "summaries": ensure_dir(root / "summaries"),
        "transcripts": ensure_dir(EXTERNAL_TRANSCRIPTS_DIR),
        "reviews": ensure_dir(root / "reviews"),
        "quality_reports": ensure_dir(root / "quality-reports"),
        "quarantine": ensure_dir(root / "quarantine"),
        "state": ensure_dir(root / "state"),
    }


def link_note_path(config: dict[str, Any], record: dict[str, Any]) -> Path:
    title = clean_filename_part(record.get("title") or record.get("url"))
    return paths(config)["links"] / f"{{link}} {title} – {record['date']}.md"


def write_link_note(config: dict[str, Any], record: dict[str, Any]) -> Path:
    path = link_note_path(config, record)
    record["note_path"] = str(path)
    lines = [
        "---",
        "tags:",
        "  - type/link",
        "  - source/telegram",
        f"  - status/{record.get('status', 'new')}",
        f"date: {record['date']}",
        f"source_url: \"{record['url']}\"",
        f"kind: {record.get('kind', 'web')}",
        f"status: {record.get('status', 'new')}",
        f"message_id: {record.get('message_id', '')}",
        f"channel: \"{record.get('channel', '')}\"",
        "---",
        "",
        f"# {record.get('title') or record['url']}",
        "",
        f"- URL: {record['url']}",
        f"- Kind: `{record.get('kind', 'web')}`",
        f"- Status: `{record.get('status', 'new')}`",
        f"- Added: {record.get('created_at', '')}",
        "",
    ]
    if record.get("transcript_path"):
        lines.append(f"- Transcript: `{record['transcript_path']}`")
    if record.get("brief_path"):
        lines.append(f"- Brief: `{record['brief_path']}`")
    if record.get("video_path"):
        lines.append(f"- Video: `{record['video_path']}`")
    if record.get("summary_path"):
        lines.append(f"- Summary: `{record['summary_path']}`")
    if record.get("error"):
        lines.extend(["", "## Error", "", str(record["error"])])
    if record.get("message_text"):
        lines.extend(["", "## Original Telegram Message", "", record["message_text"].strip()])
    if record.get("excerpt"):
        lines.extend(["", "## Extracted Excerpt", "", record["excerpt"].strip()])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
