#!/usr/bin/env python3
"""Export Codex Desktop sessions into Second Brain.

Creates two durable artifacts:
- raw JSONL copies for archival fidelity
- readable Markdown summaries for search and future analysis
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


HOME = Path.home()
CODEX_DIR = HOME / ".codex"
CODEX_SESSIONS = CODEX_DIR / "sessions"
CODEX_SESSION_INDEX = CODEX_DIR / "session_index.jsonl"
VAULT = HOME / "AI AGENT FOLDER" / "Second Brain"
EXPORT_ROOT = VAULT / "sessions" / "codex"
EXPORTS_DIR = EXPORT_ROOT / "exports"
RAW_DIR = EXPORT_ROOT / "raw"
STATE_DIR = EXPORT_ROOT / "state"
STATE_FILE = STATE_DIR / "export_state.json"
INDEX_FILE = EXPORT_ROOT / "index.md"


SENSITIVE_PATTERNS = [
    (re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{30,}\b"), "[REDACTED_TELEGRAM_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(api[_-]?key|api[_-]?hash|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"), r"\1=[REDACTED]"),
]


NOISE_PREFIXES = (
    "<environment_context>",
    "<system_context>",
    "<developer_context>",
    "<permissions",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Codex Desktop sessions to Second Brain.")
    parser.add_argument("--days", type=int, default=3, help="Look back this many days for changed sessions.")
    parser.add_argument("--all", action="store_true", help="Export every available session.")
    parser.add_argument("--include-raw", action="store_true", help="Also write redacted raw JSONL copies.")
    parser.add_argument("--max-message-chars", type=int, default=2000, help="Max chars per message in markdown.")
    return parser.parse_args()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"sessions": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"sessions": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    if not CODEX_SESSION_INDEX.exists():
        return titles
    with CODEX_SESSION_INDEX.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = item.get("id")
            title = item.get("thread_name")
            if sid and title:
                titles[sid] = title
    return titles


def redact(text: str) -> str:
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_raw_json(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key == "encrypted_content":
                sanitized[key] = "[REDACTED_ENCRYPTED_CONTENT]"
            elif key == "base_instructions":
                sanitized[key] = "[REDACTED_BASE_INSTRUCTIONS]"
            else:
                sanitized[key] = sanitize_raw_json(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_raw_json(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def write_redacted_raw(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open(encoding="utf-8") as src, destination.open("w", encoding="utf-8") as dst:
        for line in src:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                dst.write(redact(line))
                continue
            dst.write(json.dumps(sanitize_raw_json(item), ensure_ascii=False, separators=(",", ":")) + "\n")


def clean_text(text: str, max_chars: int) -> str:
    text = redact(text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "..."
    return text


def is_noise(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return True
    return any(stripped.startswith(prefix) for prefix in NOISE_PREFIXES)


def slugify(value: str, max_len: int = 36) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\wа-яА-ЯёЁ]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:max_len].strip("-") or "untitled"


def short_title(value: str, max_len: int = 42) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len].rstrip(" .,-") or "Untitled"


def extract_session(path: Path, max_message_chars: int) -> dict | None:
    meta: dict = {}
    messages: list[dict] = []
    counts = {"user": 0, "assistant": 0}

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
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in {"user", "assistant"}:
                continue
            chunks = payload.get("content") or []
            texts: list[str] = []
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                text = ""
                if chunk.get("type") == "input_text":
                    text = chunk.get("text") or ""
                elif chunk.get("type") == "output_text":
                    text = chunk.get("text") or ""
                if text and not is_noise(text):
                    texts.append(text)
            if not texts:
                continue
            counts[role] += 1
            messages.append({"role": role, "text": clean_text("\n\n".join(texts), max_message_chars)})

    if not meta and not messages:
        return None
    return {"meta": meta, "messages": messages, "counts": counts}


def markdown_for_session(session: dict, source_path: Path, title: str, digest: str) -> str:
    meta = session["meta"]
    session_id = meta.get("id") or source_path.stem
    started = parse_iso(meta.get("timestamp"))
    date = started.date().isoformat() if started else datetime.fromtimestamp(source_path.stat().st_mtime).date().isoformat()
    cwd = meta.get("cwd", "")
    counts = session["counts"]

    lines = [
        "---",
        "tags:",
        "  - type/transcript",
        "  - project/self",
        "  - topic/codex",
        f"date: {date}",
        "source: codex-desktop",
        f"session_id: {session_id}",
        f"sha256: {digest}",
        "---",
        "",
        f"# Codex session: {title}",
        "",
        f"- Date: {date}",
        f"- Session id: `{session_id}`",
        f"- CWD: `{cwd}`" if cwd else "- CWD: unknown",
        f"- Source file: `{source_path}`",
        f"- Messages: user {counts.get('user', 0)}, assistant {counts.get('assistant', 0)}",
        "",
        "## Conversation",
        "",
    ]

    for index, message in enumerate(session["messages"], 1):
        role = "User" if message["role"] == "user" else "Assistant"
        lines.extend([f"### {index}. {role}", "", message["text"], ""])
    return "\n".join(lines).rstrip() + "\n"


def discover_sessions(args: argparse.Namespace) -> list[Path]:
    if not CODEX_SESSIONS.exists():
        return []
    if args.all:
        return sorted(CODEX_SESSIONS.rglob("*.jsonl"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    paths = []
    for path in CODEX_SESSIONS.rglob("*.jsonl"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if mtime >= cutoff:
            paths.append(path)
    return sorted(paths)


def update_index(exported: list[dict]) -> None:
    all_exports = sorted(EXPORTS_DIR.rglob("*.md"), reverse=True)
    lines = [
        "# Codex Sessions Archive",
        "",
        "Автоархив Codex Desktop sessions для хранения и последующего анализа.",
        "",
        f"Last updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "## Latest Export Run",
    ]
    if exported:
        for item in exported:
            rel = item["markdown"].relative_to(EXPORT_ROOT)
            lines.append(f"- [[{rel.as_posix()}|{item['title']}]]")
    else:
        lines.append("- No changed sessions in this run.")
    lines.extend(["", "## All Exported Sessions"])
    for path in all_exports[:300]:
        rel = path.relative_to(EXPORT_ROOT)
        lines.append(f"- [[{rel.as_posix()}|{path.stem}]]")
    INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.include_raw:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    titles = load_titles()
    state = load_state()
    sessions_state = state.setdefault("sessions", {})
    exported: list[dict] = []

    for source in discover_sessions(args):
        digest = sha256(source)
        source_key = str(source)
        if sessions_state.get(source_key, {}).get("sha256") == digest:
            continue

        session = extract_session(source, args.max_message_chars)
        if not session:
            continue
        meta = session["meta"]
        session_id = meta.get("id") or source.stem
        title = short_title(titles.get(session_id) or meta.get("thread_name") or session_id)
        started = parse_iso(meta.get("timestamp"))
        date = started.date().isoformat() if started else datetime.fromtimestamp(source.stat().st_mtime).date().isoformat()
        year = date[:4]
        month = date[5:7]
        slug = slugify(title)

        export_dir = EXPORTS_DIR / year / month
        raw_dir = RAW_DIR / year / month
        export_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        digest_id = digest[:8]
        markdown_name = f"{{self}} {{transcript}} Codex {slug} {digest_id} – {date}.md"
        markdown_path = export_dir / markdown_name
        raw_path = raw_dir / source.name

        markdown_path.write_text(markdown_for_session(session, source, title, digest), encoding="utf-8")
        if args.include_raw:
            write_redacted_raw(source, raw_path)

        sessions_state[source_key] = {
            "sha256": digest,
            "session_id": session_id,
            "title": title,
            "date": date,
            "markdown": str(markdown_path),
            "raw": str(raw_path) if args.include_raw else None,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        exported.append({"title": title, "markdown": markdown_path})

    state["last_run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    update_index(exported)

    print(f"Export root: {EXPORT_ROOT}")
    print(f"Changed sessions exported: {len(exported)}")
    print(f"Tracked sessions: {len(sessions_state)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
