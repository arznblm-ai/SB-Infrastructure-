#!/usr/bin/env python3
"""Shared helpers for the Research Department scouts."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DEFAULT_VAULT_PATH = Path("/Users/anton/AI AGENT FOLDER/Second Brain")
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "research-department"
DEFAULT_WORK_DIR = Path.home() / ".research-department"
DEFAULT_LOG_FILE = Path.home() / "Library" / "Logs" / "research-department.log"
DEFAULT_OUTPUT_DIR = "education"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_TELEGRAM_CONFIG_FILE = DEFAULT_CONFIG_DIR / "telegram_config.json"
DEFAULT_LAST_RUN_FILE = DEFAULT_CONFIG_DIR / "last_run.json"

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][^\s]*")
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\n\r\t]+')
MULTISPACE_RE = re.compile(r"\s+")
TRACKING_QUERY_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "si",
}

TOPIC_KEYWORDS = {
    "ai-native-business": (
        "agency",
        "агентств",
        "агентство",
        "automation",
        "автоматиза",
        "бизнес",
        "business",
        "operations",
        "ops",
        "workflow",
        "воронк",
        "рост",
        "sales",
    ),
    "creative-ai": (
        "creative",
        "креатив",
        "campaign",
        "кампан",
        "design",
        "дизайн",
        "brand",
        "бренд",
        "video",
        "image",
        "визуал",
        "ugc",
        "ad",
        "ads",
        "реклама",
    ),
    "vibecoding": (
        "vibe",
        "вайб",
        "coding",
        "код",
        "codex",
        "cursor",
        "windsurf",
        "bolt",
        "replit",
        "agent",
        "agents",
        "prompt",
        "developer",
        "разработ",
    ),
}

DEFAULT_CONFIG: dict[str, Any] = {
    "telegram": {
        "api_id": "",
        "api_hash": "",
        "session_file": "~/.config/research-department/telegram.session",
        "channels": [
            "@channel_name_1",
            "@channel_name_2",
        ],
        "max_posts_per_channel": 20,
        "language_filter": ["ru", "en"],
    },
    "web": {
        "queries": [
            {
                "query": "AI native business systems and agency automation",
                "topic": "ai-native-business",
            },
            {
                "query": "creative AI campaigns and advertising case studies",
                "topic": "creative-ai",
            },
            {
                "query": "vibecoding workflows with AI coding agents",
                "topic": "vibecoding",
            },
        ],
        "max_results_per_query": 10,
        "freshness_days": 2,
        "language_filter": ["ru", "en"],
        "region": "wt-wt",
        "safesearch": "off",
    },
    "vault_path": str(DEFAULT_VAULT_PATH),
    "output_dir": DEFAULT_OUTPUT_DIR,
    "last_run_file": str(DEFAULT_LAST_RUN_FILE),
    "log_file": str(DEFAULT_LOG_FILE),
}

_LOGGING_CONFIGURED = False


def default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)


def expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


def save_json_file(path: Path, data: Any) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_runtime_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config_file = expand_path(config_path or DEFAULT_CONFIG_FILE)
    raw_config = load_json_file(config_file, default_config())
    config = deep_merge(default_config(), raw_config)

    telegram_config_file = DEFAULT_TELEGRAM_CONFIG_FILE
    telegram_credentials = load_json_file(telegram_config_file, {})
    if telegram_credentials:
        config["telegram"] = deep_merge(config.get("telegram", {}), telegram_credentials)

    config["config_file"] = config_file
    config["config_dir"] = DEFAULT_CONFIG_DIR
    config["work_dir"] = DEFAULT_WORK_DIR
    config["vault_path"] = expand_path(config["vault_path"])
    config["last_run_file"] = expand_path(config["last_run_file"])
    config["log_file"] = expand_path(config["log_file"])
    config["telegram"]["session_file"] = expand_path(config["telegram"]["session_file"])
    config["telegram_config_file"] = telegram_config_file
    return config


def configure_logging(log_path: str | Path, verbose: bool = False) -> logging.Logger:
    global _LOGGING_CONFIGURED

    logger = logging.getLogger("research_department")
    if _LOGGING_CONFIGURED:
        return logger

    log_file = expand_path(log_path)
    ensure_directory(log_file.parent)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    _LOGGING_CONFIGURED = True
    return logger


def get_output_dir(config: dict[str, Any]) -> Path:
    output_dir = config.get("output_dir", DEFAULT_OUTPUT_DIR)
    return ensure_directory(config["vault_path"] / output_dir)


def load_last_run_state(config: dict[str, Any]) -> dict[str, Any]:
    state = load_json_file(config["last_run_file"], {})
    if not isinstance(state, dict):
        return {}
    state.setdefault("telegram", {})
    state.setdefault("web", {})
    return state


def save_last_run_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    save_json_file(config["last_run_file"], state)


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_PARAMS
    ]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc,
            path,
            parsed.params,
            urlencode(query, doseq=True),
            "",
        )
    )


def build_source_index(root: Path) -> set[str]:
    index: set[str] = set()
    if not root.exists():
        return index

    for path in root.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        match = re.search(r'(?m)^source_url:\s*["\']?(.+?)["\']?\s*$', text)
        if match:
            index.add(canonicalize_url(match.group(1)))

    return index


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def detect_language_code(text: str) -> str:
    cyrillic = sum(1 for char in text if "а" <= char.lower() <= "я" or char.lower() == "ё")
    latin = sum(1 for char in text if "a" <= char.lower() <= "z")

    if cyrillic == 0 and latin == 0:
        return "unknown"
    if cyrillic >= latin:
        return "ru"
    return "en"


def topic_from_text(text: str) -> str:
    lowered = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return topic
    return "other"


def normalize_text_block(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    normalized = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", normalized)


def strip_markdown_noise(text: str) -> str:
    cleaned = normalize_text_block(text)
    cleaned = cleaned.replace("*", " ").replace("#", " ")
    cleaned = MULTISPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def truncate_words(text: str, max_words: int) -> str:
    words = WORD_RE.findall(strip_markdown_noise(text))
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).strip() + "..."


def title_from_text(text: str, max_words: int = 15, max_chars: int = 110) -> str:
    title = truncate_words(text, max_words=max_words) or "Scout Note"
    if len(title) <= max_chars:
        return title
    return title[: max_chars - 1].rstrip() + "…"


def tldr_from_text(text: str, max_sentences: int = 2, max_words: int = 45) -> str:
    cleaned = strip_markdown_noise(text)
    if not cleaned:
        return "Материал собран автоматически, но текст оказался пустым."

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    chosen: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence:
            chosen.append(sentence)
        if len(chosen) >= max_sentences:
            break

    summary = " ".join(chosen) if chosen else cleaned
    words = WORD_RE.findall(summary)
    if len(words) <= max_words:
        return summary
    return " ".join(words[:max_words]).strip() + "..."


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def render_content_markdown(text: str, full_text_label: str = "Полный текст") -> str:
    normalized = normalize_text_block(text)
    if word_count(normalized) <= 500:
        return normalized

    preview = truncate_words(normalized, 140)
    return (
        f"{preview}\n\n"
        f"<details>\n"
        f"<summary>{full_text_label}</summary>\n\n"
        f"{normalized}\n\n"
        f"</details>"
    )


def sanitize_filename_part(text: str) -> str:
    sanitized = INVALID_FILENAME_CHARS_RE.sub(" ", text)
    sanitized = sanitized.replace("–", " ").replace("—", " ")
    sanitized = MULTISPACE_RE.sub(" ", sanitized).strip(" .")
    return sanitized or "без темы"


def make_scout_filename(description: str, date_str: str) -> str:
    prefix = "{research} {scout} "
    suffix = f" – {date_str}.md"
    max_len = 80 - len(prefix) - len(suffix)
    if max_len < 8:
        max_len = 8

    clean = sanitize_filename_part(description)
    if len(clean) > max_len:
        truncated = clean[:max_len].rstrip()
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        clean = truncated.strip() or clean[:max_len].strip()
    return f"{prefix}{clean}{suffix}"


def build_note_path(
    output_dir: Path,
    description: str,
    date_str: str,
    unique_hint: str | None = None,
) -> Path:
    candidate = output_dir / make_scout_filename(description, date_str)
    if not candidate.exists():
        return candidate

    if unique_hint:
        hinted = output_dir / make_scout_filename(f"{description} {unique_hint}", date_str)
        if not hinted.exists():
            return hinted

    counter = 2
    while True:
        numbered = output_dir / make_scout_filename(f"{description} {counter}", date_str)
        if not numbered.exists():
            return numbered
        counter += 1


def yaml_scalar(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def build_markdown_note(
    *,
    source_tag: str,
    topic: str,
    note_date: str,
    source_url: str,
    source_type: str,
    title: str,
    tldr: str,
    content_markdown: str,
    source_lines: list[str],
    extra_fields: dict[str, Any] | None = None,
) -> str:
    lines = [
        "---",
        "tags:",
        "  - type/scout",
        f"  - topic/{topic}",
        "  - status/unread",
        f"  - source/{source_tag}",
        f"date: {note_date}",
        f"source_url: {yaml_scalar(source_url)}",
        f"source_type: {source_type}",
    ]

    for key, value in (extra_fields or {}).items():
        if value in (None, "", [], {}):
            continue
        lines.append(f"{key}: {yaml_scalar(value)}")

    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            f"> **TL;DR:** {tldr}",
            "",
            "## Содержание",
            "",
            content_markdown.strip(),
            "",
            "## Source",
            "",
            *source_lines,
            "",
        ]
    )
    return "\n".join(lines)


def normalize_queries(raw_queries: list[Any]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for item in raw_queries:
        if isinstance(item, str) and item.strip():
            queries.append({"query": item.strip()})
        elif isinstance(item, dict) and str(item.get("query", "")).strip():
            queries.append(item)
    return queries
