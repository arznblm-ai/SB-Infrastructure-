#!/usr/bin/env python3
"""Extract a compact evidence pack from Codex Desktop session history."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


HOME = Path.home()
CODEX_DIR = HOME / ".codex"
SESSION_INDEX = CODEX_DIR / "session_index.jsonl"
SESSIONS_DIR = CODEX_DIR / "sessions"
LEGACY_HISTORY = CODEX_DIR / "history.jsonl"


DOMAIN_KEYWORDS = {
    "Second Brain / agents": [
        "second brain",
        "claude.md",
        "skill.md",
        "agent",
        "агент",
        "automation",
        "автомат",
        "daily focus",
        "weekly focus",
        "personal context",
    ],
    "Research / strategy": [
        "research",
        "ресерч",
        "competitor",
        "рынок",
        "стратег",
        "offer",
        "icp",
        "superside",
        "ugsee",
    ],
    "GitHub / coding workflow": [
        "github",
        "git",
        "commit",
        "pull request",
        "ci",
        "repo",
        "codex",
        "skills",
    ],
    "Product / business": [
        "продукт",
        "pricing",
        "прайс",
        "roadmap",
        "план работ",
        "клиент",
        "b2b",
        "пакет",
    ],
    "Frontend / app craft": [
        "frontend",
        "html",
        "react",
        "app",
        "кабинет",
        "экран",
        "ui",
        "playwright",
    ],
    "Files / operations": [
        "downloads",
        "desktop",
        "organize",
        "организ",
        "folder",
        "папк",
        "файл",
    ],
}


NOISE_PREFIXES = (
    "<environment_context>",
    "<system_context>",
    "<developer_context>",
    "<permissions",
)


SENSITIVE_PATTERNS = [
    (re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,}\b"), "[REDACTED_TELEGRAM_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(api[_-]?key|api[_-]?hash|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"), r"\1=[REDACTED]"),
]


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def clean_text(text: str, max_chars: int) -> str:
    text = text.strip()
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "..."
    return text


def is_noise(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return True
    if any(stripped.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    if stripped.startswith("<skill>") and len(stripped) > 2000:
        return True
    return False


def load_session_names() -> dict[str, str]:
    names: dict[str, str] = {}
    if not SESSION_INDEX.exists():
        return names
    with SESSION_INDEX.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = item.get("id")
            title = item.get("thread_name")
            if sid and title:
                names[sid] = title
    return names


def extract_from_session(path: Path, max_messages: int, max_chars: int) -> dict | None:
    meta: dict = {}
    messages: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = item.get("payload") or {}
            if item.get("type") == "session_meta":
                meta = payload
                continue
            if item.get("type") != "response_item":
                continue
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            texts: list[str] = []
            for chunk in payload.get("content") or []:
                if isinstance(chunk, dict) and chunk.get("type") == "input_text":
                    text = chunk.get("text") or ""
                    if not is_noise(text):
                        texts.append(clean_text(text, max_chars))
            if texts:
                messages.append(" ".join(texts))
            if len(messages) >= max_messages:
                break
    if not messages:
        return None
    return {"path": path, "meta": meta, "messages": messages}


def extract_legacy_history(cutoff: datetime, max_messages: int, max_chars: int) -> list[dict]:
    if not LEGACY_HISTORY.exists():
        return []
    messages: list[str] = []
    with LEGACY_HISTORY.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = item.get("timestamp")
            if isinstance(timestamp, (int, float)):
                dt = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
                if dt < cutoff:
                    continue
            text = item.get("display") or ""
            if text and not is_noise(text):
                messages.append(clean_text(text, max_chars))
            if len(messages) >= max_messages:
                break
    if not messages:
        return []
    return [{"path": LEGACY_HISTORY, "meta": {"id": "legacy-history"}, "messages": messages}]


def classify(messages: list[str]) -> Counter:
    counter: Counter = Counter()
    joined = "\n".join(messages).lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(joined.count(keyword) for keyword in keywords)
        if score:
            counter[domain] = score
    return counter


def render_markdown(records: list[dict], days: int, cutoff: datetime) -> str:
    names = load_session_names()
    all_messages = [message for record in records for message in record["messages"]]
    domain_counter = classify(all_messages)
    term_counter: Counter = Counter()
    for message in all_messages:
        for term in re.findall(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_+-]{2,}", message.lower()):
            if term not in {"the", "and", "или", "для", "что", "как", "это", "мне", "you", "are"}:
                term_counter[term] += 1

    now = datetime.now(timezone.utc)
    lines = [
        "# Codex Growth Evidence Pack",
        "",
        f"Period: last {days} days ({cutoff.date()} to {now.date()}, UTC)",
        f"Sessions with user messages: {len(records)}",
        f"User requests sampled: {len(all_messages)}",
        "",
        "## Domain Signals",
    ]
    if domain_counter:
        for domain, score in domain_counter.most_common():
            lines.append(f"- {domain}: {score}")
    else:
        lines.append("- No strong domain signals detected.")

    lines.extend(["", "## Repeated Terms"])
    for term, count in term_counter.most_common(25):
        lines.append(f"- {term}: {count}")

    lines.extend(["", "## Sessions"])
    for record in records:
        meta = record["meta"]
        sid = meta.get("id", "unknown")
        title = names.get(sid, "Untitled")
        date = meta.get("timestamp", "unknown date")
        cwd = meta.get("cwd", "")
        path = str(record["path"])
        lines.extend(
            [
                "",
                f"### {title}",
                f"- Date: {date}",
                f"- Session id: {sid}",
                f"- CWD: {cwd}",
                f"- Path: {path}",
                "- User requests:",
            ]
        )
        for index, message in enumerate(record["messages"], 1):
            lines.append(f"  {index}. {message}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Codex Desktop growth-analysis evidence.")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days.")
    parser.add_argument("--out", type=Path, help="Write markdown evidence to this path.")
    parser.add_argument("--max-messages", type=int, default=16, help="Max user messages per session.")
    parser.add_argument("--max-chars", type=int, default=700, help="Max chars per user message.")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    records: list[dict] = []

    if SESSIONS_DIR.exists():
        for path in sorted(SESSIONS_DIR.rglob("*.jsonl")):
            record = extract_from_session(path, args.max_messages, args.max_chars)
            if not record:
                continue
            session_date = parse_iso(record["meta"].get("timestamp"))
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if (session_date and session_date >= cutoff) or mtime >= cutoff:
                records.append(record)

    records.extend(extract_legacy_history(cutoff, args.max_messages, args.max_chars))
    records.sort(key=lambda r: r["meta"].get("timestamp", ""))

    markdown = render_markdown(records, args.days, cutoff)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(args.out)
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
